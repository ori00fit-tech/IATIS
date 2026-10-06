"""
backtest/shadow_divergence_membership.py
---------------------------------------
Hypothesis Discovery Engine -- Family Membership Writer, decision layer
(operator's own locked Design Gate, built on Gate 0B's Divergence-
Claim Attempt Semantics and Gate 0C's Family Membership Record
Contract).

THE "ATTEMPTED" CONDITION (Gate 0B, locked verbatim, reused here --
never re-derived): a catastrophic-divergence claim is attempted for a
hypothesis the first time BOTH hold:

    n_T(H) >= 40          (the already-locked n_T completeness gate)
    AND
    p_value is not None   (backtest.shadow_outcome_aggregate.
                            compute_catastrophic_divergence_p_value()
                            actually produced a real number)

This module owns that condition check. storage.shadow_divergence_
membership (one layer down) never evaluates it -- it only persists
whatever this module decides to write, matching every other storage/
backtest split in this codebase (e.g. backtest.shadow_decision_
snapshot.capture_decision_snapshot() vs. storage.shadow_decision_
snapshot.try_insert()).

NOT CALLED FROM backtest.shadow_record.build_shadow_record() (operator's
own locked integration decision): that function, and every function in
backtest.shadow_outcome_aggregate it composes, is locked as performing
NO storage write of any kind. Writing a membership row is a SEPARATE
concern, called ALONGSIDE build_shadow_record() by a future
orchestrator -- which does not exist yet. This function is therefore
intentionally dormant: WIRED=NO, reachable from nowhere outside its own
tests, exactly like every other dormant layer of this engine until a
dedicated later phase wires and proves it.

NO n_eff/k_eff (Gate 0F's dependence methodology is designed but not
yet wired into compute_catastrophic_divergence_p_value() itself -- this
module freezes whatever p_value/counts the caller supplies, nothing
more). NO Bonferroni, NO classify_significance(), NO verdict, NO
promotion, NO execution -- all explicitly out of scope, per the locked
Design Gate.
"""
from __future__ import annotations

from typing import Any

from storage.shadow_divergence_membership import try_insert_membership

N_MIN_TERMINAL_CONFIRMED = 40  # reused verbatim from the locked n_T completeness gate -- never redefined


def record_family_membership_if_attempted(
    hypothesis_id: str,
    *,
    n_t: int,
    p_value: float | None,
    baseline_p: float,
    tp_count: int,
    sl_count: int,
    request_ids: list[str],
    hypothesis_fingerprint: str,
    research_code_commit: str | None,
) -> dict[str, Any] | None:
    """The SOLE entry point. Checks the locked "attempted" condition
    (n_t >= 40 AND p_value is not None); if NOT met, returns None
    WITHOUT any storage call at all -- no DB round-trip for a
    non-attempt. If met, calls storage.shadow_divergence_membership.
    try_insert_membership() -- which itself returns None if this
    hypothesis_id already has a membership row (idempotent, no
    duplicate, the existing row is left untouched) or the newly
    inserted row otherwise.

    All evidence fields (tp_count, sl_count, p_value, baseline_p,
    request_ids, hypothesis_fingerprint, research_code_commit) are
    caller-supplied, exactly as already computed -- this function
    never recomputes any of them."""
    if n_t < N_MIN_TERMINAL_CONFIRMED or p_value is None:
        return None
    return try_insert_membership(
        hypothesis_id=hypothesis_id,
        hypothesis_fingerprint=hypothesis_fingerprint,
        research_code_commit=research_code_commit,
        tp_count_at_entry=tp_count,
        sl_count_at_entry=sl_count,
        p_value_at_entry=p_value,
        baseline_p_at_entry=baseline_p,
        request_ids=request_ids,
    )
