"""tests/test_execution_authorization_adversarial.py -- the operator's
own explicitly required adversarial + restart proofs for Phase 9
(Governed Execution Authorization): concurrent claim races, expired/
revoked denial, DB-failure fail-closed, the timeout-never-REJECTED
invariant (the exact bug Gate 0 found live in execution/ctrader_client.py
today), and the "restart never creates a second submission attempt"
restart invariant -- without inventing any orphan-recovery mechanism,
per the operator's own explicit refusal to let Phase 9 design one."""
from __future__ import annotations

import threading

import pytest

from execution import authorization as auth


def _issue(**overrides) -> dict:
    base = dict(
        decision_id="LIVE-IDENTITY-REQUEST-abc", policy_event_id="POLICY-EVENT-abc",
        hypothesis_id="SINGLE-ENGINE-HYPOTHESIS-abc", symbol="EURUSD", timeframe="H4",
        side="BUY", entry_type="MARKET", risk_preset="balanced", risk_definition_hash="hash123",
        ttl_seconds=60.0,
    )
    base.update(overrides)
    return auth.issue_authorization(**base)


# --- adversarial test 1: concurrent claim --------------------------------


def test_concurrent_claim_exactly_one_worker_wins():
    row = _issue()
    authorization_id = row["authorization_id"]
    results: list[dict] = []
    lock = threading.Lock()

    def _worker():
        r = auth.claim(authorization_id)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    # every thread sees the SAME final row (status CLAIMED) -- but only
    # one of them was ever the one whose own UPDATE applied; we cannot
    # observe that distinction from the outside (by design, matching
    # claim_queued_cells()'s own precedent), so the externally-provable
    # property is: the row converges to exactly one CLAIMED outcome,
    # never anything else, and the database itself never recorded two
    # independent claimed_at values.
    assert all(r["status"] == auth.CLAIMED for r in results)
    claimed_timestamps = {r["claimed_at"] for r in results}
    assert len(claimed_timestamps) == 1, "concurrent claims must converge on a single claimed_at -- never two"


def test_concurrent_begin_submission_exactly_one_attempt_recorded():
    row = _issue()
    authorization_id = row["authorization_id"]
    auth.claim(authorization_id)
    results: list[dict] = []
    lock = threading.Lock()

    def _worker():
        r = auth.begin_submission(authorization_id)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r["status"] == auth.SUBMITTED for r in results)
    submitted_timestamps = {r["submitted_at"] for r in results}
    assert len(submitted_timestamps) == 1, "concurrent begin_submission() calls must record exactly one submission moment"


# --- adversarial test 2: expired authorization -> zero executions ----------


def test_expired_authorization_produces_zero_claims():
    row = _issue(ttl_seconds=0.001)
    import time

    time.sleep(0.05)
    result = auth.claim(row["authorization_id"])
    assert result["status"] == auth.EXPIRED
    # and critically: begin_submission() can never follow, since CLAIMED
    # was never reached
    submission_attempt = auth.begin_submission(row["authorization_id"])
    assert submission_attempt["status"] == auth.EXPIRED  # unchanged, never SUBMITTED


def test_revoked_authorization_produces_zero_claims():
    row = _issue()
    auth.revoke(row["authorization_id"], "operator cancelled before claim")
    result = auth.claim(row["authorization_id"])
    assert result["status"] == auth.REVOKED  # unchanged, claim never applies


# --- adversarial test 3: database failure -> FAIL CLOSED -------------------


def test_database_failure_during_claim_propagates_never_returns_a_false_success():
    row = _issue()

    def boom():
        raise RuntimeError("D1 unreachable")

    import storage.execution_authorization as sea
    original = sea.d1_client.d1_connection
    sea.d1_client.d1_connection = boom
    try:
        with pytest.raises(RuntimeError, match="D1 unreachable"):
            auth.claim(row["authorization_id"])
    finally:
        sea.d1_client.d1_connection = original

    # the authorization was never silently moved to CLAIMED by a half-
    # completed call -- the crash happened before any write committed.
    restored = auth.get_authorization(row["authorization_id"])
    assert restored["status"] == auth.ISSUED


def test_database_failure_during_begin_submission_propagates_fail_closed():
    row = _issue()
    auth.claim(row["authorization_id"])

    def boom():
        raise RuntimeError("D1 unreachable")

    import storage.execution_authorization as sea
    original = sea.d1_client.d1_connection
    sea.d1_client.d1_connection = boom
    try:
        with pytest.raises(RuntimeError):
            auth.begin_submission(row["authorization_id"])
    finally:
        sea.d1_client.d1_connection = original

    restored = auth.get_authorization(row["authorization_id"])
    assert restored["status"] == auth.CLAIMED  # never silently advanced to SUBMITTED


# --- adversarial test 4: the core invariant -- timeout is NEVER REJECTED ---


