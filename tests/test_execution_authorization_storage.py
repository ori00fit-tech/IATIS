"""tests/test_execution_authorization_storage.py -- storage-layer tests
for storage/execution_authorization.py (Phase 9): the atomic compare-
and-set primitives themselves, independent of execution.authorization's
own validation layer."""
from __future__ import annotations

from storage import execution_authorization as storage_auth


def _issue(**overrides) -> dict:
    base = dict(
        authorization_id="EXEC-AUTH-test0001",
        decision_id="LIVE-IDENTITY-REQUEST-abc", policy_event_id="POLICY-EVENT-abc",
        hypothesis_id="SINGLE-ENGINE-HYPOTHESIS-abc", symbol="EURUSD", timeframe="H4",
        side="BUY", entry_type="MARKET", risk_preset="balanced", risk_definition_hash="hash123",
        client_order_id="CO-test0001",
        issued_at="2026-01-01T00:00:00+00:00", expires_at="2099-01-01T00:00:00+00:00",
    )
    base.update(overrides)
    return storage_auth.insert_issued(**base)


def test_insert_issued_creates_a_row_with_status_issued():
    row = _issue()
    assert row["status"] == storage_auth.ISSUED
    assert row["claimed_at"] is None
    assert row["resolved_at"] is None


def test_authorization_id_and_client_order_id_are_unique(fake_d1):
    _issue(authorization_id="EXEC-AUTH-dup", client_order_id="CO-dup")
    import pytest

    from storage import d1_client
    with pytest.raises(Exception):
        with d1_client.d1_connection() as con:
            storage_auth._init(con)
            con.execute(
                """INSERT INTO research_execution_authorizations
                   (authorization_id, decision_id, policy_event_id, hypothesis_id, symbol, timeframe,
                    side, entry_type, risk_preset, risk_definition_hash, status, client_order_id,
                    issued_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("EXEC-AUTH-dup", "d", "p", "h", "EURUSD", "H4", "BUY", "MARKET", "balanced", "hash",
                 storage_auth.ISSUED, "CO-dup", "2026-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"),
            )


def test_get_authorization_returns_none_for_unknown_id():
    assert storage_auth.get_authorization("EXEC-AUTH-ghost") is None


def test_try_claim_flips_issued_to_claimed():
    _issue(authorization_id="EXEC-AUTH-claim1", client_order_id="CO-claim1")
    row = storage_auth.try_claim("EXEC-AUTH-claim1")
    assert row["status"] == storage_auth.CLAIMED
    assert row["claimed_at"] is not None


def test_try_claim_on_already_claimed_is_a_no_op_not_a_second_claim():
    _issue(authorization_id="EXEC-AUTH-claim2", client_order_id="CO-claim2")
    first = storage_auth.try_claim("EXEC-AUTH-claim2")
    second = storage_auth.try_claim("EXEC-AUTH-claim2")
    assert first["status"] == storage_auth.CLAIMED
    assert second["status"] == storage_auth.CLAIMED
    assert first["claimed_at"] == second["claimed_at"]  # untouched by the second call


def test_try_claim_materializes_expired_lazily():
    _issue(authorization_id="EXEC-AUTH-exp1", client_order_id="CO-exp1", expires_at="2000-01-01T00:00:00+00:00")
    row = storage_auth.try_claim("EXEC-AUTH-exp1")
    assert row["status"] == storage_auth.EXPIRED
    assert row["denial_reason"]


def test_try_claim_returns_none_for_unknown_id():
    assert storage_auth.try_claim("EXEC-AUTH-ghost") is None


def test_try_revoke_flips_issued_to_revoked():
    _issue(authorization_id="EXEC-AUTH-rev1", client_order_id="CO-rev1")
    row = storage_auth.try_revoke("EXEC-AUTH-rev1", "operator cancelled")
    assert row["status"] == storage_auth.REVOKED
    assert row["revoked_at"] is not None


def test_try_revoke_after_claimed_is_a_no_op():
    _issue(authorization_id="EXEC-AUTH-rev2", client_order_id="CO-rev2")
    storage_auth.try_claim("EXEC-AUTH-rev2")
    row = storage_auth.try_revoke("EXEC-AUTH-rev2", "too late")
    assert row["status"] == storage_auth.CLAIMED  # unchanged -- revoke after CLAIMED never applies


def test_try_begin_submission_flips_claimed_to_submitted():
    _issue(authorization_id="EXEC-AUTH-sub1", client_order_id="CO-sub1")
    storage_auth.try_claim("EXEC-AUTH-sub1")
    row = storage_auth.try_begin_submission("EXEC-AUTH-sub1")
    assert row["status"] == storage_auth.SUBMITTED
    assert row["submitted_at"] is not None


def test_try_begin_submission_without_claim_first_is_a_no_op():
    _issue(authorization_id="EXEC-AUTH-sub2", client_order_id="CO-sub2")
    row = storage_auth.try_begin_submission("EXEC-AUTH-sub2")
    assert row["status"] == storage_auth.ISSUED  # still ISSUED -- never skips CLAIMED


def test_try_begin_submission_called_twice_only_applies_once():
    _issue(authorization_id="EXEC-AUTH-sub3", client_order_id="CO-sub3")
    storage_auth.try_claim("EXEC-AUTH-sub3")
    first = storage_auth.try_begin_submission("EXEC-AUTH-sub3")
    second = storage_auth.try_begin_submission("EXEC-AUTH-sub3")
    assert first["status"] == storage_auth.SUBMITTED
    assert second["status"] == storage_auth.SUBMITTED
    assert first["submitted_at"] == second["submitted_at"]


def test_try_resolve_sets_resolved_at_and_broker_fields():
    _issue(authorization_id="EXEC-AUTH-res1", client_order_id="CO-res1")
    storage_auth.try_claim("EXEC-AUTH-res1")
    storage_auth.try_begin_submission("EXEC-AUTH-res1")
    row = storage_auth.try_resolve("EXEC-AUTH-res1", storage_auth.CONFIRMED, broker_order_id="BRK-1", broker_state="FILLED")
    assert row["status"] == storage_auth.CONFIRMED
    assert row["resolved_at"] is not None
    assert row["broker_order_id"] == "BRK-1"


def test_try_mark_unknown_never_sets_resolved_at():
    _issue(authorization_id="EXEC-AUTH-unk1", client_order_id="CO-unk1")
    storage_auth.try_claim("EXEC-AUTH-unk1")
    storage_auth.try_begin_submission("EXEC-AUTH-unk1")
    row = storage_auth.try_mark_unknown("EXEC-AUTH-unk1", broker_state="TIMEOUT")
    assert row["status"] == storage_auth.UNKNOWN
    assert row["resolved_at"] is None


def test_try_reconcile_resolves_unknown_to_confirmed():
    _issue(authorization_id="EXEC-AUTH-rec1", client_order_id="CO-rec1")
    storage_auth.try_claim("EXEC-AUTH-rec1")
    storage_auth.try_begin_submission("EXEC-AUTH-rec1")
    storage_auth.try_mark_unknown("EXEC-AUTH-rec1", broker_state="TIMEOUT")
    row = storage_auth.try_reconcile("EXEC-AUTH-rec1", storage_auth.REJECTED, broker_order_id=None, broker_state="REJECTED_CONFIRMED")
    assert row["status"] == storage_auth.REJECTED
    assert row["resolved_at"] is not None


def test_list_unknown_authorizations_lists_only_unknown_rows():
    _issue(authorization_id="EXEC-AUTH-list1", client_order_id="CO-list1")
    storage_auth.try_claim("EXEC-AUTH-list1")
    storage_auth.try_begin_submission("EXEC-AUTH-list1")
    storage_auth.try_mark_unknown("EXEC-AUTH-list1", broker_state="TIMEOUT")
    _issue(authorization_id="EXEC-AUTH-list2", client_order_id="CO-list2")  # stays ISSUED

    rows = storage_auth.list_unknown_authorizations()
    ids = {r["authorization_id"] for r in rows}
    assert "EXEC-AUTH-list1" in ids
    assert "EXEC-AUTH-list2" not in ids


def test_every_transition_against_every_terminal_status_is_a_no_op():
    """Terminal immutability, proven directly at the storage layer: once
    REVOKED, no further write function can move the row anywhere."""
    _issue(authorization_id="EXEC-AUTH-term1", client_order_id="CO-term1")
    storage_auth.try_revoke("EXEC-AUTH-term1", "cancelled")
    before = storage_auth.get_authorization("EXEC-AUTH-term1")

    storage_auth.try_claim("EXEC-AUTH-term1")
    storage_auth.try_begin_submission("EXEC-AUTH-term1")
    storage_auth.try_resolve("EXEC-AUTH-term1", storage_auth.CONFIRMED, broker_order_id=None, broker_state=None)
    storage_auth.try_mark_unknown("EXEC-AUTH-term1", broker_state="x")
    storage_auth.try_reconcile("EXEC-AUTH-term1", storage_auth.CONFIRMED, broker_order_id=None, broker_state=None)

    after = storage_auth.get_authorization("EXEC-AUTH-term1")
    assert after == before
    assert after["status"] == storage_auth.REVOKED
