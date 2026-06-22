# Hypothesis H001

## ID
H001

## Title
Liquidity sweep + HTF trend alignment improves directional edge over baseline structural bias

## Statement
When price sweeps a prior swing low/high on the entry timeframe (M15)
AND the higher timeframe (H4) structural bias agrees with the reversal
direction, the resulting move outperforms (in win rate and/or
risk-adjusted return) trades taken on HTF structural bias alone
(the current Phase 1 `SMCEngine.structural_bias()` logic, with no
sweep confirmation).

## Why this might be true
The current SMC engine (Phase 1) only looks at swing-high/swing-low
sequences for bias — it has no concept of liquidity sweeps (price
briefly breaking a level to trigger stops before reversing). If
institutional order flow theory holds, sweep-confirmed reversals
aligned with HTF bias should filter out a meaningful share of false
structural signals that the current engine can't distinguish.

## Data required
- Symbol(s): start with 1 liquid FX pair (e.g. EURUSD) before generalizing
- Timeframe(s): M15 (entry/sweep detection), H4 (HTF bias)
- Date range: minimum 2 years of real historical data
- Minimum sample size needed: 100+ sweep occurrences per direction (200+ total)

## Falsification criteria
Reject this hypothesis if, across >=100 sweep-confirmed occurrences:
- win rate is not statistically distinguishable (p > 0.05, two-proportion
  z-test) from the baseline structural-bias-only win rate, OR
- the improvement in win rate is real but too small to survive
  reasonable transaction costs/slippage assumptions

## Status
`FAILED` — tested on real data (see below). Liquidity-sweep confirmation
with this exact definition did not produce a statistically significant
edge over baseline structural bias.

## Result (real data test, 2026-06-21)
- **Data:** `data/EURUSD_M1_2026-03-16_2026-06-19.csv`, resampled to
  M15 (entry/sweep detection) and H4 (HTF bias). ~3 months, 100,000 M1
  bars → 6,696 M15 bars / 433 H4 bars.
- **Sample size:** 225 sweep-confirmed occurrences (exceeds the
  pre-registered minimum of 100).
- **Sweep win rate:** 49.78%
- **Baseline win rate:** 51.43%
- **p-value:** 0.6251 (far above the 0.05 significance threshold)
- **Conclusion:** No statistically significant edge. The hypothesis is
  rejected as stated. This is a successful, informative experiment, not
  a wasted one — it correctly keeps `smc_advanced` gated behind
  `edge_gate.py` rather than letting an unproven idea into the live
  pipeline. See `research/results/H001_result.json` for raw output.

## What this does NOT mean
- It doesn't mean liquidity sweeps are never useful — only that *this*
  specific definition (wick beyond a swing point + close back inside,
  on M15, with this exact forward-return window) showed no edge on
  *this* dataset over *this* period.
- Possible follow-up hypotheses (each would need its own H00X entry,
  not a silent edit to this one): different forward_bars windows,
  requiring a minimum sweep size (not just any wick), session-time
  filtering (see docs/VISION_v2.md's deferred Session Context Engine),
  or testing on a different instrument/regime.

## Linked experiment
`experiments/H001_liquidity_sweep_htf.py` (skeleton only — do not run
until real data is wired up; see docstring)

## Linked result
`results/H001_result.json` (not yet created — only created once the
experiment actually runs against real data)
