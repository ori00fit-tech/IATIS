"""
execution/authorization.py
---------------------------------------
Phase 9 — Governed Execution Authorization.

Sits between a governed LIVE DECISION (Phase 8C's own Live Identity
Request audit record) and a BROKER SUBMISSION. No code in this module
ever calls a broker, ever imports execution.trade_executor, execution.
ctrader_client, execution.oanda_client, execution.dukascopy_jforex_client,
scheduler.py, or main.py -- this phase is DESIGNED, IMPLEMENTED, and
TESTED, but deliberately NOT WIRED into any live entry point. The cutover
that connects this module to a real broker call is a SEPARATE, later
phase (the operator's own Phase 15), never this one.

THE LOCKED STATE MACHINE (operator's own final contract, verbatim):

    ISSUED
     ├── CLAIMED
     │    └── SUBMITTED
     │          ├── CONFIRMED
     │          ├── REJECTED
     │          └── UNKNOWN
     │                 ├── CONFIRMED
     │                 └── REJECTED
     ├── REVOKED   (ISSUED only)
     └── EXPIRED   (materialized lazily at a claim() attempt, never a
                    proactive sweep)

NON-NEGOTIABLE INVARIANTS (operator's own locked wording):

  1. NO AUTHORIZATION = NO BROKER SUBMISSION. There is no function
     anywhere in this module that can move a row to SUBMITTED except
     begin_submission(), and it only ever succeeds from CLAIMED.
  2. ONE AUTHORIZATION = AT MOST ONE SUBMISSION ATTEMPT. begin_submission()
     is itself an atomic compare-and-set (CLAIMED -> SUBMITTED); once it
     has succeeded once, no call in this module can ever make it succeed
     again for the same authorization_id. SUBMITTED means an external
     submission attempt MAY have occurred -- it does NOT mean the order
     was accepted, filled, or even reached the broker. The real outcome
     comes only from resolve() (a definitive adapter response) or
     mark_unknown() + reconcile() (an ambiguous one).
  3. TIMEOUT / AMBIGUOUS OUTCOME -> UNKNOWN, NEVER REJECTED, NEVER FALSE.
     This is the exact bug Gate 0 found live in execution/ctrader_
     client.py (a timed-out order placement returns success=False today)
     -- this module's own domain model is built from the start to never
     repeat it. See tests/test_execution_authorization_adversarial.py's
     own fake-adapter timeout test for the non-mocked proof of this
     specific invariant.
  4. UNKNOWN -> NO AUTOMATIC RETRY. There is no retry()/resubmit()
     function anywhere in this module. The only way out of UNKNOWN is
     reconcile(), called by a SEPARATE reconciliation worker (out of
     scope for Phase 9's own implementation -- this module only exposes
     the primitives: list_unknown_authorizations() to find candidates,
     reconcile() to resolve one).
  5. CLAIMED (or SUBMITTED) AFTER A PROCESS RESTART -> STAYS BLOCKED, NO
     AUTOMATIC RECLAIM. This module deliberately contains no reclaim(),
     no force_expire_claimed(), no "recover orphaned authorizations"
     function of any kind. An authorization orphaned mid-flight by a
     crashed process stays exactly where it was, forever, until a FUTURE,
     separate recovery contract is designed and locked -- inventing that
     recovery semantics now was explicitly refused by the operator.
  6. TERMINAL STATE IS IMMUTABLE. CONFIRMED, REJECTED, REVOKED, EXPIRED
     never transition again -- every storage.execution_authorization
     write function's own WHERE clause already makes this structurally
     true (a terminal status never matches any transition's expected
     prior status), proven by tests/test_execution_authorization.py's
     own "every transition attempted against every terminal status is a
     no-op" sweep.
  7. resolved_at is REQUIRED exactly for CONFIRMED/REJECTED, and MUST
     remain NULL for UNKNOWN -- enforced by storage.execution_
     authorization's own try_resolve()/try_reconcile() (which always set
     it) versus try_mark_unknown() (which never touches it).
  8. Phase 9 reuses NOTHING from execution.trade_executor
     (min_score_to_execute, max_open_trades, allow_live_trading never
     appear here) -- those remain out of scope until a later phase
     formally governs them.

Identity fields (operator's own locked mapping, Gate 0's own finding):
decision_id refers to Phase 8C's OWN Live Identity Request audit record
-- its own storage layer's `request_id` (the Phase 8C governed identity,
carrying hypothesis_id/risk_preset/preset_definition_hash already) --
never Phase 7's bare research_live_decisions.decision_id, which (called
standalone) carries no hypothesis attribution at all.
policy_event_id refers LITERALLY to Phase 6's own Symbol Policy ledger's
`event_id` -- there is no first-class "Policy" entity yet (that is a
LATER phase's job); naming this field policy_id now would fabricate an
identity this engine does not yet have.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from storage import execution_authorization as storage_auth
from storage.execution_authorization import (
    CLAIMED,
    CONFIRMED,
    EXPIRED,
    ISSUED,
    REJECTED,
    REVOKED,
    SUBMITTED,
    TERMINAL_STATUSES,
    UNKNOWN,
)

_VALID_SIDES = ("BUY", "SELL")
_RESOLVABLE_OUTCOMES = (CONFIRMED, REJECTED)

# Re-exported status constants -- this module's own public vocabulary for
# every caller/test, so nothing outside storage/*.py needs to import
# storage.execution_authorization directly.
__all__ = [
    "ISSUED", "CLAIMED", "SUBMITTED", "UNKNOWN", "CONFIRMED", "REJECTED", "REVOKED", "EXPIRED",
    "TERMINAL_STATUSES", "ExecutionAuthorizationError",
    "issue_authorization", "get_authorization", "claim", "revoke", "begin_submission",
    "resolve", "mark_unknown", "reconcile", "list_unknown_authorizations",
]


class ExecutionAuthorizationError(Exception):
    """Structural misuse only (unknown authorization_id, invalid input,
    an invalid outcome value) -- NEVER raised for an ordinary lost race
    or an expired/revoked authorization, which are routine, expected
    outcomes the caller is expected to inspect via the returned row's own
    `status`, exactly matching storage.research_matrix.claim_queued_
    cells()'s own established "a lost race is not a bug" precedent."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_client_order_id(authorization_id: str) -> str:
    """Deterministic from authorization_id alone -- never random, never
    time-based. The SAME authorization_id always derives the SAME
    client_order_id, so a broker adapter (a later phase's job) can use it
    for idempotent submission without this module inventing a separate
    idempotency-key concept."""
    digest = hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()
    return f"CO-{digest[:24]}"


