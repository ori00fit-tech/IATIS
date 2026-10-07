"""
backtest/shadow_live_evidence_resolver.py
---------------------------------------
Hypothesis Discovery Engine -- Live Evidence Resolver (operator's own
locked Design Gate, 2026-10, "External Scheduler/Runner", Phase:
External Shadow Observe Runner). The ONE place that fetches
`promotions`/`cell`/`family` for a hypothesis_id and hands them to
backtest.shadow_evidence_assembly.assemble_shadow_evidence() -- exactly
the `evidence_resolver` callable backtest.shadow_roster_composer.
compose_shadow_observe_entries() already requires.

NO NEW STORAGE LOGIC (operator's own locked principle): every read here
is an already-existing, already-public storage getter, called exactly
as every other consumer already calls it:

    storage.hypothesis_promotion.list_promotions_for_hypothesis(hypothesis_id)
            |
            v
    backtest.hypothesis_baseline.resolve_canonical_baseline(hypothesis_id, promotions)
            |
            v
    storage.research_matrix.get_cell(cell_id)        -- cell_id is None -> get_cell(None) -> None
            |
            v
    storage.research_matrix.get_family(cell["family_id"])
            |
            v
    backtest.shadow_evidence_assembly.assemble_shadow_evidence(hypothesis_id, promotions, cell, family)

No duplicate canonical-cell resolution logic is written here --
resolve_canonical_baseline() is called exactly once, purely to learn
WHICH cell_id to fetch; assemble_shadow_evidence() calls it again
internally to actually assemble evidence (a cheap, pure, no-I/O
recomputation -- the SAME accepted pattern already used across this
engine, e.g. backtest.shadow_outcome_evidence's own canonical-identity
resolution).

`storage.research_matrix.get_cell(None)` (when resolve_canonical_
baseline() did not resolve a cell_id) is never special-cased here --
its own underlying `SELECT ... WHERE cell_id=?` with a None parameter
simply matches no row and returns None, exactly as `cell=None` should
reach assemble_shadow_evidence() for its own CELL_NOT_FOUND/NO_
CANONICAL_CELL guards to handle.

NON-NEGOTIABLE (operator's own locked scope boundary): this module
never imports backtest.promotion_gate, backtest.shadow_roster_composer,
backtest.shadow_observe_orchestrator, backtest.shadow_record, backtest.
shadow_integration, backtest.shadow_divergence_membership, backtest.
shadow_effective_sample, storage.live_roster, backtest.live_roster,
execution.authorization, execution.trade_executor, scheduler.py, or
main.py. No try/except anywhere in this module -- every exception from
every storage call or from resolve_canonical_baseline()/assemble_
shadow_evidence() propagates completely unchanged.
"""
from __future__ import annotations

from typing import Any

from backtest.hypothesis_baseline import resolve_canonical_baseline
from backtest.shadow_evidence_assembly import assemble_shadow_evidence
from storage.hypothesis_promotion import list_promotions_for_hypothesis
from storage.research_matrix import get_cell, get_family

__all__ = ["resolve_live_shadow_evidence"]


def resolve_live_shadow_evidence(hypothesis_id: str) -> dict[str, Any]:
    """The SOLE entry point. Fetches `promotions`/`cell`/`family` for
    `hypothesis_id` from storage (the only I/O this module performs),
    then returns assemble_shadow_evidence()'s own return dict verbatim
    -- {"assembly_state": ..., "evidence": ...}.

    FAIL-FAST: any exception from any storage call, from resolve_
    canonical_baseline(), or from assemble_shadow_evidence() propagates
    completely unchanged."""
    promotions = list_promotions_for_hypothesis(hypothesis_id)
    baseline = resolve_canonical_baseline(hypothesis_id, promotions)
    cell = get_cell(baseline["cell_id"])
    family = get_family(cell["family_id"]) if cell else None
    return assemble_shadow_evidence(hypothesis_id, promotions, cell, family)
