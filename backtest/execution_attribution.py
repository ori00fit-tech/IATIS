"""
backtest/execution_attribution.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15C — Execution Attribution Foundation
(domain layer).

Answers exactly one question: "which hypothesis_id is this real, already-
recorded SHADOW decision (Phase 8C's own request_id) attributable to" --
and answers it with a single, structural source of identity: the EXACT
hypothesis_id stored on that request's own row in research_live_identity_
requests (storage.hypothesis_live_request.get_live_identity_request()),
re-read fresh every call, never accepted as a caller-supplied parameter.
create_attribution_record()'s own signature makes this a structural
guarantee, not a convention: it takes ONLY request_id. There is no
parameter through which a caller could supply hypothesis_id, policy_id,
authorization_id, execution_attempt_id, trade_id, or outcome_signal_id --
that shape of call is impossible, not merely discouraged.

NON-NEGOTIABLE (operator's own locked Phase 15C scope boundary): a
research_execution_attribution row records ONLY that a decision identity
exists and is attributable -- it is NEVER evidence that any authorization,
execution attempt, fill, or outcome happened. Every attribution row this
phase can ever produce has all four downstream fields NULL; this module
contains NO function that populates any of them (no link_authorization(),
no link_execution_attempt(), no link_trade(), no link_outcome() exists
anywhere in this file) -- those belong to a future, separate Phase 15D
Design Gate, once real authorization/attempt/fill/outcome entities exist
to link. This module never imports backtest.promotion_gate, backtest.
policy_health, backtest.live_roster, backtest.shadow_observation, backtest.
shadow_evidence, execution.authorization, execution.trade_executor,
storage.engine_tracker, scheduler.py, or main.py, and contains no time-
window/proximity-based matching logic of any kind -- a time-window join
(the precedent already shipped in storage.engine_tracker.
engine_trade_attribution() for a different, engine-vote problem) is
explicitly rejected here as insufficiently certain for hypothesis/policy
governance attribution (operator's own locked reasoning: an inferred
temporal proximity is not a confirmed causal link).

policy_id is always NULL -- research_live_identity_requests has no
policy_id column (only gate_policy_event_id, a different, Phase 6/7
identity), so there is no honest source to derive it from today. This is
a disclosed data gap, never a silently-invented value.

classify_attribution_state()'s own strict lifecycle invariant (operator's
own locked correction): DECISION_IDENTITY_ONLY -> AUTHORIZED -> ATTEMPTED
-> FILLED -> OUTCOME_LINKED is a STRICT PREFIX chain -- a populated field
with an earlier, required field still NULL is never read as reaching that
later state; it is an invalid, inconsistent row, and this function raises
rather than returning a classification that would misrepresent it. Phase
15C itself can never produce such a row (every field it writes is NULL
except hypothesis_id/request_id/attribution_id/created_at), so this
invariant check cannot fire against anything 15C itself creates -- it
exists to protect every FUTURE caller (15D) against a half-written link.
"""
from __future__ import annotations

import uuid
from typing import Any

from storage import execution_attribution as storage_attribution
from storage.hypothesis_live_request import get_live_identity_request

DECISION_IDENTITY_ONLY = "DECISION_IDENTITY_ONLY"
AUTHORIZED = "AUTHORIZED"
ATTEMPTED = "ATTEMPTED"
FILLED = "FILLED"
OUTCOME_LINKED = "OUTCOME_LINKED"

_LIFECYCLE_FIELDS = (
    ("authorization_id", AUTHORIZED),
    ("execution_attempt_id", ATTEMPTED),
    ("trade_id", FILLED),
    ("outcome_signal_id", OUTCOME_LINKED),
)

__all__ = [
    "DECISION_IDENTITY_ONLY", "AUTHORIZED", "ATTEMPTED", "FILLED", "OUTCOME_LINKED",
    "ExecutionAttributionError", "create_attribution_record", "get_attribution_record",
    "classify_attribution_state",
]


class ExecutionAttributionError(Exception):
    """Structural misuse only (an empty or unknown request_id) or a
    structurally invalid lifecycle sequence detected by classify_
    attribution_state() -- never raised for an ordinary, valid
    DECISION_IDENTITY_ONLY attribution row, which is always a returned,
    descriptive result."""


def create_attribution_record(request_id: str) -> dict[str, Any]:
    """The SOLE creation entry point. Takes ONLY request_id -- hypothesis_id
    is ALWAYS derived from the real, already-persisted Phase 8C row this
    request_id names (storage.hypothesis_live_request.
    get_live_identity_request()), never accepted as a separate argument.
    Raises ExecutionAttributionError if request_id names no real Live
    Identity Request -- never fabricates an identity.

    Idempotent: if an attribution row already exists for this request_id,
    returns that EXISTING row unchanged (never a new row, never an error)
    -- there is only ever one honest attribution row per request_id, so a
    repeat call can never conflict with itself."""
    if not request_id:
        raise ExecutionAttributionError("create_attribution_record: request_id is required.")

    live_request = get_live_identity_request(request_id)
    if live_request is None:
        raise ExecutionAttributionError(
            f"create_attribution_record: unknown request_id {request_id!r} -- no real Live Identity "
            f"Request exists to attribute an identity from."
        )

    existing = storage_attribution.get_attribution_by_request_id(request_id)
    if existing is not None:
        return existing

    attribution_id = f"ATTRIBUTION-{uuid.uuid4().hex[:16]}"
    row = storage_attribution.try_insert(
        attribution_id=attribution_id, request_id=request_id, hypothesis_id=live_request["hypothesis_id"],
    )
    if row is None:
        # Lost a race against a concurrent create for the same request_id --
        # the other call's row is the real one; fetch and return it.
        row = storage_attribution.get_attribution_by_request_id(request_id)
    return row


def get_attribution_record(request_id: str) -> dict[str, Any] | None:
    return storage_attribution.get_attribution_by_request_id(request_id)


def classify_attribution_state(record: dict[str, Any]) -> str:
    """Pure, deterministic classification -- no DB, no I/O. Enforces the
    STRICT prefix-chain invariant documented in this module's own
    docstring: raises ExecutionAttributionError if a later field is
    populated while an earlier, required field is still NULL (a broken
    sequence), rather than ever returning a classification that would
    misrepresent such a row."""
    seen_gap = False
    highest_valid_index = -1
    for i, (field, _label) in enumerate(_LIFECYCLE_FIELDS):
        if record.get(field) is not None:
            if seen_gap:
                raise ExecutionAttributionError(
                    f"classify_attribution_state: invalid attribution sequence -- {field!r} is populated "
                    f"but an earlier required field in the chain is NULL."
                )
            highest_valid_index = i
        else:
            seen_gap = True
    if highest_valid_index == -1:
        return DECISION_IDENTITY_ONLY
    return _LIFECYCLE_FIELDS[highest_valid_index][1]
