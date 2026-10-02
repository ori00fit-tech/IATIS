"""
backtest/promotion_monitor.py
---------------------------------------
Hypothesis Discovery Engine, Phase 13C — Promotion Monitor.

NON-NEGOTIABLE (operator's own locked scope boundary):

    assess_evidence_staleness()
            |
            v
    descriptive result (CURRENT / DEGRADED / INVALIDATED)
            |
            v
    human / a FUTURE, SEPARATE governance layer
            |
            v
    backtest.policy_registry.revoke_policy() -- IF AND WHEN that layer
    decides to act

This module NEVER calls revoke_policy() or activate_policy() itself,
NEVER writes to any storage/*.py table, and NEVER introduces a new
Policy lifecycle state (no PAUSED, no DOWNGRADED -- Phase 10's locked
lifecycle, DRAFT -> VALIDATED -> ACTIVE -> REVOKED, stays exactly as
accepted). "The evidence degraded" and "someone revoked the policy" are
two deliberately separate facts, owned by two deliberately separate
layers -- Evidence layer != Decision authority != Execution authority,
the same three-way split this engine has held since Phase 9/10/12.

Pure and deterministic: assess_evidence_staleness() computes nothing
statistical itself -- it only compares TWO already-computed backtest.
evidence_classification.classify_evidence() snapshots (the one recorded
at promotion time, and a fresh one supplied by the caller) by severity
ordering. It never re-runs significance/mission/robustness classification
itself.
"""
from __future__ import annotations

from typing import Any

from backtest.evidence_classification import (
    INSUFFICIENT_EVIDENCE,
    MIXED,
    PROMISING,
    REJECTED,
    STRONG,
    WEAK,
)

CURRENT = "CURRENT"
DEGRADED = "DEGRADED"
INVALIDATED = "INVALIDATED"

# Severity ordering for the five real tiers -- INSUFFICIENT_EVIDENCE and
# REJECTED are handled as an unconditional INVALIDATED case below, never
# placed on this numeric scale (neither is "a weaker positive result",
# both are "no longer a usable positive claim at all").
_SEVERITY_ORDER: dict[str, int] = {WEAK: 0, MIXED: 1, PROMISING: 2, STRONG: 3}
_INVALIDATING_CLASSIFICATIONS = frozenset({REJECTED, INSUFFICIENT_EVIDENCE})


def assess_evidence_staleness(*, current_evidence: dict[str, Any], evidence_at_promotion: dict[str, Any]) -> dict[str, Any]:
    """Compares `current_evidence["classification"]` against
    `evidence_at_promotion["classification"]` -- both are classify_
    evidence()'s own return dicts, read verbatim.

    CURRENT:     classification is unchanged or improved.
    DEGRADED:    classification dropped to a lower (but still positive)
                 tier than at promotion time.
    INVALIDATED: current classification is REJECTED or INSUFFICIENT_
                 EVIDENCE -- the backing evidence no longer supports a
                 positive claim at all, regardless of what it was at
                 promotion time.

    Returns a descriptive dict only -- no action is taken, no function
    outside this one is called."""
    current = current_evidence.get("classification")
    at_promotion = evidence_at_promotion.get("classification")

    if current in _INVALIDATING_CLASSIFICATIONS:
        return {
            "status": INVALIDATED,
            "reason": f"current evidence classification is {current!r} -- no longer a usable positive claim",
            "current_classification": current,
            "classification_at_promotion": at_promotion,
        }

    current_rank = _SEVERITY_ORDER.get(current)
    promotion_rank = _SEVERITY_ORDER.get(at_promotion)
    if current_rank is not None and promotion_rank is not None and current_rank < promotion_rank:
        return {
            "status": DEGRADED,
            "reason": f"evidence classification dropped from {at_promotion!r} to {current!r} since promotion",
            "current_classification": current,
            "classification_at_promotion": at_promotion,
        }

    return {
        "status": CURRENT,
        "reason": "evidence classification is unchanged or improved since promotion",
        "current_classification": current,
        "classification_at_promotion": at_promotion,
    }
