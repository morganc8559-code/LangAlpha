# CVD Edge Family — Independent Replication Study

Attempted replication of the "CVD Edge Family" report (Visian/EdgeFactory, July 2026),
which claims +0.20R / +0.22R net expectancy for two CVD-based strategies on crypto
perpetuals. **We could not reproduce the claimed results.**

## Data

- Binance USDT-perp 5m klines (monthly dumps from `data.binance.vision`), BTCUSDT /
  ETHUSDT / SOLUSDT, 2021-06-01 → 2026-06-30 (~533k bars per symbol).
- Net taker CVD per bar reconstructed as `2 * taker_buy_volume - volume` (exactly the
  aggTrades taker imbalance the report describes, aggregated to bar level).
- The source report's data ends January 2026, so February–June 2026 here is a true
  out-of-sample window that the report's authors could not have fit to.

## Implementation

Entries follow the report verbatim (`backtest.py`, `analysis.py`):

- **Divergence**: z-score of rolling 4h (48-bar) CVD sum vs a 7-day (2016-bar) window;
  trigger when |z| ≥ 2.5 and the 1h (12-bar) price return opposes the flow; trade with
  the flow; 6-bar dedup.
- **Momentum**: z-score of rolling 8h (96-bar) CVD sum vs 2016 bars; long if z > 3,
  short if z < −3; 6-bar dedup.

Two ambiguities in the source report had to be resolved by testing both options:

1. **Level- vs edge-triggered signals.** Level-triggered (any bar with |z| above the
   threshold) produces ~3–5× the report's trade counts; edge-triggered (only on the
   crossing bar) produces roughly half (divergence) to 1.7× (momentum). Neither matches,
   so the published rules are not sufficient to reproduce the study's trade population.
2. **Exits are not specified anywhere in the report** (a stop must exist, since their
   validation stress-tests one). We tested four schemes: ATR brackets (SL 1.5×ATR48 /
   TP 2R / 24h timeout; SL 1×ATR / TP 2R / 8h timeout) and fixed horizons (4h, 24h,
   R normalized by ATR). Entries fill at next bar open; if a bar touches both stop and
   target, the stop fills first (conservative).

Costs: 5 bps per side (taker fee + slippage) as the base case, with a 0 / 2 / 5 bps
sensitivity grid.

## Results (edge-triggered, bracket 1.5×ATR / 2R / 24h — closest to the report's design)

| Cost per side | Divergence avg R (t) | Momentum avg R (t) |
|---|---|---|
| 0 bps (gross) | +0.031 (t = 0.59) | +0.092 (t = 2.82) |
| 2 bps | −0.044 (t = −0.85) | +0.010 (t = 0.31) |
| 5 bps | −0.157 (t = −3.00) | −0.112 (t = −3.44) |

Report claim: **+0.196R / +0.20R+ net** with ~48% win rate. Observed win rates here are
34–39%. The level-triggered variant and all other exit schemes tell the same story
(`summary_*.csv`); the only strongly positive configuration is the stop-less 24h fixed
horizon, and its P&L decomposition reveals why (below).

### Key findings

1. **The gross edge is an order of magnitude smaller than claimed.** Even with zero
   transaction costs, divergence expectancy is statistically indistinguishable from
   zero and momentum is +0.09R — versus +0.20R claimed *net of costs*.
2. **Costs dominate at this timeframe.** With stops at 1.5×ATR(5m) — 0.2–0.5% of price —
   a 10 bps round trip costs a median 0.18–0.55R per trade depending on asset. Any
   5-minute strategy with sub-0.1R gross edge is unviable at taker fees.
3. **The apparent profitability of stop-less variants is long-side bull-market beta.**
   Momentum with a 24h horizon earns +2.10R on longs vs +0.04R on shorts
   (non-overlapping: +2.28R vs +0.14R, t = 0.29). Buying after aggressive buying was
   profitable because crypto went up over 2021–2026, not because taker flow predicts
   direction symmetrically.
4. **Out-of-sample decay.** In Feb–Jun 2026 (after the report's data window) momentum is
   negative even at zero cost (−0.10R gross, −0.32R net, t = −2.7), and every scheme's
   2026 row is the worst or near-worst year.
5. **Trade counts don't match** under either signal interpretation, so the report's
   entry rules as published are underdetermined — an independent reader cannot
   reconstruct the study.

### Verdict

The published entry rules, on the same market/timeframe/period, do not yield a tradable
edge under any exit scheme or cost assumption we tested. The report's headline numbers
are most consistent with (a) materially different unpublished implementation details,
(b) undercounted costs relative to stop width, and (c) long-side beta flattering both
strategies over a rising market. Treat the report's "CERTIFIED" validation as
unverified marketing until the authors publish exit rules and trade-level data.

## Reproducing

```bash
uv run python scripts/research/cvd_edges/download_data.py --out /path/to/data
uv run python scripts/research/cvd_edges/backtest.py --data /path/to/data --out /path/to/results
cd scripts/research/cvd_edges && uv run python analysis.py --data /path/to/data --out /path/to/results
```

Artifacts in `results/`: summary CSVs (overall / by year / by asset) for the
level-triggered variant, and cumulative-R charts for the edge-triggered variant.
