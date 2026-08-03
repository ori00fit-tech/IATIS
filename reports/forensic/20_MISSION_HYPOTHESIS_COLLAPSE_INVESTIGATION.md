# Mission Center Named-Hypothesis Execution — Forensic Investigation (2026-08-03)

## Trigger

Operator report: mission `fff9806b90c2` (EURUSD, "Search across named
hypotheses" enabled, 50 trials) produced 50 nominally-distinct trials that
were all identical — hypothesis H02, timeframe H1, engines smc+ict+nnfx,
PF 0.810, trades 402 — with the UI's own "Search space — No variation —
every trial identical" diagnostic firing. The operator's hypothesis: the
named-hypothesis bundles were lost somewhere in the UI → API → storage →
sampler → trial pipeline.

**Method**: full end-to-end trace of the pipeline (not assumption), plus
running this session's own already-existing regression tests that
specifically prove multi-hypothesis behavior against the *current* code
(several later phases — Diagnostic Infrastructure fingerprinting, Engine
Variants, Feature Mining, Edge Discovery — touched the same files after
Hypothesis Bundles shipped, so "it worked when built" doesn't prove "it
still works now" without re-running).

## A. Root cause

**There is no pipeline defect.** Traced every stage:

1. **Frontend** (`MissionCenter.tsx`'s `MissionBuilder.submit()`,
   `hypothesis_bundle_choices: bundles.map((b) => ({...}))`) — maps
   **every** entry in the `bundles` React state array into the request
   body. No truncation, no stale-closure bug, no silent drop.
2. **API** (`execution/routes/missions.py`) — `_MissionRequest.
   hypothesis_bundle_choices: list[dict] | None`, passed verbatim into
   `MissionSearchSpace(...)` for validation, then JSON-serialized whole
   into `argv` (`--hypothesis-bundle-choices`).
3. **Storage** — `mission_runner.py`'s `_search_space_dict()` serializes
   `space.hypothesis_bundle_choices` (the full tuple) into the mission's
   `search_space_json` row, unabridged.
4. **Sampler** (`backtest/optimizer.py::suggest_point()`) — when
   `space.hypothesis_bundle_choices` is set, calls **exactly one**
   `trial.suggest_categorical(_HYPOTHESIS_IDX_KEY, list(range(len(space.
   hypothesis_bundle_choices))))` — with N bundles, Optuna genuinely has N
   categorical choices to sample from (TPE/random/grid/NSGA2 all
   correctly explore this).
5. **Trial materialization** (`resolve_point()`) — pulls timeframes/
   engines/indicators/context_filters/engine_variants ALL from the ONE
   bundle selected by `_HYPOTHESIS_IDX_KEY`, atomically — no field-mixing
   across bundles is possible (there is no code path that reads a field
   from `bundle[i]` and another from `bundle[j]`).
6. **Backtest invocation / stored trial result** — `evaluate_point()`
   passes the resolved point straight into `build_engine_config_override
   (...)`/`run_backtest(...)`, no further transformation.

**The actual explanation for mission `fff9806b90c2`'s result**: the
operator's screenshot of the Mission Builder at the time shows exactly
**one** hypothesis card ("H02") in the bundle list, not multiple. With
`len(hypothesis_bundle_choices) == 1`, `_HYPOTHESIS_IDX_KEY` has exactly
one possible value — every trial samples it, every trial is identical,
`grid_size()`/`classify_search_space_variation()` correctly report "no
variation." **This is the system working exactly as designed, not a
collapse of multiple hypotheses into one** — there was only ever one
hypothesis in that specific mission's actual request.

**Live proof, not just static reading**: re-ran this session's own
existing end-to-end regression test against the **current** code —
`tests/test_mission_runner.py::test_mission_run_with_hypothesis_bundles_uses_both_bundles`
constructs a real `MissionConfig` with 2 hypothesis bundles (`"SMC
only"` vs. `"NNFX + Wyckoff + Price Action"`, different engine sets),
runs the real `run_mission()` orchestrator against synthetic OHLCV, and
asserts trials for BOTH bundles appear in the leaderboard. **This test
passes today**, proving the pipeline currently and correctly handles the
genuine multi-hypothesis case — the exact scenario the operator's
hypothesized bug would have broken.

## B. The real, narrower gap found — and fixed

Although the hypothesized architectural bug does not exist, the
investigation surfaced a real, legitimate gap matching the operator's own
Invariant 5 in spirit: **a mission in hypothesis-search mode with only
1 hypothesis was silently accepted and ran N wasted trials with zero
warning before execution.** `MissionSearchSpace.__post_init__` already
rejected an *empty* `hypothesis_bundle_choices` list, but not a
*single-entry* one — mathematically valid, but pointless as a "search."

### Fix

**`execution/routes/missions.py`** (mission-creation endpoint only — see
below for why not the shared dataclass): added a fail-fast check
immediately after `MissionSearchSpace`'s existing validation, before any
argv/subprocess work:

```python
if body.hypothesis_bundle_choices is not None and len(body.hypothesis_bundle_choices) == 1:
    raise HTTPException(
        status_code=400,
        detail=(
            "hypothesis_bundle_choices has only 1 entry — hypothesis-search "
            "mode requires 2+ named hypotheses to search across, or every "
            "trial will sample the same one. Add another hypothesis, or omit "
            "hypothesis_bundle_choices to run a fixed-configuration mission."
        ),
    )
```

**Why not added to `MissionSearchSpace.__post_init__` itself**: that
shared dataclass is also used to *reconstruct already-completed*
missions (`backtest/mission_validator.py`'s `run_validation()`, the
`/research/missions/{id}/meta-analysis` endpoint) — a mission that
already ran with 1 bundle (like `fff9806b90c2`) must still be
reconstructible for its own detail/meta-analysis views. The guard
belongs only at the mission-**creation** entry point.

**`dashboard/frontend/.../MissionCenter.tsx`** (`MissionBuilder.submit()`):
mirrored client-side check with the same message, avoiding an
unnecessary round-trip:

```tsx
if (bundles.length === 1) {
  setError('Add at least 2 hypotheses to search across, or turn off "Search across named hypotheses" to run one fixed configuration.')
  return
}
```

## C. Tests

All against the **current** code, not assumed from history:

- **Re-ran, confirmed still passing** (proves no regression from later
  phases): `tests/test_optimizer.py -k hypothesis` (20/20),
  `tests/test_mission_runner.py -k hypothesis` (2/2, including the
  authoritative `test_mission_run_with_hypothesis_bundles_uses_both_bundles`
  end-to-end proof).
- **New**: `tests/test_missions.py::test_missions_create_rejects_a_single_hypothesis_bundle`
  — a real HTTP request with exactly 1 bundle → 400, message contains
  "2+ named hypotheses" (pins the fix).
- **New**: `tests/test_missions.py::test_missions_create_accepts_two_hypothesis_bundles`
  — the real, intended 2-bundle case → 200 (regression guard: the fix
  must not reject the case it's meant to allow).
- Full targeted run: `tests/test_missions.py tests/test_optimizer.py
  tests/test_mission_runner.py` — 140/140 passed.
- `tsc -b`, `oxlint src` — clean, zero new warnings.

## D. Before / after

**BEFORE** (mission `fff9806b90c2`, reconstructed from its actual
submitted shape): `hypothesis_bundle_choices` = 1 entry ("H02") →
`n_trials_per_symbol` = 50 → 50 trials, 1 effective configuration,
silently accepted, no warning.

**AFTER**: submitting the same 1-bundle request now returns
`400 Bad Request` with an actionable message *before* any trial runs.
Submitting a genuine 2+-bundle request (verified via the existing,
re-confirmed end-to-end test) produces trials spanning **all** supplied
bundles, sampled independently by the configured sampler — proving the
"multiple hypotheses → multiple effective configurations" property holds
for the case that actually matters.

## E. Remaining limitations (stated, not hidden)

- This fix prevents a **1-bundle** hypothesis-search mission from
  launching. It does **not** implement the fuller "unique effective
  configurations / duplicate trials" reporting the operator's spec
  requested (Invariant 4: requested vs. registered vs. executed
  hypotheses, duplicate-trial counts) — that is a real, separate,
  larger UI/backend reporting feature, not a bug fix, and was not built
  here to keep this pass scoped to the actual defect found. The existing
  `search_space_kind`/"No variation" diagnostic already covers the
  degenerate case at the mission level; a full fingerprint-based
  duplicate-trial count is a legitimate future enhancement.
- Cannot inspect mission `fff9806b90c2`'s actual persisted
  `search_space_json` from this sandbox (no live D1 credentials) to
  directly confirm it had exactly 1 bundle rather than something more
  exotic — the conclusion rests on (a) the operator's own screenshot
  showing 1 hypothesis card in the builder at the time, and (b) the
  fact that every other pipeline stage was independently verified
  correct via direct code reading and a real, currently-passing
  end-to-end test using 2 genuinely different bundles. The operator can
  confirm directly on the VPS via `GET /research/missions/fff9806b90c2`'s
  `search_space_json` field.

## F. Git diff summary

Modified: `execution/routes/missions.py` (+18 lines, fail-fast check),
`dashboard/frontend/src/modules/mission-center/MissionCenter.tsx`
(+7 lines, client-side mirror), `tests/test_missions.py` (+2 tests).

Not touched (per the operator's explicit safety rules): any engine file
(`smc`, `ict`, `nnfx`, `quant`, `wyckoff`, `market_structure`, RSI/ATR
thresholds), any backtest profitability calculation, `backtest/
optimizer.py`, `backtest/mission_runner.py` (both read-only confirmed
correct, zero changes needed).

## G. Status

No pipeline defect found or fixed — confirmed, with a real end-to-end
test, that multiple named hypotheses are correctly preserved and
independently sampled through the entire UI-to-trial pipeline. One real,
narrower gap (a degenerate 1-hypothesis "search" silently running N
wasted trials) found and fixed with a fail-fast validation, both server-
and client-side, with regression tests. Commit follows.
