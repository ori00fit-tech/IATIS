# Mission Prune Forensic Audit — mission `e0d21655978a` (2026-08-04)

**Read-only investigation. No code was changed. No thresholds, pruning
behavior, or engine logic were modified. `git status` is clean before
and after this pass.**

## Trigger

Operator screenshot of mission `e0d21655978a` (EURUSD, sampler=random,
objective=profit_factor, "Mixed (signal + risk variation)" search space
— i.e. hypothesis-bundle mode): 18 hypothesis-bundle trials `complete`,
9 `pruned`. The `pruned` trials cluster almost entirely on two named
hypotheses that each enable only 2 engines:

| Hypothesis | Engines | State | Trades (per pruned trial) |
|---|---|---|---|
| H-EUR-08 — PriceAction + Wyckoff | price_action, wyckoff | pruned ×2 | 1, 1 |
| H-EUR-06 — SMC + Price Action | smc, price_action | pruned ×7 | 2, 2, 2, 2, 2, 2, 2 |

...while other hypotheses in the *same mission* completed normally:

| Hypothesis | Engines | State | Trades (range) |
|---|---|---|---|
| H-EUR-05 — Quant + Wyckoff | quant, wyckoff | complete ×4+ | 21–31 |
| H-EUR-04 — NNFX + Wyckoff | nnfx, wyckoff | complete ×5+ | 48–92 |
| H-EUR-09 — All Prod4 + Quant | smc, price_action, nnfx, wyckoff, quant | complete ×3+ | 410–554 |
| H-EUR-07 — SMC + NNFX | smc, nnfx | complete ×5+ | 361–594 |

The operator's own framing, correctly stated up front: the UI does not
expose *why* a trial was pruned, and "few trades" must not be silently
equated with "bad strategy" without verifying the actual mechanism.
This audit determines the mechanism, cited to file:line, before any
opinion is offered about whether it's correct.

## 1. Where a trial can become `pruned` — exhaustive search

```
grep -rn "trial\.report|should_prune|optuna\.pruners|import.*Pruner" --include="*.py" .
```
**Zero matches anywhere in this repository.** No `MedianPruner`,
`SuccessiveHalvingPruner`, `HyperbandPruner`, `trial.report()`, or
`should_prune()` call exists in this codebase at all. Optuna's
native/built-in pruning machinery is **not used**.

The *only* place `TrialState.PRUNED` is ever assigned is a manual,
IATIS-authored condition:

- `backtest/optimizer.py:515-519` (`evaluate_point()`):
  ```python
  trades = metrics.total_trades
  if trades < min_trades:
      return EvalResult(
          metrics=metrics, objective_value=None, insufficient=True,
          trades=trades, trade_records=trade_records,
      )
  ```
- `backtest/mission_runner.py:313-321` (`_run_symbol()`):
  ```python
  if result.insufficient:
      _tell_safely(study, trial, state=optuna.trial.TrialState.PRUNED)
      research_missions.record_trial(
          ..., state="PRUNED", objective_value=None, ...,
          trades=result.trades, ...,
      )
  ```

**Pruning in this codebase is exclusively a post-hoc trade-count floor,
not an early-stopping/intermediate-reporting mechanism.** `min_trades`
is the mission's own configured value (`MissionConfig.min_trades`,
threaded from the operator's mission-creation request) — this sandbox
has no live D1 access to read `e0d21655978a`'s exact stored value, but
the observed pass/fail boundary (pruned trials always had 1–2 trades;
completed trials always had ≥21) means `min_trades` for this mission
lies somewhere in `(2, 21]`.

## 2. Is pruning based on trade count?

**Yes — confirmed, exactly, with no ambiguity.** See §1. There is no
other prune condition anywhere in the codebase (no PF threshold, no
Sharpe threshold, no timeout-based prune, no data-availability check
distinct from the trade-count floor).

## 3. Where in the pipeline does this happen — full execution path (file:line)

