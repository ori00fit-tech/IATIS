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

---

## BUG-002

**Severity:** P0

**Category:** Backtest Forensics — execution simulation / gap handling.

**Claim:** `backtesting/backtest_engine.py`'s `run_backtest()` loop never
checks a newly-opened trade's own entry bar for an SL/TP touch — only
bars strictly AFTER the entry bar are ever checked. A resting stop/limit
order is live from the instant of entry, so this is a real simulation
gap, not a cosmetic one: it can silently ERASE a genuine same-bar
stop-out and report the trade as a winner instead.

**Observed:** Traced every real `check_exit()` call made during a real
`run_backtest()` run (synthetic OHLCV, real engines, real gates — no
mocks) by wrapping the function: for a trade with `entry_bar=210`, the
FIRST bar ever passed to `check_exit()` for that trade was bar **211**
(entry_bar + 1). Bar 210 — the entry bar itself, whose OPEN price was
literally the trade's entry price — was never checked at all.

**Expected:** The entry bar's own remaining high/low range (the
excursion after the open where the trade was entered) must be checked
against SL/TP immediately, the same as every subsequent bar — matching
how a real resting stop-loss order behaves in live trading.

**Evidence:**
- Direct trace against a real `run_backtest()` run confirmed via
  monkeypatched `check_exit`: 3/3 real trades produced had their first
  exit-check bar at `entry_bar + 1`, never `entry_bar`.
- Adversarial reproduction (wide intrabar wicks, tight `sl_atr_multiplier
  =0.3`): **28 of 28** trades in a real `run_backtest()` run would have
  exited via SL on their own entry bar if checked, per `check_exit()`
  applied directly to that bar — none of them were.
- **Decisive, outcome-flipping reproduction**: hand-crafted a 4-bar
  sequence where the entry bar (bar[0]) wicks below SL (a classic
  "stop-hunt" candle) then recovers, and price later rallies to TP by
  bar[3]. The PRE-FIX bar sequence the loop actually checked (bars 1-3
  only) reports **TP — a win**. Checking the entry bar itself (bar[0],
  as a correct simulation must) reports **SL — a loss**. The trade's
  final outcome, sign of PnL included, literally flips.

**File / Line (pre-fix):** `backtesting/backtest_engine.py`, the main
loop (`for i in range(config.warmup_bars, len(df) - 1):`, originally
lines 631-648) — the "Check open trade" block at the top of each
iteration only ever calls `check_exit(open_trade, next_bar, slip)` where
`next_bar = df.iloc[i + 1]` of the CURRENT iteration; by the time a
newly-opened trade's own entry bar (`df.iloc[i+1]` from the iteration
that opened it) would need checking, the loop has already advanced to
iteration `i+1`, where `next_bar` refers to `df.iloc[i+2]`.

**Execution path:** `run_backtest()` → main loop → (EXECUTE decision) →
`Trade(entry_bar=i+1, ...)` constructed → next loop iteration's
"Check open trade" block → `check_exit(trade, df.iloc[i+2], slip)`,
permanently skipping `df.iloc[i+1]`.

**Reproduction:** See the three reproductions above — all performed
against the real, unmodified (pre-fix) `run_backtest()`/`check_exit()`
code, using real synthetic OHLCV and (for the trace + 28/28 case) the
real engine pipeline, not mocks.

**Root cause:** An off-by-one in the loop's bar-index bookkeeping: the
"Check open trade" block runs once per outer-loop iteration, at the TOP
of the iteration, using that iteration's own `next_bar`. A trade opened
at the BOTTOM of iteration `i` (using that same `next_bar` as its entry
bar) is never re-presented to the "Check open trade" block using its own
entry bar — the block only ever runs again on the FOLLOWING iteration,
whose `next_bar` has already advanced one bar further.

