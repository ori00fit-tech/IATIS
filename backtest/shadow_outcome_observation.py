"""
backtest/shadow_outcome_observation.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15E — SHADOW Outcome Observation
(persistence + pure aggregation, domain layer).

PERSISTENCE SEMANTICS (operator's own locked Design Gate): an
append-only observation event log. `record_shadow_outcome_observation()`
turns a `backtest.shadow_outcome_resolver.resolve_decision_outcome()`
RETURN VALUE into a persisted row -- the resolver's own invocation is
NEVER, by itself, evidence; only an explicit call to this function makes
an evaluation a recorded observation (the operator's own "invocation !=
persisted evidence" carried-forward note). `resolve_decision_outcome()`
itself is completely unaware this module exists -- it is never imported
here as anything other than a source of the TP_HIT/SL_HIT/TIMEOUT/
DATA_GAP/NOT_YET_ASSESSABLE constants it already defines, reused
verbatim, never redefined.

IDEMPOTENCY, EXACTLY AS LOCKED (the operator's own required correction):
the real guarantee is NOT "a fresh evaluation always gets a different
evaluated_at" -- clock/representation precision could theoretically
collide. The real guarantee is:

    SAME resolver result + SAME evaluated_at -> idempotent retry
        -> the EXISTING observation is returned, never duplicated.
    A DIFFERENT observation colliding on the exact SAME
    (request_id, evaluated_at) pair is NEVER silently treated as the
        same, NEVER overwritten, NEVER silently returned as if it
        matched -- it raises ShadowOutcomeObservationError explicitly.

NON-NEGOTIABLE (operator's own locked Phase 15E scope boundary): this
module never imports backtest.promotion_gate, backtest.policy_health,
backtest.execution_attribution, execution.authorization, execution.
trade_executor, storage.outcome_tracker, storage.shadow_book, scheduler.py,
or main.py. It never computes `diverged_catastrophically`, win_rate,
profit_factor, or any P&L-denominated value -- compute_shadow_outcome_
profile() returns exactly five frequency counts over the latest
observation per request_id, nothing else. Reducing "many observations
per request_id" to "the latest one" happens ENTIRELY in pure Python here
(reduce_to_latest_observations()) -- storage/shadow_outcome_observation.py
itself contains no such logic, only plain row reads.
"""
from __future__ import annotations

import uuid
from typing import Any

from backtest.shadow_outcome_resolver import DATA_GAP, NOT_YET_ASSESSABLE, SL_HIT, TIMEOUT, TP_HIT
from storage import shadow_decision_snapshot as storage_snapshot
from storage import shadow_outcome_observation as storage_observation

__all__ = [
    "ShadowOutcomeObservationError", "record_shadow_outcome_observation",
    "reduce_to_latest_observations", "compute_shadow_outcome_profile",
]


class ShadowOutcomeObservationError(Exception):
    """Structural misuse (an unknown request_id with no persisted
    snapshot behind it) or a genuine collision between two DIFFERENT
    observations for the exact same (request_id, evaluated_at) pair --
    never raised for an ordinary idempotent retry, which always returns
    the existing row instead."""