def issue_authorization(
    *, decision_id: str, policy_event_id: str, hypothesis_id: str, symbol: str, timeframe: str,
    side: str, entry_type: str, risk_preset: str, risk_definition_hash: str, ttl_seconds: float,
) -> dict[str, Any]:
    """Issues a brand-new ISSUED authorization. Every field here is
    required and caller-supplied -- this function derives NOTHING about
    WHICH decision/policy it is for; it only records what it is told,
    exactly matching this whole engine's "identity comes from upstream,
    never guessed here" discipline."""
    if not decision_id or not policy_event_id or not hypothesis_id:
        raise ExecutionAuthorizationError(
            "issue_authorization: decision_id, policy_event_id, and hypothesis_id are all required."
        )
    if side not in _VALID_SIDES:
        raise ExecutionAuthorizationError(f"issue_authorization: side must be one of {_VALID_SIDES}, got {side!r}.")
    if not entry_type:
        raise ExecutionAuthorizationError("issue_authorization: entry_type is required.")
    if not risk_preset or not risk_definition_hash:
        raise ExecutionAuthorizationError("issue_authorization: risk_preset and risk_definition_hash are required.")
    if ttl_seconds <= 0:
        raise ExecutionAuthorizationError(f"issue_authorization: ttl_seconds must be > 0, got {ttl_seconds!r}.")

    authorization_id = f"EXEC-AUTH-{uuid.uuid4().hex[:16]}"
    client_order_id = _derive_client_order_id(authorization_id)
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)

    return storage_auth.insert_issued(
        authorization_id=authorization_id, decision_id=decision_id, policy_event_id=policy_event_id,
        hypothesis_id=hypothesis_id, symbol=symbol, timeframe=timeframe, side=side, entry_type=entry_type,
        risk_preset=risk_preset, risk_definition_hash=risk_definition_hash, client_order_id=client_order_id,
        issued_at=issued_at.isoformat(), expires_at=expires_at.isoformat(),
    )


