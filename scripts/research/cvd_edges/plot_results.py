"""Plot cumulative net R curves per edge for the primary exit scheme."""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--scheme", default="bracket_1.5atr_2R_24h")
    parser.add_argument("--trades", default="trades.parquet")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    out_dir = Path(args.results)
    trades = pd.read_parquet(out_dir / args.trades)
    trades = trades[trades["scheme"] == args.scheme].copy()
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"])

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    for ax, edge in zip(axes, ["divergence", "momentum"]):
        sub = trades[trades["edge"] == edge]
        for symbol, grp in sub.groupby("symbol"):
            grp = grp.sort_values("entry_ts")
            ax.plot(grp["entry_ts"], grp["r_net"].cumsum(), label=symbol, linewidth=1.2)
        pooled = sub.sort_values("entry_ts")
        ax.plot(pooled["entry_ts"], pooled["r_net"].cumsum(), label="ALL", color="black", linewidth=1.8)
        ax.set_title(f"CVD {edge} — cumulative net R ({args.scheme})")
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.legend()
        ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    out = out_dir / f"cumulative_r_{args.tag}{args.scheme}.png"
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
