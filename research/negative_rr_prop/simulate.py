"""Monte Carlo simulation: negative-RR strategies on the MFFU Builder 50k pipeline.

Answers: how many eval fees do you burn before your first payout, and what is the
expected cash extracted per funded account, as a function of the strategy's
risk:reward, win rate (edge), and per-trade risk size?

Account rules modeled (MFFU Builder 50k, Default variant, July 2026):
  Eval:   $50,000 start, +$3,000 profit target, $2,000 EOD-trailing MLL that
          locks once it reaches start+$100, $1,000 daily loss soft-pause,
          1-day minimum, no consistency rule, fee $153/attempt (monthly).
  Funded: same MLL mechanics. Payout buffer = $2,100 above start (MLL+$100);
          the buffer must remain in the account, so withdrawable
          = min($2,000, equity - buffer), minimum $500. First payout needs
          $500 above the buffer; later payouts need $500 net since the last
          payout. Every payout cycle needs >= 2 qualifying days and passes a
          50% consistency check (largest day <= 50% of cycle profit).
          80/20 trader split. After 5 sim payouts the account goes live.

Trade model: each trade risks `risk` dollars to win `rr * risk` with
probability `p`, minus per-trade friction (commission+slippage). The trader
takes up to `trades_per_day` trades but never takes a trade that could push
the day past the $1,000 soft-pause.

Usage:
  uv run python research/negative_rr_prop/simulate.py            # full sweep
  uv run python research/negative_rr_prop/simulate.py --quick    # smaller N
"""

from __future__ import annotations

import argparse
import dataclasses
import math

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- account rules

START = 50_000.0
EVAL_TARGET = 3_000.0
MLL = 2_000.0                    # EOD-trailing max loss limit
MLL_LOCK = START + 100.0         # floor stops trailing here
DAILY_SOFT_PAUSE = 1_000.0
EVAL_FEE = 153.0                 # per attempt (monthly; re-billed every ~22 sessions)
BILLING_DAYS = 22
BUFFER = 2_100.0                 # must stay in the funded account
MIN_PAYOUT = 500.0
MAX_PAYOUT = 2_000.0
TRADER_SPLIT = 0.80
QUALIFYING_DAY_PROFIT = 150.0    # profit needed for a day to count toward the 2-day minimum
QUALIFYING_DAYS_PER_CYCLE = 2
SIM_PAYOUTS_TO_LIVE = 5
MAX_EVAL_DAYS = 90               # give up (treated as fail; fees keep billing)
MAX_FUNDED_DAYS = 500


@dataclasses.dataclass(frozen=True)
class Strategy:
    rr: float                 # reward / risk (< 1.0 == "negative RR")
    win_rate: float
    risk: float               # dollars risked per trade (1R)
    trades_per_day: int = 6
    friction: float = 4.0     # commission + slippage per trade, dollars

    @property
    def edge_per_trade(self) -> float:
        """Expected dollars per trade after friction."""
        return (self.win_rate * self.rr - (1 - self.win_rate)) * self.risk - self.friction

    @property
    def breakeven_wr(self) -> float:
        return (1 + self.friction / self.risk) / (1 + self.rr)


def simulate_day(rng: np.random.Generator, s: Strategy) -> tuple[float, float]:
    """One trading day. Returns (day_pnl, worst_intraday_pnl)."""
    pnl = 0.0
    worst = 0.0
    for _ in range(s.trades_per_day):
        # respect the $1,000 daily soft-pause: skip trades that could exceed it
        if pnl - s.risk - s.friction < -DAILY_SOFT_PAUSE:
            break
        if rng.random() < s.win_rate:
            pnl += s.rr * s.risk - s.friction
        else:
            pnl -= s.risk + s.friction
        worst = min(worst, pnl)
    return pnl, worst