def record_shadow_outcome_observation(resolver_result: dict[str, Any]) -> dict[str, Any]:
    """The SOLE persistence entry point. `resolver_result` is the WHOLE
    dict resolve_decision_outcome() returns (request_id, hypothesis_id,
    outcome, resolved_bar_time, evaluated_at) -- read as one trusted
    object, never from separately-supplied fields that could drift
    apart. Raises ShadowOutcomeObservationError if request_id names no
    real, persisted decision snapshot -- never fabricates an identity.

    Idempotent ONLY for a byte-identical retry (same request_id, same
    evaluated_at, same outcome/resolved_bar_time/hypothesis_id) -- see
    this module's own docstring for the exact, locked distinction
    between that and a genuine collision, which raises instead of ever
    being silently accepted.

    `hypothesis_id` is NEVER trusted from `resolver_result` -- it is
    derived exclusively from the real, persisted decision snapshot this
    request_id names (the same "single source of identity" discipline
    Phase 15C's create_attribution_record() already established), so a
    resolver_result carrying a mismatched hypothesis_id can never cause
    a wrongly-attributed row to be persisted."""
    request_id = resolver_result["request_id"]
    outcome = resolver_result["outcome"]
    resolved_bar_time = resolver_result["resolved_bar_time"]
    evaluated_at = resolver_result["evaluated_at"]

    snapshot = storage_snapshot.get_snapshot_by_request_id(request_id)
    if snapshot is None:
        raise ShadowOutcomeObservationError(
            f"record_shadow_outcome_observation: unknown request_id {request_id!r} -- no persisted "
            f"decision snapshot exists to attribute this observation to."
        )
    hypothesis_id = snapshot["hypothesis_id"]

    observation_id = f"SHADOW-OUTCOME-OBSERVATION-{uuid.uuid4().hex[:16]}"
    row = storage_observation.try_insert(
        observation_id=observation_id, request_id=request_id, hypothesis_id=hypothesis_id,
        outcome=outcome, resolved_bar_time=resolved_bar_time, evaluated_at=evaluated_at,
    )
    if row is not None:
        return row

    existing = storage_observation.get_observation_by_request_id_and_evaluated_at(request_id, evaluated_at)
    if existing is None:
        raise ShadowOutcomeObservationError(
            f"record_shadow_outcome_observation: insert for request_id {request_id!r} at "
            f"evaluated_at {evaluated_at!r} was denied by the UNIQUE constraint, but no existing "
            f"row could be found -- a structural inconsistency, never silently guessed past."
        )

    if (existing["outcome"] == outcome and existing["resolved_bar_time"] == resolved_bar_time
            and existing["hypothesis_id"] == hypothesis_id):
        return existing  # a byte-identical retry -- the SAME observation, never overwritten

    raise ShadowOutcomeObservationError(
        f"record_shadow_outcome_observation: a DIFFERENT observation already exists for "
        f"request_id {request_id!r} at evaluated_at {evaluated_at!r} (existing outcome="
        f"{existing['outcome']!r}, new outcome={outcome!r}) -- never silently overwritten, "
        f"never silently accepted as a match."
    )


def reduce_to_latest_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure, deterministic reduction -- no DB, no I/O. Groups by
    request_id and keeps only the row with the greatest `evaluated_at`
    per group (string comparison, safe because every evaluated_at this
    phase ever produces comes from the SAME resolver's own
    datetime.isoformat() on a timezone-aware UTC value, which sorts
    lexicographically identically to chronological order). This is the
    ONLY place "latest per request_id" is ever decided -- storage/
    shadow_outcome_observation.py itself contains no such logic."""
    latest_by_request: dict[str, dict[str, Any]] = {}
    for observation in observations:
        request_id = observation["request_id"]
        current = latest_by_request.get(request_id)
        if current is None or observation["evaluated_at"] > current["evaluated_at"]:
            latest_by_request[request_id] = observation
    return list(latest_by_request.values())


def compute_shadow_outcome_profile(hypothesis_id: str, window: int) -> dict[str, Any]:
    """Reads storage.shadow_outcome_observation.
    list_observations_for_hypothesis() (a plain, most-recent-`window`-
    rows read, mirroring backtest.shadow_observation.
    compute_shadow_observed_profile()'s own exact shape), reduces to the
    latest observation per request_id, and returns EXACTLY five
    frequency counts over that reduced set -- never a win_rate, never a
    profit_factor, never any P&L-denominated value, and never
    `diverged_catastrophically` (explicitly out of this phase's scope,
    per the pre-registration spec's own §11)."""
    observations = storage_observation.list_observations_for_hypothesis(hypothesis_id, limit=window)
    latest = reduce_to_latest_observations(observations)
    return {
        "observation_count": len(latest),
        "tp_hit_count": sum(1 for o in latest if o["outcome"] == TP_HIT),
        "sl_hit_count": sum(1 for o in latest if o["outcome"] == SL_HIT),
        "timeout_count": sum(1 for o in latest if o["outcome"] == TIMEOUT),
        "data_gap_count": sum(1 for o in latest if o["outcome"] == DATA_GAP),
        "not_yet_assessable_count": sum(1 for o in latest if o["outcome"] == NOT_YET_ASSESSABLE),
    }