```
Mission Center UI (MissionBuilder.submit(), MissionCenter.tsx)
  → builds hypothesis_bundle_choices: [{name, timeframes, engines,
    indicators, context_filters, engine_variants}, ...] + risk_param_
    ranges/grid + min_trades, POSTs to /research/missions
  ↓
execution/routes/missions.py — _MissionRequest validated via
  MissionSearchSpace(...); argv built; backtest/mission_runner.py
  launched as a subprocess job
  ↓
backtest/mission_runner.py :: run_mission() → _run_symbol() (per symbol)
  builds an Optuna Study (sampler = mc.sampler, here "random")
  loop:
    trial = study.ask()                              [line 287]
    raw_params = suggest_point(trial, mc.search_space, grid_mode)
                                                       [line 288, optimizer.py]
      — when hypothesis_bundle_choices is set, this samples EXACTLY
        ONE categorical index (_HYPOTHESIS_IDX_KEY) selecting WHICH
        named bundle this trial uses; every other risk-override
        dimension (sl_atr_multiplier, min_rr, ...) is sampled
        independently, continuously, per-trial
    point = resolve_point(mc.search_space, raw_params) [line 289]
      — pulls timeframes/engines/indicators/context_filters/
        engine_variants ATOMICALLY from the ONE selected bundle
        (never mixed across bundles) + this trial's own risk_overrides
    result = evaluate_point(symbol, train_df, point,
                             mc.min_trades, mc.objective_metric)
                                                       [line 293, optimizer.py:480]
      engine_config = build_engine_config_override(
          timeframes=point["timeframes"], engines_enabled=...,
          indicators=..., context_filters=..., engine_variants=...)
                                                       [optimizer.py:501-507]
        — merges ONLY these 5 keys over a real load_config() snapshot.
          confluence.min_engines_agreeing / min_informative_weight_share /
          min_score_to_trade / weights are NEVER touched — they stay at
          PRODUCTION values for every ad-hoc Mission Center run
          (config.yaml: min_engines_agreeing=2, min_informative_weight_
          share=0.6).
      bt = run_backtest(df, cfg, engine_config=engine_config)
                                                       [optimizer.py:509,
                                                        backtesting/backtest_engine.py]
        — runs the FULL, COMPLETE backtest over the entire symbol
          period, every bar from warmup_bars to len(df)-1. No early
          stop, no intermediate check, no per-trial timeout inside
          this call. Only the ENTIRE mission's max_wall_clock_seconds
          budget is checked BETWEEN trials (mission_runner.py:280-283),
          never inside one trial's own backtest.
        — per bar: tally_votes(outputs, weights)       [confluence/voting_system.py:102]
          agree_count = raw count of ENABLED engines whose effective_
          bias (score >= MIN_CONVICTION_SCORE=20) agrees on direction
          ok = (vote.agree_count >= min_engines and ...)
                                                       [backtesting/backtest_engine.py:802-810]
          — the quorum gate. Only bars clearing quorum AND score AND
            info-share AND veto AND indicator/context filters produce
            a trade.
      records = [trade_to_record(t, symbol) for t in bt.trades]
      metrics = calculate_metrics(records, ...)         [optimizer.py:511]
      trades = metrics.total_trades                     [optimizer.py:514]
      if trades < min_trades: insufficient=True, objective_value=None
                                                       [optimizer.py:515-519]
    if result.insufficient:
      _tell_safely(study, trial, state=PRUNED)          [mission_runner.py:314]
      record_trial(state="PRUNED", objective_value=None,
                    trades=result.trades, ...)          [mission_runner.py:315-321]
    else:
      _tell_safely(study, trial, values=result.objective_value)
      record_trial(state="COMPLETE", objective_value=result.objective_value, ...)
                                                       [mission_runner.py:322-330]
```

## 4. Reconstructed lifecycle of the listed trials

**Cannot be individually re-fetched from live D1 in this sandboxed
session** (no `.env`/D1 credentials here — the same disclosed
limitation as every other Mission-Center-related investigation this
session). What CAN be established with certainty from the code path
above, applied to every one of the 9 listed trials:

- H-EUR-08 trial 2, trial 7 (1 trade each) and H-EUR-06 trials 4, 6,
  18, 19, 21, 22, 23 (2 trades each): each ran a **complete, full-period
  backtest** (`run_backtest`), each produced fewer closed trades than
  `min_trades`, each was reported to Optuna as `PRUNED` and recorded
  with `objective_value=None`. **The tight clustering of trade counts
  (H-EUR-06: exactly 2, every single time, across 7 trials with
  DIFFERENT sampled risk parameters; H-EUR-08: exactly 1, both times)
  is itself evidence**: if the bottleneck were the risk-parameter sweep
  (SL/RR sizing), trade counts would be expected to vary somewhat
  across draws. Near-identical, near-zero counts regardless of the
  risk draw point at a bottleneck **upstream of risk sizing** — i.e. in
  signal generation / the confluence quorum, not in how positions are
  sized once a signal exists.

