"""
backtest/shadow_observe_orchestrator.py
---------------------------------------
Hypothesis Discovery Engine -- SHADOW Observe Orchestrator (operator's
own locked Design Gate, 2026-10, HEAD=84e428f). The first production
composition of Phase 15A (backtest.shadow_observation.run_shadow_cycle())
and Phase 15D (backtest.shadow_decision_snapshot.capture_decision_
snapshot()): for every entry this cycle actually observes, capture its
decision snapshot into research_shadow_decision_snapshots -- nothing
else.

EVIDENCE COLLECTOR ONLY, NEVER A DECISION LAYER (operator's own locked
framing, verbatim): this orchestrator "يجمع ويُسجل Observe evidence
فقط؛ لا يعرف promotion، ولا الإحصاء، ولا membership، ولا execution، ولا
يقرر أي شيء." It does not know what a `shadow_record`, an n_eff/k_eff,
or a membership row even is.

PROMOTION_GATE DEPENDENCY RESOLUTION (operator's own locked decision):
run_shadow_cycle() itself requires `fresh_promotion_gate_result` baked
into each entry of its own `entries` parameter -- that is an EXISTING,
already-locked dependency of run_shadow_cycle()'s own input contract,
not something this orchestrator introduces. This module NEVER calls
backtest.promotion_gate.evaluate_promotion_gate() and NEVER imports
backtest.promotion_gate at all -- building/refreshing `entries`
(including each entry's own fresh_promotion_gate_result) is entirely
the EXTERNAL CALLER's responsibility, exactly mirroring how cadence is
already an external caller's responsibility (backtest.shadow_
observation's own locked "no clock/scheduling logic here" rule).

    External caller
        |
        +-- builds/refreshes entries (incl. fresh_promotion_gate_result)
        v
    run_shadow_observe_cycle(entries, base_config)
        |
        +-- run_shadow_cycle(entries, base_config)   -- Phase 15A, unchanged
        |        |
        |        +-- evaluate_live_identity_request() per eligible entry
        |
        +-- capture_decision_snapshot() per observed=True result -- Phase 15D, unchanged
                 |
                 v
        research_shadow_decision_snapshots

ONE CYCLE = ONE CALL (operator's own locked semantics): this function
contains no clock/sleep/retry/scheduling logic of its own -- cadence is
entirely the external caller's decision, exactly as backtest.
shadow_observation.run_shadow_cycle() already locks for itself. An empty
`entries` list is a legitimate, SUCCESSFUL cycle with zero evidence --
never an error, never a skip.

FAIL-FAST, NO ISOLATION (operator's own locked decision, the SAME
precedent run_shadow_cycle() and capture_decision_snapshot() already
each independently hold): no try/except anywhere in this module. Any
exception raised by run_shadow_cycle() or capture_decision_snapshot()
propagates completely unchanged -- no partial result, no isolation
between entries. External alerting/reporting on a raised exception is
the CALLER's responsibility (e.g. a future scheduler wrapper, mirroring
main.py/scheduler.py's own existing _send_error_once pattern) -- never
built inside this module.

IDEMPOTENCY (inherited, not re-implemented): capture_decision_snapshot()
is already idempotent per request_id (a repeat call returns the
existing row, never a duplicate, never an error) -- this module adds no
idempotency logic of its own; calling run_shadow_observe_cycle() twice
with the same `entries` is already safe for exactly that reason.

"CYCLE COMPLETED" != "EVIDENCE PRODUCED" (operator's own locked
distinction): a successful return (no exception raised) is the
execution-completion fact; `captured_count` is the evidence-production
fact. `captured_count == 0` on a successful return is NOT a failure --
every entry may have failed this cycle's fresh eligibility check, or
had no decision_snapshot (confluence did not pass) -- both are honest,
ordinary outcomes. This module deliberately returns NO `completed`/
`success` key at all: the absence of a raised exception already IS that
fact: adding a flag that merely restates it would be a redundant, driftable
second source of the same truth (operator's own locked correction).

NON-NEGOTIABLE (operator's own locked scope boundary): this module never
imports backtest.promotion_gate, backtest.shadow_outcome_evidence,
backtest.shadow_record, backtest.shadow_integration, backtest.shadow_
divergence_membership, backtest.shadow_effective_sample, storage.
live_roster, backtest.live_roster, execution.authorization, execution.
trade_executor, scheduler.py, or main.py. It performs no storage read
or write of its own -- every read/write happens inside the two functions
it composes, unchanged. It never discovers the roster itself, never
decides cadence, never retries, never isolates a failing entry, and
never computes anything statistical. No try/except anywhere in this
module -- every exception from every call it makes propagates completely
unhandled, by design.
"""
from __future__ import annotations

from typing import Any

from backtest.shadow_decision_snapshot import capture_decision_snapshot
from backtest.shadow_observation import run_shadow_cycle

__all__ = ["run_shadow_observe_cycle"]


def run_shadow_observe_cycle(*, entries: list[dict[str, Any]], base_config: dict[str, Any]) -> dict[str, Any]:
    """The SOLE entry point. `entries` is exactly run_shadow_cycle()'s own
    `entries` parameter (a list of {"roster_entry", "fresh_promotion_
    gate_result"}, already freshly built by the external caller) -- never
    built, enumerated, or refreshed by this function itself.

    For every result run_shadow_cycle() returns with `observed=True`,
    calls capture_decision_snapshot(result["live_identity_request"])
    exactly once, in the same order. `observed=False` results are never
    passed to capture_decision_snapshot() at all.

    Returns exactly:
        {"entries_count": int, "observed_count": int, "captured_count": int,
         "results": [{**run_shadow_cycle() result, "decision_snapshot_captured": bool}, ...]}
    `decision_snapshot_captured` is True iff capture_decision_snapshot()
    returned a non-None row (a snapshot now exists, whether newly
    inserted or already idempotently present) -- False for every
    observed=False result AND for an observed=True result whose
    decision_snapshot was None (confluence did not pass).

    FAIL-FAST: any exception from run_shadow_cycle() or capture_
    decision_snapshot() propagates completely unchanged. No partial
    result is ever returned."""
    cycle_results = run_shadow_cycle(entries=entries, base_config=base_config)

    results = []
    for result in cycle_results:
        snapshot = capture_decision_snapshot(result["live_identity_request"]) if result["observed"] else None
        results.append({**result, "decision_snapshot_captured": snapshot is not None})

    return {
        "entries_count": len(entries),
        "observed_count": sum(1 for r in cycle_results if r["observed"]),
        "captured_count": sum(1 for r in results if r["decision_snapshot_captured"]),
        "results": results,
    }