**Impact:** Every historical backtest run through this engine (which
also underlies `backtest/mission_runner.py`, `backtest/walk_forward.py`,
and `backtest/robustness.py` — all of which call `run_backtest()`) could
have missed genuine same-bar stop-outs, especially in volatile
conditions (news spikes, thin liquidity, tight stops relative to
intrabar range) where a wick-and-recover candle is common. This biases
reported win rate, profit factor, and drawdown in an unpredictable
direction (not uniformly optimistic or pessimistic — depends on whether
the missed bar's price recovers favorably or unfavorably before the next
checked bar) — a real threat to the "is the experiment itself valid"
question this whole audit exists to answer. **Confirmed NOT to touch the
live decision pipeline** — `backtesting/backtest_engine.py` is
backtest-only; `main.py`/`scheduler.py` use a separate live pipeline that
never imports this module — so this fix changes measurement accuracy
only, never live trading behavior, and does not conflict with CLAUDE.md
rule 6 ("never change entries/exits/thresholds mid-sample," which
governs the live forward-demo system).

**Fix (CONFIRMED, applied same phase):** Immediately after constructing
a new `Trade` and incrementing `result.execute_count`, the loop now also
calls `check_exit(open_trade, next_bar, slip)` against that SAME entry
bar before moving to the next iteration — if it fires, the trade is
closed and recorded right there (same `_close_trade`/`result.trades.append`
path every other exit already uses), exactly mirroring how the existing
"Check open trade" block handles every later bar.

**Regression test:**
`tests/test_backtest_exits.py::test_run_backtest_checks_exit_on_the_entry_bar_itself`
(real `run_backtest()` run, real engines, asserts ≥1 trade now exits with
`exit_bar == entry_bar`) and
`tests/test_backtest_exits.py::test_run_backtest_entry_bar_exit_flips_a_would_be_missed_stopout`
(the literal outcome-flip proof — TP under the old bar sequence, SL once
the entry bar is checked). Full suite re-run after the fix: 168 tests
across `test_backtest_runner_wf.py`, `test_robustness.py`,
`test_engine_variants.py`, `test_phase1.py`, `test_behavior.py`,
`test_decision_timeframe.py`, and the golden-value regression suite
`test_engine_config_extraction_no_behavior_change.py` — zero failures.

**Status:** FIXED, tested, regression-pinned. **Not yet re-run against
historical committed backtest reports** — any PF/win-rate numbers in
`research/results/registry.json` or `reports/` computed before this fix
may shift slightly on re-run; this is a measurement-accuracy correction,
not a claim that any prior number was fabricated. Re-running
already-registered evidence hypotheses against the fixed engine is a
natural follow-up, not performed as part of this forensic pass (out of
scope — this pass fixes the tool, not the historical record).

## BUG-003

**Severity:** P2

**Category:** Backtest Forensics — equity-curve reporting accuracy (direct
side-effect of the BUG-002 fix above, not an independent finding).

**Claim:** After BUG-002's fix (checking a trade's own entry bar for a
same-bar SL/TP exit), the `equity_curve` list entry recorded for that
same bar is stale — it still shows the pre-trade balance. The trade's
real PnL only appears in the NEXT entry, one bar later than it actually
happened.

**Observed:** For a trade with `entry_bar == exit_bar` (i.e. it both
opens and closes on the same bar), `result.equity_curve[i]` (the point
appended during the loop iteration that opened the trade) equals the
PREVIOUS point exactly — the trade's `pnl_usd` doesn't show up until
`result.equity_curve[i+1]`.

**Expected:** The equity_curve entry for the bar a trade's PnL was
realized on should reflect that PnL immediately — no report/metric
built from this array should have to look one entry ahead to find out
what happened on a given bar.

**Evidence:** Reproduced directly against the (already-BUG-002-fixed)
`run_backtest()` using the same wide-wick/tight-SL synthetic setup as
BUG-002's own reproduction (300 bars, seed 3, `sl_atr_multiplier=0.3`):
28/28 same-bar-exit trades all showed `equity_curve[idx_at_open_iter] ==
equity_curve[idx_at_open_iter - 1]` (unchanged) while
`equity_curve[idx_at_open_iter + 1]` carried the full PnL delta —
confirmed by direct inspection of the array around each trade's index,
not inferred.