## 5. Question 4 — when exactly does the prune happen?

**(C) after a minimum-trade check** is the correct answer, with one
precision: the check happens **strictly after a fully-completed
backtest**, not instead of one, and not as an early-stopping mechanism.

- (A) before the backtest completed — **false**. `run_backtest()` runs
  to completion first.
- (B) during intermediate reporting — **false**. No intermediate
  reporting exists (no `trial.report()` anywhere).
- (D) because of Optuna's pruner — **false**. No Optuna pruner is
  configured or used (§1).
- (E) because of objective/PF — **false, and important**: `objective_
  metric` (e.g. `profit_factor`) is **never even read** for a pruned
  trial — `evaluate_point()` returns at the trade-count check, before
  `getattr(metrics, objective_metric)` is ever called (`optimizer.py:521`
  is unreachable when `trades < min_trades`). Profitability plays **no
  role whatsoever** in the prune decision.
- (F) timeout — false, see §3.
- (G) no valid decisions/trades were produced — **closest correct
  framing**, generalized to "fewer than `min_trades` trades were
  produced," not "zero."
- (H) another condition — none found.

## 6. PriceAction engine resolution — v1 or v2?

**Cannot be determined with certainty for H-EUR-06/H-EUR-08 from this
sandbox.** What is established:

- `resolve_point()` (`optimizer.py`) reads `engine_variants` straight
  from whichever bundle a trial's `_HYPOTHESIS_IDX_KEY` selected — each
  named hypothesis bundle carries its **own, independently-configured**
  `engine_variants: Record<string,string>` map (default `{}` = all v1
  engines), set per-bundle by the operator in the Mission Builder's
  `EngineVariantPicker` at mission-creation time.
- The operator's own screenshot **proves this mission actively uses the
  variant mechanism**: Trial 16's breakdown (hypothesis H-EUR-05 —
  Quant + Wyckoff) explicitly shows `Engine variants: wyckoff=v2`. This
  rules out assuming every bundle in this mission defaults to v1.
- Whether H-EUR-06 or H-EUR-08 also set `price_action=v2` (or
  `wyckoff=v2`) is a **separate, independent choice per bundle** — it
  cannot be inferred from H-EUR-05's setting, and this sandbox has no
  live access to `e0d21655978a`'s stored `search_space_json` to check
  directly.

**Fastest, most authoritative way to resolve this**: open H-EUR-06's
or H-EUR-08's own trial "View" breakdown panel in Mission Center —
identical UI element to the one already shown for Trial 16 — its
"Engine variants:" line answers this with zero ambiguity, faster than
any further code trace from this sandbox could.

## 7. Comparison table — what's actually knowable today

| Hypothesis | Engines (bundle) | Trades (typical) | Objective computed? | gate_rejections breakdown available? |
|---|---|---|---|---|
| H-EUR-08 PriceAction+Wyckoff | 2 | 1 | No (pruned before objective read) | **No — see §9 gap** |
| H-EUR-06 SMC+PriceAction | 2 | 2 | No (pruned) | **No** |
| H-EUR-04 NNFX+Wyckoff | 2 | 48–92 | Yes | No |
| H-EUR-07 SMC+NNFX | 2 | 361–594 | Yes | No |
| H-EUR-05 Quant+Wyckoff | 2 | 21–31 | Yes | No |
| H-EUR-09 All Prod4+Quant | 5 | 410–554 | Yes | No |

**The "number of decisions / neutral decisions / rejected by risk gate
/ rejected by context filters / rejected by execution constraints /
intermediate objective values" fields the operator asked for are NOT
currently recorded for ANY Mission Center trial, pruned or complete —
this is a real, confirmed observability gap, not something withheld
from this specific mission.** See §9.

## 8. Why H-EUR-06/H-EUR-08 produced so few trades

**Primary, confirmed mechanism**: `confluence.min_engines_agreeing = 2`
(`config.yaml:25`) is **never modified** by `build_engine_config_
override()` (it only touches `data.timeframes`/`engines.enabled`/
`engines.variants`/`indicators.filters`/`context_filters.filters` —
confirmed by reading the function; `confluence` is never a key it
writes). For a bundle with **exactly 2 engines enabled**, `agree_count
>= 2` means **both** enabled engines must independently produce an
above-conviction-threshold (score ≥ 20) vote in the **same direction on
the same bar** — full unanimity, not a 2-of-N majority.

