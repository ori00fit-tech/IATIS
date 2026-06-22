# Research Layer — Edge Gate

**Rule (non-negotiable):** no engine logic may be enabled in
`config.yaml` (`engines.enabled.*: true`) until a hypothesis for it has
gone through this pipeline and produced a documented, positive result.

This exists because the project's biggest risk isn't bad code — it's
**fooling ourselves** into thinking a clean architecture means a
profitable system. Architecture is necessary, not sufficient.

## Flow

```
hypotheses/   -> write down the claim, in plain language, before any code
experiments/  -> the actual test script that tries to validate/reject it
results/      -> the output: numbers, not opinions
notebooks/    -> exploratory analysis (optional, not authoritative)
```

A hypothesis only "passes" if its `results/` entry shows a positive,
statistically meaningful edge over a reasonable sample (see
`hypotheses/TEMPLATE.md` for what "meaningful" means here). A clean
backtest curve with 20 trades is not evidence — it's noise.

## Status tracking

`results/registry.json` is the single source of truth for which
hypotheses have passed, failed, or are still pending. `main.py` and
`config.yaml` should never enable an engine that doesn't have a `PASSED`
entry here.

## Why this lives outside engines/

Code in `engines/` is meant to look production-ready. Code in
`research/` is meant to look like what it is: an experiment that might
fail. Keeping them physically separate stops "I'll just enable it
temporarily to see" from quietly becoming permanent.
