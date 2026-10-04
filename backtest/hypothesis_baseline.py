"""
backtest/hypothesis_baseline.py
---------------------------------------
Hypothesis Discovery Engine -- Expected-Baseline Canonicity (operator's
own locked Design Gate).

THE PROBLEM, precisely (operator's own locked Gate 0 finding):
`research_matrix_cells.source_hypothesis_id` is deliberately non-unique
-- a hypothesis may be legitimately re-executed under a different
research_code_commit or data_provider, producing a second, coexisting
cell. No function anywhere resolves "the" cell for a hypothesis_id from
storage (backtest.hypothesis_execution._canonical_cell_for_hypothesis()
is an in-memory fingerprint re-derivation for drift-checking only, never
a storage query). This module is that resolution -- but ONLY for an
already-promoted hypothesis; there is no fallback for an unpromoted one.

CRITICAL CORRECTION the operator's own quick check surfaced before
locking this contract: "a Promotion record exists" and "this hypothesis
was promoted" are NOT the same fact. backtest.hypothesis_promotion's own
decision vocabulary is deliberately three-valued -- PROMOTED,
NOT_PROMOTED, BLOCKED -- and record_promotion() persists UNCONDITIONALLY
regardless of which one results (research_hypothesis_promotions is an
append-only FORENSIC ledger of every promotion evaluation ever made, not
a table of successful promotions only). This module filters to
decision == PROMOTED exclusively; a NOT_PROMOTED or BLOCKED row is never
treated as evidence of a canonical baseline.

MULTIPLICITY, handled precisely: the SAME (hypothesis_id, mission_id,
cell_id) triple can legitimately produce several PROMOTED rows over
time (re-evaluated as the forensic ledger grows, per
compute_promotion_id()'s own locked append-only-on-governance-change
rule) -- that is NOT ambiguity, since they all agree on the same
cell_id. Ambiguity exists only when DIFFERENT cell_ids each reach
PROMOTED for the same hypothesis_id.

NO INVENTED TIE-BREAK (operator's own locked decision): unlike Phase
15E's "latest observation wins" (an already-established precedent for
that different table), there is no existing precedent anywhere for
"most recent Promotion wins" -- so none is invented here. Multiple
distinct PROMOTED cell_ids resolve to an explicit, named ambiguity
state, listing the conflicting cell_ids, never a silent pick.

MECHANISM (locked): a pure function over a CALLER-SUPPLIED list of
Promotion records -- no storage read of its own. The caller already has
(or fetches via storage.hypothesis_promotion.list_promotions_for_
hypothesis()) the promotions list; this module only resolves canonicity
from it.

SCOPE BOUNDARY (locked): resolves WHICH cell_id is canonical only --
extracting that cell's own profit_factor/win_rate/trade_count is a
separate, later, explicitly deferred step. Also explicitly deferred:
the two-sample live-vs-baseline comparator, 0.65's meaning as a
catastrophic-divergence threshold, any n/threshold/Bonferroni, any
promotion-lifecycle mutation, execution, scheduler wiring.

NON-NEGOTIABLE (operator's own locked scope boundary): this module
never imports storage.hypothesis_promotion or any other storage module
-- the caller already has the promotions list in hand. It never imports
backtest.promotion_gate, backtest.policy_health,
backtest.execution_attribution, execution.authorization,
execution.trade_executor, storage.outcome_tracker, storage.shadow_book,
scheduler.py, or main.py. It reuses backtest.hypothesis_promotion's own
PROMOTED constant verbatim, never redefines it.
"""
from __future__ import annotations

from typing import Any

from backtest.hypothesis_promotion import PROMOTED

CANONICAL_CELL_RESOLVED = "CANONICAL_CELL_RESOLVED"
NO_PROMOTION_EXISTS = "NO_PROMOTION_EXISTS"
MULTIPLE_PROMOTIONS_AMBIGUOUS = "MULTIPLE_PROMOTIONS_AMBIGUOUS"

__all__ = [
    "CANONICAL_CELL_RESOLVED", "NO_PROMOTION_EXISTS", "MULTIPLE_PROMOTIONS_AMBIGUOUS",
    "HypothesisBaselineError", "resolve_canonical_baseline",
]


class HypothesisBaselineError(Exception):
    """Structural misuse only -- a promotion dict in the supplied list
    whose own hypothesis_id does not match the hypothesis_id argument.
    Never raised for an ordinary resolution result --
    MULTIPLE_PROMOTIONS_AMBIGUOUS is a legitimate, descriptive finding,
    not an error."""


def resolve_canonical_baseline(
    hypothesis_id: str,
    promotions: list[dict[str, Any]],
) -> dict[str, Any]:
    """The SOLE entry point. `promotions` is the WHOLE list of Promotion
    records for this hypothesis_id (as storage.hypothesis_promotion.
    list_promotions_for_hypothesis() returns, or structurally equivalent)
    -- never a hand-filtered subset, since this function itself does the
    PROMOTED-only filtering and the distinct-cell_id collapsing.

    Returns exactly: {hypothesis_id, canonicity_state, cell_id,
    conflicting_cell_ids}. `cell_id` is non-None iff canonicity_state is
    CANONICAL_CELL_RESOLVED. `conflicting_cell_ids` is non-None iff
    canonicity_state is MULTIPLE_PROMOTIONS_AMBIGUOUS (sorted, every
    distinct cell_id that reached PROMOTED).

    Pure -- no storage read or write, no network call, no randomness,
    no invented tie-break among several PROMOTED cell_ids.
    """
    for promotion in promotions:
        if promotion["hypothesis_id"] != hypothesis_id:
            raise HypothesisBaselineError(
                f"resolve_canonical_baseline: promotion {promotion.get('promotion_id')!r} has "
                f"hypothesis_id {promotion['hypothesis_id']!r}, which does not match the "
                f"hypothesis_id argument {hypothesis_id!r} -- comparing promotions across "
                f"unrelated hypotheses is never a valid resolution."
            )

    distinct_promoted_cell_ids = sorted({
        promotion["cell_id"] for promotion in promotions if promotion["decision"] == PROMOTED
    })

    if len(distinct_promoted_cell_ids) == 0:
        return {
            "hypothesis_id": hypothesis_id,
            "canonicity_state": NO_PROMOTION_EXISTS,
            "cell_id": None,
            "conflicting_cell_ids": None,
        }
    if len(distinct_promoted_cell_ids) == 1:
        return {
            "hypothesis_id": hypothesis_id,
            "canonicity_state": CANONICAL_CELL_RESOLVED,
            "cell_id": distinct_promoted_cell_ids[0],
            "conflicting_cell_ids": None,
        }
    return {
        "hypothesis_id": hypothesis_id,
        "canonicity_state": MULTIPLE_PROMOTIONS_AMBIGUOUS,
        "cell_id": None,
        "conflicting_cell_ids": distinct_promoted_cell_ids,
    }