**File / Line (pre-fix):** `backtesting/backtest_engine.py`, main loop —
`result.equity_curve.append(balance)` (originally line 644) runs once
per iteration, BEFORE the "Skip if in trade or not on step" check and
before the pipeline/entry logic that opens a new trade and (post
BUG-002) immediately checks it for a same-bar exit. That same-bar close
updates `balance` AFTER the equity append for that same iteration has
already happened.

**Execution path:** `run_backtest()` main loop, iteration `i` → equity
append (pre-trade balance) → pipeline runs, EXECUTE decision → `Trade`
opened → BUG-002's same-bar `check_exit()` fires → `_close_trade()`
updates `balance` → loop moves to iteration `i+1` → NEXT equity append
is the first point to reflect the updated balance.

**Reproduction:** Ran the real `run_backtest()` against the same
synthetic OHLCV as BUG-002's `test_run_backtest_checks_exit_on_the_
entry_bar_itself`, located every same-bar-exit trade's corresponding
`equity_curve` index by loop-index arithmetic
(`idx_at_open_iter = (entry_bar - 1) - warmup_bars + 1`), and directly
compared `equity_curve[idx_at_open_iter]` /
`equity_curve[idx_at_open_iter - 1]` / `equity_curve[idx_at_open_iter +
1]` against each trade's known `pnl_usd`. Confirmed the discrepancy on
all 28 same-bar trades in the reproduction set.

**Root cause:** A code-ordering gap: `equity_curve.append(balance)`
executes unconditionally near the top of each loop iteration, but the
same-bar exit check BUG-002 introduced runs later in that same
iteration — so the equity snapshot is taken before that iteration's own
trade-close side effect exists.

**Impact:** Does not affect any trade's own recorded outcome (entry/
exit/PnL/win-loss — all correct per BUG-002's fix); does not affect
final balance, total return, or `execute_count`/`win_rate` (all computed
from `result.trades`, not `equity_curve`). It does affect
`BacktestResult.compute()`'s `max_drawdown_pct` and `sharpe_ratio`
(both derived directly from `equity_curve`), and every consumer that
renders the equity/drawdown curve or monthly-returns heatmap from this
array (`backtest/report.py`'s `chart_data.json`, Backtesting Charts'
`EquityCurveSvg`/`DrawdownCurveSvg`, `MonthlyReturnsHeatmap`) — for a
same-bar-exit trade specifically, the curve shows a one-bar-late step
instead of the PnL landing on the bar it actually happened on. Confined
to same-bar-exit trades only (a minority of trades in most runs); does
not touch the live decision pipeline for the same reason BUG-002 didn't
(`backtesting/backtest_engine.py` is backtest-only).

**Fix (CONFIRMED, applied same phase):** Immediately after the same-bar
`_close_trade()` call updates `balance`, the loop now patches the
already-appended entry in place: `result.equity_curve[-1] = balance` —
correcting that bar's point to include the same-bar trade's PnL, rather
than restructuring the append's position in the loop (which would have
required threading the append past every existing `continue` in the
gate-rejection branches).

**Regression test:**
`tests/test_backtest_exits.py::test_same_bar_exit_updates_equity_curve_immediately_not_one_bar_late`
— re-derives the exact `equity_curve` index for every same-bar-exit
trade in a real `run_backtest()` run and asserts the delta between that
index and the previous one equals the trade's `pnl_usd` exactly
(`pytest.approx`, `abs=1e-6`). Full suite re-run after the fix: 12/12 in
`tests/test_backtest_exits.py`.

**Status:** FIXED, tested, regression-pinned. Same disclosed limitation
as BUG-002: historical `reports/`/`research/results/registry.json`
evidence computed before this fix is not re-run — this is a
measurement-accuracy correction to the tool, not a retroactive claim
about any prior recorded number.
