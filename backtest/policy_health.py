"""
backtest/policy_health.py
---------------------------------------
Hypothesis Discovery Engine, Phase 14 — Policy Health Classification
(Governance/Classification Layer only).

Phase 14 classifies the SEVERITY OF DEVIATIONS WITHIN A SINGLE PROFILE
SNAPSHOT. Temporal persistence/escalation (the operator's own original
roadmap language, "persistent anomaly -> DEGRADED") is explicitly
DEFERRED to a future, separate live-surveillance/data-attribution phase
-- this module has no history parameter, keeps no state between calls,
and DEGRADED/PAUSED here NEVER mean "this has persisted over time", only
"this many fields show a hard deviation in this one snapshot". A future
reader must never cite this module's own output as proof that an anomaly
has been observed more than once.

NOT part of this module's scope (operator's own explicit, locked
deferral -- none of this exists here, by design):

    live trades -> outcome_tracker -> aggregator -> policy attribution
        -> expected baseline generation -> health engine

`expected_profile`/`observed_profile` are plain caller-supplied dicts.
Where that data comes from, how a policy's own trades get attributed to
it (storage.outcome_tracker's `outcomes` table has no policy_id column
today -- confirmed by this phase's own Gate 0 audit), and how an
"expected" baseline is ever generated in the first place are ALL
explicitly out of scope -- a separate, later phase's job. This module
never imports storage/*.py, execution/*.py, scheduler.py, or main.py,
and is reachable from nowhere outside its own tests.

NON-NEGOTIABLE: PAUSED is a DESCRIPTIVE classification label only --
"the defined health criteria currently fail for this snapshot" -- it is
a DIFFERENT vocabulary from backtest.policy_registry's own Policy
lifecycle (DRAFT/VALIDATED/ACTIVE/REVOKED, Phase 10, locked and
unmodified) and carries NO authority to change it. This module never
calls policy_registry.revoke_policy()/activate_policy(), never touches
execution of any kind, and never pauses anything by itself -- acting on
a PAUSED classification (if ever) is entirely a future, separate
governance layer's decision.

LOCKED COMPARISON CONTRACT (both profiles require ALL five fields;
a missing field is a structural error, never a silently-skipped or
defaulted one):

    Field                | Concerning direction | SOFT ratio | HARD ratio
    ---------------------+-----------------------+------------+------------
    profit_factor        | decrease              | x0.85      | x0.65
    win_rate              | decrease              | x0.85      | x0.65
    max_drawdown           | increase              | x1.15      | x1.50
    execution_slippage      | increase              | x1.15      | x1.50
    trade_count               | either (symmetric)    | 30% abs dev | 80% abs dev

(the symmetric HARD fraction is 80%, not 100% -- a 100% fraction would
mean a COMPLETE collapse to zero trades lands exactly on the boundary,
never strictly exceeding it, which would wrongly leave total frequency
collapse classified as merely SOFT; 80% was chosen specifically so that
observed=0 against any positive expected value is unambiguously HARD)

These thresholds are PHASE-14-OWNED, deliberately coarse constants
(matching backtest.robustness.py's own precedent -- its +/-30% STABLE
band is documented as "a coarse fragility screen, not a precision
estimate") -- easy to find and revise, never presented as independently
derived or validated against real data (none exists yet for this
purpose, per this phase's own Gate 0 finding).

ESCALATION RULE (operator's own locked replacement for temporal
persistence -- breadth across fields WITHIN one snapshot, never time):

    0 hard, 0 soft  -> HEALTHY
    0 hard, >=1 soft -> WATCH
    1 hard           -> DEGRADED
    >=2 hard         -> PAUSED

Every applicable reason is accumulated (never short-circuited on the
first field checked), matching backtest.promotion_gate's own established
discipline.
"""
from __future__ import annotations

from typing import Any

HEALTHY = "HEALTHY"
WATCH = "WATCH"
DEGRADED = "DEGRADED"
PAUSED = "PAUSED"

REQUIRED_FIELDS = ("trade_count", "profit_factor", "max_drawdown", "win_rate", "execution_slippage")

_DECREASE_IS_CONCERNING = ("profit_factor", "win_rate")
_INCREASE_IS_CONCERNING = ("max_drawdown", "execution_slippage")
_SYMMETRIC_FIELDS = ("trade_count",)

