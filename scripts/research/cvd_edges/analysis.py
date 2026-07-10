"""Follow-up diagnostics for the CVD edge backtest.

1. Edge-triggered signal variant — fire only when |z| CROSSES the threshold
   (likely what produces the report's much lower trade counts).
2. Long vs short attribution — is momentum P&L just long-side beta?
3. Non-overlapping resample — recompute t-stats keeping only trades whose
   holding windows don't overlap, to undo autocorrelation inflation.
4. Out-of-sample split at the report's data end (2026-02-01).
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import (
    SCHEMES,
    dedup,
    load_symbol,
    run_trades,
    summarize,
    zscore_of_rolling_sum,
)

REPORT_END = pd.Timestamp("2026-02-01", tz="UTC")


def signals_divergence_edge(df: pd.DataFrame) -> pd.Series:
    z = zscore_of_rolling_sum(df["delta"], 48)
    ret_1h = df["close"].pct_change(12)
    above = z.abs() >= 2.5
    crossing = above & ~above.shift(1, fill_value=False)
    direction = np.sign(z).where(crossing & (np.sign(ret_1h) == -np.sign(z)), 0.0)
    return direction.fillna(0.0)


def signals_momentum_edge(df: pd.DataFrame) -> pd.Series:
    z = zscore_of_rolling_sum(df["delta"], 96)
    above = z.abs() >= 3.0
    crossing = above & ~above.shift(1, fill_value=False)
    direction = np.sign(z).where(crossing, 0.0)
    return direction.fillna(0.0)


def non_overlapping(trades: pd.DataFrame, bar_minutes: int = 5) -> pd.DataFrame:
    """Greedily keep trades (per symbol) whose holding windows don't overlap."""
    kept = []
    for _, grp in trades.groupby("symbol"):
        grp = grp.sort_values("entry_ts")
        last_exit = pd.Timestamp.min.tz_localize("UTC")
        for row in grp.itertuples():
            entry = pd.Timestamp(row.entry_ts)
            if entry >= last_exit:
                kept.append(row.Index)
                last_exit = entry + pd.Timedelta(minutes=bar_minutes * row.bars_held)
    return trades.loc[kept]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    data_dir, out_dir = Path(args.data), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    edges = {"divergence": signals_divergence_edge, "momentum": signals_momentum_edge}
    all_trades = []
    for parquet in sorted(data_dir.glob("*_5m.parquet")):
        symbol = parquet.stem.replace("_5m", "")
        df = load_symbol(parquet)
        for edge_name, signal_fn in edges.items():
            direction = dedup(signal_fn(df))
            print(f"{symbol} {edge_name} (edge-triggered): {int((direction != 0).sum())} signals")
            for scheme in SCHEMES:
                trades = run_trades(df, direction, scheme)
                trades["symbol"] = symbol
                trades["edge"] = edge_name
                trades["scheme"] = scheme.name
                all_trades.append(trades)

    trades = pd.concat(all_trades, ignore_index=True)
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True)
    trades.to_parquet(out_dir / "trades_edge_triggered.parquet", index=False)

    print("\n=== edge-triggered: overall ===")
    print(summarize(trades, ["edge", "scheme"]).to_string())

    print("\n=== edge-triggered: by year ===")
    print(summarize(trades, ["edge", "scheme", "year"]).to_string())

    print("\n=== edge-triggered: long vs short ===")
    print(summarize(trades, ["edge", "scheme", "side"]).to_string())

    print("\n=== edge-triggered: in-sample (< 2026-02) vs out-of-sample ===")
    trades["sample"] = np.where(trades["entry_ts"] < REPORT_END, "in_sample", "oos_2026")
    print(summarize(trades, ["edge", "scheme", "sample"]).to_string())

    print("\n=== edge-triggered: non-overlapping trades only ===")
    frames = []
    for (edge, scheme), grp in trades.groupby(["edge", "scheme"]):
        frames.append(non_overlapping(grp))
    no = pd.concat(frames)
    print(summarize(no, ["edge", "scheme"]).to_string())
    print("\n=== non-overlapping: long vs short ===")
    print(summarize(no, ["edge", "scheme", "side"]).to_string())


if __name__ == "__main__":
    main()
