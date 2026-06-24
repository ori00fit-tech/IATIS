# Hypothesis H002

## ID
H002

## Title
Qualified liquidity sweep (minimum size + trending regime) improves
win rate over H001's unfiltered sweep definition

## Statement
When price sweeps a prior swing low/high on M15 AND:
  1. The sweep wick is at least 1× ATR(14) in size (not just any wick)
  2. The H1 regime is TRENDING (not RANGING) at the time of the sweep
  3. The H1 structural bias agrees with the reversal direction

...then the next 20 M15 bars close in the expected direction more often
than both (a) a random-entry baseline and (b) H001's unfiltered sweep
definition (win_rate=0.4978).

## Why this might be true (building on H001's failure)

H001 failed because it accepted any wick beyond a swing point, including
tiny micro-wicks in ranging markets that have no institutional significance.
Two refinements that market microstructure theory suggests should matter:

1. **Sweep size:** A sweep of at least 1 ATR is large enough to trigger
   meaningful stop clusters and attract institutional order flow. Sub-ATR
   wicks are noise. H001 included all wicks equally.

2. **Regime filter:** Liquidity sweeps in trending markets are more likely
   to be institutional "stop hunts" before continuation. In ranging markets,
   any reversal from a sweep is equally likely to be faded again. H001 did
   not filter by regime.

## Data required
- Symbol: EURUSD (same dataset as H001 for comparability)
- Timeframes: M15 (sweep detection), H1 (regime + structural bias)
- Date range: same 2026-03-16 to 2026-06-19 dataset
- Minimum sample size: 60 qualified sweeps (pre-registered before running)
  Note: qualification filters will reduce sample from H001's 225 — this
  is expected and acceptable if the edge improves proportionally.

## Falsification criteria (decided BEFORE running)
Reject H002 if ANY of the following:
  - Sample size < 30 qualified sweep occurrences (insufficient data)
  - Win rate not statistically distinguishable from H001 baseline (0.4978)
    at p ≤ 0.05 (two-proportion z-test)
  - Win rate improvement over H001 < 5 percentage points absolute
    (not worth the added complexity of the filter)

## Status
`PENDING` — experiment script ready, requires real data (already available
in data/EURUSD_M1_2026-03-16_2026-06-19.csv).

## If PASSED
Enables: `smc_advanced` engine (order blocks, FVG, BOS/CHOCH detection)
— the SMC features currently marked NOT_IMPLEMENTED_PHASE_3 in
`engines/smc_engine.py` will be implemented and activated.

## If FAILED
Document which sub-hypothesis is wrong (size filter? regime filter? both?)
and design H003 with a different angle — e.g. session-time filtering
(London open sweeps only) or using BOS instead of sweep as the signal.

## Linked experiment
`experiments/H002_qualified_sweep.py`

## Linked result
`results/H002_result.json` (created when experiment runs)
