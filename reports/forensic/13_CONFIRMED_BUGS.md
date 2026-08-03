# Confirmed Bugs — Forensic System Audit Phase 1

Only entries with complete, reproduced evidence appear here. No fabricated
or speculative entries — an unreproduced suspicion is UNCONFIRMED, not a
bug, per the operator's own required discipline.

---

## BUG-001

**Severity:** P1

**Category:** Validation Forensics (research/statistical integrity)

**Claim:** Mission Center's validation flow ties a validation run's
symbol set to nothing about the trial being validated — an operator
(or a client bug) can "validate" a trial against a set of symbols that
structurally excludes the trial's own symbol, and the system will
silently accept it and report a NO_EDGE/WEAK_LEAD/STRONG_LEAD verdict
as if it were legitimate cross-symbol evidence.

**Observed:** `tests/test_missions.py`'s own canonical "valid" validation
request fixture (`_VALID_VALIDATION_BODY`, before this fix) validated an
`EURUSD` trial against `validation_symbols: ["GBPUSD", "XAUUSD"]` — a
combination that excludes `EURUSD` entirely — and was accepted as
ordinary, correct input. No code path anywhere (client, Pydantic model,
storage DDL, or `backtest/mission_validator.py`) checked
`trial_symbol` against `validation_symbols`.

**Expected:** A validation run should be explicit about whether it is
confirming the trial's own symbol (a narrower, same-instrument check) or
testing generalization to other symbols (the system's actual
cross-symbol-generalization claim, `MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD
= 3` requiring ≥3 *other* symbols to reach `STRONG_LEAD`). Mixing the two
without any code-level distinction risks silently reporting a same-symbol
confirmation as if it were cross-symbol evidence.

**Evidence:**
- `execution/routes/missions.py`'s `missions_validate` handler (pre-fix):
  the only enforced rule was `len(validation_symbols) < 2` → 400. No
  membership check against `trial_symbol` existed at any layer.
- `dashboard/frontend/src/modules/mission-center/MissionCenter.tsx`'s
  `ValidateAction` (pre-fix): `validationSymbols` initialized to
  `useState<string[]>([])` — empty, never pre-seeded with the trial's own
  symbol, and the only client-side check was `validationSymbols.length <
  2`.
- `backtest/mission_validator.py`'s `run_validation()` (pre-fix): the
  verdict computation (`NO_EDGE`/`WEAK_LEAD`/`STRONG_LEAD`) ran
  identically regardless of whether `validation_symbols` included
  `trial_symbol` or not.

**File / Line (pre-fix):**
- `execution/routes/missions.py`, `missions_validate` (validation-symbols
  gate, originally ~lines 407-413).
- `backtest/mission_validator.py`, `run_validation()` (verdict branch,
  originally ~lines 349-355).

**Execution path:** `POST /research/missions/{id}/validate` →
`missions_validate` → subprocess `python3 -m backtest.mission_validator`
→ `run_validation()` → `research_mission_validations.set_validation_status(...,
overall_verdict=...)`.

**Reproduction:** `tests/test_missions.py`'s pre-fix `_VALID_VALIDATION_BODY`
(trial_symbol=`EURUSD`, validation_symbols=`["GBPUSD", "XAUUSD"]`) passed
the server's own validation gate with `status_code == 200` and no warning.

**Root cause:** The Validation flow was designed and built entirely around
cross-symbol generalization (the `MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD`
threshold, the ≥2-symbol requirement) without ever adding an explicit mode
concept — the assumption that "validation symbols are always some other
symbols" was implicit, undocumented, and unenforced.

**Impact:** An operator could unknowingly generate a same-symbol-inclusive
or same-symbol-exclusive validation and read its `NO_EDGE`/`WEAK_LEAD`/
`STRONG_LEAD` verdict as generic evidence quality, without the system ever
distinguishing "this only re-confirms the trial's own training symbol"
from "this genuinely tests generalization to other instruments" — a real
risk of false confidence in exactly the way this whole audit phase exists
to catch.

**Fix (CONFIRMED, applied same phase):**
- `backtest/mission_validator.py`: new `SAME_SYMBOL`/`CROSS_SYMBOL`
  constants + `VALIDATION_MODES`; `ValidationConfig.validation_mode:
  str = SAME_SYMBOL` (new default); `run_validation()` branches verdict
  computation by mode — `SAME_SYMBOL` produces `SAME_SYMBOL_CONFIRMED`/
  `SAME_SYMBOL_NOT_CONFIRMED` (a deliberately distinct vocabulary from
  `NO_EDGE`/`WEAK_LEAD`/`STRONG_LEAD`, which stay reserved for
  cross-symbol claims); `CROSS_SYMBOL` path is byte-for-byte unchanged.
- `execution/routes/missions.py`: `_ValidationRequest.validation_mode:
  str = "SAME_SYMBOL"`; the symbol gate is now mode-branched —
  `SAME_SYMBOL` requires `validation_symbols` to be omitted/empty
  (defaults to `[trial_symbol]`) or exactly `[trial_symbol]`, **any other
  value is a 400, FAIL HARD**, never silently substituted or widened;
  `CROSS_SYMBOL` keeps today's exact ≥2-symbols rule, unchanged.
- `storage/research_mission_validations.py` / `storage/migrations.py`
  (migration version 7): `validation_mode` column added, DDL default
  `'CROSS_SYMBOL'` (honestly describing every historical row's real
  shape — the opposite of the new API default, intentionally).
- Frontend: `ValidateAction` now defaults to `SAME_SYMBOL` mode with the
  trial's own symbol pre-filled and displayed read-only; a mode toggle
  reveals `CROSS_SYMBOL` mode with a persistent banner reading exactly
  "CROSS-SYMBOL VALIDATION — this is not same-symbol validation."

**Regression test:** `tests/test_missions.py::test_missions_validate_same_symbol_fails_hard_on_mismatched_symbols`
(the literal invariant test — a `SAME_SYMBOL` request with mismatched
symbols now returns 400), plus
`tests/test_mission_validator.py::test_cross_symbol_mode_still_uses_cross_symbol_vocabulary`
(the byte-for-byte CROSS_SYMBOL regression proof) and 3 additional
`SAME_SYMBOL`-vocabulary tests in the same file.

**Status:** FIXED, tested, regression-pinned.