def get_authorization(authorization_id: str) -> dict[str, Any] | None:
    return storage_auth.get_authorization(authorization_id)


def _require_existing(authorization_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise ExecutionAuthorizationError(f"unknown authorization_id {authorization_id!r}.")
    return row


def claim(authorization_id: str) -> dict[str, Any]:
    """Atomic ISSUED -> CLAIMED (+ lazy EXPIRED materialization). Returns
    the row as it now stands; the caller checks `row["status"] ==
    CLAIMED` to know whether THIS call won. Never raises for a lost race
    or an expired authorization -- only for an authorization_id that
    never existed at all."""
    return _require_existing(authorization_id, storage_auth.try_claim(authorization_id))


def revoke(authorization_id: str, reason: str) -> dict[str, Any]:
    """Atomic ISSUED -> REVOKED only. A call against an authorization
    already past ISSUED (CLAIMED or later) is a no-op by design -- the
    locked contract's own explicit refusal to allow revoke after claim."""
    if not reason:
        raise ExecutionAuthorizationError("revoke: reason is required.")
    return _require_existing(authorization_id, storage_auth.try_revoke(authorization_id, reason))


def begin_submission(authorization_id: str) -> dict[str, Any]:
    """Atomic CLAIMED -> SUBMITTED -- the "at most one submission
    attempt" gate. MUST be called, and its result's `status` MUST be
    confirmed to equal SUBMITTED, BEFORE any adapter/broker call is made
    by a caller one layer above this module. If this call's own row
    comes back with status != SUBMITTED, no submission may be attempted
    -- there is no second chance."""
    return _require_existing(authorization_id, storage_auth.try_begin_submission(authorization_id))


def resolve(
    authorization_id: str, outcome: str, *, broker_order_id: str | None = None, broker_state: str | None = None,
) -> dict[str, Any]:
    """Atomic SUBMITTED -> {CONFIRMED, REJECTED} for a DEFINITIVE adapter
    response. Never call this for a timeout or an ambiguous response --
    use mark_unknown() instead (invariant #3)."""
    if outcome not in _RESOLVABLE_OUTCOMES:
        raise ExecutionAuthorizationError(f"resolve: outcome must be one of {_RESOLVABLE_OUTCOMES}, got {outcome!r}.")
    return _require_existing(
        authorization_id,
        storage_auth.try_resolve(authorization_id, outcome, broker_order_id=broker_order_id, broker_state=broker_state),
    )


def mark_unknown(authorization_id: str, *, broker_state: str | None = None) -> dict[str, Any]:
    """Atomic SUBMITTED -> UNKNOWN. Call this for a broker timeout or any
    response that cannot be confidently classified CONFIRMED/REJECTED --
    NEVER collapse that ambiguity into REJECTED (invariant #3, the exact
    bug found live in execution/ctrader_client.py today)."""
    return _require_existing(authorization_id, storage_auth.try_mark_unknown(authorization_id, broker_state=broker_state))


def reconcile(
    authorization_id: str, outcome: str, *, broker_order_id: str | None = None, broker_state: str | None = None,
) -> dict[str, Any]:
    """Atomic UNKNOWN -> {CONFIRMED, REJECTED}, called by a reconciliation
    worker (not part of this module's own scope) once it has queried the
    broker directly and obtained a definitive answer. There is
    deliberately no way to call this with "still unknown" -- a
    reconciliation pass that cannot yet resolve an authorization simply
    does not call this function for it; the row stays UNKNOWN and
    reappears in the next list_unknown_authorizations() pass (invariant
    #4 -- no automatic retry, no automatic resolution)."""
    if outcome not in _RESOLVABLE_OUTCOMES:
        raise ExecutionAuthorizationError(f"reconcile: outcome must be one of {_RESOLVABLE_OUTCOMES}, got {outcome!r}.")
    return _require_existing(
        authorization_id,
        storage_auth.try_reconcile(authorization_id, outcome, broker_order_id=broker_order_id, broker_state=broker_state),
    )


def list_unknown_authorizations(limit: int = 100) -> list[dict[str, Any]]:
    return storage_auth.list_unknown_authorizations(limit)