def run_eval(rng: np.random.Generator, s: Strategy) -> tuple[bool, int]:
    """One eval attempt. Returns (passed, trading_days_used)."""
    equity = START
    floor = START - MLL
    for day in range(1, MAX_EVAL_DAYS + 1):
        day_pnl, worst = simulate_day(rng, s)
        # intraday MLL breach (floor is static intraday but breach is real-time)
        if equity + worst <= floor:
            return False, day
        equity += day_pnl
        if equity >= START + EVAL_TARGET:
            return True, day          # 1-day minimum is always satisfied
        floor = min(max(floor, equity - MLL), MLL_LOCK)  # trail EOD, lock at start+100
        if equity <= floor:
            return False, day
    return False, MAX_EVAL_DAYS


@dataclasses.dataclass
class FundedResult:
    payouts: list[float]              # gross payout amounts (pre-split)
    days_to_first_payout: int | None
    days_survived: int
    reached_live: bool


def run_funded(rng: np.random.Generator, s: Strategy) -> FundedResult:
    """One sim-funded account, withdrawing the max allowed as early as allowed."""
    equity = START
    floor = START - MLL
    payouts: list[float] = []
    days_to_first: int | None = None
    cycle_days: list[float] = []      # daily pnl within the current payout cycle
    profit_at_last_payout = 0.0

    for day in range(1, MAX_FUNDED_DAYS + 1):
        day_pnl, worst = simulate_day(rng, s)
        if equity + worst <= floor:
            return FundedResult(payouts, days_to_first, day, False)
        equity += day_pnl
        floor = min(max(floor, equity - MLL), MLL_LOCK)
        if equity <= floor:
            return FundedResult(payouts, days_to_first, day, False)
        cycle_days.append(day_pnl)

        # ---- payout check (EOD) ----
        profit = equity - START
        above_buffer = equity - (START + BUFFER)
        cycle_profit = profit - profit_at_last_payout
        qualifying = sum(1 for d in cycle_days if d >= QUALIFYING_DAY_PROFIT)
        wins = [d for d in cycle_days if d > 0]
        consistent = bool(wins) and cycle_profit > 0 and max(wins) <= 0.5 * cycle_profit
        eligible = (
            above_buffer >= MIN_PAYOUT
            and cycle_profit >= MIN_PAYOUT
            and qualifying >= QUALIFYING_DAYS_PER_CYCLE
            and consistent
        )
        if eligible:
            amount = min(MAX_PAYOUT, above_buffer)
            equity -= amount
            payouts.append(amount)
            if days_to_first is None:
                days_to_first = day
            profit_at_last_payout = equity - START
            cycle_days = []
            if len(payouts) >= SIM_PAYOUTS_TO_LIVE:
                return FundedResult(payouts, days_to_first, day, True)

    return FundedResult(payouts, days_to_first, day, False)


# ---------------------------------------------------------------- sweep harness


