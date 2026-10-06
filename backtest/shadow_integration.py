"""
backtest/shadow_integration.py
---------------------------------------
Hypothesis Discovery Engine -- Membership Integration (operator's own
locked Design Gate, 2026-10, decision C): the actual caller that links
ONE SHADOW evaluation to BOTH backtest.shadow_record.
compose_shadow_record() (the Phase 13 shape) and backtest.
shadow_divergence_membership.record_family_membership_if_attempted()
(the family-membership ledger), from a SINGLE backtest.
shadow_outcome_evidence.evaluate_shadow_evidence() call -- never two, and
never a copy of either consumer's own formula.

THE MEMBERSHIP-WRITE PRECONDITION (operator's own locked decision,
explicit, not implied by n_t/p_value alone):

    canonical_identity_state == CANONICAL_IDENTITY_RESOLVED
    AND n_t >= N_MIN_TERMINAL_CONFIRMED
    AND p_value is not None
    AND baseline_p is not None

The last three already compose from record_family_membership_if_attempted()'s
own locked "attempted" gate (n_t/p_value) -- re-checked explicitly here
anyway, by the operator's own locked instruction, rather than relying on
an unstated implication between independently-defined evidence fields
when storage's own NOT NULL contract (research_shadow_divergence_
membership.baseline_p_at_entry) depends on it. The first condition is
new: canonical identity (hypothesis_fingerprint/research_code_commit) is
computed independently of n_t/p_value/baseline_p, and
record_family_membership_if_attempted() cannot be called at all without
it (hypothesis_fingerprint is a required, non-Optional argument).

record_family_membership_if_attempted() itself is NOT modified by this
module -- this precondition lives here, one layer above it, exactly as
locked. compute_catastrophic_divergence_p_value() is likewise untouched.

canonical_identity_state == NO_CANONICAL_CELL or IDENTITY_MISMATCH NEVER
blocks the Phase 13 shadow_record -- compose_shadow_record() is always
called, unconditionally, from the same evidence (operator's own locked
rule: identity is independent of completion). Only the membership write
is gated on identity.

NON-NEGOTIABLE (operator's own locked scope boundary): this module never
imports backtest.promotion_gate, backtest.policy_health, backtest.
execution_attribution, execution.authorization, execution.trade_executor,
storage.outcome_tracker, storage.shadow_book, scheduler.py, or main.py.
It never computes n_eff/k_eff, never calls Bonferroni/classify_
significance(), never mutates compute_catastrophic_divergence_p_value()
or record_family_membership_if_attempted(). No production caller exists
yet -- WIRED=NO, reachable from nowhere outside its own tests, exactly
like every other dormant layer of this engine until a dedicated later
phase wires and proves it.
"""
from __future__ import annotations

from typing import Any

from backtest.shadow_divergence_membership import record_family_membership_if_attempted
from backtest.shadow_outcome_evidence import CANONICAL_IDENTITY_RESOLVED, evaluate_shadow_evidence
from backtest.shadow_record import N_MIN_TERMINAL_CONFIRMED, compose_shadow_record

__all__ = ["evaluate_and_record_shadow"]


def evaluate_and_record_shadow(
    hypothesis_id: str,
    *,
    base_config: dict[str, Any],
    promotions: list[dict[str, Any]],
    cell: dict[str, Any] | None,
    validation_results: list[dict[str, Any]],
    api_key: str | None = None,
) -> dict[str, Any]:
    """The SOLE entry point. Evaluates SHADOW evidence exactly ONCE (one
    network pass, via evaluate_shadow_evidence()), then feeds that SAME
    evidence to compose_shadow_record() (always, unconditionally) and,
    only when the membership-write precondition holds (see module
    docstring), to record_family_membership_if_attempted().

    Returns {"shadow_record": dict, "membership": dict | None} --
    `membership` is None whenever the precondition is not met OR storage
    itself denies the insert (an existing membership row already present
    for this hypothesis_id) -- both are legitimate non-writes, and this
    function's return shape does not distinguish between them."""
    evidence = evaluate_shadow_evidence(
        hypothesis_id, base_config=base_config, promotions=promotions,
        cell=cell, validation_results=validation_results, api_key=api_key,
    )
    shadow_record = compose_shadow_record(evidence)

    membership = None
    if (
        evidence["canonical_identity_state"] == CANONICAL_IDENTITY_RESOLVED
        and evidence["n_t"] >= N_MIN_TERMINAL_CONFIRMED
        and evidence["p_value"] is not None
        and evidence["baseline_p"] is not None
    ):
        membership = record_family_membership_if_attempted(
            hypothesis_id,
            n_t=evidence["n_t"],
            p_value=evidence["p_value"],
            baseline_p=evidence["baseline_p"],
            tp_count=evidence["tp_count"],
            sl_count=evidence["sl_count"],
            request_ids=evidence["request_ids"],
            hypothesis_fingerprint=evidence["hypothesis_fingerprint"],
            research_code_commit=evidence["research_code_commit"],
        )

    return {"shadow_record": shadow_record, "membership": membership}
