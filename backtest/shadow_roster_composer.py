"""
backtest/shadow_roster_composer.py
---------------------------------------
Hypothesis Discovery Engine -- Roster-to-Entries Composer (operator's
own locked Design Gate, 2026-10, "Observe Operational Composition",
Phase: Shadow Operational Composition). Turns a list of active SHADOW
roster entries into exactly the `entries` shape backtest.
shadow_observe_orchestrator.run_shadow_observe_cycle() (commit 7b8b9fb)
-- and, one layer below it, backtest.shadow_observation.
run_shadow_cycle() -- already require: {"roster_entry", "fresh_
promotion_gate_result"}.

"COMPOSER REVIEWS; IT NEVER DECIDES" (operator's own locked framing,
verbatim): this module never re-implements any part of backtest.
promotion_gate.evaluate_promotion_gate()'s own eligibility logic. For
each roster entry, it assembles fresh evidence (backtest.shadow_
evidence_assembly.assemble_shadow_evidence(), via a caller-supplied
resolver -- this module performs no storage I/O of its own to build
promotions/cell/family itself) and calls evaluate_promotion_gate(
target_stage=SHADOW, ...) FRESH, then reads its `eligibility` verdict
verbatim. `cross_symbol_confirmed=False` is passed unconditionally --
verified directly from backtest.promotion_gate.py's own source that
this argument is never even read for target_stage=SHADOW (only LIMITED/
ACTIVE check it), so its value here is immaterial, never a design
decision needing an abstraction of its own.

EXCLUDED, NEVER REMOVED (operator's own locked rule, reused verbatim
from backtest.live_roster.check_current_eligibility()'s own already-
locked Phase 15A principle: "a failed fresh eligibility check means
skip this cycle only"): a roster entry whose evidence assembly fails
(any assembly_state != ASSEMBLED) OR whose fresh SHADOW eligibility
check comes back NOT_ELIGIBLE is simply left OUT of the returned
`entries` list for THIS cycle -- this module never calls backtest.
live_roster.remove_from_roster()/storage.live_roster.try_remove(), and
never persists a rejection record anywhere. The roster itself is never
touched. The exclusion reason is only ever logged (see below), never
written to storage.

OBSERVABILITY (operator's own locked decision, this Design Gate):
every exclusion is logged once, at INFO level, naming the hypothesis_id
and the reason (the assembly_state, or the fresh promotion_gate_result's
own `reasons` list) -- via the SAME utils.logger.get_logger() convention
already used throughout this codebase (scheduler.py, main.py, every
backtest/*.py module with real I/O). No new logging framework, no
persistence of these log lines anywhere.

NON-NEGOTIABLE (operator's own locked scope boundary): this module
never imports storage.live_roster or backtest.live_roster (roster
discovery/enumeration is the EXTERNAL caller's job -- `roster_entries`
is caller-supplied), never imports backtest.shadow_record, backtest.
shadow_integration, backtest.shadow_divergence_membership, backtest.
shadow_effective_sample, backtest.shadow_observe_orchestrator,
execution.authorization, execution.trade_executor, scheduler.py, or
main.py. It performs no storage read or write, no network call. No
try/except anywhere in this module -- every exception from evidence
assembly or evaluate_promotion_gate() propagates completely unchanged.
It never decides, enforces, or references cadence in any way.
"""
from __future__ import annotations

from typing import Any, Callable

from backtest.promotion_gate import ELIGIBLE, SHADOW, evaluate_promotion_gate
from backtest.shadow_evidence_assembly import ASSEMBLED
from utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["compose_shadow_observe_entries"]


def compose_shadow_observe_entries(
    roster_entries: list[dict[str, Any]],
    evidence_resolver: Callable[[str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """The SOLE entry point. `roster_entries` is a list of storage.
    live_roster row dicts (each carrying its own `hypothesis_id`) --
    enumerated by the EXTERNAL caller (e.g. storage.live_roster.
    list_active_entries()), never by this module itself.

    `evidence_resolver` is a caller-supplied callable: hypothesis_id ->
    backtest.shadow_evidence_assembly.assemble_shadow_evidence()'s own
    return dict (or structurally equivalent) -- this module never
    constructs `promotions`/`cell`/`family` itself; fetching those is
    entirely the resolver's own job.

    Returns a list of {"roster_entry": ..., "fresh_promotion_gate_result": ...}
    -- exactly run_shadow_cycle()'s own `entries` shape -- containing
    ONLY the roster entries whose fresh evidence assembled AND whose
    fresh SHADOW-stage evaluate_promotion_gate() call came back
    ELIGIBLE. Every other roster entry is excluded from the returned
    list (logged once, at INFO level) -- never removed from the roster,
    never recorded as a rejection anywhere.

    FAIL-FAST: any exception from `evidence_resolver` or evaluate_
    promotion_gate() propagates completely unchanged."""
    entries: list[dict[str, Any]] = []
    for roster_entry in roster_entries:
        hypothesis_id = roster_entry["hypothesis_id"]
        assembly = evidence_resolver(hypothesis_id)
        if assembly["assembly_state"] != ASSEMBLED:
            logger.info(
                f"shadow_roster_composer: excluding hypothesis_id={hypothesis_id!r} from this "
                f"observe cycle -- evidence assembly_state={assembly['assembly_state']!r}."
            )
            continue

        fresh_promotion_gate_result = evaluate_promotion_gate(
            target_stage=SHADOW, evidence=assembly["evidence"], cross_symbol_confirmed=False,
        )
        if fresh_promotion_gate_result["eligibility"] != ELIGIBLE:
            logger.info(
                f"shadow_roster_composer: excluding hypothesis_id={hypothesis_id!r} from this "
                f"observe cycle -- not SHADOW-eligible: {fresh_promotion_gate_result['reasons']!r}."
            )
            continue

        entries.append({"roster_entry": roster_entry, "fresh_promotion_gate_result": fresh_promotion_gate_result})
    return entries