_SOFT_RATIO: dict[str, float] = {
    "profit_factor": 0.85, "win_rate": 0.85, "max_drawdown": 1.15, "execution_slippage": 1.15,
}
_HARD_RATIO: dict[str, float] = {
    "profit_factor": 0.65, "win_rate": 0.65, "max_drawdown": 1.50, "execution_slippage": 1.50,
}
_SYMMETRIC_SOFT_FRACTION = 0.30
_SYMMETRIC_HARD_FRACTION = 0.80


class PolicyHealthError(Exception):
    """Structural misuse only (a profile missing a required field) --
    never raised for an ordinary HEALTHY/WATCH/DEGRADED/PAUSED outcome,
    which is always a returned, descriptive result, never an exception."""


def _require_fields(profile: dict[str, Any], name: str) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in profile]
    if missing:
        raise PolicyHealthError(f"{name} is missing required field(s): {missing}")


def compare_profiles(*, expected_profile: dict[str, Any], observed_profile: dict[str, Any]) -> dict[str, Any]:
    """Pure, deterministic field-by-field comparison -- no DB, no I/O, no
    statistics computed, only the multiplicative ratio checks the locked
    contract table above specifies. Multiplicative (never dividing by
    `expected`) so an expected value of 0 never raises a ZeroDivisionError
    -- it simply means any nonzero observed deviation in the concerning
    direction is flagged (a deliberately conservative, not a crashing,
    behavior)."""
    _require_fields(expected_profile, "expected_profile")
    _require_fields(observed_profile, "observed_profile")

    soft_fields: list[str] = []
    hard_fields: list[str] = []
    reasons: list[str] = []

    for field in _DECREASE_IS_CONCERNING:
        expected, observed = expected_profile[field], observed_profile[field]
        if observed < expected * _HARD_RATIO[field]:
            hard_fields.append(field)
            reasons.append(f"{field}: observed {observed!r} is a HARD deviation below expected {expected!r}")
        elif observed < expected * _SOFT_RATIO[field]:
            soft_fields.append(field)
            reasons.append(f"{field}: observed {observed!r} is a SOFT deviation below expected {expected!r}")

    for field in _INCREASE_IS_CONCERNING:
        expected, observed = expected_profile[field], observed_profile[field]
        if observed > expected * _HARD_RATIO[field]:
            hard_fields.append(field)
            reasons.append(f"{field}: observed {observed!r} is a HARD deviation above expected {expected!r}")
        elif observed > expected * _SOFT_RATIO[field]:
            soft_fields.append(field)
            reasons.append(f"{field}: observed {observed!r} is a SOFT deviation above expected {expected!r}")

    for field in _SYMMETRIC_FIELDS:
        expected, observed = expected_profile[field], observed_profile[field]
        deviation = abs(observed - expected)
        if deviation > expected * _SYMMETRIC_HARD_FRACTION:
            hard_fields.append(field)
            reasons.append(f"{field}: observed {observed!r} deviates HARD from expected {expected!r}")
        elif deviation > expected * _SYMMETRIC_SOFT_FRACTION:
            soft_fields.append(field)
            reasons.append(f"{field}: observed {observed!r} deviates SOFT from expected {expected!r}")

    return {"hard_fields": hard_fields, "soft_fields": soft_fields, "reasons": reasons}


def assess_policy_health(*, expected_profile: dict[str, Any], observed_profile: dict[str, Any]) -> dict[str, Any]:
    """The SOLE public entry point. See this module's own docstring for
    the full locked contract -- in particular, this classifies ONE
    snapshot's own deviation severity; it is NOT evidence of anything
    persisting over time."""
    comparison = compare_profiles(expected_profile=expected_profile, observed_profile=observed_profile)
    hard_count = len(comparison["hard_fields"])
    soft_count = len(comparison["soft_fields"])

    if hard_count >= 2:
        status = PAUSED
    elif hard_count == 1:
        status = DEGRADED
    elif soft_count >= 1:
        status = WATCH
    else:
        status = HEALTHY

    return {
        "status": status,
        "reasons": comparison["reasons"],
        "hard_fields": comparison["hard_fields"],
        "soft_fields": comparison["soft_fields"],
    }