class _FakeBrokerAdapter:
    """A minimal stand-in broker adapter used ONLY to prove this module's
    OWN contract -- it is not, and must never become, a real broker
    client. Deliberately modeled on the exact failure mode Gate 0 found
    live in execution/ctrader_client.py: an ambiguous/timed-out response
    with no definitive fill/reject answer."""

    def submit_and_wait(self, *, times_out: bool):
        if times_out:
            raise TimeoutError("broker did not respond within the deadline")
        return {"accepted": True, "broker_order_id": "BRK-123"}


def test_broker_timeout_during_submission_becomes_unknown_never_rejected_never_false():
    """The non-negotiable proof: a caller that submits through begin_
    submission() -> (fake adapter call that times out) -> must call
    mark_unknown(), and this module structurally has NO path by which a
    timeout could instead land on REJECTED or on any boolean False -- the
    exact bug found live in execution/ctrader_client.py:1983-1986 today
    (`CTraderResult(success=False, error="Order timed out...")`)."""
    row = _issue()
    auth.claim(row["authorization_id"])
    submitted = auth.begin_submission(row["authorization_id"])
    assert submitted["status"] == auth.SUBMITTED

    adapter = _FakeBrokerAdapter()
    try:
        adapter.submit_and_wait(times_out=True)
        caller_outcome = "CONFIRMED_ADAPTER_RETURNED"
    except TimeoutError:
        caller_outcome = "TIMEOUT"

    assert caller_outcome == "TIMEOUT"
    result = auth.mark_unknown(row["authorization_id"], broker_state="TIMEOUT")

    assert result["status"] == auth.UNKNOWN
    assert result["status"] != auth.REJECTED
    assert result["resolved_at"] is None  # not a resolution -- still ambiguous


def test_unknown_outcome_never_retried_automatically_no_retry_function_exists():
    row = _issue()
    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    auth.mark_unknown(row["authorization_id"], broker_state="TIMEOUT")

    # the only way out is reconcile() -- proven structurally in
    # test_execution_authorization.py's own
    # test_no_reclaim_or_retry_or_auto_expire_function_exists(); here we
    # prove the BEHAVIORAL side: doing nothing leaves it UNKNOWN forever,
    # it never silently resolves itself.
    unchanged = auth.get_authorization(row["authorization_id"])
    assert unchanged["status"] == auth.UNKNOWN

    resolved = auth.reconcile(row["authorization_id"], auth.CONFIRMED, broker_order_id="BRK-999")
    assert resolved["status"] == auth.CONFIRMED
    assert resolved["resolved_at"] is not None


# --- restart invariant: never a second submission attempt ------------------


def test_restart_between_claimed_and_submitted_never_allows_a_second_submission_attempt():
    """Simulates a process death right after CLAIMED succeeds but before
    begin_submission() is ever called (the exact window the operator
    flagged). A 'recovering' process does NOT get any special reclaim
    path -- it only has the SAME public functions as any fresh caller.
    The required, and ONLY required, proof: whatever it does, it can
    never cause TWO submission attempts, and the authorization is never
    silently abandoned into a state that looks executable again."""
    row = _issue()
    authorization_id = row["authorization_id"]
    claimed = auth.claim(authorization_id)
    assert claimed["status"] == auth.CLAIMED
    # --- process "dies" here; nothing else happens ---

    # the "recovering" process's only available moves:
    reclaim_attempt = auth.claim(authorization_id)
    assert reclaim_attempt["status"] == auth.CLAIMED  # still CLAIMED -- a second claim() is a no-op, not a new claim

    first_submission = auth.begin_submission(authorization_id)
    assert first_submission["status"] == auth.SUBMITTED

    second_submission_attempt = auth.begin_submission(authorization_id)
    assert second_submission_attempt["status"] == auth.SUBMITTED  # unchanged
    assert second_submission_attempt["submitted_at"] == first_submission["submitted_at"], (
        "a second begin_submission() call after restart must NEVER record a new submission moment"
    )


def test_orphaned_claimed_with_no_further_action_stays_blocked_forever_not_auto_expired_not_auto_revoked():
    """The operator's own explicit refusal to invent recovery semantics:
    an authorization stuck at CLAIMED (process died, nothing ever called
    begin_submission()) does NOT get auto-expired, auto-revoked, or
    auto-reclaimed by anything in this module -- it simply stays CLAIMED,
    even well past its own expires_at, until a future, separate recovery
    contract exists."""
    row = _issue(ttl_seconds=0.2)
    claimed = auth.claim(row["authorization_id"])
    assert claimed["status"] == auth.CLAIMED  # confirm the claim itself landed before expiry
    import time

    time.sleep(0.4)  # now well past expires_at

    # claim() on an already-CLAIMED row never re-evaluates expiry -- the
    # lazy-EXPIRED materialization only ever applies to an ISSUED row.
    still_claimed = auth.claim(row["authorization_id"])
    assert still_claimed["status"] == auth.CLAIMED
