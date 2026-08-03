# Mission Search-Space Claim — Forensic Finding

**Status:** CONFIRMED (structural design property, not a data-integrity or
execution bug). Fix applied in the same change that produced this report.

## Claim (operator's own words, translated)

> The Mission Center UI implies it searches engine + timeframe + context
> combinations, but Meta-Analysis reported that 22 trials only varied
> risk/cost parameters and all shared the same entry-signal stream. If this
> is not just a user-configuration choice but an actual Mission/Search-Space
> implementation defect, this is a serious problem.

## Evidence

- **Code location**: `dashboard/frontend/src/modules/mission-center/MissionCenter.tsx`,
  `MissionBuilder`'s `submit()`, lines 571-575 (verified again while writing
  this report):
  ```tsx
  timeframes_choices: [hypothesisMode ? bundles[0].timeframes : timeframes],
  engine_set_choices: [hypothesisMode ? bundles[0].engines : engines],
  indicator_set_choices: [hypothesisMode ? toIndicatorSpecs(bundles[0].indicatorFilters) : indicatorSpecs],
  context_filter_set_choices: [hypothesisMode ? toContextSpecs(bundles[0].contextFilters) : contextSpecs],
  ```
  In flat mode (`hypothesisMode === false`, the UI's default state on every
  new mission), all four signal dimensions are wrapped in a **single-element
  array** — regardless of how many timeframes/engines/indicators/context
  filters the operator selected inside that one combination.
- **Reproduction**: `tests/test_optimizer.py::test_flat_mode_request_shape_has_no_signal_variation`
  builds a `MissionSearchSpace` from the exact shape flat mode sends
  (single-element tuples, each containing multiple values) and asserts
  `search_space_has_signal_variation(space) is False` — proving this holds
  structurally, independent of how rich the one selected combination is.
- **Mechanism**: `backtest/optimizer.py`'s `suggest_point()` calls
  `trial.suggest_categorical(_TF_IDX_KEY, list(range(len(space.timeframes_choices))))`
  for each of the four dimensions. With exactly one choice per dimension,
  every trial necessarily samples index 0 — the sampler has nothing else to
  choose. Only `risk_param_ranges`/`risk_param_grid` (continuous, sampled
  independently) can vary trial to trial in flat mode.

## Root cause

Not a sampler bug, not a data-integrity bug, not a Mission/Search-Space
implementation defect distinct from what the UI actually sends. It is a
**truth-in-UI gap**: flat mode was always designed to fix one combination
and sweep only risk/cost parameters (this is exactly what the pre-existing
"Search across named hypotheses" toggle and Hypothesis Bundles feature was
built to fix, for operators who opt in) — but nothing in the flat-mode UI
told the operator this would happen before they launched a mission and
interpreted 22 trials as 22 independent tests of different strategies.

The system's own dependence-detection machinery (`search_space_has_signal_variation()`,
`backtest/meta_analysis.py`'s `DEPENDENT_TRIALS_LEAD_ONLY` override) already
caught this and correctly downgraded the mission's significance — the
Meta-Analysis report the operator read was the system telling the truth,
not failing to.

## Fix applied

`MissionBuilder` now renders a persistent amber warning whenever
`hypothesisMode` is `false`:

> "Flat mode fixes ONE engine/timeframe/indicator/context combination for
> every trial — only risk/cost parameters actually vary across trials. To
> search across genuinely different signal configurations, enable 'Search
> across named hypotheses' below and define 2+ bundles."

No backend logic changed — this is a warning, not a behavior change, since
flat mode's one-combo-per-mission design is intentional and correct for its
own purpose (isolating risk/cost sensitivity on one fixed strategy).

## Regression test

`tests/test_optimizer.py::test_flat_mode_request_shape_has_no_signal_variation`
— permanent pin. If flat mode ever legitimately gains multi-choice support,
this test must be revisited deliberately, not silently broken.
