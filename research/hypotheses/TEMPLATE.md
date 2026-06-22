# Hypothesis Template

Copy this file to `hypotheses/HXXX_short_name.md` and fill it in
**before** writing any experiment code. If you can't fill in
"Falsification criteria" honestly, the hypothesis isn't specific enough
to test yet.

---

## ID
H001

## Title
(one line, e.g. "Liquidity sweep + HTF trend alignment improves win rate")

## Statement
What exactly are you claiming? Be specific enough that someone else
could try to prove you wrong.

> Example: "When price sweeps a prior swing low on M15 AND H4 structure
> is bullish, the next 20 M15 bars close higher than the sweep low more
> often than a random entry baseline."

## Why this might be true
Brief reasoning — what market behavior would explain this if it's real?

## Data required
- Symbol(s):
- Timeframe(s):
- Date range:
- Minimum sample size needed: (state this *before* running the test)

## Falsification criteria
What result would make you reject this hypothesis? Decide this BEFORE
looking at results — otherwise you'll rationalize whatever you see.

> Example: "Reject if win rate is not statistically distinguishable
> (p > 0.05) from a random-entry baseline with the same exit rules,
> across at least 100 occurrences."

## Status
`PENDING` | `PASSED` | `FAILED` | `INCONCLUSIVE`

## Linked experiment
`experiments/H001_*.py`

## Linked result
`results/H001_result.json`
