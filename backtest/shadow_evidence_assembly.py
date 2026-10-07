"""
backtest/shadow_evidence_assembly.py
---------------------------------------
Hypothesis Discovery Engine -- Evidence Assembly (operator's own locked
Design Gate, 2026-10, "Observe Operational Composition", Phase: Shadow
Operational Composition). The first function that builds a FRESH
backtest.evidence_classification.classify_evidence() input for an
arbitrary hypothesis_id, for the sole purpose of a fresh SHADOW-stage
backtest.promotion_gate.evaluate_promotion_gate() eligibility check.

NO NEW SOURCE OF TRUTH (operator's own locked principle): this module
invents nothing. It composes three already-existing, already-locked
pieces, each read verbatim:

  canonical cell identity  -- backtest.hypothesis_baseline.
      resolve_canonical_baseline() (reused verbatim, same guards and
      same NO_CANONICAL_CELL/CELL_NOT_FOUND/SOURCE_HYPOTHESIS_MISMATCH
      literals already used for the identical dependency pattern in
      backtest.hypothesis_baseline_statistic.
      evaluate_canonical_baseline_statistic() -- imported from there,
      never redefined).

  `significance`  -- backtest.multiple_testing.classify_significance()
      applied to the canonical cell's own `stage_a_p_value` and the
      caller-supplied family's own `planned_n`/`family_alpha` -- the
      SAME composition already used for real, today, at backtest.
      matrix_orchestrator.py:320. classify_significance() ALREADY
      handles `stage_a_p_value is None` by returning "INSUFFICIENT_DATA"
      (its own existing, locked contract) -- this module adds NO second
      guard for that case: classify_evidence() already turns
      "INSUFFICIENT_DATA" into INSUFFICIENT_EVIDENCE, which the SHADOW
      gate's own `_MIN_CLASSIFICATION` set already excludes. Fail-closed
      here is achieved by REUSING that existing chain, never by a
      redundant assembly-level guard that would duplicate it.

  `mission_verdict`  -- the canonical cell's own `stage_b_verdict`,
      read directly, verbatim -- the SAME field backtest.
      hypothesis_baseline_statistic.py already reads for the identical
      cell. No second mission-validation query is ever made.

  `robustness`  -- ALWAYS None in this phase (operator's own locked,
      deliberate scope decision, not a placeholder): no robustness-
      result producer exists anywhere in this engine today, and
      classify_evidence()'s own contract already handles `robustness=
      None` correctly (its absence never upgrades or downgrades
      anything). Building a robustness producer merely to satisfy this
      module would be inventing evidence that does not yet exist.
      Wiring a real one in is a separate, future, independently-
      authorized Design Gate.

MISSING_FAMILY_PARAMS (the one genuinely NEW guard this module adds):
raised as an assembly_state, not an exception, when `family` is None or
is missing `planned_n`/`family_alpha` -- classify_significance() cannot
be called at all without them (there is no honest default `n_trials`/
`family_alpha` to substitute).

FAIL-CLOSED, NO FALLBACK, NO GUESS (operator's own locked principle):
any assembly_state other than ASSEMBLED means `evidence` is None, full
stop -- never a guessed/defaulted evidence dict, and the caller
(backtest.shadow_roster_composer) treats every non-ASSEMBLED state the
same way: exclude this hypothesis_id from the current observe cycle.

NON-NEGOTIABLE (operator's own locked scope boundary): this module
performs NO I/O of its own -- `promotions`/`cell`/`family` are entirely
caller-supplied, exactly as backtest.hypothesis_baseline_statistic.
evaluate_canonical_baseline_statistic() already requires for the
identical dependency. It never imports storage.*, backtest.promotion_
gate (building the promotion_gate CALL is backtest.shadow_roster_
composer's own job, one layer up), backtest.shadow_record, backtest.
shadow_integration, backtest.shadow_divergence_membership, backtest.
shadow_effective_sample, backtest.shadow_observe_orchestrator,
execution.authorization, execution.trade_executor, scheduler.py, or
main.py. No try/except anywhere in this module -- every exception from
resolve_canonical_baseline() (e.g. its own HypothesisBaselineError)
propagates completely unchanged.
"""
from __future__ import annotations

from typing import Any

from backtest.evidence_classification import classify_evidence
from backtest.hypothesis_baseline import CANONICAL_CELL_RESOLVED, resolve_canonical_baseline
from backtest.hypothesis_baseline_statistic import (
    CELL_NOT_FOUND,
    NO_CANONICAL_CELL,
    SOURCE_HYPOTHESIS_MISMATCH,
)
from backtest.multiple_testing import classify_significance

MISSING_FAMILY_PARAMS = "MISSING_FAMILY_PARAMS"
ASSEMBLED = "ASSEMBLED"

__all__ = [
    "ASSEMBLED", "MISSING_FAMILY_PARAMS", "NO_CANONICAL_CELL", "CELL_NOT_FOUND",
    "SOURCE_HYPOTHESIS_MISMATCH", "assemble_shadow_evidence",
]


def assemble_shadow_evidence(
    hypothesis_id: str,
    promotions: list[dict[str, Any]],
    cell: dict[str, Any] | None,
    family: dict[str, Any] | None,
) -> dict[str, Any]:
    """The SOLE entry point. `promotions` is storage.hypothesis_
    promotion.list_promotions_for_hypothesis()'s own return list (or
    structurally equivalent) -- the WHOLE list, never a hand-filtered
    subset, same as every other consumer of resolve_canonical_
    baseline(). `cell` is storage.research_matrix.get_cell() applied to
    whatever cell_id resolve_canonical_baseline() resolves, or None.
    `family` is storage.research_matrix's own family row (needs
    `planned_n` and `family_alpha`) for that cell's own `family_id`, or
    None.

    Returns exactly:
        {"assembly_state": ASSEMBLED, "evidence": <classify_evidence() result>}
    or, on any fail-closed guard:
        {"assembly_state": NO_CANONICAL_CELL | CELL_NOT_FOUND |
                            SOURCE_HYPOTHESIS_MISMATCH | MISSING_FAMILY_PARAMS,
         "evidence": None}

    Pure -- no storage read or write, no network call, no randomness.
    FAIL-FAST: any exception from resolve_canonical_baseline() (e.g. a
    structural HypothesisBaselineError) propagates completely
    unchanged."""
    baseline = resolve_canonical_baseline(hypothesis_id, promotions)
    if baseline["canonicity_state"] != CANONICAL_CELL_RESOLVED:
        return {"assembly_state": NO_CANONICAL_CELL, "evidence": None}

    if cell is None:
        return {"assembly_state": CELL_NOT_FOUND, "evidence": None}

    if cell.get("source_hypothesis_id") != hypothesis_id:
        return {"assembly_state": SOURCE_HYPOTHESIS_MISMATCH, "evidence": None}

    if family is None or family.get("planned_n") is None or family.get("family_alpha") is None:
        return {"assembly_state": MISSING_FAMILY_PARAMS, "evidence": None}

    significance = classify_significance(
        cell.get("stage_a_p_value"), family["planned_n"], family["family_alpha"],
    )
    evidence = classify_evidence(
        significance=significance, mission_verdict=cell.get("stage_b_verdict"), robustness=None,
    )
    return {"assembly_state": ASSEMBLED, "evidence": evidence}
