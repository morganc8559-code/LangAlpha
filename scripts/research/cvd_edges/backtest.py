"""Backtest of the "CVD Edge Family" report (Visian/EdgeFactory, July 2026).

Two strategies on 5m USDT-perp bars, entries exactly as specified in the report:

Edge 1 — CVD Divergence (absorption):
  - net taker CVD summed over a rolling 4h window (48 bars)
  - z-scored against a 7-day rolling window (2016 bars)
  - trigger: |z| >= 2.5 AND 1h price return (12 bars) opposite in sign to z
  - trade in the direction of z; suppress new signals for 6 bars

Edge 2 — CVD Momentum (continuation):
  - net taker CVD summed over a rolling 8h window (96 bars)
  - z-scored against 2016 bars
  - trigger: z > 3 -> long, z < -3 -> short; suppress 6 bars

The report does not specify exits, so several transparent exit schemes are
tested. R is defined as pnl / initial stop distance (bracket schemes) or
pnl / ATR (fixed-horizon schemes). Entries fill at the next bar's open.
If a bar touches both stop and target, the stop is assumed to fill first
(conservative). Costs are charged per side on entry and exit.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ZWIN = 2016  # 7-day z-score lookback (bars)
ATR_WIN = 48
DEDUP_BARS = 6
COST_PER_SIDE = 0.0005  # 5 bps per side: taker fee + slippage, fully loaded


@dataclass(frozen=True)
class ExitScheme:
    name: str
    sl_atr: float | None  # stop distance in ATRs (None = no bracket)
    tp_atr: float | None  # target distance in ATRs
    timeout_bars: int


SCHEMES = [
    ExitScheme("bracket_1.5atr_2R_24h", sl_atr=1.5, tp_atr=3.0, timeout_bars=288),
    ExitScheme("bracket_1atr_2R_8h", sl_atr=1.0, tp_atr=2.0, timeout_bars=96),
    ExitScheme("horizon_4h", sl_atr=None, tp_atr=None, timeout_bars=48),
    ExitScheme("horizon_24h", sl_atr=None, tp_atr=None, timeout_bars=288),
]


def load_symbol(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df = df.sort_values("open_time").reset_index(drop=True)
    df["delta"] = 2.0 * df["taker_buy_volume"] - df["volume"]  # net taker CVD per bar
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ),
    )
    df["atr"] = tr.rolling(ATR_WIN).mean()
    return df


def zscore_of_rolling_sum(delta: pd.Series, sum_win: int) -> pd.Series:
    s = delta.rolling(sum_win).sum()
    mu = s.rolling(ZWIN).mean()
    sd = s.rolling(ZWIN).std()
    return (s - mu) / sd


def signals_divergence(df: pd.DataFrame) -> pd.Series:
    z = zscore_of_rolling_sum(df["delta"], 48)
    ret_1h = df["close"].pct_change(12)
    direction = np.sign(z).where((z.abs() >= 2.5) & (np.sign(ret_1h) == -np.sign(z)), 0.0)
    return direction.fillna(0.0)


def signals_momentum(df: pd.DataFrame) -> pd.Series:
    z = zscore_of_rolling_sum(df["delta"], 96)
    direction = pd.Series(0.0, index=df.index)
    direction[z > 3.0] = 1.0
    direction[z < -3.0] = -1.0
    return direction


def dedup(direction: pd.Series) -> pd.Series:
    """Suppress signals for DEDUP_BARS bars after each accepted signal."""
    out = direction.to_numpy().copy()
    idx = np.flatnonzero(out != 0)
    last = -10**9
    for i in idx:
        if i - last <= DEDUP_BARS:
            out[i] = 0.0
        else:
            last = i
    return pd.Series(out, index=direction.index)


def run_trades(df: pd.DataFrame, direction: pd.Series, scheme: ExitScheme) -> pd.DataFrame:
    """Simulate trades bar-by-bar. Entry at next bar open after the signal bar."""
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    atr = df["atr"].to_numpy()
    ts = df["ts"].to_numpy()
    n = len(df)

    trades = []
    for sig_i in np.flatnonzero(direction.to_numpy() != 0):
        entry_i = sig_i + 1
        if entry_i >= n or not np.isfinite(atr[sig_i]) or atr[sig_i] <= 0:
            continue
        side = direction.iloc[sig_i]
        entry = o[entry_i]
        unit = atr[sig_i]  # R denominator basis
        stop_dist = scheme.sl_atr * unit if scheme.sl_atr else None
        exit_i = min(entry_i + scheme.timeout_bars - 1, n - 1)
        exit_price = c[exit_i]
        exit_kind = "timeout"

        if stop_dist is not None:
            sl = entry - side * stop_dist
            tp = entry + side * scheme.tp_atr * unit
            for j in range(entry_i, exit_i + 1):
                hit_sl = l[j] <= sl if side > 0 else h[j] >= sl
                hit_tp = h[j] >= tp if side > 0 else l[j] <= tp
                if hit_sl:  # conservative: stop fills first if both touched
                    exit_price, exit_i, exit_kind = sl, j, "stop"
                    break
                if hit_tp:
                    exit_price, exit_i, exit_kind = tp, j, "target"
                    break

        pnl = side * (exit_price - entry)
        denom = stop_dist if stop_dist is not None else unit
        cost = 2 * COST_PER_SIDE * entry
        r_net = (pnl - cost) / denom
        trades.append(
            {
                "entry_ts": ts[entry_i],
                "side": int(side),
                "entry": entry,
                "exit": exit_price,
                "exit_kind": exit_kind,
                "bars_held": exit_i - entry_i + 1,
                "r_net": r_net,
            }
        )
    out = pd.DataFrame(trades)
    if not out.empty:
        out["year"] = pd.to_datetime(out["entry_ts"]).dt.year
    return out


def summarize(trades: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = trades.groupby(by)["r_net"]
    summary = g.agg(trades="count", avg_r="mean", win_rate=lambda x: (x > 0).mean())
    summary["t_stat"] = g.mean() / g.sem()
    return summary.round(4)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    data_dir, out_dir = Path(args.data), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    edges = {"divergence": signals_divergence, "momentum": signals_momentum}
    all_trades = []
    for parquet in sorted(data_dir.glob("*_5m.parquet")):
        symbol = parquet.stem.replace("_5m", "")
        df = load_symbol(parquet)
        print(f"{symbol}: {len(df)} bars {df['ts'].iloc[0]} .. {df['ts'].iloc[-1]}")
        for edge_name, signal_fn in edges.items():
            direction = dedup(signal_fn(df))
            n_signals = int((direction != 0).sum())
            print(f"  {edge_name}: {n_signals} signals")
            for scheme in SCHEMES:
                trades = run_trades(df, direction, scheme)
                trades["symbol"] = symbol
                trades["edge"] = edge_name
                trades["scheme"] = scheme.name
                all_trades.append(trades)

    trades = pd.concat(all_trades, ignore_index=True)
    trades.to_parquet(out_dir / "trades.parquet", index=False)

    for key, label in [(["edge", "scheme"], "overall"),
                       (["edge", "scheme", "year"], "by_year"),
                       (["edge", "scheme", "symbol"], "by_asset")]:
        summary = summarize(trades, key)
        summary.to_csv(out_dir / f"summary_{label}.csv")
        print(f"\n=== {label} ===")
        print(summary.to_string())


if __name__ == "__main__":
    main()
