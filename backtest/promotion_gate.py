"""
backtest/promotion_gate.py
---------------------------------------
Hypothesis Discovery Engine, Phase 13A/13B — Promotion Evidence Contract
+ Promotion Gate.

ELIGIBLE POLICY <- COMPLETE EVIDENCE, never GOOD CLASSIFICATION -> POLICY
(operator's own explicit correction). backtest.evidence_classification.
classify_evidence()'s own five-tier output (Phase 12) is ONE input this
gate reads -- the minimum classification bar per target stage -- never
the sole criterion. This module adds NO new statistics, NO new
validation engine, NO new Policy lifecycle state, and performs NO
execution or storage write of any kind. It is a pure, deterministic
reducer over already-computed evidence.

LOCKED PROMOTION EVIDENCE CONTRACT (13A, operator's own final table):

    Target   | min classify_evidence()      | CROSS_SYMBOL | additional evidence
    ---------+-------------------------------+--------------+----------------------
    SHADOW   | WEAK / MIXED / PROMISING /    | NOT_REQUIRED | none
             | STRONG                        |              |
    LIMITED  | PROMISING / STRONG            | REQUIRED     | completed SHADOW record
             |                               |              | (no catastrophic divergence)
    ACTIVE   | STRONG, with mission_verdict  | REQUIRED     | completed LIMITED review
             | == STRONG_LEAD specifically   |              | (acceptable performance)

"Failure = NOT_ELIGIBLE, never permanent rejection" -- a NOT_ELIGIBLE
result is a descriptive, re-evaluable state (mirroring Phase 10's own
NO_POLICY/CONFLICT -> NO_TRADE discipline), never a terminal verdict this
module itself enforces.

SHADOW's own evidence bar is DELIBERATELY NOT CROSS_SYMBOL-gated
(operator's own locked reasoning): "SHADOW = evidence sufficient to
justify observation, not evidence sufficient to prove generalization
independence." A SHADOW-eligible result therefore carries an explicit
`note` making clear it is NOT promotion-grade for LIMITED/ACTIVE -- never
silently read as such.

NON-NEGOTIABLE (operator's own locked scope boundary): this module never
calls backtest.policy_registry.activate_policy()/revoke_policy(), never
calls execution.authorization, never writes to any storage/*.py table,
and is reachable from nowhere outside its own tests. `shadow_record`/
`limited_review` are caller-supplied dicts following a MINIMAL shape
contract this module needs for its own pass/fail logic
(`{"completed": bool, "diverged_catastrophically": bool}` /
`{"completed": bool, "acceptable_performance": bool}`) -- the mechanism
that actually PRODUCES either dict (a real shadow-observation engine, a
real limited-deployment review) is explicitly OUT OF SCOPE for Phase 13
and is a separate, later effort's job.
"""
from __future__ import annotations

from typing import Any

from backtest.evidence_classification import MIXED, PROMISING, STRONG, WEAK
from backtest.mission_validator import STRONG_LEAD

SHADOW = "SHADOW"
LIMITED = "LIMITED"
ACTIVE = "ACTIVE"
_VALID_TARGET_STAGES = (SHADOW, LIMITED, ACTIVE)

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"

_MIN_CLASSIFICATION: dict[str, frozenset[str]] = {
    SHADOW: frozenset({WEAK, MIXED, PROMISING, STRONG}),
    LIMITED: frozenset({PROMISING, STRONG}),
    ACTIVE: frozenset({STRONG}),
}


class PromotionGateError(Exception):
    """Structural misuse only (an unrecognized target_stage) -- never
    raised for an ordinary NOT_ELIGIBLE outcome, which is always a
    returned, descriptive result, never an exception."""


def evaluate_promotion_gate(
    *, target_stage: str, evidence: dict[str, Any], cross_symbol_confirmed: bool,
    shadow_record: dict[str, Any] | None = None, limited_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The SOLE entry point. `evidence` is classify_evidence()'s OWN
    return dict, read verbatim (`evidence["classification"]`,
    `evidence["inputs"]["mission_verdict"]`) -- never recomputed or
    re-derived. Every applicable reason a target_stage is NOT_ELIGIBLE is
    accumulated (never short-circuited on the first failure), so a
    caller always sees the FULL picture of what's missing."""
    if target_stage not in _VALID_TARGET_STAGES:
        raise PromotionGateError(
            f"evaluate_promotion_gate: target_stage must be one of {_VALID_TARGET_STAGES}, got {target_stage!r}."
        )

    classification = evidence.get("classification")
    mission_verdict = (evidence.get("inputs") or {}).get("mission_verdict")
    reasons: list[str] = []

    allowed = _MIN_CLASSIFICATION[target_stage]
    if classification not in allowed:
        reasons.append(
            f"evidence classification {classification!r} does not meet the {target_stage} bar {sorted(allowed)}"
        )

    if target_stage in (LIMITED, ACTIVE) and not cross_symbol_confirmed:
        reasons.append(f"{target_stage} requires CROSS_SYMBOL-confirmed independent validation evidence")

    if target_stage == ACTIVE and mission_verdict != STRONG_LEAD:
        reasons.append(
            f"ACTIVE requires mission_verdict == {STRONG_LEAD!r} specifically, got {mission_verdict!r} -- "
            f"a STRONG classification alone is not sufficient"
        )

    if target_stage == LIMITED:
        if not shadow_record or not shadow_record.get("completed"):
            reasons.append("LIMITED requires a completed SHADOW record")
        elif shadow_record.get("diverged_catastrophically"):
            reasons.append("LIMITED requires the completed SHADOW record to show no catastrophic divergence")

    if target_stage == ACTIVE:
        if not limited_review or not limited_review.get("completed"):
            reasons.append("ACTIVE requires a completed LIMITED review")
        elif not limited_review.get("acceptable_performance"):
            reasons.append("ACTIVE requires the completed LIMITED review to show acceptable performance")

    result: dict[str, Any] = {
        "eligibility": NOT_ELIGIBLE if reasons else ELIGIBLE,
        "target_stage": target_stage,
        "reasons": reasons,
    }
    if target_stage == SHADOW and not reasons:
        result["note"] = (
            "SHADOW eligibility does not imply LIMITED/ACTIVE promotion-grade readiness -- "
            "CROSS_SYMBOL independence was not required for this stage."
        )
    return result
