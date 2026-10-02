"""
backtest/live_roster.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15A — SHADOW Live Evaluation Roster
(domain layer).

Roster membership != current eligibility (operator's own locked Phase 15A
principle): a Roster entry's own promotion_gate_snapshot_json proves WHY a
hypothesis_id was admitted to SHADOW consideration at add_to_roster()
time -- it is never re-read later as if it still proves current
authorization. check_current_eligibility() answers the DIFFERENT, FRESH
question -- is this hypothesis_id STILL eligible for THIS cycle -- from a
caller-supplied, already-computed backtest.promotion_gate.
evaluate_promotion_gate() result, never recomputed or re-derived here.

NON-NEGOTIABLE: a failed fresh eligibility check means "skip this cycle
only". Nothing in this module calls storage.live_roster.try_remove() as a
side effect of an ineligible result -- auto-removal lifecycle policy is
explicitly deferred to a future, separate decision. check_current_
eligibility() never mutates anything; it is a pure read+classify function
over its own two arguments, no storage access at all.

ONE-WAY DEPENDENCY DIRECTION (operator's own locked requirement):
    promotion_gate result -> live_roster -> shadow_observation
This module imports backtest.promotion_gate's own public constants (to
validate the shape of a caller-supplied result) but never calls
evaluate_promotion_gate() itself. It never imports backtest.
hypothesis_live_request, backtest.shadow_observation, backtest.
policy_health, or anything under execution/*.py.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from backtest.promotion_gate import ELIGIBLE, SHADOW
from storage import live_roster as storage_live_roster

ACTIVE = storage_live_roster.ACTIVE
REMOVED = storage_live_roster.REMOVED

__all__ = [
    "ACTIVE", "REMOVED", "LiveRosterError",
    "add_to_roster", "remove_from_roster", "get_roster_entry", "check_current_eligibility",
]


class LiveRosterError(Exception):
    """Structural misuse only (missing required arguments) -- never
    raised for an ordinary denial (a promotion_gate_result that isn't
    SHADOW-eligible shaped, or an ACTIVE entry already exists for this
    hypothesis_id), which is always a returned, descriptive result, never
    an exception."""


def add_to_roster(
    *, hypothesis_id: str, promotion_gate_result: dict[str, Any], added_by: str, added_reason: str,
) -> dict[str, Any]:
    """Admits hypothesis_id to the SHADOW Roster. Requires an ALREADY-
    COMPUTED promotion_gate_result (backtest.promotion_gate.
    evaluate_promotion_gate()'s own return dict, read verbatim) proving
    target_stage == SHADOW and eligibility == ELIGIBLE -- this function
    never calls evaluate_promotion_gate() itself. The snapshot is
    persisted VERBATIM (json-encoded) as the durable record of WHY this
    hypothesis_id was admitted -- never re-interpreted later as proof of
    CURRENT eligibility (see check_current_eligibility()).

    Returns {"added": True, "roster_entry": <row>} on success, or
    {"added": False, "reason": ...} on an ordinary denial (not a SHADOW-
    eligible result, or an ACTIVE entry already exists) -- never an
    exception for either of those."""
    if not hypothesis_id or not added_by or not added_reason:
        raise LiveRosterError("add_to_roster: hypothesis_id, added_by, and added_reason are all required.")
    if promotion_gate_result.get("target_stage") != SHADOW:
        return {"added": False,
                "reason": f"promotion_gate_result target_stage must be {SHADOW!r}, "
                          f"got {promotion_gate_result.get('target_stage')!r}."}
    if promotion_gate_result.get("eligibility") != ELIGIBLE:
        return {"added": False,
                "reason": f"promotion_gate_result eligibility must be {ELIGIBLE!r}, "
                          f"got {promotion_gate_result.get('eligibility')!r}: "
                          f"{promotion_gate_result.get('reasons')!r}."}

    roster_entry_id = f"ROSTER-{uuid.uuid4().hex[:16]}"
    row = storage_live_roster.try_insert_active(
        roster_entry_id=roster_entry_id, hypothesis_id=hypothesis_id, added_by=added_by,
        added_reason=added_reason,
        promotion_gate_snapshot_json=json.dumps(promotion_gate_result, sort_keys=True, default=str),
    )
    if row is None:
        return {"added": False,
                "reason": f"an ACTIVE roster entry already exists for hypothesis_id {hypothesis_id!r}."}
    return {"added": True, "roster_entry": row}


def remove_from_roster(*, roster_entry_id: str, removed_by: str, removed_reason: str) -> dict[str, Any]:
    """Atomic ACTIVE -> REMOVED. Raises LiveRosterError only for an
    unknown roster_entry_id -- an already-REMOVED entry is returned
    UNCHANGED (a no-op), matching storage.live_roster.try_remove()'s own
    idempotent contract."""
    if not removed_by or not removed_reason:
        raise LiveRosterError("remove_from_roster: removed_by and removed_reason are both required.")
    row = storage_live_roster.try_remove(roster_entry_id, removed_by, removed_reason)
    if row is None:
        raise LiveRosterError(f"remove_from_roster: unknown roster_entry_id {roster_entry_id!r}.")
    return row


def get_roster_entry(roster_entry_id: str) -> dict[str, Any] | None:
    return storage_live_roster.get_roster_entry(roster_entry_id)


def check_current_eligibility(
    *, roster_entry: dict[str, Any], fresh_promotion_gate_result: dict[str, Any],
) -> dict[str, Any]:
    """The FRESH question, answered exclusively from the caller's own two
    already-computed inputs -- NEVER from roster_entry's own stored
    promotion_gate_snapshot_json (that snapshot proves history, not
    current fact, and this function never even reads it). Pure: never
    mutates roster_entry, never touches storage.live_roster at all.

    Returns {"eligible_this_cycle": bool, "reason": str | None}."""
    if roster_entry.get("status") != ACTIVE:
        return {"eligible_this_cycle": False,
                "reason": f"roster entry status is {roster_entry.get('status')!r}, not {ACTIVE!r}."}
    if fresh_promotion_gate_result.get("target_stage") != SHADOW:
        return {"eligible_this_cycle": False,
                "reason": f"fresh promotion_gate_result target_stage must be {SHADOW!r}, "
                          f"got {fresh_promotion_gate_result.get('target_stage')!r}."}
    if fresh_promotion_gate_result.get("eligibility") != ELIGIBLE:
        return {"eligible_this_cycle": False,
                "reason": f"fresh promotion_gate_result eligibility is "
                          f"{fresh_promotion_gate_result.get('eligibility')!r}: "
                          f"{fresh_promotion_gate_result.get('reasons')!r}."}
    return {"eligible_this_cycle": True, "reason": None}
