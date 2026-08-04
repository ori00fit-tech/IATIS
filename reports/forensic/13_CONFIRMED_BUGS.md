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

## BUG-004

**Severity:** P0

**Category:** Backtest Forensics — P&L calculation correctness (cost
modeling). Directly relevant to CLAUDE.md's "measured edge" claim, since
it affects exactly the carrier assets (XAUUSD, BTCUSD, ETHUSD) that
claim is based on.

**Claim:** `run_backtest()`'s trade-closing logic (`_close_trade`)
never subtracted `commission_pips` from a trade's `pnl_usd` for any
non-forex asset class (metal/index — which covers XAUUSD, XAGUSD,
USOIL, US30, NAS100, SPX500, and crypto BTCUSD/ETHUSD per
`BacktestConfig.from_profile`'s asset-class mapping), even though
`REAL_SPREAD_PIPS` (measured real broker spreads, 2026-07-06) supplies
non-trivial commission values for exactly those symbols (XAUUSD: 12.0
pips = $12/lot; BTCUSD: 1200.0 pips = $12/lot; ETHUSD: 290.0 pips =
$2.90/lot; etc.).

**Observed:** For a closed XAUUSD trade, `trade.pnl_usd` equals its
gross P&L (`price_diff * position_size * dollar_per_point`) exactly —
zero commission deducted, despite `config.commission_pips == 12.0`.
`trade.pnl_pips` (a separate, display-only field) DOES include the
commission subtraction, so the discrepancy is silent: the "pips" figure
looks cost-aware while the actual money figure used for every
downstream metric is not.

**Expected:** Every closed trade's `pnl_usd` — forex or not — should
reflect the same real trading cost (commission + swap) that
`REAL_SPREAD_PIPS`/`data/swap_rates.json` were built to model.

**Evidence:** Reproduced directly against the real, unmodified
(pre-fix) `run_backtest()` using synthetic XAUUSD-shaped OHLCV (400
bars, seed 7, H4). `BacktestConfig.from_profile("XAUUSD", ...)` resolved
`asset_class="metal"`, `commission_pips=12.0`, `pip_size=0.01`,
`dollar_per_point=100.0`. Across 5 real closed trades, every one showed
`trade.pnl_usd == gross_usd` to floating-point precision (zero
commission subtracted), while the hand-computed expected commission
(`commission_pips * pip_size * position_size * dollar_per_point`) was
$0.73-$0.89 per trade — a real, nonzero, silently-dropped cost.

**File / Line (pre-fix):** `backtesting/backtest_engine.py`, the
`_close_trade` closure inside `run_backtest()` (originally lines
599-629):
```python
trade.pnl_usd = _calc_pnl_usd(diff, trade.position_size, trade.entry_price)
if ac == "forex":
    trade.pnl_usd -= (config.commission_pips + swap_pips) * _pip_value_usd(
        trade.entry_price, trade.position_size
    )
elif swap_pips:
    trade.pnl_usd -= swap_pips * config.pip_size * trade.position_size
```
The `if ac == "forex":` branch is the ONLY place commission is ever
subtracted from `pnl_usd`. The `elif swap_pips:` branch handles swap
only for non-forex, and — a second, related, currently-DORMANT bug
found in the same lines — is missing the `* dpp` (dollar-per-point)
scale factor that `_calc_pnl_usd` uses everywhere else for non-forex,
so even that swap deduction would have been wrong once
`swap_pips_per_night` is ever nonzero for a metal/index/crypto symbol
(currently always 0.0 in practice — CLAUDE.md: "Swap model ships OFF
(`data/swap_rates.json` all zeros)" — so this half of the bug has never
fired live, but was live-armed to fire incorrectly the moment swap
rates are filled in for gold/crypto).

**Execution path:** `run_backtest()` main loop → any exit path
(`check_exit()` SL/TP/gap, the BUG-002 same-bar check, or the
end-of-data forced close) → `_close_trade()` → `ac != "forex"` branch →
commission silently never applied.

**Reproduction:** Ran the real `run_backtest()` on synthetic XAUUSD
OHLCV, inspected every closed trade's `pnl_usd` against a hand-computed
gross P&L and hand-computed expected commission cost using the exact
same formula `_calc_pnl_usd` uses for non-forex. 5/5 trades confirmed
zero commission applied pre-fix; 5/5 confirmed correct commission
applied post-fix (delta between gross and net exactly equals
`commission_pips * pip_size * position_size * dollar_per_point` to
within floating-point tolerance).

**Root cause:** The `_close_trade` closure's cost-deduction branch was
written forex-first (`_pip_value_usd`'s USD-per-pip formula only makes
sense for forex's 100,000-unit lot convention) and the non-forex
`elif` branch was added later for swap only, without also covering
commission — an incomplete generalization when non-forex asset-class
support (metals/indices/crypto) was added to this cost-modeling layer.

**Impact:** Every historical backtest, walk-forward run, robustness
sweep, and Mission Center trial for a non-forex symbol
(XAUUSD/XAGUSD/USOIL/US30/NAS100/SPX500/BTCUSD/ETHUSD) computed via
this engine has had its trade-level P&L systematically overstated by
the real commission amount — for every single trade, not just some.
This is a real bias in the positive direction on exactly the carrier
assets CLAUDE.md identifies as the system's measured edge
("disciplined trend-capture on carrier assets (XAUUSD, BTCUSD,
ETHUSD)"). The per-trade magnitude observed in this reproduction was
modest relative to typical trade PnL (~$0.7-0.9 commission vs.
~$100-200 typical trade PnL in the sample, i.e. roughly a 0.5-1% cost
drag per trade) — this is NOT a claim that the measured carrier-asset
edge is fabricated or reverses under the fix, only that every PF/
win-rate/drawdown number computed from this engine for these symbols
was measured with real trading cost silently omitted, in the direction
that makes the system look better than it is. Confirmed NOT to touch
the live decision pipeline (same reasoning as BUG-002/003 —
`backtesting/backtest_engine.py` is backtest-only, never imported by
`main.py`/`scheduler.py`), so live trade execution and its own
commission handling (wherever that lives in the execution layer) are
unaffected by this bug or its fix.

**Fix (CONFIRMED, applied same phase):** Replaced the `elif swap_pips:`
branch with a unified non-forex cost branch that subtracts BOTH
commission and swap, scaled consistently with `_calc_pnl_usd`'s own
non-forex formula (`cost_pips * pip_size * position_size * dpp`) —
fixing the missing commission deduction and the dormant `dpp`-scaling
gap in the swap path in the same change, since both were the same root
cause (an incomplete non-forex generalization of the cost-deduction
branch).

**Regression test:**
`tests/test_backtest_exits.py::test_run_backtest_deducts_commission_from_pnl_usd_for_non_forex_assets`
— real `run_backtest()` run on synthetic XAUUSD data, asserts every
closed trade's `gross_usd - pnl_usd` equals the exact expected
commission cost. Full suite re-run after the fix: 13/13 in
`tests/test_backtest_exits.py`.

**Status:** FIXED, tested, regression-pinned. Same disclosed limitation
as BUG-002/BUG-003: historical `reports/`/`research/results/registry.json`
evidence for non-forex symbols computed before this fix is NOT re-run
as part of this forensic pass — this fixes the measurement tool, it
does not retroactively revise any already-recorded PF/win-rate number.
**Flagged as a priority follow-up, not performed here**: given this
bug's direct bearing on the carrier-asset PF claims CLAUDE.md's
"measured edge" statement rests on, re-running the registered
XAUUSD/BTCUSD/ETHUSD evidence hypotheses against the fixed engine (and
comparing pre/post-fix PF) is a natural, high-value next step — out of
scope for this pass (which fixes the tool, not the historical record),
same boundary already established for BUG-002/BUG-003.

## BUG-005

**Severity:** P1

**Category:** Engine Forensics — Sentiment engine lookahead (external
data source, not OHLC). Found during the ICT→Quant→Wyckoff→Divergence→
Sentiment→Macro REAL/PARTIAL/STUB audit, item #2 of the forensic
roadmap. Disabled by default (`config/engines.yaml` `enabled.sentiment:
false`, not part of prod4) — does not affect the live pipeline or the
carrier-asset (XAUUSD/BTCUSD/ETHUSD) PF claims, but is reachable via
Mission Center's already-shipped ad-hoc engine-toggle for research runs.

**Claim:** `engines/sentiment_engine.py`'s COT (`_load_cot_data`) and
MarketAux (`_marketaux_sentiment_signal`) data sources have no per-date
history at all — both always answer with data as of TODAY (wall-clock
"now"), regardless of which historical bar a backtest is simulating.
Neither function receives or consults the bar's own timestamp.

**Observed:** A hand-constructed `SentimentEngine` (with `_symbol` set,
matching how the LIVE pipeline — but not the backtest pipeline —
constructs it) called with `mtf_data` whose last bar is dated
2021-01-10 returned `BULLISH, score=48` sourced from a COT snapshot
whose own `timestamp` field was captured 2026-08-03 — over 5 years of
"future" information relative to the simulated bar, used as if it were
contemporaneous.

**Important nuance, verified before fixing (not assumed)**: this exact
scenario is NOT currently reachable through the real, wired
`run_backtest()` engine-construction loop
(`backtesting/backtest_engine.py:512-542`), which never sets
`engine._symbol` on any engine it constructs (confirmed: `grep -rn
"_symbol" engines/*.py` shows only `sentiment_engine.py` reads it;
`grep -n "_symbol" backtesting/backtest_engine.py` returns nothing).
Sentiment's `symbol` variable therefore always resolves to `"UNKNOWN"`
in any real backtest today, so `_load_cot_data("UNKNOWN")` reads a
nonexistent file (returns `None`) and `_marketaux_sentiment_signal
("UNKNOWN")` short-circuits immediately (`"UNKNOWN"` is not in
`MARKETAUX_SYMBOL_MAP`) — both silently no-op, and Sentiment falls back
to the causally-safe retail price-position proxy only. **This is a
latent landmine, not a currently-firing bug**: the lookahead
vulnerability is real and reproducible in the underlying functions, but
is currently blocked by an unrelated gap (missing `_symbol`
propagation) rather than by design. If that separate gap is ever fixed
— a plausible, innocent-looking future change (e.g. to make Sentiment
functional in backtests at all) — the lookahead trap would fire
immediately and silently, corrupting any research run that enables
Sentiment. Fixing the vulnerability at its source (regardless of the
`_symbol` gap's own status) is the correct, forward-safe fix.

**Expected:** Sentiment's external, current-snapshot-only data sources
(COT, MarketAux) should only ever be consulted when the decision bar's
own timestamp is genuinely close to "now" (live trading) — never for a
historical/backtest bar, where "now"'s positioning/news would not have
existed at that point in the simulated timeline.

**Evidence:** Reproduced directly: (1) a manually-`_symbol`-set engine
with a real COT cache file returned BULLISH/48 for a 2021-dated bar
before the fix; (2) confirmed via direct inspection that
`run_backtest()`'s real engine-construction loop never sets `_symbol`,
so this exact path is not reachable through the wired system today;
(3) confirmed the fix preserves live behavior exactly — a bar dated at
real `pd.Timestamp.now()` still returns BULLISH/48 from the same COT
data post-fix, while the 2021-dated bar now correctly returns NEUTRAL
with COT/MarketAux both skipped.

**File / Line (pre-fix):** `engines/sentiment_engine.py`,
`SentimentEngine.analyze()` (originally lines 198-312) —
`cot = _load_cot_data(symbol)` and
`marketaux = _marketaux_sentiment_signal(symbol)` were called
unconditionally, with no check against the bar's own timestamp anywhere
in the function.

**Execution path:** `SentimentEngine.analyze(mtf_data)` →
`_load_cot_data(symbol)` (reads `data/cot/{SYMBOL}.json`, a single
most-recently-downloaded weekly snapshot, no date parameter) and/or
`_marketaux_sentiment_signal(symbol)` → `fundamentals.marketaux_client.
get_news_sentiment(symbol, hours_back=48)` (a live HTTP call with its
cutoff measured from real `time.time()`) — reachable whenever Sentiment
is enabled for a run, live or backtest, with no bar-time awareness.

**Reproduction:** `SentimentEngine()` constructed with `_symbol` set
manually (bypassing the currently-blocking, unrelated `_symbol`-
propagation gap in `run_backtest()`), `IATIS_COT_DIR` pointed at a temp
dir containing a real COT snapshot payload, called with `mtf_data` whose
last bar was dated 2021-01-10 — returned BULLISH/48 pre-fix, NEUTRAL
(COT/MarketAux both skipped, `raw["is_live"] is False`) post-fix. A
second call with `mtf_data` ending at real `pd.Timestamp.now()`
confirmed identical BULLISH/48 output before and after the fix — proving
live behavior is completely unchanged.

**Root cause:** COT and MarketAux are fundamentally "current snapshot
only" data sources (a weekly cache file with no history; a live news API
with no historical archive) — the engine was written assuming it would
only ever be called in live trading (where the current bar genuinely IS
"now"), with no defensive check for the case where it's called on a
historical bar instead (backtesting, Mission Center research).

**Impact:** Zero impact on the live pipeline or CLAUDE.md's
carrier-asset PF claims (Sentiment stays `enabled: false`, not part of
prod4). Real impact on the integrity of ANY future evaluation of H012
(Sentiment/COT) or H021 (MarketAux) via backtesting or Mission Center —
without this fix, enabling Sentiment for research would silently
contaminate every historical trial with today's positioning/news,
making any measured PF for a "Sentiment-enabled" run meaningless. Also
flags a second, separate, currently-real gap worth a future look:
`run_backtest()` never propagates `_symbol` to constructed engines at
all, which today accidentally protects against this exact lookahead (by
making COT/MarketAux silently inert) but ALSO means Sentiment's
COT/MarketAux contribution can never be genuinely evaluated via
backtesting until that gap is deliberately addressed — not fixed as
part of this bug (would need a design decision about historical
COT/MarketAux archives, out of scope here).

**Fix (CONFIRMED, applied same phase):** New `_bar_time_is_live(bar_time,
tolerance_hours=72.0)` helper — `True` only when the bar's own timestamp
is within `tolerance_hours` of real wall-clock now. `analyze()` now
computes `bar_time = df.index[-1]` and gates both `_load_cot_data(...)`
and `_marketaux_sentiment_signal(...)` behind `is_live`, skipping both
(falling back to the retail proxy only) whenever `is_live` is False.
`raw["is_live"]` added for transparency. Threshold configurable via
`self.thresholds.get("live_data_tolerance_hours", 72.0)`, matching every
other engine's `.get(key, DEFAULT)` convention. In live trading the most
recent completed candle is always within a few hours of now, so this is
a no-op there — confirmed by reproduction (3).

**Regression test:**
`tests/test_sentiment_engine.py::test_cot_and_marketaux_are_skipped_for_a_historical_bar`
(the authoritative proof — real COT/MarketAux data available, historical
bar, both must be `assert_not_called()`),
`tests/test_sentiment_engine.py::test_cot_and_marketaux_are_used_for_a_live_bar`
(regression pin — live bar behaves exactly as before),
`tests/test_sentiment_engine.py::test_bar_time_is_live_helper_boundary`
(direct unit test of the gate's boundary behavior). Two pre-existing
tests needed updating (not silently left broken): `tests/
test_sentiment_engine.py`'s `_flat_df()` helper and `tests/
test_cot_download.py::test_sentiment_engine_consumes_real_cot` both used
a fixed historical date that this fix correctly began gating off — both
now anchor to real `pd.Timestamp.now()` so they keep exercising the
COT/MarketAux logic they were actually written to test, per the
established "test asserted a reality this fix legitimately changed"
pattern already seen for BUG-002/003/004's own regression suites this
session. Full suite re-run after the fix:
`tests/test_sentiment_engine.py` (11/11) and `tests/test_cot_download.py`
(11/11).

**Status:** FIXED, tested, regression-pinned. The separate `run_backtest()`
`_symbol`-propagation gap noted above is explicitly NOT fixed as part of
this bug — flagged as a distinct, future design decision (would need a
plan for historical COT/MarketAux archives before Sentiment could be
genuinely backtestable), not silently bundled in here.

---

## GOVERNANCE-001 (hardening, not a bug)

**Severity:** P1 (governance hardening — nothing is currently broken;
this closes a gap that would only matter if a future config change
opened it).

**Category:** Live-capital governance. `research/edge_gate.py`.

**Claim:** `check_edge_gate()`'s own module docstring states RESEARCH
status means "approved for paper trading / data collection only (not
live)" — but until this fix, that distinction was enforced only by
convention, never in code. `check_edge_gate()` treated `PASSED` and
`RESEARCH` identically (both simply had to be `in ALLOWED_STATUSES`),
and the actual live-vs-demo decision lived entirely in
`execution/trade_executor.py`'s `allow_live_trading` flag — a
completely separate mechanism with zero cross-check against which
hypothesis status backs the engines actually voting on that decision.

**Observed:** Confirmed via `research/edge_gate.py`'s
`ENGINE_HYPOTHESIS_MAP`: every currently-enabled prod4 engine
(smc→H101, price_action→H102, nnfx→H004, wyckoff→H006) is `RESEARCH`
status, not `PASSED`. Confirmed via `grep -rn "allow_live_trading"` that
this flag is checked in exactly one place
(`execution/trade_executor.py:211`, `if env != "demo" and not
self.allow_live_trading:`) — a broker-account-environment check with no
awareness of engine/hypothesis state at all. So the only thing
currently standing between these RESEARCH-status engines and real
capital is one global boolean, not a per-engine promotion check.

**Expected:** If `allow_live_trading` is ever set `True`, an engine
whose backing hypothesis is `RESEARCH` (or `PASSED` without qualifying
evidence per `PROMOTION_CRITERIA`) should never be allowed to
contribute to a live-capital decision — the code should enforce this,
not just the docstring.

**Fix (CONFIRMED, applied same phase):** `check_edge_gate()` gains an
`allow_live_trading: bool = False` parameter. When `True`, every
enabled engine's hypothesis must be genuinely `PASSED` AND clear
`PROMOTION_CRITERIA` (via the newly-factored-out
`_promotion_criteria_unmet()`, the same check `audit_passed_hypotheses()`
already used) — otherwise `EdgeNotProvenError` is raised loudly at boot.
`main.py`'s `build_active_engines()` now threads
`config["execution"]["allow_live_trading"]` into this call.
**Completely inert today**: `allow_live_trading` is `False` in the real
`config.yaml`, so this new branch never executes under current
configuration — verified by the full existing test suite passing
unchanged. This is a forward-looking hardening, not a fix to any
currently-firing bug.

**Regression tests:**
`tests/test_promotion_criteria.py::test_allow_live_trading_false_is_unaffected_by_research_status`
(regression pin — current production config unaffected),
`test_allow_live_trading_true_blocks_research_status_engine` (the
authoritative proof — a RESEARCH-status engine is refused once the flag
flips),
`test_allow_live_trading_true_blocks_passed_without_qualifying_evidence`
(a PASSED status alone still isn't enough — must clear
`PROMOTION_CRITERIA` too),
`test_allow_live_trading_true_permits_genuinely_qualifying_passed_engine`
(the positive case — not a blanket ban on live trading, only on
unproven engines). Full suite re-run after the fix: 2155 passed, 2
skipped, zero failures.

**Status:** APPLIED. Identified independently and confirmed by direct
code reading (`ENGINE_HYPOTHESIS_MAP`, `allow_live_trading`'s one call
site) before implementing — not taken on faith from any external
report.

---

## BUG-006

**Severity:** P0

**Category:** Risk Engine Forensics — live sovereign risk gate
(`risk/risk_engine.py`). This module's own docstring: *"it has the
authority to make a trade not exist at all... any single failure blocks
the trade. No partial credit."* Confirmed used by the live pipeline
(`main.py:455`, `_risk_gate()`) — not a backtest-only concern.

**Claim:** Every hard-gate check in `evaluate_risk()` is a `>=`/`<`/`>`
comparison. In Python, a comparison against `NaN` is ALWAYS `False` —
so a NaN input to ANY of `current_drawdown_pct`, `entry_price`/
`stop_loss_price`/`take_profit_price` (feeding the RR calc),
`correlated_exposure_pct`, or `current_open_risk_pct` would silently
BYPASS that specific hard gate instead of blocking the trade. Separately,
a negative or zero `account_balance` (a real, reachable state — see
`risk/live_portfolio_state.py`'s `equity = starting_balance + Σpnl`,
which can genuinely go negative after enough real losses) produced a
NEGATIVE position size rather than being refused outright. A third,
independent gap: the RR floor only ever compares magnitudes via `abs()`,
never which SIDE of entry the stop/target actually sit on — a backwards
stop (on the same side as the target) could compute a technically-passing
RR ratio.

**Observed:** Direct reproduction, pre-fix:
- `current_drawdown_pct=NaN` → `passed=True` (the drawdown-stop hard
  halt never fired).
- `entry_price=NaN` → `passed=True`, `position_size_units=0.0` (the RR
  floor never fired; `passed=True` is still the wrong signal even
  though the size happened to compute to zero).
- `correlated_exposure_pct=NaN` → `passed=True` (the correlation cap
  never fired).
- `account_balance=-500.0` → `passed=True`, `position_size_units=-1000.0`
  (a negative position size).
- `entry_price=1.10, stop_loss_price=1.20, take_profit_price=1.30`
  (target above entry implies a long, so the stop should be below
  entry — instead it's above, on the same side as the target) →
  `passed=True` (RR computed as 2.0 from magnitudes alone, clearing the
  2.0 floor despite the backwards geometry).

**Expected:** A sovereign risk gate must fail CLOSED on invalid,
degenerate, or nonsensical input — refuse the trade, never silently
compute a "passed" result from corrupted or garbage numbers.

**File / Line (pre-fix):** `risk/risk_engine.py`, `evaluate_risk()`
(originally lines 62-144) — no input validation existed at all before
the hard-gate comparisons began.

**Execution path:** `main.py:_risk_gate()` → `compute_portfolio_state()`
(supplies `account_balance`/`current_drawdown_pct`/
`correlated_exposure_pct`, all real, D1-backed, arithmetic on real
closed-trade data — not synthetic) + `range_atr()`-derived entry/stop/
target → `RiskInputs(...)` → `evaluate_risk(risk_inputs, config)` — the
final sovereign check before a live trade is sized and (via
`execution/trade_executor.py`) potentially executed.

**Reproduction:** `tests/test_risk_engine_fuzzing.py` — direct calls to
the real `evaluate_risk()` with NaN/inf in every numeric `RiskInputs`
field individually and in combination, negative/zero account balance,
degenerate SL/TP geometry (SL==entry, TP==entry, backwards stop for
both long and short), and a boundary/adversarial value sweep (`0.0,
-1.0, 1.0, NaN, inf, -inf, 1e300, -1e300`) across every combination of
`account_balance`/`entry_price`/`stop_loss_price`/`take_profit_price`
confirming the function never raises and never returns `passed=True`
on any invalid combination. 32/32 pass post-fix.

**Root cause:** `evaluate_risk()` trusted every numeric input
unconditionally and relied entirely on Python's native comparison
operators to reject bad values — but IEEE-754 NaN semantics make every
such comparison silently `False`, the exact opposite of "fail closed."
The directional-sanity gap is a separate root cause: `RiskInputs` has
no explicit `direction` field, and the RR calculation's `abs()` usage
discards which side of entry each level sits on.

**Impact:** This is the live decision pipeline's final risk check, not
a backtest-only measurement bug. Confirmed NOT currently exploitable via
the real, wired call site for the backwards-stop case specifically
(`main.py`'s own SL/TP construction is provably always correctly
oriented relative to direction — `stop = entry - direction * atr *
mult`), but the NaN-bypass and negative-balance gaps ARE reachable
through real, plausible upstream states: a real broker/data-feed outage
producing NaN prices, or a real losing streak driving computed equity
negative. **This is the single most severe class of finding in this
forensic pass** — a corrupted-data or already-blown-account scenario is
precisely when a risk gate must be MOST conservative, and this one was
silently permissive instead.

**Fix (CONFIRMED, applied same phase):** (1) A new fail-closed
validation block at the top of `evaluate_risk()`: every numeric
`RiskInputs` field is checked with `math.isfinite()`; any NaN/inf
immediately returns `passed=False` naming the offending field(s). (2)
`account_balance <= 0` immediately returns `passed=False`. (3) A new
directional-sanity check after the RR floor: infers intended direction
from which side of `entry_price` the `take_profit_price` sits on, and
rejects a `stop_loss_price` on the wrong (same) side as a "backwards
stop." All three checks are additive-only — every existing passing case
(confirmed via the pre-existing `tests/test_risk_rr_boundary.py`,
`tests/test_phase1.py` risk tests) continues to pass unchanged.

**Regression tests:** New `tests/test_risk_engine_fuzzing.py` (32
tests) — per-field NaN/inf rejection, negative/zero balance rejection,
SL==entry / TP==entry degenerate-geometry rejection, backwards-stop
rejection for both long and short (with correct-geometry regression
pins that these are NOT falsely rejected), drawdown/exposure/
correlation boundary values, a combined-adversarial case, and a
no-crash sweep across 8×8×8×8 fuzzed value combinations. Existing
suites re-run unchanged and green: `tests/test_risk_rr_boundary.py`,
`tests/test_phase1.py`, `tests/test_behavior.py`,
`tests/test_live_portfolio_state.py` (59/59).

**Status:** FIXED, tested, regression-pinned. Full suite verified
clean: 2187 passed, 2 skipped, zero failures.

## BUG-007

**Severity:** P1

**Category:** Measurement-Instrument Forensics — `backtest/monte_carlo.py`,
the next stage of the audit chain downstream of the already-fixed
`backtest_engine.py` (`backtest_engine.py` → `mission_runner.py`/
`runner.py` → `walk_forward.py`/`robustness.py`/`monte_carlo.py`). A bug
here can silently misreport the risk profile of every Monte Carlo
simulation run by every caller (`backtest/runner.py`, Mission Center's
validation pipeline via `mission_validator.py`).

**Claim:** `run_monte_carlo()`'s `risk_of_ruin` was computed from
**final equity vs. starting capital**:
```python
if (initial_capital - equity) / initial_capital >= ruin_threshold:
    ruins += 1
```
`equity` here is the sum of a fixed multiset of trade PnLs — an
order-independent statistic, identical for every permutation the
function's own shuffle produces. The function already computes a
genuinely path-dependent quantity in the very same loop iteration —
`max_dd` (peak-to-trough intra-sequence drawdown) — and never uses it
for the ruin check. "Risk of Ruin" is supposed to answer "how likely is
a catastrophic loss AT ANY POINT along the path," which structurally
requires the path-dependent quantity, not the order-independent one.

A second, unrelated issue in the same file: `MonteCarloResult.
print_summary()` was defined at the same indentation level as
`run_monte_carlo()`'s own function body — i.e. nested INSIDE that
function, after its own `return` statement — making it permanently
unreachable dead code, never an actual method of the `MonteCarloResult`
dataclass (confirmed via `hasattr(instance, "print_summary")` returning
`False` pre-fix).

**Observed:** Direct reproduction — a trade sequence with a positive
overall sum (`total_return ≈ +9.5%` in one prior test run) reported
`risk_of_ruin = 0.0%` at `ruin_threshold=0.03` under the pre-fix formula
(final equity never dips below the 3% threshold from starting capital
for an overall-profitable sequence, so the check can *never* fire for
any such sequence, regardless of how violent the intra-sequence swings
were). A direct, independent recomputation of the SAME shuffled
simulations using `max_dd >= 0.03` as the criterion showed 372/1000 =
37.2% of paths genuinely breached a 3% intra-sequence drawdown at some
point — the entire class of catastrophic-swing paths was invisible to
the metric.

**Expected:** `risk_of_ruin` should reflect the fraction of simulated
paths whose intra-sequence drawdown (already computed as `max_dd`)
reaches the ruin threshold, independent of whether the sequence ends up
net profitable.

**File/Line:** `backtest/monte_carlo.py`, inside `run_monte_carlo()`'s
per-simulation loop (the `if (initial_capital - equity) / initial_capital
>= ruin_threshold:` line, immediately after `max_dds.append(max_dd *
100)`); `print_summary()`'s incorrect indentation (previously nested
after `run_monte_carlo()`'s own `return MonteCarloResult(...)`).

**Execution path:** `backtest/runner.py`'s `write_summary()` and
`backtest/mission_validator.py`'s per-symbol validation both call
`run_monte_carlo()` on real closed trades and surface `risk_of_ruin`
directly in `backtest_summary_*.json` / a mission validation's Monte
Carlo block — an operator-facing number, not an internal-only detail.

**Reproduction:** `tests/test_monte_carlo_forensics.py` —
(1) `hasattr(MonteCarloResult(...), "print_summary")` plus a real call,
confirming the dataclass method now exists and runs; (2) a hand-built,
overall-profitable trade sequence whose shuffled orderings must, by
construction, sometimes pass through a large intra-sequence drawdown,
confirming `risk_of_ruin > 0` post-fix (impossible pre-fix for any
overall-profitable sequence); (3) a direct, independent re-shuffle
(same seed) recomputation using `max_dd >= ruin_threshold` cross-checked
bit-for-bit against `run_monte_carlo()`'s own reported `risk_of_ruin`;
(4) a floor case confirming small, non-threatening trades still report
`0.0%` (no false positives introduced); (5) the pre-existing
insufficient-trades short-circuit (`< 5` closed trades) still returns
an all-zero result unchanged.

**Root cause:** The ruin check used the wrong in-scope variable —
`equity` (order-independent) instead of the already-computed `max_dd`
(path-dependent) — sitting two lines above it in the same loop
iteration. Not a missing computation, a wrong-variable bug.

**Impact:** Every Monte Carlo report this codebase has ever produced
understated risk of ruin for any overall-profitable trade sequence,
regardless of how severe its intra-sequence drawdowns were — the exact
scenario "Risk of Ruin" exists to warn about. Separately noted, NOT
treated as a code bug requiring a fix in this pass: pure-permutation
(without-replacement) shuffling cannot change a fixed multiset's sum/
mean/std, so `median_return`/`p5_return`/`p95_return`/`mean_return`/
`probability_profit`/`median_sharpe`/`p5_sharpe` are all measured to be
near-identical across every simulation (confirmed empirically —
`median_sharpe` and `p5_sharpe` agreed to 1e-15 relative precision in a
1000-simulation run) — these are real, non-fabricated numbers, but they
provide essentially zero genuine uncertainty quantification under this
methodology. Only `max_dd`-derived statistics are genuinely
path-dependent. This is a **methodology limitation** (pure-permutation
vs. bootstrap-with-replacement is a legitimate, separate design
decision requiring its own consideration — not an unambiguous bug to
silently "fix" by changing the resampling scheme) and is recorded here
for transparency, not remediated in this pass.

**Fix (CONFIRMED, applied same phase):** Changed the ruin check to
reuse the already-computed `max_dd`:
```python
if max_dd >= ruin_threshold:
    ruins += 1
```
No new computation — `max_dd` was already being tracked in the same
loop for the `max_dds` list. Moved `print_summary()`'s method body to
its correct location inside the `MonteCarloResult` `@dataclass` block
(immediately after its `p5_sharpe` field) and deleted the dead
duplicate that remained after `run_monte_carlo()`'s `return`.

**Regression tests:** New `tests/test_monte_carlo_forensics.py` (5
tests, all passing) — see Reproduction above.

**Status:** FIXED, tested, regression-pinned. Full suite verified
clean: 2192 passed, 2 skipped, zero failures (isolated from two
unrelated, pre-existing live-credential test assumptions that broke
only because this session's `.env` gained real `GEMINI_API_KEY`/
`ALPACA_API_KEY`/`ALPACA_API_SECRET` values for separate, unrelated
manual verification purposes — confirmed via a controlled re-run with
those three values blanked, restored immediately after).

## Measurement-Instrument Audit — walk_forward.py / robustness.py (closure)

Continuing the same chain (`backtest_engine.py` → `mission_runner.py`/
`runner.py` → `walk_forward.py`/`robustness.py`/`monte_carlo.py`, the
last of which is BUG-007 above), `backtest/walk_forward.py` and
`backtest/robustness.py` were both read line-by-line and checked
specifically for: window-splitting/embargo math, warmup-bar collision
bugs (the class of bug already found and fixed once in this exact area
— the walk-forward `TypeError` prerequisite bug the "Backtesting Lab
Pro Phase A" work fixed before this session), parameter-sweep/
engine-override composition, sensitivity-band computation, and CLI
argument wiring.

**One real, non-behavioral finding — FIXED:** `walk_forward.py`'s
`split_windows()` docstring claimed *"Window 1's warmup comes from the
head of the dataset, so its tradeable span is shorter — this is stated
in results rather than papered over."* Direct reproduction (a 1000-bar
synthetic H4 series split into 3 windows) showed this is false under
the current implementation: every window's tradeable span is
`usable // n_windows` bars except the LAST, which absorbs the integer-
division remainder — window 1 is never shorter than windows 2..N-1.
This is a stale/inaccurate comment, not a computation bug (the actual
`bars`/`trades`/`profit_factor` values reported per window are
unaffected) — fixed the docstring to state the real, reproduced
behavior instead of a claim that no longer matches the code.

**No further measurement bugs confirmed** in either module after
scrutiny of: `split_windows()`'s embargo math (verified the warmup
slice is exactly `warmup_bars` for every window, matching where
`run_backtest()`'s own decision loop starts, `range(config.warmup_bars,
len(df) - 1)` — no window can trade into its own embargo or another
window's bars); `run_walk_forward()`'s per-window `BacktestConfig`
construction (fixed parameters across all windows, no adaptive
leakage, matching the module's own documented "consistency test, not
train/optimize" scope); `robustness.py`'s `_run_point()`/
`run_param_sweep()` composition of `engine_overrides` with the swept
parameter (dict-merge semantics correctly let an explicit sweep value
override a stale `engine_overrides` entry for the same field — matches
the module's own documented "perturbs around your override, not the
production default" note); a specific, checked-and-ruled-out hypothesis
that a per-symbol frozen sweep parameter could default to exactly
`0.0` (which would make every multiplier point identical and the
sweep vacuously "STABLE") — confirmed false by direct inspection of
`BacktestConfig`'s dataclass defaults (`commission_pips: float = 0.5`,
`slippage_pips: float = 0.5`) and `REAL_SPREAD_PIPS` (no zero entries);
the `_STABLE_BAND` relative-tolerance comparison's behavior at `PF=0`
and `PF=inf` baselines (both degrade to correct, conservative
classifications, not silent false negatives).

**Status:** CLOSED. Task "audit walk_forward.py / monte_carlo.py /
robustness.py for measurement bugs" complete — BUG-007 (monte_carlo.py)
is the one confirmed, fixed bug from this pass; the docstring fix above
is the only other finding, applied directly (no regression risk, no
`BUG-00X` entry warranted for a comment-only change with zero effect on
reported values).

## BUG-008

**Severity:** P1 (dormant today — the engine is disabled and zero-
weighted in production, so no live trade is affected right now — but a
real, confirmed correctness defect that would misprice ~3 of the 24
configured symbols the instant this engine is ever re-enabled).

**Category:** Engine correctness — `engines/macro_engine.py`. Triggered
by re-checking a batch of externally-sourced audit claims against the
real code (per this session's standing rule: never accept an audit's
word for a finding — reproduce it against the actual code first). Most
of that batch's claims were not independently verified in this pass
(scope: pick the most concrete, checkable ones); this one was, and
confirmed real.

**Claim:** `MacroEngine`'s DXY-to-bias mapping (`decide()`: `if
dxy_direction == "up": bias = Bias.BEARISH; else: bias = Bias.BULLISH`)
is completely symbol-agnostic — the engine's own `analyze()` accepts
`mtf_data` only to satisfy the abstract signature and never reads it,
and (confirmed by grep) never referenced `self._symbol` at all prior to
this fix. The fixed mapping "a stronger dollar is bearish for the pair"
is correct for every symbol where USD is the QUOTE currency (EURUSD,
GBPUSD, AUDUSD, NZDUSD) or a USD-denominated non-FX asset (XAUUSD,
BTCUSD, indices) — but backwards for USDJPY, USDCHF, and USDCAD, where
USD is the BASE currency: a stronger dollar makes those three pairs
RISE, not fall.

**Observed:** Direct reproduction — identical DXY-rising input fed
through the real `MacroEngine.analyze()` twice, once with `self._symbol
= "EURUSD"` and once with `self._symbol = "USDJPY"` (matching
`main.py`'s real, existing `engine._symbol = symbol` attribute-
assignment pattern, the same mechanism BUG-005 already established as
load-bearing for per-symbol engine context): EURUSD correctly came back
`BEARISH`; USDJPY ALSO came back `BEARISH` pre-fix — objectively wrong,
since a rising dollar should make USDJPY rise (BULLISH), not fall.

**Expected:** USDJPY/USDCHF/USDCAD should receive the OPPOSITE DXY-
driven bias from every other configured symbol on identical DXY data.

**File/Line:** `engines/macro_engine.py`, `decide()`'s DXY-direction
branch (previously ~lines 229-234); `MacroEngine.analyze()` (never read
`self._symbol`, pre-fix).

**Execution path:** Currently NONE live — confirmed `config/
engines.yaml`'s `enabled.macro: false` and `config.yaml`'s
`confluence.weights.macro: 0.0`, so `main.py::build_active_engines()`
never constructs a `MacroEngine` instance in production today. Reachable
only via Mission Center's ad-hoc `engine_variants`/engine-toggle sandbox
(explicitly labeled "exploratory, not evidence" there) — and would
become live-reachable the instant a human re-enables `macro` following
a fresh pre-registered hypothesis, per this engine's own frozen-state
governance. Fixed now rather than left as a landmine for that future
re-enable, matching BUG-005's precedent exactly.

**Reproduction:** `tests/test_macro_engine.py` (7 new tests) —
`is_usd_base_symbol()` unit tests (accepts USDJPY/USDCHF/USDCAD
case-insensitively, rejects EURUSD/GBPUSD/XAUUSD/BTCUSD/US30/empty
string); `decide()`-level tests proving the mapping is unchanged
(`usd_is_base=False`, the default) for the non-base case and correctly
inverted (`usd_is_base=True`) for both DXY-rising and DXY-falling
inputs; the authoritative end-to-end reproduction — identical DXY data
through two real `MacroEngine` instances differing only in `_symbol`
(`"EURUSD"` vs `"USDJPY"`) producing opposite bias; a regression pin
that omitting `_symbol` entirely (every existing zero-arg construction
site, and the backtest engine-construction loop, which does not set it)
still defaults to `usd_is_base=False` — today's original mapping,
unaffected by this fix. 43/43 pass.

**Root cause:** The engine was written assuming USD is always the
"other side" of the pair (true for the majority of configured symbols)
without ever checking which currency is actually the base — a missing
case, not a wrong formula.

**Impact:** Zero live impact today (engine disabled + zero-weighted).
Would have produced a systematically inverted signal for exactly the
subset of symbols where the assumption doesn't hold (USDJPY/USDCHF/
USDCAD — 3 of the 24 symbols in `config/symbols.yaml`) the moment the
engine is ever promoted to live use.

**Fix (CONFIRMED, applied same phase):** New `is_usd_base_symbol(symbol)`
helper (`symbol.upper().startswith("USD")`, matching this codebase's own
FX naming convention). `decide()` gains an optional `usd_is_base: bool =
False` parameter (default preserves the original mapping for every
existing caller); when `True`, the DXY-up/down → bias mapping is
inverted, with the reasons text explicitly stating why. `MacroEngine.
analyze()` now computes `usd_is_base = is_usd_base_symbol(getattr(self,
"_symbol", ""))` and threads it into `decide()`; `raw["usd_is_base"]` is
also surfaced for visibility/debugging.

**Regression tests:** `tests/test_macro_engine.py` (+7 tests, see
Reproduction above). Full suite verified clean: 2193 passed, 2 skipped
(the same 6 pre-existing, unrelated credential-dependent failures from
BUG-007's note — confirmed unchanged, zero new failures).

**Status:** FIXED, tested, regression-pinned.

---

## BUG-009

**Severity:** P3 (dormant, currently zero live impact)

**Category:** Engine correctness — statistical formula (external audit
claim #9/44, "Quant: annualized volatility assumes 365 days — forex is
261 days only")

**Claim (external audit, verified independently rather than taken on its
word):** `engines/quant_engine.py::_bars_per_year()` always computes
`bars_per_year = (365 * 24 * 60) / minutes_per_bar`, regardless of asset
class. Real FX/metals trade only ~261 days/year (52 weeks × 5 trading
days), not 365. Since `realized_vol_annualized = sigma_bar *
sqrt(bars_per_year)`, using 365 instead of 261 for FX symbols overstates
`sqrt(bars_per_year)` — and therefore `realized_vol_annualized` — by
`sqrt(365/261) ≈ 1.183`, i.e. **~18.3% too HIGH**.

**Important correction to the audit's own claim:** the external audit
stated this makes volatility "understated by 15%, leading to oversized
positions." Independently re-derived the math rather than accepting the
audit's word: the direction is backwards — using 365 in place of 261
makes annualized volatility TOO HIGH, not too low, which (if the field
were ever scored) would bias toward UNDER-sized positions, not
oversized ones. Recorded here so the correct direction is on record, not
the audit's stated one.

**Observed:** `_bars_per_year("D1", {})` returned `365.0` for every
timeframe/symbol combination prior to this fix, including real FX pairs
like EURUSD/XAUUSD.

**Expected:** A symbol identified as FX/metals (not 24/7) should
annualize using ~261 trading days; a 24/7 asset (crypto) should keep 365.

**File/Line:** `engines/quant_engine.py::_bars_per_year()` (lines
~64-68 pre-fix), `extract_features()` (line ~249, the only call site),
`QuantEngine.analyze()` (never passed a symbol into `extract_features()`
pre-fix).

**Execution path:** Currently NONE live — confirmed `config/
engines.yaml`'s `engines.enabled.quant: false`, so `main.py::
build_active_engines()` never constructs a live `QuantEngine`.
Additionally confirmed by grep that `decide()` never reads
`features["realized_vol_annualized"]` or `features["bars_per_year_used"]`
— both are informational-only fields, serialized into `raw`/`features`
but never consulted for `bias`/`score`. So even before this fix, the
claimed causal impact ("oversized positions → bigger losses") was FALSE
as stated: zero fields feeding this formula ever reached a trading
decision. Reachable only via Mission Center's ad-hoc engine-toggle
sandbox (exploratory, not evidence) and would become live-reachable only
after a future re-enable following a fresh pre-registered hypothesis.

**Reproduction:** `tests/test_indicators_quant_stats.py` (+4 tests) —
no-symbol-context calls keep the exact prior 365-day values (backward-
compatibility pin); a real FX symbol (`"EURUSD"`/`"XAUUSD"`) gets the
corrected `261.0`-day count, config-overridable via
`trading_days_per_year_fx`; a crypto symbol (`"BTCUSD"`/`"ETHUSDT"`)
keeps `365.0`; the `bars_per_year_default` unknown-timeframe fallback is
unaffected by symbol. `tests/test_quant_engine_v2.py` (+3 tests) —
`extract_features()`'s new optional 4th `symbol` param defaults to `""`
and is fully backward-compatible with every existing 3-positional-arg
call; a real FX symbol changes `bars_per_year_used`/
`realized_vol_annualized` but leaves `decide()`'s `bias`/`score`
byte-identical (the direct proof the field is decision-inert);
`QuantEngine.analyze()` correctly threads `getattr(self, "_symbol", "")`
through (same pattern as BUG-008's `MacroEngine` fix) with the same
zero-decision-impact property confirmed end-to-end via `safe_analyze()`.
124/124 pass across both files.

**Root cause:** The annualization formula was written assuming a 24/7
trading calendar (correct for crypto, the engine's original test/dev
context) without ever branching on asset class.

**Impact:** Zero live impact today (engine disabled; affected fields
never scored). Would produce a ~18% overstated `realized_vol_annualized`
for FX/metals symbols the moment the engine is re-enabled AND that field
is wired into a scored decision in the future — neither is true today.

**Fix (CONFIRMED, applied same phase):** New `_is_24_7_asset(symbol)`
helper (mirrors `core/market_quality.py`'s inline crypto-ticker pattern,
same precedent as BUG-008's `is_usd_base_symbol()`). `_bars_per_year()`
gains an optional `symbol: str = ""` parameter (default preserves the
exact prior 365-day behavior for every existing caller); when a non-24/7
symbol is positively identified, uses `t.get("trading_days_per_year_fx",
261.0)` instead of 365. `extract_features()` gains the matching optional
`symbol: str = ""` 4th parameter, threaded into `_bars_per_year()`.
`QuantEngine.analyze()` now passes `getattr(self, "_symbol", "")`.
`config/engines.yaml`'s `thresholds.quant` block gains
`trading_days_per_year_fx: 261.0`.

**Regression tests:** `tests/test_indicators_quant_stats.py` (+4),
`tests/test_quant_engine_v2.py` (+3) — see Reproduction above.

**Status:** FIXED, tested, regression-pinned.

---

## BUG-010

**Severity:** P1 (live, currently active — affects the carrier assets
CLAUDE.md identifies as the system's only measured edge)

**Category:** Data integrity — live-feed freshness (follow-up to
external audit claim "ICT Judas Swing on an unclosed candle," which
this investigation generalized: the root cause is not ICT-specific)

**Claim investigated:** does any engine risk evaluating a still-forming
(not yet closed) candle as if it were the finalized "current bar,"
given every engine reads `mtf_data[tf].iloc[-1]` as the decision bar and
`main.py`'s own decision-report comment explicitly assumes `df_base.
index[-1]` is "the last closed candle of the decision timeframe"?

**Observed:** `core/ccxt_provider.py::fetch_ccxt()` calls `exchange.
fetch_ohlcv(...)` (ccxt/Binance) with no trimming of the response — a
well-documented behavior of Binance's kline API is to include the
current, still-in-progress candle as the last element when the
requested window extends to "now" (which it always does here — `since_
dt = now - timedelta(days=days)`, and the pagination loop fetches until
Binance stops returning full batches, i.e. right up to the latest
available candle). `core/data_providers.py::fetch_multi_timeframe_with_
failover()` performs zero validation on what any provider returns
before handing it to `main.py`. `scheduler.py::run_loop()` sleeps a
fixed `interval_minutes` after each run rather than aligning to bar-
close boundaries, so a scheduled run can fire at any offset into a bar
window — exactly the condition under which a still-forming last bar
would be returned.

**Expected:** `df.index[-1]` should always be a genuinely closed bar for
every provider, matching what `main.py`'s own comment already assumes.

**Execution path:** `core/data_providers.DEFAULT_CHAINS["crypto"] =
["ccxt", "alpaca", "twelve_data", "finnhub"]` — ccxt is the FIRST-choice
provider for BTCUSD/ETHUSD, and `_NATIVE_TF["ccxt"]` includes H4/D1
NATIVELY (confirmed: no resample step sits between the raw ccxt fetch
and `views[tf]` for these symbols) — meaning a still-forming candle
would flow directly into every engine's `mtf_data` for exactly the two
carrier assets (alongside XAUUSD) CLAUDE.md identifies as the system's
only measured edge. `_fetch_alpaca()` (the crypto fallback) was checked
too and has the identical gap — no trimming of its own "latest N bars"
response either.

**Reproduction:** `tests/test_provider_chains.py` (+9 tests) —
`_drop_still_forming_bar()` unit tests (trims a bar whose own window
hasn't closed yet; no-op for an already-closed last bar; no-op on an
unrecognized timeframe; no-op/empty-safe on an empty frame; correctly
localizes a naive index before comparing to `now`); the authoritative
end-to-end proof at `fetch_with_failover()` (a mocked ccxt provider
returning a still-forming last bar has it stripped before the caller
ever sees it, regardless of which of the 9 providers served it — this
is the single choke point shared by `fetch_multi_timeframe_with_
failover`, `execution/routes/analyze.py`, and `scripts/cross_provider_
diff.py`); a provider whose entire response is just the one
still-forming bar is correctly treated as a failure and falls through
to the next provider in the chain, never silently handing a caller a
single moving-target bar; a regression pin proving an already-
well-behaved provider (returns only closed bars) is completely
unaffected — same bar count in and out. 27/27 pass in the file.

**Root cause:** No provider fetch path was ever verified to guarantee
the invariant `main.py`'s own comment already assumed — a genuine gap
between documented intent and enforced behavior, not a wrong formula in
any single provider.

**Impact:** For BTCUSD/ETHUSD specifically (H4/D1, native via ccxt, no
resample to incidentally catch it), every engine's read of the
"current" bar (ICT's Judas swing check, but also any pattern/swing/
level detector reading `.iloc[-1]` in any engine) could evaluate a
still-changing high/low/close until the real close, producing an
unstable, repainting signal on exactly the assets this system's live
trading depends on. FX/metals via cTrader were checked as a secondary
question and found lower-risk (cTrader's historical-trendbar API is
conventionally documented to serve closed bars only, unlike ccxt's
"latest N" kline semantics) but were given the same protection anyway
since it costs nothing to apply generically and closes the gap even if
that assumption is ever wrong for a specific broker/instrument.

**Fix (CONFIRMED, applied same phase):** New `core.data_providers.
_drop_still_forming_bar(df, interval)` — drops the last row only when
its own bar-duration window (`bar_open_time + duration`) hasn't fully
elapsed relative to real UTC "now"; a no-op for the common case of an
already-closed last bar. Wired into `fetch_with_failover()`'s single
success path (not into each of the 9 individual provider functions),
so every current and future provider gets the protection automatically
with zero per-provider code duplication. A provider whose entire
response becomes empty after trimming is treated as a normal failure
(`DataFetchError`), falling through the chain exactly like an empty
response always has.

**Regression tests:** `tests/test_provider_chains.py` (+9 tests, see
Reproduction above).

**Status:** FIXED, tested, regression-pinned.

---

## BUG-011

**Severity:** P1 (backtest-measurement — directly undermines the
platform's central, measured "H4 with D1 confirmation" claim)

**Category:** Lookahead-adjacent data-freshness bug in the MTF-view
construction path (backtest/injected/replay only — confirmed the live
decision pipeline is unaffected).

**Claim (found during a forensic audit pass over `backtesting/
backtest_engine.py`'s position-sizing/warmup/MTF-alignment logic, areas
not yet scrutinized line-by-line this session — the entry/SL/TP/close
paths were already independently verified):** every backtest bar's D1
(and, for an H1-base config, H4) confirmation view is built by
`core.timeframe_sync.build_multi_timeframe_view()`, which calls
`df.resample(rule)` on the TRUNCATED base-timeframe window
(`window = df.iloc[:i+1]`, `backtesting/backtest_engine.py:679`
pre-fix). Pandas' `resample()` buckets by calendar day regardless of
whether the base data actually covers the full day — so unless the
truncated window happens to end exactly at a day boundary (1 of 6
possible H4 bars/day), the last row of the resulting D1 frame is a
PARTIAL, still-forming "today" candle, not the last fully-closed day.
`confluence/mtf_confirmation.py::check_mtf_confirmation()` then reads
`ewm(...).iloc[-1]`/ADX off that same partial row and folds it directly
into the ±8-point MTF score adjustment CLAUDE.md's whole "H4 with D1
confirmation" measured-edge claim rests on.

**Observed (reproduced directly, not assumed):** synthetic H4 OHLCV
truncated mid-day-3 (only 3 of 6 H4 bars present for that day) → the
pre-fix D1 view's last row was labeled `2026-01-03` (the current,
partial day) with a `close` computed from only those 3 bars —
`101.03`, matching NEITHER the true last-closed day (`2026-01-02`,
`100.898`) NOR the true fully-formed `2026-01-03` (`101.193`, using all
6 bars once the day actually completes).

**Expected:** the D1 (or H4) view's last row must be the last FULLY
CLOSED period as of the base window's own last bar — the same
still-forming-bar contract BUG-010 already established for live fetches.

**File/Line:** `core/timeframe_sync.py::resample()` (line ~35 pre-fix,
no still-forming protection at all) and `build_multi_timeframe_view()`
(line ~59, called by every one of `backtesting/backtest_engine.py`
line 679/`main.py`'s injected-replay + native-fetch-fallback paths,
`scripts/m15_smart_backtest.py`, `scripts/engine_ablation.py`,
`scripts/diagnose_h009_gap.py`).

**Execution path:** every backtest bar's pipeline run (`run_backtest`'s
main loop, gated by `config.use_mtf_confirmation`, default ON) calls
`build_multi_timeframe_view(window, timeframes)` before engine
evaluation — so this affected essentially every MTF-confirmation score
in every backtest/walk-forward/robustness/Mission-Center trial ever run
with an H4 or H1 base timeframe (the vast majority; `config.yaml`'s
production `data.timeframes: [H4, D1, H1]`). **Live trading is
unaffected**: confirmed `main.py::_load_market_data()`'s real (non-
fallback) path fetches D1 natively via `load_multi_timeframe_with_
failover()` — never resampled — and BUG-010 already trims any
still-forming bar from that native fetch. Only the resample-from-a-
finer-series construction path (backtest/injected/replay, and `main.py`'s
rarely-hit native-failover-exception fallback) was exposed.

**Reproduction:** `tests/test_timeframe_sync.py` (new file, 8 tests) —
direct before/after comparison against a hand-built full-vs-truncated
H4 series (proves the exact partial-candle contamination and its fix);
an exact-day-boundary case (proves the fix doesn't over-trim a genuinely
complete day); backward-compat (`base_minutes` omitted → old, un-trimmed
behavior preserved byte-for-byte, since the parameter is opt-in);
empty-DataFrame no-op; unsupported-timeframe still raises;
`build_multi_timeframe_view()` threads `base_minutes` automatically for
both a D1-from-H4 and a combined H4-and-D1-from-H1 scenario.

**Root cause:** `core.timeframe_sync.resample()` was written assuming
its input DataFrame always fully covers every bucket it produces — true
for a live, natively-fetched, already-closed-bar series, but false for
any TRUNCATED window (exactly the shape a backtest's bar-by-bar loop
produces via `df.iloc[:i+1]`). No still-forming-bucket check existed
anywhere in this module.

**Impact:** Every backtest/walk-forward/robustness/Mission-Center run
using MTF confirmation on an H4 or H1 base timeframe measured its
D1-agreement score against a data-source that, for 5 of 6 possible H4
decision points per day, was reading a partial/wrong daily candle
instead of the last closed one — directly undermining the reliability of
every historically-reported PF/win-rate number that used the default
`use_mtf_confirmation=True` (the documented default, matching the "the
backtest must simulate the SAME system that trades" design principle).
Does not change entries/exits/thresholds (rule 6 is not implicated —
this is a data-construction correctness fix, not a strategy change);
does re-measure every future backtest against genuinely closed D1/H4
data going forward.

**Fix (CONFIRMED, applied same phase):** `core/timeframe_sync.py::
resample()` gains an optional `base_minutes: int | None = None`
parameter (default `None` preserves the exact prior, un-trimmed
behavior for any caller that omits it — though after this fix, no
in-repo caller does). When given, the resampled series' LAST row is
dropped if the base series' own last bar doesn't reach that row's true
period end — the same still-forming-bar protection BUG-010 gave live
fetches, generalized to the resample-from-a-finer-series construction
path. `build_multi_timeframe_view()` now always passes its own computed
`base_minutes` through automatically, so every existing call site
(backtest, injected/replay, `main.py`'s native-fetch-fallback,
`scripts/m15_smart_backtest.py`, `scripts/engine_ablation.py`) gets the
fix with zero call-site changes.

**Collateral golden-value update:** `tests/test_engine_config_extraction_
no_behavior_change.py`'s pinned Wyckoff-scenario-A golden score legitimately
changed (`40.0` → `25.0`, bias unchanged `BULLISH`) — its fixed-`end`-
timestamp fixture's H4/D1 views were previously reading a partial last
candle; this fix corrects that input, and the golden value was
recaptured against the now-correct data (same discipline already
established for that file's own prior wall-clock-flake fix). All 5
other engine/scenario combinations in that file were independently
re-verified unaffected.

**Regression tests:** `tests/test_timeframe_sync.py` (+8, new file) —
see Reproduction above.

**Status:** FIXED, tested, regression-pinned.

---

## BUG-012

**Severity:** P2 (backtest-measurement — a headline KPI, not a trading
decision)

**Category:** Statistical-formula annualization bug — same family as
BUG-009 (`engines/quant_engine.py`'s hardcoded 365-day assumption),
recurring at the backtest-report layer.

**Claim (found during the same forensic audit pass as BUG-011):**
`BacktestResult.compute()` (`backtesting/backtest_engine.py`) computed
Sharpe ratio as `returns.mean() / returns.std() * sqrt(252)` — a fixed
daily-return annualization factor — regardless of the base (decision)
timeframe's actual bar cadence. `equity_curve` gets exactly one point
per BASE-timeframe bar (the unconditional `result.equity_curve.append
(balance)` inside the main loop, happens even on `step_bars`-skipped
bars), not one point per calendar day. The production base timeframe is
H4 (`config.yaml` `data.timeframes: [H4, D1, H1]`) — 6 bars/day, not 1
— so `returns` is an H4-frequency series, and annualizing it with
`sqrt(252)` (the correct factor only for a genuinely daily series)
undercounts the true periods/year by a factor of 6.

**Observed (reproduced directly):** for the SAME real equity curve from
a real H4-base-timeframe `run_backtest()` call, the pre-fix formula
(`sqrt(252)`) and the timeframe-aware correct formula (`sqrt(252*6)`)
differ by exactly `sqrt(6) ≈ 2.449` — confirmed via `pytest.approx`
equality in the regression test below, not just derived on paper.

**Expected:** Sharpe's annualization factor must scale with the base
timeframe's real bars/year (`252 * bars_per_day` for non-24/7 assets,
`365 * bars_per_day` for crypto — mirroring BUG-009's own 252-vs-365
trading-calendar distinction), not a flat daily constant.

**File/Line:** `backtesting/backtest_engine.py::BacktestResult.compute()`
(line ~332 pre-fix, `np.sqrt(252)` hardcoded); `equity_curve.append`
(line ~654, one point per base-timeframe bar, confirmed by direct read).

**Execution path:** every `BacktestResult.compute()` call (every
`run_backtest()` invocation — backtest/walk_forward/robustness/Mission
Center trials) with a non-daily base timeframe. Purely a reported-KPI
issue: `sharpe_ratio` is a summary/diagnostic field, never consulted by
any gating/decision logic (confirmed by grep — `run_backtest()`'s own
EXECUTE/NO_TRADE decision never reads it), so this never changed which
trades were taken, only how the run's own Sharpe was reported downstream
(Backtesting Charts KPI cards, Mission Center leaderboards/consensus
claims, `research/edge_gate.py`'s promotion-criteria consumers if Sharpe
is ever added there).

**Reproduction:** `tests/test_backtest_exits.py` (+3 tests) —
`_periods_per_year()` unit tests across D1/H4/H1 × forex/crypto;
`test_backtest_result_sharpe_uses_base_timeframe_bar_cadence` runs a
real synthetic H4 `run_backtest()`, computes Sharpe two ways from the
SAME real equity curve (the old flat-`sqrt(252)` formula and the fixed
timeframe-aware one), and asserts `result.sharpe_ratio` matches the
corrected formula exactly and differs from the naive one by precisely
`sqrt(6)`; `test_backtest_result_sharpe_unchanged_for_d1_base_timeframe`
is the explicit backward-compatibility pin — a D1-base-timeframe run's
Sharpe is BYTE-IDENTICAL before/after this fix (`periods_per_year(D1,...)
== 252`, the same constant as before).

**Root cause:** the Sharpe formula was written assuming a daily-return
series (correct only when the base timeframe IS D1) without ever
branching on the actual decision timeframe's bar cadence — the same
class of oversight BUG-009 already found and fixed once in
`engines/quant_engine.py`, recurring here undetected.

**Impact:** every backtest report's Sharpe ratio was systematically
UNDERSTATED by `sqrt(bars_per_day)` for any non-daily base timeframe —
`~2.45x` understated for the production H4 default, `~4.9x` for H1. No
trading-decision impact (Sharpe is never gated on); real impact on any
downstream comparison, ranking, or promotion criterion that uses Sharpe
(Mission Center leaderboards, Experiment Comparison, any future
`edge_gate.py` Sharpe threshold).

**Fix (CONFIRMED, applied same phase):** new `_periods_per_year(timeframe,
symbol)` helper (mirrors BUG-009's `_is_24_7_asset` precedent for the
252-vs-365 trading-calendar distinction) computing `days_per_year *
(1440 / bar_minutes)`. `BacktestResult` gains a `timeframe: str = "D1"`
field (default reproduces the OLD `sqrt(252)` behavior exactly for any
caller that never sets it — `bars_per_day=1` when `timeframe="D1"`).
`run_backtest()`'s `BacktestResult(...)` construction now passes
`timeframe=timeframes[0] if timeframes else "D1"`. `compute()`'s Sharpe
line now uses `sqrt(_periods_per_year(self.timeframe, self.symbol))`
instead of the hardcoded `sqrt(252)`.

**Regression tests:** `tests/test_backtest_exits.py` (+3) — see
Reproduction above.

**Status:** FIXED, tested, regression-pinned.

---

## Findings investigated and NOT treated as bugs (same forensic pass)

**Reversal-veto default mismatch (Mission Center engine-toggle exposure,
MEDIUM confidence, no code change applied):** `BacktestConfig.
use_reversal_veto` defaults `True` (`backtesting/backtest_engine.py`),
while `main.py`'s live decision path has `check_reversal_veto()`
structurally removed (not merely disabled by config — the call site
doesn't exist there at all, per the file's own comment: "H013 reversal
veto removed from the live path... stays in confluence/reversal_veto.py
for backtesting/backtest_engine.py and scripts/engine_ablation.py, which
still A/B it in research"). Today this is a coincidental no-op for the
DEFAULT backtest configuration: the veto requires ≥2 of
{Divergence, Wyckoff, Sentiment} non-NEUTRAL, and `config/engines.yaml`
only enables Wyckoff among that trio — matching prod4 exactly, so the
default backtest never triggers it either. It becomes a real, armed
discrepancy only if an operator uses Mission Center's engine-toggle
sandbox to enable Divergence or Sentiment for an ad-hoc research run —
at which point the veto can fire in that backtest with no live-equivalent
counterpart. Every backtest a Mission Center trial produces is already
labeled "exploratory, not evidence," and CLAUDE.md rule 6 (never change
entries/exits/thresholds mid-sample without a pre-registered hypothesis)
argues against silently changing this gate's default behavior outside
that discipline. **No code change applied** — documented here as a
known, bounded discrepancy for anyone reading a Mission Center trial
that used a non-default engine set with the reversal veto still on.

**0.01-lot position-size floor "overshooting" risk_per_trade during deep
drawdown (LOW-MEDIUM confidence, verdict: NOT A BUG):** `_calc_position_
size()`'s `max(0.01, ...)` floor can produce a position whose actual
dollar risk at the stop materially exceeds the configured
`risk_per_trade` when the risk-correct size would be below 0.01 lots
(most plausible when `balance` has already shrunk significantly, e.g.
deep into a drawdown). Concrete numbers checked: `balance=$50`,
`risk_per_trade=1%%` → correct size `≈0.0004` lots, floored to `0.01` →
actual risk `≈$12.50` (a 25× overshoot of the configured 1%%). On
reflection this is **realistic broker-constraint modeling, not a
measurement bug**: real brokers genuinely enforce a 0.01-lot minimum: a
live account in the same situation would be forced to take the same
oversized risk (or skip the trade entirely) — the backtest correctly
reflects that real-world floor rather than pretending risk scales
infinitely small. Not fixed. Flagged here because it IS a real, material
effect on `max_drawdown_pct`/Monte-Carlo risk-of-ruin fidelity for any
run that lets balance shrink substantially (long single-symbol runs,
walk-forward, Monte Carlo resampling) — worth knowing about when
interpreting those numbers, not worth "fixing" away the realism.

---

## BUG-013

**Severity:** P4 (diagnostics-only — zero effect on any trading decision
or reported PF/win-rate)

**Category:** Diagnostic-funnel mislabeling (backtest gate-rejection
attribution), found during the same forensic audit pass as BUG-011/
BUG-012, Finding 5 (originally logged as LOW confidence, confirmed and
fixed once its exact mechanism was pinned down).

**Claim:** `BacktestResult.gate_rejections` exists specifically to turn
"0/4 CONSISTENT" from a dead end into a diagnosable funnel (its own
docstring). The `if/elif` chain attributing a rejected bar to a specific
bucket had no branch for "the winning bias itself is NEUTRAL" — that
case fell through to the final `else: result.gate_rejections["votes"]
+= 1`, the SAME bucket used for "a real direction won but not enough
engines/weight agreed on it." An operator reading the funnel could not
distinguish "no real signal existed" from "a real signal existed but
was too weak."

**Observed (reproduced directly):** `confluence.voting_system.tally_votes()`
was read and confirmed: whenever it returns `winning_bias == NEUTRAL`
(an exact conviction tie, or all-NEUTRAL engine outputs), `agree_count`
is ALWAYS `0` — so `vote.agree_count >= min_engines` and `vote.winning_
bias.value != "NEUTRAL"` were failing SIMULTANEOUSLY for every NEUTRAL-
winner bar, with no way for the pre-fix elif chain to tell that apart
from a real-direction-but-insufficient-agreement bar. Confirmed with a
real synthetic-data backtest run (`min_engines_agreeing` forced
impossibly high to make every real-direction bar land in "votes"): the
pre-fix code would have shown `votes: 360` (342 genuine insufficient-
agreement + 18 genuinely NEUTRAL, indistinguishable); post-fix,
`votes: 342` and the new `neutral_bias: 18` are cleanly separated.

**Expected:** a NEUTRAL winning bias (no real direction) should be
attributed to its own bucket, distinct from "a real direction existed
but didn't reach quorum."

**File/Line:** `backtesting/backtest_engine.py` — `gate_rejections`
default dict (line ~282) and the rejection-attribution `if/elif` chain
(line ~812, final `else` branch pre-fix).

**Execution path:** every rejected bar in every backtest/walk-forward/
robustness/Mission-Center trial whose winning bias was NEUTRAL. `ok`
(the actual EXECUTE/NO_TRADE boolean) is completely unaffected — this
is a pure post-hoc attribution change, zero effect on which bars execute
or any reported trade/PF/win-rate.

**Reproduction:** `tests/test_backtest_exits.py` (+2 tests) —
`test_tally_votes_neutral_winner_always_has_zero_agree_count` pins the
load-bearing invariant this fix depends on (a conviction-tie scenario
and an all-NEUTRAL-engines scenario both confirm `agree_count == 0`
whenever `winning_bias == NEUTRAL`); `test_gate_rejections_
distinguishes_neutral_bias_from_insufficient_votes` runs a real
synthetic-data backtest with `min_engines_agreeing` forced to 99 (so
every real-direction bar is guaranteed to land in "votes") and asserts
BOTH `gate_rejections["votes"] > 0` and `gate_rejections["neutral_bias"]
> 0` on the same run, plus `execute_count == 0` (confirming the fix
changes attribution only, never execution).

**Root cause:** the elif chain's final `else` branch was a catch-all
written before the NEUTRAL-vs-insufficient-agreement distinction was
considered — both conditions of `ok`'s vote-related clauses
(`agree_count >= min_engines`, `winning_bias != NEUTRAL`) can fail at
once, and the pre-fix code had no way to attribute the rejection to
whichever was the more informative cause.

**Impact:** none on trading behavior. Real impact only on the
readability of the `gate_rejections` diagnostic funnel — anyone using it
to distinguish "the market gave no real signal" from "a real signal
existed but conviction was too weak/scattered" (e.g. for Mission Center
consensus analysis, or manually diagnosing a low-trade-count run) now
gets the correct breakdown.

**Fix (CONFIRMED, applied same phase):** `gate_rejections` gains a new
`"neutral_bias"` key (additive — no existing consumer asserts an exact
key set, confirmed by grep across `backtest/runner.py`, `backtest/
walk_forward.py`, `scripts/engine_ablation.py`, and every test file).
The elif chain gains one new branch, `elif vote.winning_bias.value ==
"NEUTRAL": result.gate_rejections["neutral_bias"] += 1`, inserted before
the final `else` (now purely "a real direction won but didn't reach
quorum").

**Regression tests:** `tests/test_backtest_exits.py` (+2) — see
Reproduction above.

**Status:** FIXED, tested, regression-pinned.

---

## BUG-014

**Severity:** P0 (live-trading impact — the exact symptom reported live
by the operator, still reproducing on the current deployed commit)

**Category:** cTrader connection lifecycle (execution/ctrader_client.py) —
found while re-investigating the operator's report that the P0 fixes
from `16_CTRADER_LIFECYCLE_AUDIT.md` (2026-08-04, commits `06a77e0`
through `9b039ab`) had NOT resolved the live symptom. The VPS was first
confirmed to be running commit `96ea36c` (>= `9b039ab`), ruling out the
stale-deploy theory that report's own §1 named as the most likely
explanation — this is a genuinely new, previously-unaudited defect on
current code, not a repeat of an already-fixed one.

**Claim:** `_on_error(context, failure)` — the errback attached to
*every* `client.send()` Deferred (app_auth, account_auth, trader_req,
symbols_list, reconcile, symbol_details, subscribe_spots, trendbars) —
has no way to tell whether the `failure` it just received belongs to
the CURRENT, live `self._client` or to a previous, already-torn-down
client. `_on_tcp_connected` and `_on_disconnect` both explicitly guard
against exactly this race (`if client is not None and client is not
self._client: return`, with an inline comment naming it as the fix for
the 2026-07-22 ALREADY_LOGGED_IN/fd-leak storm) — `_on_error` was never
given the same guard.

**Observed (live logs, operator-supplied, 2026-08-04 04:57–05:03 UTC,
VPS commit `96ea36c`):** a rapid, self-sustaining cycle —
`TCP_CONNECTED` → `ALREADY_LOGGED_IN` (correctly handled, bootstrap
"continuing") → seconds later, `❌ Protocol error (app_auth): (15.0,
'Deferred')` → `TCP_CONNECTED → ERROR` → `DISCONNECTED` → `Giving up
after 10 reconnect attempts` → immediate reconnect → repeat, every
2–18 seconds, for the full 6-minute window supplied. `AUTH_TIMEOUT =
15.0` (the exact literal in the log line) confirmed the failure is
Twisted's own `Deferred.addTimeout` `TimeoutError(15.0, "Deferred")` —
`str()` of that 2-arg exception renders as the literal tuple shown in
the log, not a defect in our own error-formatting code. Correlated
`journalctl -u iatis-scheduler` output for the same window showed every
EXECUTE-verdict signal declined with `DataFetchError: cTrader feed:
connect failed` — the connection never surviving long enough to reach
`READY` directly explains the "connects, but nothing ever trades"
symptom as a downstream consequence of this bug, not a separate defect.

**Expected:** an errback firing for a request sent by a client that has
since been superseded by a new `connect()` (`_stop_client()` tears down
the old Twisted service but does not — and by design of Twisted's
`send()`/timeout API, cannot — cancel that client's own already-
scheduled response-timeout callback) must be ignored, exactly like a
superseded `_on_tcp_connected`/`_on_disconnect` callback already is.

**File/Line:** `execution/ctrader_client.py` — `_on_error` (was line
1010, no guard); all 8 `d.addErrback(lambda f: self._on_error(context,
f))` call sites (`_send_app_auth`, `_send_account_auth`,
`_send_trader_req`, `_send_symbols_list_req`, `_send_reconcile_req`,
`_send_symbol_details_req`, `_get_spot_scaled`'s `subscribe()` closure,
`get_trendbars`'s `send()` closure).

**Execution path:** every `client.send(...)` call in the file routes
its errback through `_on_error`. The two auth-stage requests
(`AUTH_TIMEOUT = 15.0`) are the ones observed live, but every context
was equally vulnerable — a slow `symbols_list`/`reconcile`/`trendbars`
response from a torn-down client could just as easily have clobbered a
healthy, later connection's state.

**Reproduction:** `tests/test_ctrader_message_handlers.py` (+3 new
tests): `test_on_error_from_the_current_client_still_sets_error_state`
(regression pin — passing the live client explicitly must behave
identically to the pre-fix, client-agnostic `None` case, never
suppressing a real error); `test_on_error_from_a_superseded_client_is_ignored`
(the authoritative proof — sets `self._client` to a NEW object, drives
`_state` to `APP_AUTH_OK` as if a reconnect already succeeded, then
fires `_on_error("app_auth", <stale-object>, TimeoutError(...))` and
asserts `_state` is completely unclobbered); `test_on_error_from_a_
superseded_client_is_ignored_even_for_a_real_error` (the guard applies
uniformly, not just to the benign-ALREADY_LOGGED_IN case already
covered elsewhere in this file).

**Root cause:** `_on_error`'s signature (`context: str, failure: Any`)
never carried a `client` reference at all, unlike its two sibling
callbacks — an omission from when the superseded-client guard pattern
was first introduced (commits `06a77e0`/`a62d5ca`), not something that
regressed later. `_stop_client()`'s own docstring already documents
that it is a *best-effort* teardown of the Twisted service/socket; it
was never responsible for — and cannot, via the third-party
`ctrader-open-api`/Twisted `Deferred.addTimeout` API — cancel an
already-scheduled response-timeout callback on that old client's own
in-flight `send()` calls.

**Impact:** live/demo cTrader connections could not reliably survive
past their own auth handshake once ANY prior connection attempt's
15-second auth timer was still pending when a new attempt progressed —
directly matching the operator's reported "connects but never trades"
symptom (feed never reaches `READY`, `TradeExecutor` declines every
signal with `DataFetchError: cTrader feed: connect failed`). No effect
on backtesting/research code paths (this file's `CTraderClient` is a
live/paper-execution-only construct, not imported by `backtesting/
backtest_engine.py`).

**Fix (CONFIRMED, applied same phase):** `_on_error` gains a `client:
Any` parameter and the identical superseded-client guard already used
by `_on_tcp_connected`/`_on_disconnect`
(`if client is not None and client is not self._client: return`,
logged at DEBUG). All 8 call sites now pass the `client` that actually
issued the request — the 6 method-scoped sites already had `client` as
a parameter in scope; the 2 closure-based sites (`_get_spot_scaled`,
`get_trendbars`) now capture `client = self._client` at send-time
(the same instant the request is actually dispatched) instead of
re-reading `self._client` implicitly, so the guard has a meaningful,
frozen reference to compare against by the time the errback fires.

**Regression tests:** `tests/test_ctrader_message_handlers.py` (+3, see
Reproduction above). Existing `_on_error` tests in that file, `tests/
test_ctrader_execution_logic.py`, and every other consumer updated to
pass the new `client` argument (`None` where the test doesn't care
which client — preserves their original client-agnostic intent
exactly). Full affected-file suite (`test_provider_chains.py` +
`test_ctrader_client.py` + `test_ctrader_message_handlers.py` +
`test_ctrader_execution_logic.py`) re-run: 152/152 passing (up from
141/141 in the prior audit pass, +11 net new/updated). Full project
suite re-run with zero regressions outside these files.

**Status:** FIXED, tested, regression-pinned. **Operator action still
required**: deploy this commit (`git pull` + `sudo systemctl restart
iatis-scheduler iatis-api`, as two separate commands per CLAUDE.md's
ops runbook) and watch `journalctl -u iatis-scheduler` for one full
connection cycle — expect `TCP_CONNECTED` → (optionally `ALREADY_LOGGED_IN`,
handled) → `APP_AUTH_OK` → `ACCOUNT_AUTH_OK` → `READY` to complete
cleanly with no further `Protocol error (app_auth)`/`Giving up` lines
absent a genuine new disconnect, and for EXECUTE-verdict signals to stop
declining with `DataFetchError: cTrader feed: connect failed`.

---

## BUG-015

**Severity:** P0 (live decision quality — the operator's own reported
"NNFX = Insufficient data / MTF D1 = inert" symptom, and 665 NNFX
"Insufficient data" decisions the operator's own philosophy audit
counted directly)

**Category:** Data-layer archive deepening (`core/data_providers.py`) —
found while investigating the operator's report that NNFX (needs 210
decision-TF bars for EMA200) and the MTF D1 gate (needs 50 D1 bars) were
starving on live FX/metals symbols, where Twelve Data's free plan 403s on
native H4/D1 and the pipeline falls back to resampling from a thin H1
fetch (~500 H1 bars → ~125 H4 bars, well under NNFX's floor).

**Claim:** `_deepen_with_history()` exists specifically to left-extend a
short fetched/resampled frame from a local disk archive before it starves
NNFX/MTF — its own comment already names the exact symptom class ("A
provider-capped window... resamples to < 210 H4 bars / < 50 D1 bars,
which is exactly the DATA STARVATION class the audit flags"). This
mechanism was silently dead in every real deployment, for two independent
reasons layered on top of each other.

**Observed (confirmed by direct code read, not assumed):**
1. **Filename mismatch.** `_deepen_with_history()` looked for `data/
   {SYMBOL}_{TF}_2y.csv` — the cache path `core/data_manager.py`'s
   separate, unrelated `DataManager` class writes via its own `_cache_
   path()`. Nothing in the live or scheduled pipeline ever populates this
   file (grepped: `DataManager(` is only ever constructed by the manual,
   un-timered `scripts/download_all_data.py` — confirmed against every
   `.timer`/`.service` unit in the repo, none reference it). The tool that
   actually *is* meant to populate this archive, `scripts/download_deep_
   history.py` (measures real per-provider depth — ~6.5y H4 / ~19y D1 on
   Twelve Data Free), writes a completely different filename: `data/
   {SYMBOL}_{TF}_deep.csv`. The two names never matched, on any commit —
   confirmed via `git log -S _deepen_with_history` that both functions
   have coexisted with mismatched names since the deepener was introduced
   (`7c0400b`, 2026-07-22).
2. **Wrong object deepened, independent of (1).** Even with the filename
   fixed, `fetch_multi_timeframe_with_failover()` called `_deepen_with_
   history(symbol, best_base_label, best_base_df)` on the pre-resample
   FETCH BASE (e.g. `"H1"`, the only timeframe Twelve Data Free actually
   serves natively) — never on the H4/D1 timeframes actually being
   resampled. `download_deep_history.py` never produces an H1-labeled
   archive (its own `label = {"4h": "H4", "1day": "D1"}` and default
   `--timeframes 4h 1day` — it downloads H4/D1 series directly). So a
   base-level archive lookup could never find a matching file in
   practice, even after fix (1) alone — proven by writing
   `test_multi_tf_failover_deepens_starved_h4_resample_from_archive`
   against fix (1) only, which failed with `50 >= 210` (the resampled H4
   view stayed exactly as thin as the fetch, unchanged) before fix (2)
   was applied.

**Expected:** a thin, starved H4/D1 view (whether fetched natively-but-
capped, e.g. FCS's 300-bar limit, or resampled from a thin base) should be
left-extended from the real, already-downloadable `data/{SYMBOL}_{TF}_
deep.csv` archive before being handed to NNFX/the MTF gate.

**File/Line:** `core/data_providers.py` — `_deepen_with_history()`'s
archive path construction, and `fetch_multi_timeframe_with_failover()`'s
resample block (the `if best_base_df is not None:` section).

**Execution path:** every live/paper decision for an FX/metals/indices
symbol whose provider chain has no native H4/D1 source available at
request time (Twelve Data Free plan-gated, or a temporary cTrader outage
falling through to Twelve Data) — i.e. exactly the class the operator's
own philosophy audit already measured at 665 NNFX "Insufficient data"
decisions and an inert MTF D1 gate.

**Reproduction:** `tests/test_provider_chains.py` (+7 new tests, zero
prior coverage existed for `_deepen_with_history` at all):
`test_deepen_with_history_noop_when_no_archive_file` (regression pin —
existing no-archive behavior unchanged); `test_deepen_with_history_
reads_the_deep_csv_archive` (the archive is actually found and used, at
the corrected filename); `test_deepen_with_history_ignores_a_stray_2y_
csv_file` (the authoritative proof for fix (1) — a file under the OLD,
wrong name is never picked up, so this pins that the fix changed *which*
path is read, not just that *some* file works); `test_deepen_with_
history_fresh_bars_win_on_overlap` (the freshness guarantee — a real
overlapping-timestamp merge, fresh values win, archive still supplies the
older non-overlapping bars); `test_deepen_with_history_noop_when_already_
deep_enough` (short-circuits before touching disk at all once a view is
already ≥900 bars); `test_multi_tf_failover_deepens_starved_h4_resample_
from_archive` (the end-to-end proof for fix (2) — the exact live shape:
no native H4 provider, a thin 200-bar H1 fetch resamples to 50 H4 bars,
and only with a real `EURUSD_H4_deep.csv` archive present does the final
H4 view clear NNFX's 210-bar floor; this test was written and confirmed
failing against fix (1) alone before fix (2) was applied, then passing
after).

**Root cause:** two independent, additive defects — a filename that never
matched between the archive's reader and its only real writer, and the
deepener being wired onto the wrong object (the pre-resample base,
instead of each final target timeframe) given what that writer actually
produces (already-resampled H4/D1 series, not a base-timeframe one).
Neither defect alone fully explains the dead mechanism; both had to be
fixed for it to work at all.

**Impact:** none on any evidence/registry/backtest code path (`_deepen_
with_history` is exclusively part of the LIVE data-fetch chain,
`fetch_multi_timeframe_with_failover`, never called by `backtesting/
backtest_engine.py` or any research/Mission Center path). Direct impact
on live decision quality: NNFX (a load-bearing engine per CLAUDE.md's
frozen state — "nnfx + price_action are load-bearing") and the MTF D1
confirmation gate were operating on starved data on every FX/metals
symbol without a native H4/D1 provider, exactly matching the operator's
own measured 665-decision starvation count and the live 1.11%-vs-~15%
backtest EXECUTE-rate gap this session's own prior audit flagged as
"should shrink once data is fixed."

**Fix (CONFIRMED, applied same phase):**
1. `_deepen_with_history()`'s archive path corrected to `data/{SYMBOL}_
   {TF}_deep.csv`.
2. `fetch_multi_timeframe_with_failover()` restructured to call `_deepen_
   with_history(symbol, tf, views[tf])` once per FINAL timeframe (native
   or resampled) after the resample loop, instead of once on the
   pre-resample base before it — the archive-vs-writer mismatch this
   corrects is described in Root cause above.

**Regression tests:** `tests/test_provider_chains.py` (+7, see
Reproduction above). Full affected-file suite: 40/40 passing (33
pre-existing + 7 new). Full project suite re-run with zero regressions
outside this file.

**Status:** FIXED, tested, regression-pinned. **Operator action still
required — this fix only makes the deepening mechanism functional; it
does not itself create the archive.** On the VPS: `python3 scripts/
download_deep_history.py` (needs `TWELVE_DATA_API_KEY` set; per its own
docstring, defaults to every enabled symbol in `config.yaml`'s `data.
twelve_data_symbols`, timeframes `4h 1day`) to populate `data/{SYMBOL}_
{H4,D1}_deep.csv` for the live symbol universe. Re-run periodically (no
systemd timer exists for this yet — worth a separate, small follow-up if
the archive needs to stay current, though for a multi-year deep archive
this is a low-frequency need, not urgent). After a fresh archive exists,
watch `main.py`'s own `DATA STARVATION` log line — it should stop firing
for FX/metals symbols on Twelve Data Free once the archive is present.

**Confirmed applied live (2026-08-04):** operator ran the download script
on the deployed VPS after pulling this fix — 22 real archive files
written (`EURUSD_H4_deep.csv` 10,744 bars/6.5y through `ETHUSD_D1_deep.csv`
3,117 bars/8.5y, etc.), all with `dups=0, high<low=0`.

---

## Operational finding (not a code bug) — expired cTrader access token

**Trigger:** after deploying BUG-014 (superseded-client guard) and
confirming, via `sudo systemctl status iatis-scheduler` (single stable
process, PID unchanged since restart, no crash-restart loop), that the
cTrader `ALREADY_LOGGED_IN`/reconnect-storm class was genuinely resolved,
the operator still observed the connection never reaching `READY`. The
operator's own hypothesis was a second, undetected concurrent-connection
race (the same class BUG-014 fixed).

**Finding:** the live scheduler log contains the actual, unambiguous
answer, from the server itself:
```
State transition: TCP_CONNECTED → APP_AUTH_OK
Application authenticated
Server error (ProtoOAErrorRes): CH_ACCESS_TOKEN_INVALID — Access token expired
State transition: APP_AUTH_OK → ERROR
```
This is a *different* cTrader error code from `ALREADY_LOGGED_IN` (which
already fires once earlier in the same log and is already handled as
benign) — `CH_ACCESS_TOKEN_INVALID` is the server's account-auth-stage
validity check on the literal `accessToken` string in the request, not a
session-concurrency signal. A double-connect race would reproduce
`ALREADY_LOGGED_IN` again, not this code. Ruled out concurrency directly:
`systemctl status` showed exactly one process; this codebase has three
independent, already-tested guards against a second live session
(in-process singleton lock, cross-process `flock`, and BUG-014's
superseded-client guard) — none of their failure modes produce
`CH_ACCESS_TOKEN_INVALID`.

**Root cause:** cTrader Open API access tokens expire (~30 days per
Spotware's own OAuth docs, `expiresIn: 2628000` seconds in the token
response). No code path in this repo has ever refreshed the token
automatically — confirmed by grep, zero references to
`CTRADER_REFRESH_TOKEN` anywhere in `execution/ctrader_client.py` or
any other live/scheduled path; `scripts/measure_ctrader_spread.py`
already had a comment anticipating exactly this failure mode
("refresh it via CTRADER_REFRESH_TOKEN, or re-run the OAuth flow") but
nothing implemented it.

**Resolution (new tool, not a fix to existing code):** `scripts/
ctrader_refresh_access_token.py` — exchanges the operator's saved
`CTRADER_REFRESH_TOKEN` for a fresh `CTRADER_ACCESS_TOKEN` via cTrader's
own documented refresh flow (`GET https://openapi.ctrader.com/apps/token`,
`grant_type=refresh_token`, per help.ctrader.com/open-api/
account-authentication/). Print-only by default; `--write` updates
`.env` in place (preserving every other line) and prints the exact
restart commands. Since cTrader issues a *new* refresh token on every
refresh, both the new access token and new refresh token are always
surfaced/saved together.

**Tests:** `tests/test_ctrader_refresh_access_token.py` (8, all
`requests.get`-mocked, no live network call in the suite) — the token
endpoint/params are exactly as documented; readable `RuntimeError`s on
a non-200 response, a non-JSON response, a response missing
`accessToken`, and a network exception; the `.env` in-place rewrite
preserves every other line/comment, appends the key if absent, and its
anchored regex never clobbers a similarly-prefixed key (e.g.
`CTRADER_ACCESS_TOKEN_EXPIRY`) when only `CTRADER_ACCESS_TOKEN` should
change.

**Status:** tool shipped, tested. **Operator action required**: run
`python3 scripts/ctrader_refresh_access_token.py --write` on the VPS,
then `sudo systemctl restart iatis-scheduler` and `sudo systemctl
restart iatis-api` (as two separate commands), then watch one full
connection cycle for `READY` with no further `CH_ACCESS_TOKEN_INVALID`.

---

## BUG-016

**Severity:** P0 (live connection integrity — this is a second,
independent root cause of the exact symptom class `16_CTRADER_LIFECYCLE_
AUDIT.md` investigated: overlapping `TCP_CONNECTED`/`APP_AUTH_OK` lines,
`ALREADY_LOGGED_IN`, and an `INVALID_REQUEST — Trading account is not
authorized` error observed on live logs after a genuine `ACCOUNT_AUTH_OK`
— matches the operator's own hypothesis, stated during the live-log
review, that "maybe it connects twice at the same time and interference/
split happens.")

**Category:** cTrader connection lifecycle (`execution/ctrader_client.py`
`connect()`) — found during a follow-up trace of `core/data_providers.py`'s
`get_shared_ctrader_client()` and `execution/ctrader_client.py:1721`'s
`test_connection()`, prompted by a live log showing a second `🔌
Connecting to demo.ctraderapi.com:5035...` line appear while a prior
connection attempt's own state transitions were still in flight.

**Claim:** `16_CTRADER_LIFECYCLE_AUDIT.md` (2026-08-04) concluded "No path
exists (by direct code read, not assumed) for two live, authenticated
`CTraderClient` sessions to exist simultaneously from this codebase" —
true for the *concurrent-call* race that audit checked (two threads/
processes racing to construct a client at the same instant, guarded by
`get_shared_ctrader_client()`'s `threading.Lock` and `_acquire_process_
lock()`'s cross-process `flock`), but incomplete: it did not check what
happens to a connection attempt *after* `connect()` gives up on it.

**Observed (confirmed by direct code read):** `connect()`'s `check_
status()` polling closure (`execution/ctrader_client.py`, inside
`connect()`) has three branches: reached `READY` → `connected.set()`;
reached `ERROR` → `connected.set()`; `elapsed > timeout` → sets
`error_holder[0]` and calls `connected.set()`. The third branch — the
ordinary connect-timeout path — does nothing else: it does not call
`client.stopService()`, does not cancel the pending Twisted `Deferred`
chain, and does not touch `self._client`. `connect()` then returns
`False` to its caller, but the underlying `ctrader_open_api.Client`
object it created (`client = Client(...)`, `self._client = client`) is
still fully alive: `client.startService()` already ran, and the reactor
thread keeps driving its connection/auth handshake (`_on_tcp_connected` →
`_send_app_auth` → ... ) completely independently of the Python call that
already returned `False`.

`core/data_providers.py`'s `get_shared_ctrader_client()` — the sole live
caller for the data-fetch path — never retries `connect()` on the *same*
`CTraderClient` instance: `client = CTraderClient(); if not client.connect
(timeout=30): raise DataFetchError(...)` discards `client` entirely on
failure; `_ctrader_feed_client` stays `None`. The *next* call (next
scheduler tick, next symbol) re-enters the lock, sees `_ctrader_feed_
client is None`, and constructs a **brand-new** `CTraderClient()` instance,
calling `connect()` on it too. If the *first* attempt's underlying
connection is still alive in the background (the common case for a
connect that was merely slow, not actually broken — e.g. any tick where
the broker took longer than the 30s ceiling to answer app-auth), two
independent, live `CTraderClient` Python objects now exist simultaneously,
both driving their own auth handshake against the *same* account+app —
exactly the double-connection scenario the operator suspected. The
existing superseded-client guards in `_on_tcp_connected`/`_on_disconnect`/
`_on_error` (`if client is not None and client is not self._client:`) do
**not** protect against this: each guard is scoped to a single
`CTraderClient` instance's own `self._client` attribute, so it cannot
detect or suppress a second, entirely separate `CTraderClient` object's
callbacks. `get_shared_ctrader_client()`'s `threading.Lock` and
`_acquire_process_lock()`'s `flock` also do not protect against this: both
guard *construction*, and by the time the second instance is constructed
the first `connect()` call has already returned (lock released) — there
is nothing "concurrent" for either lock to catch.

**Expected:** a `connect()` attempt that gives up (timeout, protocol
error, or an unhandled exception before reaching `READY`) must leave no
live connection behind — the Client it created should be torn down before
`connect()` reports failure, so a subsequent fresh attempt is never
racing an orphaned prior one for the account's single session slot.

**File/Line:** `execution/ctrader_client.py`, `CTraderClient.connect()`
— the `if error_holder[0]:`, the post-`connected.wait()` fallback
(`ConnectionState` not `READY`), and the outer `except Exception:` branch,
all three of which previously `return False` with no teardown.

**Execution path:** any live scheduler tick where `get_shared_ctrader_
client()` (data-fetch path) or a direct `CTraderClient().connect()` caller
(e.g. `test_connection()`) issues a `connect()` that does not reach
`READY` within its timeout window while the broker/handshake is still
genuinely making progress in the background — not a hard/immediate
failure, but a slow one. Every such attempt left a zombie session running
that the next scheduler tick's fresh `get_shared_ctrader_client()` call
would then race against.

**Reproduction:** `tests/test_ctrader_client.py` (+4 new tests, zero
prior coverage existed for `connect()`'s failure-path teardown — every
existing `connect()`-adjacent test relies on `ctrader_open_api`/`twisted`
being absent from this sandbox and only ever exercises the outer
`except ImportError` short-circuit): `test_abandon_incomplete_connection_
stops_client_and_nulls_reference` / `..._is_a_noop_when_no_client_exists`
(unit-level proof of the new teardown helper); `test_connect_timeout_
tears_down_the_orphaned_client` (the literal regression test — injects
minimal fake `ctrader_open_api`/`twisted.internet.reactor` modules via
`sys.modules` so `connect()`'s real body runs deterministically with no
network, forces the timeout branch with a strictly-increasing fake clock,
and asserts the `Client` instance `connect()` created had `stopService()`
called on it exactly once and `self._client` is `None` afterward);
`test_connect_success_does_not_tear_down_the_live_client` (control case —
a `connect()` that reaches `READY` must never call the new teardown, and
`self._client` must still reference the live, running client).

**Root cause:** `connect()`'s timeout/error/exception paths conflated
"stop waiting for this attempt" with "stop this attempt" — they only ever
did the former. `get_shared_ctrader_client()`'s no-retry-on-same-instance
design (itself correct and necessary — a failed instance shouldn't be
reused) then means a torn-down-in-Python-only client keeps running,
unsupervised, as a real, separate session.

**Impact:** live cTrader data-fetch and execution paths only (`core/
data_providers.py`'s `_fetch_ctrader`/`get_shared_ctrader_client`,
`execution/trade_executor.py`'s use of the same shared client) — no
research/backtest/Mission Center code path touches `execution/
ctrader_client.py` at all. Directly explains, as a second and previously
unaccounted-for mechanism alongside `16_CTRADER_LIFECYCLE_AUDIT.md`'s
already-fixed 2026-07 reconnect-storm class: overlapping connection-state
log lines, repeated `ALREADY_LOGGED_IN`, and account-session errors
(`INVALID_REQUEST — Trading account is not authorized`) that occur *after*
a real `ACCOUNT_AUTH_OK` — consistent with a second, competing session
displacing the first mid-handshake.

**Fix (CONFIRMED, applied same phase):** new `CTraderClient._abandon_
incomplete_connection()` — tears down (`self._stop_client(...)`) and nulls
`self._client` — called from all three of `connect()`'s failure-return
sites (`error_holder[0]` branch, the post-wait non-`READY` fallback, and
the outer `except Exception:` handler) before returning `False`. The
success path (`self._state == ConnectionState.READY`) is untouched. Nulling
`self._client` also means any callback that still fires from the
now-stopped, orphaned client afterward is correctly ignored by the
existing superseded-client guards (`client is not None and client is not
self._client`) instead of being able to clobber a later, genuinely live
connection's state.

**Regression tests:** `tests/test_ctrader_client.py` (+4, see Reproduction
above). Full affected-file suite: `test_ctrader_client.py` +
`test_ctrader_message_handlers.py` + `test_ctrader_execution_logic.py` +
`test_provider_chains.py` — 162/162 passing (158 pre-existing + 4 new).
Full project suite re-run with zero regressions outside these files.

**Status:** FIXED, tested, regression-pinned. No operator action required
— this is a pure code fix with no external state to update (unlike
BUG-015's archive-population step or the token-refresh finding above).
Recommended, not required: after deploying, watch a live scheduler run
for the previously-reported overlapping-`🔌 Connecting`/`INVALID_REQUEST`
pattern to confirm it no longer recurs — this cannot be verified from
this sandboxed session (no live broker/network access here).
