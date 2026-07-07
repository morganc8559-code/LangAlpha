"""Charts for the negative-RR / MFFU Builder 50k payout analysis.

Reads results/summary.csv (produced by simulate.py) and re-simulates a handful
of funded-account equity paths for the hero chart. Palette: dataviz reference
instance (light mode), used unchanged in fixed slot order.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from simulate import BUFFER, START, Strategy, simulate_day, MLL, MLL_LOCK, MAX_PAYOUT, MIN_PAYOUT, QUALIFYING_DAY_PROFIT, QUALIFYING_DAYS_PER_CYCLE, SIM_PAYOUTS_TO_LIVE

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#e34948"]  # slots 1,2,3,4,6

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": "sans-serif",
        "text.color": INK,
        "axes.edgecolor": BASELINE,
        "axes.labelcolor": INK2,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.axisbelow": True,
        "font.size": 11,
    }
)

RESULTS = "research/negative_rr_prop/results"


def funded_path(rng: np.random.Generator, s: Strategy, max_days: int = 220):
    """Replay of run_funded() that records the daily equity path and payouts."""
    equity, floor = START, START - MLL
    path, payout_marks = [0.0], []
    cycle_days: list[float] = []
    profit_at_last_payout, n_payouts = 0.0, 0
    for day in range(1, max_days + 1):
        day_pnl, worst = simulate_day(rng, s)
        if equity + worst <= floor:
            path.append(floor - START)
            return path, payout_marks, "blown"
        equity += day_pnl
        floor = min(max(floor, equity - MLL), MLL_LOCK)
        if equity <= floor:
            path.append(floor - START)
            return path, payout_marks, "blown"
        cycle_days.append(day_pnl)
        profit = equity - START
        above_buffer = equity - (START + BUFFER)
        cycle_profit = profit - profit_at_last_payout
        qualifying = sum(1 for d in cycle_days if d >= QUALIFYING_DAY_PROFIT)
        wins = [d for d in cycle_days if d > 0]
        consistent = bool(wins) and cycle_profit > 0 and max(wins) <= 0.5 * cycle_profit
        if (
            above_buffer >= MIN_PAYOUT
            and cycle_profit >= MIN_PAYOUT
            and qualifying >= QUALIFYING_DAYS_PER_CYCLE
            and consistent
        ):
            amount = min(MAX_PAYOUT, above_buffer)
            equity -= amount
            n_payouts += 1
            payout_marks.append((day, equity - START, amount))
            profit_at_last_payout = equity - START
            cycle_days = []
            if n_payouts >= SIM_PAYOUTS_TO_LIVE:
                path.append(equity - START)
                return path, payout_marks, "live"
        path.append(equity - START)
    return path, payout_marks, "alive"


def dollars(x, _pos=None):
    return f"${x:,.0f}"


def chart_equity_curves():
    """Hero: what a negative-RR grind actually looks like in the funded stage."""
    s = Strategy(rr=0.5, win_rate=(1 + 4.0 / 250) / 1.5 + 0.02, risk=250.0)
    rng = np.random.default_rng(7)
    paths = [funded_path(rng, s) for _ in range(60)]
    # pick representatives: one live, one paid-then-blown, one never-paid
    rep = {}
    for path, marks, outcome in paths:
        key = outcome if outcome != "blown" else ("paid" if marks else "never")
        rep.setdefault(key, (path, marks))
    order = [("live", SERIES[0], "reached live (5 payouts)"),
             ("paid", SERIES[2], "paid, then blown"),
             ("never", SERIES[4], "blown before any payout")]

    fig, ax = plt.subplots(figsize=(9, 5), dpi=200)
    for path, marks, outcome in paths:
        ax.plot(path, color=GRID, lw=0.8, zorder=1)
    for key, color, label in order:
        if key not in rep:
            continue
        path, marks = rep[key]
        ax.plot(path, color=color, lw=2, zorder=3, label=label)
        if marks:
            ax.scatter([m[0] for m in marks], [m[1] for m in marks],
                       s=42, color=color, edgecolors=SURFACE, linewidths=1.5, zorder=4)
    ax.axhline(BUFFER, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=2)
    ax.text(2, BUFFER + 60, "payout buffer  $2,100", color=INK2, fontsize=9.5)
    ax.axhline(0, color=BASELINE, lw=1)
    ax.set_xlabel("trading days (funded stage)")
    ax.set_ylabel("account profit")
    ax.yaxis.set_major_formatter(FuncFormatter(dollars))
    ax.set_title(
        "Builder 50k funded stage — negative-RR grind (0.5R reward, 70% win rate, $250 risk)\n"
        "60 simulated accounts; dots = $ withdrawals",
        loc="left", fontsize=11.5, color=INK,
    )
    leg = ax.legend(loc="upper left", frameon=False, fontsize=10)
    for t in leg.get_texts():
        t.set_color(INK2)
    fig.tight_layout()
    fig.savefig(f"{RESULTS}/equity_curves.png")
    plt.close(fig)


def chart_fees_to_payout(df: pd.DataFrame):
    """Expected eval spend before the first payout, by edge, one line per RR."""
    sub = df[(df.risk == 250) & (df.risk == df.funded_risk) & (df.edge_pct >= 0)]
    fig, ax = plt.subplots(figsize=(9, 5), dpi=200)
    rrs = sorted(sub.rr.unique())
    for i, rr in enumerate(rrs):
        d = sub[sub.rr == rr].sort_values("edge_pct")
        ax.plot(d.edge_pct, d.fees_to_first_payout, color=SERIES[i % len(SERIES)],
                lw=2, marker="o", ms=6, markeredgecolor=SURFACE, markeredgewidth=1.2,
                label=f"RR {rr:g}")
    ax.set_yscale("log")
    ticks = [250, 500, 1000, 2000, 4000, 8000]
    ax.set_yticks(ticks)
    ax.set_yticks([], minor=True)
    ax.yaxis.set_major_formatter(FuncFormatter(dollars))
    ax.set_xlabel("win-rate edge over breakeven (percentage points)")
    ax.set_ylabel("expected eval fees before first payout (log)")
    ax.set_title(
        "What you pay MFFU before your first payout — \\$250 risk/trade, \\$153 evals",
        loc="left", fontsize=11.5, color=INK,
    )
    leg = ax.legend(loc="upper right", frameon=False, fontsize=10)
    for t in leg.get_texts():
        t.set_color(INK2)
    fig.tight_layout()
    fig.savefig(f"{RESULTS}/fees_to_first_payout.png")
    plt.close(fig)


def chart_ev_per_eval(df: pd.DataFrame):
    """EV per $153 eval attempt at +2 and +4 pts of edge, grouped by RR."""
    sub = df[(df.risk == 250) & (df.risk == df.funded_risk) & df.edge_pct.isin([2.0, 4.0])]
    rrs = sorted(sub.rr.unique())
    x = np.arange(len(rrs))
    w = 0.36
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=200)
    for j, (edge, color) in enumerate(zip([2.0, 4.0], [SERIES[0], SERIES[1]])):
        vals = [sub[(sub.rr == rr) & (sub.edge_pct == edge)].ev_per_eval_.iloc[0] for rr in rrs]
        bars = ax.bar(x + (j - 0.5) * w, vals, width=w - 0.04, color=color,
                      label=f"+{edge:g} pts edge", zorder=3)
        for b, v in zip(bars, vals):
            ax.annotate(f"${v:,.0f}", (b.get_x() + b.get_width() / 2, v),
                        xytext=(0, 4), textcoords="offset points",
                        ha="center", color=INK2, fontsize=9)
    ax.axhline(0, color=BASELINE, lw=1)
    ax.set_xticks(x, [f"RR {rr:g}" for rr in rrs])
    ax.yaxis.set_major_formatter(FuncFormatter(dollars))
    ax.set_ylabel("expected profit per eval bought")
    ax.set_title(
        "Expected value of one \\$153 eval, by strategy RR — \\$250 risk/trade",
        loc="left", fontsize=11.5, color=INK,
    )
    ax.margins(y=0.12)
    leg = ax.legend(loc="upper right", frameon=False, fontsize=10)
    for t in leg.get_texts():
        t.set_color(INK2)
    fig.tight_layout()
    fig.savefig(f"{RESULTS}/ev_per_eval.png")
    plt.close(fig)


if __name__ == "__main__":
    df = pd.read_csv(f"{RESULTS}/summary.csv")
    df = df.rename(columns={"ev_per_eval_$": "ev_per_eval_"})
    chart_equity_curves()
    chart_fees_to_payout(df)
    chart_ev_per_eval(df)
    print("charts written to", RESULTS)
