# Phase 15D — SHADOW Outcome/Divergence Evidence: Pre-Registration Spec

**Status (as of this document):**

| Property | Status |
|---|---|
| DESIGNED | NO (this document is the pre-registration step, not the Design Gate) |
| IMPLEMENTED | NO |
| TESTED | NO |
| WIRED | NO |
| PROVEN | NO |
| ACCEPTED | NO |

**This document contains no code and authorizes none.** It exists solely to resolve, in writing, *what* Phase 15D measures and *how* it would prove that measurement is honest — before any implementation choice is made. Per CLAUDE.md's own Rule 1 ("pre-register before you build... a decision rule written BEFORE any result exists") and Rule 3 ("the promotion bar is code, not prose"), this spec is written before any mechanism exists to populate it, and nothing here may later be read as evidence itself.

No file outside `docs/` is touched by this document. `backtest/hypothesis_live_request.py` (Phase 8C), `scheduler.py`, `main.py`, `execution/*.py`, `backtest/promotion_gate.py`, and every already-accepted Phase 15A/15B/15C file are unmodified and unaffected by writing this spec.

---

## 1. Decision identity

The unit being evaluated is **one `request_id`** from `research_live_identity_requests` (Phase 8C), the same canonical identity Phase 15C already locked for attribution. `hypothesis_id` is derived the same way Phase 15C derives it — from the real stored request row, never a separately-supplied parameter. One SHADOW Outcome Evidence record maps to at most one `request_id` (1:1, mirroring Phase 15C's own cardinality rule).

## 2. Entry snapshot

The evaluator needs the **exact** `entry_price`/`stop_loss`/`take_profit`/`symbol`/`timeframe`/decision `bar_time` that existed in `main.run_pipeline()`'s own `report` dict **at the moment the governed SHADOW decision was computed** — confirmed by Gate 0 to exist in `report` whenever `conf.passed` (`main.py:732-738`), independent of `final_verdict`. This snapshot must be **captured at that exact moment and persisted verbatim** — it may **never** be re-derived later by calling `run_pipeline()` again (Gate 0's own finding: `run_pipeline()` is pure with respect to its config argument only, not with respect to live market/provider state at a later wall-clock time, so a second call cannot be trusted to reproduce the same values).

**Side** (BUY/SELL) must be captured from the **same** source `execution/trade_executor.py` privately uses today (`report["confluence"]["vote"]["winning_bias"]`, mapped `BULLISH`→BUY / else→SELL) — at snapshot time, from the same `report`, never re-derived from a different or later source.

**No-snapshot case**: a decision where confluence did not pass (`entry_price`/`stop_loss`/`take_profit` all `None` in `report`) has nothing to evaluate. Such a request_id gets **no** evidence record at all — never a fabricated or zeroed one. (See §5, `NO_SNAPSHOT` is explicitly not an outcome state for this reason — it is simply the absence of a record.)

## 3. Future price source & "first bar after decision"

**Deferred to the A/B/C mechanism decision** (§13) — this spec does not choose a source. It instead fixes the *requirement* any chosen source must satisfy:

- "The decision's own bar" = the bar whose close produced the governed computation. Its close time is `T_D`.
- "First eligible resolution bar" = the earliest bar, on the hypothesis's own timeframe, for the same symbol, with open time `> T_D` (strictly after the decision bar — never the same bar, to remove any same-bar ambiguity about what was "already known" at decision time).
- Whatever mechanism supplies subsequent bars (§13's A/B/C), it must do so only as bars described above — never a bar with open time `<= T_D`.

## 4. Look-ahead proof requirement (falsifiable invariant)

Locked as a **structural property any implementation must satisfy and any test suite must directly verify**:

> For every bar the evaluator consumes to resolve request_id `R`'s outcome, that bar's own timestamp must be strictly greater than `R`'s decision bar close time `T_D`, **and** the evaluator process itself must never run, for `R`, at a wall-clock time earlier than the real-world time those bars actually closed.

The second clause specifically forbids ever "replaying" a backfilled historical dataset to resolve a SHADOW decision made in the live system at some real past time `T_D`, unless the backfilled data's own provenance can prove it was not available before its own real close time — i.e., ordinary historical OHLCV data is fine (it closed when it closed, in the real world, regardless of when it happens to be downloaded), but nothing may ever feed the evaluator a bar whose *information content* could have influenced, or been influenced by awareness of, the original decision's own computation. Any implementation must include a direct, adversarial test proving this invariant (a bar at or before `T_D` is never accepted/consumed, under parametrized boundary cases).

## 5. Outcome taxonomy

| State | Meaning |
|---|---|
| `TP_HIT` | `take_profit` level reached at or before `stop_loss`, per §6's tie-break, within the evaluation horizon (§5a) |
| `SL_HIT` | `stop_loss` level reached at or before `take_profit`, per §6 |
| `TIMEOUT` | Neither level reached within the evaluation horizon |
| `UNRESOLVED` | Evaluation horizon not yet elapsed, or required subsequent bars are not yet available — a **pending**, not a failing, state |
| `DATA_GAP` | The evaluation horizon elapsed but required subsequent bars are missing/incomplete for reasons other than "not yet elapsed" (see §8) |

**`NO_SNAPSHOT` is not listed here** — per §2, a request with no entry/SL/TP never gets a record, so it cannot have an outcome state at all.

**§5a — Evaluation horizon**: needs a concrete value before implementation. Candidate, reusing an **existing, already-shipped precedent** rather than inventing a new number: `config.yaml`'s own `execution.max_open_trade_hours: 168` (7 days ≈ 42 H4 bars) — the same open-position hygiene horizon the live executor already uses. **This candidate needs your explicit confirmation**, not a silent default — a SHADOW decision's own timeframe may warrant a different horizon than a real open position's.

## 6. Ambiguous-bar tie-break rule

**Locked, reusing the existing precedent verbatim** (no new rule invented): `storage/outcome_tracker.py::auto_close_outcomes()` and `storage/shadow_book.py::auto_close_shadows()` both already use **SL-before-TP when both are touched within the same bar** ("backtest parity" convention, per Gate 0's citation). Phase 15D adopts this exact same rule for consistency with every other outcome-resolution mechanism already in this codebase — not a new, independently-chosen convention.

## 7. Costs (spread/slippage)

**None applied.** SHADOW has zero real execution-cost data (Phase 15A's own already-locked finding on `execution_slippage`). The evaluator compares raw `entry_price`/`stop_loss`/`take_profit` against raw subsequent bar prices only. This limitation is a **disclosed gap**, carried explicitly on every evidence record (mirroring Phase 15A's `divergence_reason`-style disclosure, not a silent omission) — never a fabricated spread/slippage model.

## 8. Missing data handling

If the bars required to reach a `TP_HIT`/`SL_HIT`/`TIMEOUT` determination are missing or gapped (a genuine data hole, not merely "the horizon hasn't elapsed yet"), the outcome is `DATA_GAP` — **never** silently resolved to any of `TP_HIT`/`SL_HIT`/`TIMEOUT`. This mirrors Phase 11's `DataTrustError` precedent (reject rather than guess) and the project's standing rule against inventing a value where real data doesn't exist.

## 9. "Catastrophic divergence" — computed last, not first

**Locked pipeline order** (per your own correction — this is a derived endpoint, never a starting assumption):

```
Decision Snapshot → Resolved Outcome (per request_id) → Outcome Statistics (aggregate, n ≥ some window)
    → Divergence Measurement (vs. a pre-registered expectation) → Catastrophic Divergence: TRUE / FALSE / NOT_YET_ASSESSABLE
```

Proposed structure (candidates below need your confirmation — none of these numbers are silently locked):

- **Outcome Statistics**: over a hypothesis's resolved (non-`UNRESOLVED`, non-`DATA_GAP`) SHADOW outcomes in a window, compute `win_rate = TP_HIT_count / (TP_HIT_count + SL_HIT_count + TIMEOUT_count)` and `n` (the resolved sample size).
- **Expectation baseline**: reuse the hypothesis's own **already-computed backtest-stage evidence** — the Matrix Cell's own `metrics_json`/`stage_a_metrics_json` (`backtest.metrics.Metrics`, confirmed to exist by an earlier Gate 0, reachable from `hypothesis_id` via `research_matrix_cells.source_hypothesis_id`) — specifically its `win_rate`. This reuses an existing, already-governed number instead of inventing a new "expected" value from nothing.
- **Minimum sample size before ANY divergence verdict is possible — LOCKED**: `n ≥ 40`, reusing CLAUDE.md's own D001 threshold (the smallest existing precedent in this codebase for "enough forward samples to say anything"). Below this, the result is **`NOT_YET_ASSESSABLE`** — structurally distinct from `False`, carrying forward exactly the distinction Phase 15B's own Gate 0 first drew (`divergence_assessable=False` ≠ "measured and found absent").
- **Catastrophic threshold — RESERVED / DESIGN CANDIDATE, explicitly NOT locked**: `0.65` (`backtest/policy_health.py`'s own `_HARD_RATIO` for `win_rate`) is carried forward only as a *candidate* numeric starting point for a future Design Gate — **its source and its meaning are deliberately kept separate**. Phase 14's `0.65` is a HARD *degradation*-severity ratio for an already-ACTIVE policy's live behavior; it has never been validated as a statistical definition of *catastrophic divergence* for a SHADOW decision that never executed. Reusing the number without reusing (or separately proving) the meaning would smuggle an unproven definition in through precedent alone.

  **Consequence, locked as part of this spec**: the three-state result for a given hypothesis's divergence check is `NOT_YET_ASSESSABLE` (n < 40) / `FALSE` / `TRUE` — but **`TRUE` may never be produced by any implementation** until a separate, future Design Gate has examined `0.65` (or a different threshold) on its own merits and locked it specifically as the definition of catastrophic divergence. Until that Design Gate happens, a real implementation of this spec may only ever emit `NOT_YET_ASSESSABLE` or `FALSE` — never `TRUE` — regardless of how large `n` grows or how far `win_rate` deviates. This is a deliberate, disclosed limitation, not an oversight: pre-registering a methodology must never silently pre-approve an unproven threshold just because it happens to already exist somewhere else in the codebase.

**168h and n≥40 are LOCKED by this document. The 0.65 catastrophic-divergence ratio is NOT locked — it is recorded here only as a candidate starting point for a future Design Gate**, per the above.

## 10. Virtual vs. real — non-negotiable

Every SHADOW Outcome Evidence record is permanently and explicitly tagged as simulated. It is written to a **new, separate table** — never merged into, joined against as if equivalent to, or made queryable interchangeably with `outcomes` (real/forward-demo trades) or `shadow_signals` (the pre-existing, differently-scoped gate-rejection counterfactual ledger, per Phase 15C's Gate 0 finding that these are genuinely different mechanisms). No existing live table is altered.

## 11. Promotion contract — explicitly deferred

SHADOW Outcome Evidence (this phase) is **not** itself Phase 13's `shadow_record` ({"completed": bool, "diverged_catastrophically": bool}). It is the **source data** from which a *future, separate* translator function could construct a conforming `shadow_record` — once (a) a real implementation of §§1-9 exists, (b) it has accumulated `n` past its minimum sample size for a given hypothesis, and (c) that translator itself is designed and reviewed on its own merits. Building that translator, and touching `backtest/promotion_gate.py` in any way, is explicitly **out of scope** for Phase 15D and for this document.

## 12. Historical data coverage — forward-only, never retroactive

Per Gate 0: `storage/market_bars.py` is a real, timestamp-queryable store, but it is populated only by a manual, operator-run batch script (`scripts/push_bars_to_d1.py`), on no automated schedule — it cannot be assumed to cover an arbitrary past SHADOW decision's exact moment.

**Locked principle**: SHADOW Outcome Evidence only ever evaluates decisions going **forward** from whenever its own (not-yet-chosen) data-capture mechanism begins running. It **never** retroactively evaluates a SHADOW decision already recorded before that mechanism existed, because that decision's own entry/SL/TP snapshot (§2) was never captured and is genuinely, permanently lost — attempting to reconstruct it after the fact by re-running `run_pipeline()` was already rejected in §2. Every pre-existing row in `research_live_identity_requests` from before this mechanism ships will simply never have (and must never be given) a SHADOW Outcome Evidence record.

## 13. Phase boundaries — explicitly NOT decided here

This document deliberately does **not** choose between:

- **(A)** A limited, additive extension to Phase 8C (`backtest/hypothesis_live_request.py`/`storage/hypothesis_live_request.py`) to capture the snapshot (§2) at the moment it already has `report` in hand — reopening an already-accepted phase, a first for this project.
- **(B)** A wholly new, independent boundary/producer that captures the snapshot without modifying any Phase 8C file — mechanism not yet identified (Gate 0 found no existing hook that exposes `report` outside Phase 8C's own function body today).
- **(C)** Any other mechanism the next design conversation surfaces.

That choice, and the matching choice of future-price source (§3's A/B/C), is the **next** conversation's job, informed by this spec — not this document's.

---

## Summary — what this pre-registration actually locks

| Item | Status |
|---|---|
| §5a evaluation horizon | **LOCKED**: 168h (~42 H4 bars), reused from `config.yaml`'s `max_open_trade_hours` |
| §9 minimum sample size `n` | **LOCKED**: `n ≥ 40`, reused from CLAUDE.md's D001 |
| §9 catastrophic ratio threshold | **NOT LOCKED** — `0.65` recorded only as a candidate; `TRUE` may never be emitted by any implementation until a separate future Design Gate validates a threshold's *meaning*, not merely reuses its *number* |
| §13 snapshot-capture mechanism (A/B/C) | **NOT decided here** — reserved for the next conversation |
| §6 ambiguous-bar tie-break | **LOCKED**: SL-before-TP, reused verbatim from `outcome_tracker.py`/`shadow_book.py` |
| §7 costs | **LOCKED**: none applied, disclosed gap |
| §12 historical coverage | **LOCKED**: forward-only, never retroactive |

Pre-registration is not pre-approval of an unproven threshold: this document commits to a measurement *methodology* and its *falsifiable requirements*, while explicitly refusing to let an inherited number stand in for an inherited meaning. A real implementation built from this spec can reach `NOT_YET_ASSESSABLE` or `FALSE` today; it cannot honestly reach `TRUE` until that separate threshold-meaning Design Gate closes.