def evaluate_strategy(
    s: Strategy, n_eval: int, n_funded: int, seed: int, funded_s: Strategy | None = None
) -> dict:
    """Run the pipeline. `funded_s` lets the trader size differently once funded
    (split policy); defaults to using the same strategy in both stages."""
    rng = np.random.default_rng(seed)
    funded_s = funded_s or s

    passes, pass_days, fail_days = 0, [], []
    for _ in range(n_eval):
        ok, days = run_eval(rng, s)
        if ok:
            passes += 1
            pass_days.append(days)
        else:
            fail_days.append(days)
    pass_rate = passes / n_eval

    funded = [run_funded(rng, funded_s) for _ in range(n_funded)]
    got_paid = [f for f in funded if f.payouts]
    p_paid = len(got_paid) / n_funded
    mean_gross = float(np.mean([sum(f.payouts) for f in funded]))
    mean_cash = TRADER_SPLIT * mean_gross
    p_live = sum(f.reached_live for f in funded) / n_funded
    mean_days_first = (
        float(np.mean([f.days_to_first_payout for f in got_paid])) if got_paid else math.nan
    )

    # fee accounting: one fee per attempt, re-billed every BILLING_DAYS of an attempt
    all_days = pass_days + fail_days
    fees_per_attempt = EVAL_FEE * float(
        np.mean([1 + (d - 1) // BILLING_DAYS for d in all_days])
    )
    # expected evals (and fees) burned until one funded account produces a payout
    p_attempt_pays = pass_rate * p_paid
    evals_per_payout = 1 / p_attempt_pays if p_attempt_pays > 0 else math.inf
    fees_to_first_payout = fees_per_attempt * evals_per_payout
    ev_per_attempt = pass_rate * mean_cash - fees_per_attempt

    return {
        "rr": s.rr,
        "win_rate": round(s.win_rate, 4),
        "edge_pct": round(100 * (s.win_rate - s.breakeven_wr), 2),
        "risk": s.risk,
        "funded_risk": funded_s.risk,
        "ev_per_trade": round(s.edge_per_trade, 2),
        "eval_pass_rate": round(pass_rate, 4),
        "eval_days_med": float(np.median(pass_days)) if pass_days else math.nan,
        "p_payout_given_funded": round(p_paid, 4),
        "days_to_first_payout": round(mean_days_first, 1),
        "mean_payouts_per_acct": round(float(np.mean([len(f.payouts) for f in funded])), 2),
        "p_reach_live": round(p_live, 4),
        "cash_per_funded_acct": round(mean_cash, 0),
        "evals_per_payout": round(evals_per_payout, 1),
        "fees_to_first_payout": round(fees_to_first_payout, 0),
        "ev_per_eval_$": round(ev_per_attempt, 0),
    }


def build_grid() -> list[Strategy]:
    grid: list[Strategy] = []
    # negative-RR profiles + 1:1 and 1.5:1 baselines, swept by edge over breakeven
    for rr in (0.33, 0.5, 0.75, 1.0, 1.5):
        for edge in (-0.02, 0.0, 0.02, 0.04, 0.06):
            for risk in (150.0, 250.0, 400.0):
                be = (1 + 4.0 / risk) / (1 + rr)
                wr = be + edge
                if 0.05 < wr < 0.97:
                    grid.append(Strategy(rr=rr, win_rate=wr, risk=risk))
    # the degenerate "send it" eval-passing config, for comparison
    grid.append(Strategy(rr=1.0, win_rate=0.5, risk=950.0, trades_per_day=3))
    return grid


def build_split_policies() -> list[tuple[Strategy, Strategy]]:
    """Same signal, sized aggressively in eval and conservatively once funded."""
    pairs = []
    for rr in (0.33, 0.5, 0.75):
        for edge in (0.02, 0.04):
            def mk(risk: float) -> Strategy:
                return Strategy(rr=rr, win_rate=(1 + 4.0 / risk) / (1 + rr) + edge, risk=risk)

            pairs.append((mk(500.0), mk(200.0)))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default="research/negative_rr_prop/results/summary.csv")
    args = ap.parse_args()

    n_eval = 2_000 if args.quick else 8_000
    n_funded = 1_000 if args.quick else 4_000

    rows = []
    grid = build_grid()
    for i, s in enumerate(grid):
        rows.append(evaluate_strategy(s, n_eval, n_funded, seed=1234 + i))
        print(f"[{i + 1}/{len(grid)}] rr={s.rr} wr={s.win_rate:.3f} risk={s.risk:.0f} done")

    splits = build_split_policies()
    for i, (es, fs) in enumerate(splits):
        rows.append(evaluate_strategy(es, n_eval, n_funded, seed=9876 + i, funded_s=fs))
        print(f"[split {i + 1}/{len(splits)}] rr={es.rr} eval_risk={es.risk:.0f} funded_risk={fs.risk:.0f} done")

    df = pd.DataFrame(rows).sort_values(["rr", "edge_pct", "risk"])
    df.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}")
    with pd.option_context("display.width", 250, "display.max_columns", 50):
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
