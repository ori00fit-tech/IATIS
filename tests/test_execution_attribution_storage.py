"""tests/test_execution_attribution_storage.py -- storage-layer tests for
storage/execution_attribution.py (Phase 15C): the DECISION_IDENTITY_ONLY
insert shape and the DB-level request_id uniqueness invariant, independent
of backtest.execution_attribution's own hypothesis_id derivation layer."""
from __future__ import annotations

from storage import execution_attribution as storage_attribution


def _insert(**overrides) -> dict | None:
    base = dict(
        attribution_id="ATTRIBUTION-test0001", request_id="LIVE-IDENTITY-REQUEST-test0001",
        hypothesis_id="CONFLUENCE-HYPOTHESIS-x",
    )
    base.update(overrides)
    return storage_attribution.try_insert(**base)


def test_try_insert_creates_a_row_with_all_downstream_fields_null():
    row = _insert()
    assert row["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-x"
    assert row["policy_id"] is None
    assert row["authorization_id"] is None
    assert row["execution_attempt_id"] is None
    assert row["trade_id"] is None
    assert row["outcome_signal_id"] is None
    assert row["created_at"] is not None


def test_get_attribution_by_id_returns_none_for_unknown_id():
    assert storage_attribution.get_attribution_by_id("ATTRIBUTION-ghost") is None


def test_get_attribution_by_request_id_returns_none_when_absent():
    assert storage_attribution.get_attribution_by_request_id("LIVE-IDENTITY-REQUEST-ghost") is None


def test_try_insert_blocked_by_the_real_db_level_request_id_uniqueness_index():
    """The core proof: NOT a Python len()-check -- a second attribution row
    for the EXACT SAME request_id fails to insert because the database's
    own UNIQUE index refuses it, not because this module counted anything."""
    first = _insert(attribution_id="ATTRIBUTION-u1", request_id="LIVE-IDENTITY-REQUEST-dup")
    second = _insert(attribution_id="ATTRIBUTION-u2", request_id="LIVE-IDENTITY-REQUEST-dup",
                      hypothesis_id="CONFLUENCE-HYPOTHESIS-y")

    assert first is not None
    assert second is None  # denied -- nothing inserted
    assert storage_attribution.get_attribution_by_id("ATTRIBUTION-u2") is None


def test_try_insert_is_not_blocked_across_different_request_ids():
    first = _insert(attribution_id="ATTRIBUTION-d1", request_id="LIVE-IDENTITY-REQUEST-d1")
    second = _insert(attribution_id="ATTRIBUTION-d2", request_id="LIVE-IDENTITY-REQUEST-d2")
    assert first is not None
    assert second is not None


def test_multiple_request_ids_may_share_the_same_hypothesis_id():
    first = _insert(attribution_id="ATTRIBUTION-s1", request_id="LIVE-IDENTITY-REQUEST-s1",
                     hypothesis_id="CONFLUENCE-HYPOTHESIS-shared")
    second = _insert(attribution_id="ATTRIBUTION-s2", request_id="LIVE-IDENTITY-REQUEST-s2",
                      hypothesis_id="CONFLUENCE-HYPOTHESIS-shared")
    assert first["hypothesis_id"] == second["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-shared"
    assert first["attribution_id"] != second["attribution_id"]


def test_get_attribution_by_request_id_finds_the_matching_row():
    _insert(attribution_id="ATTRIBUTION-f1", request_id="LIVE-IDENTITY-REQUEST-f1")
    found = storage_attribution.get_attribution_by_request_id("LIVE-IDENTITY-REQUEST-f1")
    assert found is not None
    assert found["attribution_id"] == "ATTRIBUTION-f1"