**This alone does not explain the pattern** — H-EUR-07 (SMC + NNFX) is
*also* a 2-engine bundle under the identical unanimous quorum, and
produced 361–594 trades; H-EUR-04 (NNFX + Wyckoff) is also 2 engines,
48–92 trades. So "2 engines ⇒ automatically starved" is **false** as a
general rule.

**The deciding variable is PriceAction's own signal behavior**, and
there is direct, in-code evidence for why it would agree with a partner
engine far less often than SMC/NNFX/Wyckoff agree with each other:
`engines/price_action_engine.py:10-11` (module docstring, unchanged
since before this investigation):

> "Correlation with NNFX was 0.975 (redundant) — now uses completely
> different indicators to add genuine diversification."

PriceAction was **deliberately redesigned to be maximally decorrelated**
from other engines' logic. That is exactly the property that, combined
with an unmodified unanimous-2-of-2 quorum, predicts near-zero
simultaneous-agreement bars when PriceAction is paired 1:1 with any
single partner — consistent with both H-EUR-06 (SMC+PriceAction) and
H-EUR-08 (PriceAction+Wyckoff) landing at 1–2 trades, while pairs of
more historically-correlated engines (SMC+NNFX, NNFX+Wyckoff) clear the
same quorum far more often.

**Secondary, unconfirmed possible contributor**:
`min_informative_weight_share = 0.6` (`config.yaml:38`) is *also*
unmodified in ad-hoc runs. With only 2 engines active, if PriceAction is
frequently NEUTRAL (silent) while its partner votes, the informative
weight fraction could fall under 0.6 on many bars too — a second,
additive filter on top of the quorum. **This cannot be confirmed or
ruled out from currently-recorded data** (§9's gap applies here too).

## 9. Real, confirmed gap found during this audit: `gate_rejections` is discarded

`backtesting/backtest_engine.py`'s `BacktestResult` computes and carries
`gate_rejections` (a dict distinguishing `"votes"` — quorum failure —
from `"neutral_bias"`, `"score"`, `"info_share"`, `"reversal_veto"`,
`"indicator_filter"`, `"context_filter"`, `"mqs"`), plus
`context_rejections`/`indicator_rejections`. Confirmed by grep: **none
of these three fields appear anywhere in `backtest/metrics.py`,
`backtest/optimizer.py`, or `backtest/mission_runner.py`.**
`evaluate_point()` (`optimizer.py:480-528`) discards `bt.gate_rejections`
entirely — only `bt.trades` (converted to `TradeRecord`s) and the
resulting `BacktestMetrics` are kept. This means: **for any Mission
Center trial, pruned or not, there is currently no way to see whether
"votes" (quorum), "neutral_bias", or another gate dominated the
rejections** — the exact breakdown item 6/7 of the operator's request
asked for is architecturally unavailable today, for every mission, not
just this one.

(For contrast: `backtest/runner.py`'s `write_summary()` — the
*non*-Mission-Center Backtesting Lab path — **does** serialize
`gate_rejections` per symbol into `backtest_summary_*.json`. The gap is
specific to the Mission Center evaluation path, `evaluate_point()`.)

## 10. Is the system conflating "few trades" with "bad strategy"?

**No — confirmed, this distinction is correctly preserved in code.**
A pruned trial's `objective_value` is `None`; its `metrics.profit_
factor`/`win_rate`/etc. are computed (since `calculate_metrics` runs
regardless of trade count) but **never read or compared to anything**
for the prune decision — the check at `optimizer.py:515` happens on
`trades` alone, before `objective_metric` is ever consulted
(`optimizer.py:521` is unreachable in this branch). The system is not
saying "this configuration performed badly" — it is saying "this
configuration did not produce enough closed trades to be a statistically
meaningful measurement," which is the intentional, correct distinction
between *insufficient evidence* and *poor performance* that this
codebase's evidence discipline (Bonferroni correction, chronological
OOS, `min_trades` floors elsewhere in the system) is built around.

One real, minor UI-wording concern, not a logic bug: the label
"pruned" itself carries a connotation (familiar from ML early-stopping)
of "this was a bad result" — that connotation does **not** match what
the mechanism actually does here. Worth a wording/tooltip review if the
operator wants to reduce this ambiguity; not fixed in this pass per
your explicit instruction.

---

## Final structured answer

**A. EXACT PRUNE CAUSE.** `backtest/optimizer.py:515-519`
(`evaluate_point()`): a trial is marked `PRUNED` (`objective_value=None`)
if and only if `metrics.total_trades < mission.min_trades`, evaluated
**after** a complete, full-period `run_backtest()` call. No Optuna-native
pruner (`MedianPruner`/`SuccessiveHalvingPruner`/`HyperbandPruner`/
`trial.report`/`should_prune`) exists anywhere in this codebase —
confirmed by an exhaustive, zero-hit repo-wide search.

**B. EVIDENCE.** File:line citations in §1/§3 above, quoted verbatim
from the current source; the observed trade-count clustering (H-EUR-06:
exactly 2 trades across 7 differently-risk-parameterized trials;
H-EUR-08: exactly 1 across 2) as corroborating evidence the bottleneck
is in signal generation, not risk sizing; the confirmed presence of
`Engine variants: wyckoff=v2` on a sibling hypothesis in the same
mission (from the operator's own screenshot).

**C. EXECUTION PATH.** Traced end-to-end in §3, file:line at every
stage: Mission Builder UI → `execution/routes/missions.py` →
`backtest/mission_runner.py::_run_symbol()` → `optimizer.py::
suggest_point/resolve_point/evaluate_point` → `build_engine_config_
override` → `run_backtest` (full backtest, per-bar quorum/score/
info-share/veto/filter gates) → `calculate_metrics` → trade-count check
→ `PRUNED` or `COMPLETE`.

**D. PRICE ACTION ENGINE ACTUALLY USED.** Not determinable from this
sandbox for H-EUR-06/H-EUR-08 specifically (no live D1 access). This
mission demonstrably uses the `engine_variants` mechanism for at least
one bundle (H-EUR-05: `wyckoff=v2`), so v1 must not be assumed by
default. Operator can confirm instantly via each trial's own "View"
breakdown panel.

**E. WHY H-EUR-06/H-EUR-08 PRODUCED SO FEW TRADES.** The unmodified
production `min_engines_agreeing=2` quorum requires full unanimity
between exactly the 2 enabled engines on every traded bar; PriceAction
is documented in its own module as deliberately redesigned to be
maximally decorrelated from other engines (to fix a prior 0.975
correlation with NNFX) — this predicts, and is consistent with, its
near-zero simultaneous-agreement rate with any single partner engine,
in contrast to more naturally-correlated pairs (SMC+NNFX, NNFX+Wyckoff)
which clear the identical quorum easily.

**F. BUG OR INTENTIONAL PRUNING?** The prune mechanism itself
(post-backtest trade-count floor, independent of profitability) is
intentional and correctly implemented — not a bug. The low trade count
that triggers it for these two hypotheses is also not a bug — it is the
predictable, correct consequence of (i) a deliberately decorrelated
PriceAction engine and (ii) an unmodified, unanimous 2-engine quorum
applied to an arbitrarily small ad-hoc bundle. **A real, separate gap
was found**: `gate_rejections`/`context_rejections`/`indicator_
rejections` are computed by `run_backtest()` but discarded by `evaluate_
point()` — Mission Center currently has zero visibility into *which*
gate rejected the majority of bars for any trial, pruned or not
(§9). This is an observability gap, not a trading-logic bug.

**G. SHOULD PRUNING BE CHANGED?** Not decided or changed in this pass,
per your explicit instruction. Two independent, non-conflicting
directions worth a deliberate future decision (neither applied here):
(1) surface `gate_rejections` per trial (pure observability, zero
change to which trials get pruned); (2) consider whether
`min_engines_agreeing` should scale with the size of an ad-hoc engine
subset rather than staying fixed at the production value regardless of
how many engines a bundle enables — a genuine design trade-off, not an
obvious fix.

**H. MINIMAL SAFE FIX (if pursued later).** Thread `bt.gate_rejections`/
`bt.context_rejections`/`bt.indicator_rejections` from `run_backtest()`'s
return value into `EvalResult` → the trial's stored `metrics_json` —
purely additive, changes nothing about which trials get pruned or how
backtests run. Any change to `min_engines_agreeing`'s scaling behavior
is a separate, larger decision needing its own explicit sign-off, since
it changes what counts as a valid trade for every future Mission Center
run.

**I. TESTS REQUIRED (if H is pursued).** A test that `evaluate_point`
(or a new opt-in param) surfaces `gate_rejections` unmutated from
`bt.gate_rejections`; a synthetic 2-engine test forcing the two engines
to always disagree (assert `gate_rejections["votes"] > 0`, zero trades)
versus always agree (assert trades > 0) — isolating the quorum
mechanism from every other gate as its own, directly-observable
regression test.

**J. NO CODE CHANGES MADE.** Confirmed — this entire investigation used
only Grep/Read tools. `git status` was clean before this pass and is
unchanged now.

---

## Addendum (2026-08-04, same day) — item H fix applied, plus a second, real finding

The operator asked for fixes and separately reported a 150-trial mission
that did not complete. Two fixes were applied in this follow-up pass.

### Fix 1 — item H: `gate_rejections`/`context_rejections`/`indicator_rejections` now surfaced

`backtest/optimizer.py`'s `EvalResult` gained three fields
(`gate_rejections`, `context_rejections`, `indicator_rejections`,
default `{}`), populated in `evaluate_point()` from
`bt.gate_rejections`/`bt.context_rejections`/`bt.indicator_rejections`
(previously read and then discarded) on **both** the PRUNED and
COMPLETE return paths — the exact gap §9 identified. `backtest/
mission_runner.py` now writes all three into every recorded trial's
`metrics_json`. `MissionCenter.tsx`'s `TrialBreakdownPanel` renders them
as a new "Gate rejections — why bars didn't trade" section, shown
**independently** of the by_regime/by_direction/by_session breakdown
(which needs closed trades and is therefore empty for exactly the
low-trade-count trials this fix is for). Tests: `tests/test_optimizer.py`
(+2, pinning both EvalResult paths carry the fields unmutated) and
`tests/test_mission_runner.py` (+1, end-to-end: every recorded trial's
`metrics_json` contains all three keys). `tsc -b`/`oxlint`/`npm run
build` clean.

### Fix 2 — new finding: `record_trial()` (a D1 write) was unguarded on every path, so one write hiccup could abort a whole mission

While tracing why a 150-trial mission might not complete, found that
`_run_symbol()`'s three `research_missions.record_trial(...)` calls
(FAIL/PRUNED/COMPLETE branches) were **not** wrapped in their own
try/except — only `evaluate_point()`'s own exceptions were guarded
("one trial's crash must never abort the mission," per this module's
own stated contract). `run_mission()`'s outer `try/except` (line 214)
does catch an unhandled exception from deep inside `_run_symbol()`, so
the mission is not left stuck forever — but it **is** marked `failed`
as a whole and every trial after the one that hit the write failure is
abandoned, even if 100+ trials before it had already succeeded. A
single transient D1 hiccup at, say, trial 80 of 150 would produce
exactly "ran a lot of trials, then didn't complete" — plausible, though
not confirmed against this specific mission (no live D1/log access from
this sandbox to verify it was the actual cause here).

**Fix**: each of the three `record_trial()` calls is now individually
wrapped in `try/except Exception`, logging a warning and continuing to
the next trial rather than propagating — Optuna's in-memory study
already has the trial via `_tell_safely()` (called first, before the
write); only the D1 row is lost, and a later resume simply re-attempts
that trial number. New regression test:
`tests/test_mission_runner.py::test_transient_record_trial_write_failure_does_not_abort_the_mission`
— monkeypatches `research_missions.record_trial` to fail once (trial 2
of 5), asserts the mission still finishes with `status="finished"` (not
`"failed"`) and 4 of 5 trials land, proving the mission survives a
single write hiccup instead of aborting.

### Verification

`tests/test_optimizer.py` (58/58), `tests/test_mission_runner.py`
(17/17, including both new tests), full project suite re-run clean
(zero regressions outside these files). `tsc -b`, `oxlint src`, `npm
run build` clean. `git status` on `research/results/registry.json`,
`config.yaml`, `config/engines.yaml` unchanged (empty diff) — neither
fix touches promotion/evidence logic or live config, matching every
other Mission Center change this session.

**Not confirmed**: whether Fix 2 is the actual cause of the operator's
specific 150-trial incomplete mission — that would need the real job's
log/status from the VPS (mission ID, and whether Mission Center shows
it as `running` (still in progress), `failed`, or stuck). Both fixes
are real, independently-justified improvements regardless of whether
Fix 2 turns out to be the exact cause here.
