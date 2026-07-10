"""Download Binance USDT-perp 5m klines (monthly dumps) for the CVD edge study.

Data source: https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}/5m/
Each monthly zip contains one CSV with columns:
open_time, open, high, low, close, volume, close_time, quote_volume,
count, taker_buy_volume, taker_buy_quote_volume, ignore

Net taker CVD per bar = taker_buy_volume - (volume - taker_buy_volume)
                      = 2 * taker_buy_volume - volume
"""

import argparse
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def month_range(start: str, end: str) -> list[str]:
    return [d.strftime("%Y-%m") for d in pd.period_range(start, end, freq="M").to_timestamp()]


def fetch_month(symbol: str, month: str, session: requests.Session) -> pd.DataFrame | None:
    url = f"{BASE}/{symbol}/5m/{symbol}-5m-{month}.zip"
    resp = session.get(url, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        raw = zf.read(name)
    # Some dumps ship with a header row, some without.
    first_line = raw.split(b"\n", 1)[0]
    header = 0 if first_line.startswith(b"open_time") else None
    df = pd.read_csv(io.BytesIO(raw), header=header, names=COLS)
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    parser.add_argument("--start", default="2021-06")
    parser.add_argument("--end", default="2026-06")
    parser.add_argument("--out", required=True, help="output directory for parquet files")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    months = month_range(args.start, args.end)
    session = requests.Session()

    for symbol in args.symbols:
        out_path = out_dir / f"{symbol}_5m.parquet"
        if out_path.exists():
            print(f"{symbol}: already downloaded, skipping")
            continue
        frames = []
        for month in months:
            df = fetch_month(symbol, month, session)
            if df is None:
                print(f"{symbol} {month}: 404 (not published yet)")
                continue
            frames.append(df)
            print(f"{symbol} {month}: {len(df)} bars")
        full = pd.concat(frames, ignore_index=True)
        full = full.drop(columns=["ignore"]).sort_values("open_time").drop_duplicates("open_time")
        full["ts"] = pd.to_datetime(full["open_time"], unit="ms", utc=True)
        full.to_parquet(out_path, index=False)
        print(f"{symbol}: wrote {len(full)} bars -> {out_path}")


if __name__ == "__main__":
    sys.exit(main())
