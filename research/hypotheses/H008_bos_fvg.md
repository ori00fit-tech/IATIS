# Hypothesis H008

## ID
H008

## Title
BOS (Break of Structure) + FVG (Fair Value Gap) confluence entry
improves win rate over random entry

## Why H001/H002/H002b Failed — and What H008 Does Differently

H001, H002, and H002b all tested **entering ON or immediately after a
liquidity sweep**. The combined result across 232 events and 3 symbols:
WR=46.12% — worse than the 49.78% H001 baseline.

The fundamental problem: a liquidity sweep tells you WHERE price went,
not WHERE it's going next. Price sweeping a low can be:
- A stop hunt before bullish reversal (what SMC theory predicts)
- Continuation of a bearish breakdown (more common in trending markets)
- Random noise (most common)

H008 adds two confirmation layers that H001/H002 lacked:

### Layer 1: BOS (Break of Structure)
After a potential stop hunt sweep, price must break above the nearest
swing high (for bullish setup) or below the nearest swing low (for bearish).
This confirms the sweep was a reversal, not a breakdown.

Without BOS: enter on any sweep (H001/H002 approach — failed)
With BOS: only enter when sweep is CONFIRMED by structural break

### Layer 2: FVG (Fair Value Gap) Entry
Instead of entering at market immediately after BOS, wait for price to
retrace into the Fair Value Gap (imbalance) created by the BOS candle.

This does three things:
1. Better entry price (lower risk)
2. Confirms the move is genuine (price respects the imbalance)
3. Tighter stop loss (below/above the FVG)

## Statement

When on M15 timeframe:
1. Price sweeps a prior swing low/high (liquidity taken)
2. Price then creates a BOS (breaks the opposite swing) within 10 bars
3. Price retraces into the FVG created by the BOS candle
4. H1 regime is TRENDING

...then the next 20 M15 bars close in the BOS direction significantly
more often than the 49.78% H001 baseline and the 46.12% H002b combined WR.

## Data Required
- EURUSD M15 (2yr Yahoo Finance H1 resampled, or direct M15 if available)
- GBPUSD M15 (same)
- H1 for regime context

## Falsification Criteria (pre-registered before running)
- PASS: p ≤ 0.05 AND improvement ≥ 10pp over 49.78% AND n ≥ 50
  (higher improvement threshold than H002 because BOS+FVG is a more
  specific pattern — if it doesn't improve by 10pp, it's not worth
  the implementation complexity)
- FAIL: p > 0.05 at n ≥ 50
- INCONCLUSIVE: n < 50 (pattern too rare on 2yr data)

## Implementation Notes
BOS detection:
  - After sweep of swing_low[i], find nearest swing_high[j] where j > i
  - BOS confirmed when close > swing_high[j] within 10 M15 bars of sweep

FVG detection:
  - On the BOS candle (the one that broke structure), FVG = gap between
    candle[t-1].high and candle[t+1].low (for bullish FVG)
  - Entry = when price retraces into FVG range

## Status
FAILED (2026-07-06) — closed by H008c.

The initial H008 run on 2yr H1-resampled data showed WR≈55.2% (n small,
p=0.23) → NEEDS_MORE_DATA. That was an artifact: H1-resampled bars are not
real M15 microstructure, and the base detector inherited a look-ahead bug
(centered swing window). H008c re-ran it correctly — real deep M15 (EURUSD
~1yr, XAUUSD ~3yr), causal swing confirmation (no look-ahead), chronological
out-of-sample split. Pooled TEST WR=0.489 (n=562, p=0.83): the coin-flip
baseline. The London+ATR filter H008b hoped would lift WR to 60% collapsed
out-of-sample (train n=11 63.6% → test 35.5%; pooled test 42.2%). BOS+FVG
has no standalone directional edge. See `H008c_oos.py`,
`results/H008c_result.json`, `results/h008c_oos_20260706_manifest.json`.

## If PASSED
Enables: `smc_advanced` engine with order blocks, FVG, BOS detection
(the features currently marked NOT_IMPLEMENTED_PHASE_3)

## If FAILED
The SMC framework as a STANDALONE entry signal generator does not have
a statistically provable edge. SMC/ICT concepts may still have value
as FILTERS (confluence with other methods) but not as primary entries.
Would pivot to: H009 — MA crossover + ATR filter as simpler baseline.

## Linked Files
- Experiment: `research/experiments/H008_bos_fvg.py` (to be written)
- Result: `research/results/H008_result.json` (when run)
