# Phase 15D — Closure Record

Companion record to `docs/PHASE_15D_SHADOW_OUTCOME_DIVERGENCE_EVIDENCE.md`
(the pre-registration spec, locked and committed at `e0a7f73`). That
document fixed *what* Phase 15D measures and *why*, before any
implementation existed. This record documents what was actually built
against that spec, what was proven, and what explicitly was not.

**Status at the time of writing this record:**

| Property | Status |
|---|---|
| DESIGNED | YES — pre-registration spec + two implementation Design Gates (Snapshot Boundary, Outcome Resolution Contract), all locked in conversation before code |
| IMPLEMENTED | YES |
| TESTED | YES |
| ACCEPTED | PENDING — this record is the basis for that decision, not the decision itself |
| WIRED | **NO** |
| PROVEN | **NO** |
| Commit | NOT AUTHORIZED at time of writing |
| Push | NOT AUTHORIZED at time of writing |

---

## 1. Files delivered (working tree only, not yet committed)

**9 working-tree paths in total: 1 modified, 8 new.** The 8 new paths split into 7 implementation/test files (this phase's actual deliverable, listed in the table below) and 1 separate governance/documentation artifact — this closure record itself (`docs/PHASE_15D_CLOSURE_RECORD.md`), which is explicitly NOT part of the implementation scope: it was written only after implementation and all testing in §4-§6 were already complete, to document that work, not to extend it.

| File | Nature | Purpose |
|---|---|---|
| `backtest/hypothesis_live_request.py` | **MODIFIED** (+61/−2 lines) | Phase 8C controlled extension (Governance Decision: approved as an additive extension, not a formal reopening). `evaluate_live_identity_request()` now also returns `decision_snapshot` — `None`, or an all-or-nothing dict (`bar_time`, `side`, `entry_price`, `stop_loss`, `take_profit`) captured from the same `report` the function already computes, at the point it already has it in hand. Every pre-existing field/behavior is unchanged (proven by the three pre-existing test files passing with zero edits). |
| `storage/shadow_decision_snapshot.py` | NEW (implementation) | D1 persistence for `research_shadow_decision_snapshots` (1:1 by `request_id`, `bar_time NOT NULL`, all-or-nothing row shape). |
| `backtest/shadow_decision_snapshot.py` | NEW (implementation) | `capture_decision_snapshot(live_identity_request_result)` — idempotent capture; `None` input ⇒ no write; genuine storage failure ⇒ `ShadowDecisionSnapshotError` (never conflated with "no snapshot"). |
| `backtest/shadow_outcome_resolver.py` | NEW (implementation) | `resolve_decision_outcome(snapshot, *, base_config)` — pure, stateless resolver implementing the locked Outcome Resolution Contract (§3 below). No storage read/write of its own. |
| `tests/test_hypothesis_live_request_snapshot.py` | NEW (test) | Contract tests for the 8C extension — all-or-nothing invariant, side derivation, `bar_time` ≠ capture time, coexistence with every pre-existing field. |
| `tests/test_shadow_decision_snapshot_storage.py` | NEW (test) | Storage-layer tests — insert shape, DB-level `request_id` uniqueness. |
| `tests/test_shadow_decision_snapshot.py` | NEW (test) | Domain-layer tests — idempotency, `None` contract, storage-failure semantics, structural isolation. |
| `tests/test_shadow_outcome_resolver.py` | NEW | Resolver contract tests (18 required scenarios + extras — see §4). |

**Diff scope, confirmed by `git diff --stat` immediately before writing this record:** exactly the one modified file above (`git diff --stat` on tracked files shows only `backtest/hypothesis_live_request.py`); `git status --porcelain` shows the 7 implementation/test files above as untracked, PLUS this closure record itself as an 8th untracked path once it was written — 9 working-tree paths in total, none of them committed. Zero diff on every protected file (`config.yaml`, `config/engines.yaml`, `config/symbols.yaml`, `research/results/registry.json`), `main.py`, `scheduler.py`, `execution/*.py`, `backtest/promotion_gate.py`, `backtest/policy_health.py`, `backtest/live_roster.py`, `backtest/shadow_observation.py`, `backtest/shadow_evidence.py`, `backtest/execution_attribution.py`, `storage/outcome_tracker.py`, `storage/shadow_book.py`, and the three pre-existing `tests/test_hypothesis_live_request*.py` files.

---

## 2. Governance Decision (recorded, not re-litigated here)

The operator's own ruling: the Phase 8C extension above is a **controlled extension** of an already-accepted phase, not a formal reopening — because it changes no existing decision behavior (`final_verdict`/`decision`/`decision_reason`/`gate_decision`/`request_id`/`hypothesis_id` are all unaffected, proven by the unmodified pre-existing test suite), changes no execution behavior (no broker/authorization/TradeExecutor path touched), and is purely additive/observability-only. This decision is recorded here for traceability; it is not reopened by this closure record.

---

## 3. Locked contracts

### 3.1 Snapshot contract (8C extension)
- All-or-nothing: `decision_snapshot` is `None`, or all four of `bar_time`/`entry_price`/`stop_loss`/`take_profit` plus a resolvable `side` are present together — never partial. A partial state raises `HypothesisExecutionError`.
- `side` derived only from `report["confluence"]["vote"]["winning_bias"]` (the same mapping `execution/trade_executor.py` uses privately, duplicated deliberately — never imported, preserving Phase 8C's own non-negotiable #9).
- `bar_time` is `T_D` — the market bar timestamp the original decision was computed from, never a capture/wall-clock substitute.
- No re-run of `main.run_pipeline()` to recover a snapshot after the fact — rejected as architecturally unsound (a second call is not guaranteed to reproduce the same live-market/provider state).

### 3.2 Outcome Resolution Contract (`docs/PHASE_15D_SHADOW_OUTCOME_DIVERGENCE_EVIDENCE.md`, as refined)
Five exhaustive, mutually exclusive states: `TP_HIT`, `SL_HIT`, `TIMEOUT`, `DATA_GAP`, `NOT_YET_ASSESSABLE`.

- **Chronological gap-before-hit invariant** (the operator's own correction to an earlier, flawed draft): bars are walked in ascending order; a `DATA_GAP` found before any later hit terminates evaluation immediately — no hit beyond an unresolved gap is ever valid. A hit found before any gap is final regardless of what follows.
- **Strict `T_D` exclusion**: only `bar_time > T_D`; a bar timestamped exactly `T_D` is never evidence.
- **`expected_freq`**: a fixed, explicit per-timeframe constant (`H1`/`H4`/`D1` only; anything else raises `ShadowOutcomeResolverError`) — never estimated from data, no tolerance beyond the exact value.
- **168h horizon**: real wall-clock elapsed time from `T_D` (`(T_now − T_D) ≥ 168h`), reusing `storage/outcome_tracker.py::_open_age_hours()`'s own arithmetic exactly — never a bar count.
- **SL-before-TP same-bar tie-break**: reused verbatim from `storage/outcome_tracker.py`/`storage/shadow_book.py`.
- **`resolved_bar_time`**: one meaning only — the bar that resolved the outcome. Non-`None` iff the outcome is `TP_HIT`/`SL_HIT`; `None` for the other three states (none of them has a single causal bar).
- **No costs**: no spread/slippage/synthetic price anywhere in the comparison.
- **Provider failure ≠ `DATA_GAP`**: `core.data_providers.DataFetchError` propagates completely unchanged — never caught, never reinterpreted as a market-data verdict.
- **Internal↔provider symbol translation**: read transiently from `base_config["data"]["twelve_data_symbols"]` (the same structure `build_governed_config()` already uses) — never persisted, never alters the snapshot's own stored (internal) symbol identity.
- **`0.65` catastrophic-divergence ratio remains `RESERVED/CANDIDATE`, not locked.** `diverged_catastrophically` is outside this contract entirely — this resolver never computes or returns it.
- **No persistence**: `resolve_decision_outcome()` is a pure function; it performs no storage write of its own (an explicit scope boundary, not an oversight — aggregating/storing resolved outcomes is a separate, future decision).

---

## 4. Tests and numbers

| Suite | Result |
|---|---|
| `tests/test_shadow_outcome_resolver.py` (targeted) | **23/23 passed** |
| Integrated batch (3 pre-existing 8C files, unmodified + all new 15D snapshot/resolver test files + `test_shadow_observation.py`) | **108/108 passed** |
| Ruff (`backtest/hypothesis_live_request.py`, both new `storage/`/`backtest/` modules, all 4 new test files) | **clean** |
| Full Suite #1 (original) | `4760 passed, 2 skipped, 1 failed` — see §5 |
| Full Suite #1 (retry, per the operator's own explicit "exactly one retry" instruction) | `4761 passed, 2 skipped, 0 failed` — **CLEAN** |
| Full Suite #2 | `4760 passed, 3 skipped, 0 failed` — **CLEAN** |
| Full Suite #1-retry vs #2 discrepancy | 1-test passed/skipped swing, explained in §5 — **0 failed in both, no regression** |

The 18 operator-specified resolver scenarios are covered exactly (bar at `T_D` ignored; provider-order-independent sorting; gap-before-hit vs. hit-before-gap; exact-`expected_freq` boundary vs. +1-second gap; SL-before-TP on both sides; `NOT_YET_ASSESSABLE`/`TIMEOUT`/`DATA_GAP` separation at the horizon; provider-exception propagation; unsupported-timeframe error; symbol-mapping non-mutation; no `captured_at`; no sibling-phase/execution/scheduler/storage coupling; no spread/slippage; no divergence logic), plus additional structural/adversarial tests beyond the required 18.

---

## 5. The original Full Suite #1 failure and its resolution

**Original Full Suite #1**: `tests/test_matrix_operational_validation.py::test_four_concurrent_workers_never_double_execute_stage_a_or_stage_b` failed with `ValueError("update_cell: cell '...' is already in terminal status 'VALIDATED'...")`.

**Investigation**:
- Zero diff exists on `backtest/matrix_orchestrator.py`, `tests/test_matrix_operational_validation.py`, or anything either depends on — this test and its subject module are completely outside Phase 15D's own diff.
- Re-run in isolation 3 times (before any code change, before any suite re-run): **fail → pass → pass** — a non-deterministic outcome from byte-identical code proves a genuine, pre-existing race condition in the 4-thread concurrency test itself, not a regression introduced by this phase.
- Per the operator's own explicit instruction, no fix was attempted and no further isolated re-runs were performed. The single authorized remedy — one full-suite re-run — was executed and came back clean (`0 failed`).

**This is recorded as a known, pre-existing, unrelated flaky test — not fixed, not hidden, not used to excuse a real failure.** It remains open for whoever next works on `backtest/matrix_orchestrator.py`'s own concurrency handling; it is explicitly out of Phase 15D's scope to fix.

## 6. The Full Suite #1(retry)-vs-#2 skip-count discrepancy

**Diagnosis** (by direct source inspection, no blind re-run): `tests/test_provider_chains.py::test_multi_tf_resample_drops_a_still_forming_h4_bucket` contains:
```python
now = pd.Timestamp.now(tz="UTC")
h4_boundary = now.floor("4h")
if now - h4_boundary < pd.Timedelta(hours=1):
    pytest.skip("too close to an H4 boundary for a deterministic mid-period base")
```
This test self-skips based on real wall-clock proximity to an H4 UTC boundary (00:00/04:00/08:00/12:00/16:00/20:00). The two ~11.5-minute full-suite runs, separated by several more minutes of intervening work, plausibly straddled such a boundary. The only other conditional (non-`fastapi`-availability) skip in the suite, `tests/test_wyckoff_engine_v2.py:251`, uses a **fixed** `seed=42` and is therefore deterministic across runs — ruled out directly by inspection.

**Conclusion**: an environmental, time-of-execution-dependent skip, unrelated to any Phase 15D file, confirmed by direct code reading rather than assumption. Both full-suite runs show `0 failed`; the identical combined total (4763) confirms no test vanished or was added unexpectedly.

---

## 7. What this phase proved, and what it explicitly did not

**Proved:**
- A decision snapshot can be captured, at the exact moment of a real SHADOW decision, with a byte-for-byte-preserved pre-existing Phase 8C contract around it.
- A forward-only, look-ahead-safe, chronologically-correct outcome resolver exists and is fully tested against the locked contract, including the gap-before-hit correctness invariant.
- Both pieces are completely isolated from execution, scheduler, promotion-gate, and every sibling phase — confirmed structurally by tests, not merely by convention.

**Explicitly NOT proved or built (WIRED=NO, PROVEN=NO):**
- No real SHADOW decision has ever actually had its outcome resolved by this code outside a test — nothing calls `resolve_decision_outcome()` from any live or scheduled path.
- No statistical edge has been measured — zero real outcome-aggregation exists yet.
- The `0.65` catastrophic-divergence ratio remains unvalidated as a *meaning*, not just unlocked as a *number* — no implementation may emit `diverged_catastrophically=True` until a separate, future Design Gate resolves this.
- No connection to `backtest/promotion_gate.py` exists or was attempted — `evaluate_promotion_gate(target_stage="LIMITED", ...)` remains structurally unreachable, exactly as before this phase.
- No persistence of resolved outcomes, no aggregation, no LIMITED execution, no authorization wiring, no broker path of any kind.

---

## 8. Next phase boundary (not started, not designed here)

Explicitly deferred, each requiring its own future Design Gate:
1. Whether/how to persist and aggregate `resolve_decision_outcome()` results per hypothesis (a new decision, not assumed by this phase).
2. The catastrophic-divergence threshold's actual *meaning* (not just a recycled number) — a dedicated Design Gate, independent of this phase.
3. The translator from aggregated outcome evidence to Phase 13's own `shadow_record` shape — out of scope here by the pre-registration spec's own §11.
4. Any operational trigger (periodic or otherwise) that would ever call this resolver on real data — Phase 15D's own modules remain, by design, unreachable from any live or scheduled entry point.

Commit/push for this phase's files is a separate decision, to be made after this record is reviewed.
