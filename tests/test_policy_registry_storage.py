"""tests/test_policy_registry_storage.py -- storage-layer tests for
storage/policy_registry.py (Phase 10): the lifecycle transitions and the
DB-level active-scope uniqueness invariant, independent of backtest.
policy_registry's own Phase 6 re-verification layer."""
from __future__ import annotations

from storage import policy_registry as storage_policy


def _draft(**overrides) -> dict:
    base = dict(
        policy_id="POLICY-test0001", policy_version="v1", symbol="EURUSD", timeframe="H4",
        regime_profile="TRENDING", hypothesis_id="SINGLE-ENGINE-HYPOTHESIS-x", engine="wyckoff",
        engine_version="v2", decision_type="SINGLE_ENGINE", risk_preset="balanced",
        risk_definition_hash="hash123", bundle_id=None, policy_event_id="POLICY-EVENT-x",
    )
    base.update(overrides)
    return storage_policy.insert_draft(**base)


def test_insert_draft_creates_a_row_with_status_draft():
    row = _draft()
    assert row["status"] == storage_policy.DRAFT
    assert row["activated_at"] is None
    assert row["revoked_at"] is None


def test_get_policy_returns_none_for_unknown_id():
    assert storage_policy.get_policy("POLICY-ghost") is None


def test_try_set_validated_flips_draft_to_validated():
    _draft(policy_id="POLICY-v1")
    row = storage_policy.try_set_validated("POLICY-v1")
    assert row["status"] == storage_policy.VALIDATED


def test_try_set_validated_on_non_draft_is_a_no_op():
    _draft(policy_id="POLICY-v2")
    storage_policy.try_set_validated("POLICY-v2")
    second = storage_policy.try_set_validated("POLICY-v2")
    assert second["status"] == storage_policy.VALIDATED  # unchanged, not re-applied


def test_try_set_active_flips_validated_to_active():
    _draft(policy_id="POLICY-a1")
    storage_policy.try_set_validated("POLICY-a1")
    row = storage_policy.try_set_active("POLICY-a1")
    assert row["status"] == storage_policy.ACTIVE
    assert row["activated_at"] is not None


def test_try_set_active_without_validation_first_is_a_no_op():
    _draft(policy_id="POLICY-a2")
    row = storage_policy.try_set_active("POLICY-a2")
    assert row["status"] == storage_policy.DRAFT  # unchanged


def test_try_set_active_blocked_by_the_real_db_level_uniqueness_index():
    """The core proof: NOT a Python len()-check -- a second policy for
    the EXACT SAME (symbol, timeframe, regime_profile) scope fails to
    activate because the database's own partial UNIQUE index refuses
    it, not because this module counted anything."""
    _draft(policy_id="POLICY-u1", symbol="EURUSD", timeframe="H4", regime_profile="TRENDING")
    _draft(policy_id="POLICY-u2", symbol="EURUSD", timeframe="H4", regime_profile="TRENDING",
           hypothesis_id="SINGLE-ENGINE-HYPOTHESIS-y", policy_event_id="POLICY-EVENT-y")
    storage_policy.try_set_validated("POLICY-u1")
    storage_policy.try_set_validated("POLICY-u2")

    first = storage_policy.try_set_active("POLICY-u1")
    second = storage_policy.try_set_active("POLICY-u2")

    assert first["status"] == storage_policy.ACTIVE
    assert second["status"] == storage_policy.VALIDATED  # denied -- stays VALIDATED
    assert second["denial_reason"]


def test_try_set_active_is_not_blocked_across_different_scopes():
    _draft(policy_id="POLICY-s1", symbol="EURUSD", timeframe="H4", regime_profile="TRENDING")
    _draft(policy_id="POLICY-s2", symbol="GBPUSD", timeframe="H4", regime_profile="TRENDING",
           hypothesis_id="SINGLE-ENGINE-HYPOTHESIS-y", policy_event_id="POLICY-EVENT-y")
    storage_policy.try_set_validated("POLICY-s1")
    storage_policy.try_set_validated("POLICY-s2")

    first = storage_policy.try_set_active("POLICY-s1")
    second = storage_policy.try_set_active("POLICY-s2")
    assert first["status"] == storage_policy.ACTIVE
    assert second["status"] == storage_policy.ACTIVE  # different symbol -- no conflict


def test_try_revoke_from_draft():
    _draft(policy_id="POLICY-r1")
    row = storage_policy.try_revoke("POLICY-r1", "abandoned")
    assert row["status"] == storage_policy.REVOKED
    assert row["revoked_at"] is not None


def test_try_revoke_from_validated():
    _draft(policy_id="POLICY-r2")
    storage_policy.try_set_validated("POLICY-r2")
    row = storage_policy.try_revoke("POLICY-r2", "no longer needed")
    assert row["status"] == storage_policy.REVOKED


def test_try_revoke_from_active():
    _draft(policy_id="POLICY-r3")
    storage_policy.try_set_validated("POLICY-r3")
    storage_policy.try_set_active("POLICY-r3")
    row = storage_policy.try_revoke("POLICY-r3", "edge degraded")
    assert row["status"] == storage_policy.REVOKED


def test_try_revoke_after_revoke_is_a_no_op():
    _draft(policy_id="POLICY-r4")
    storage_policy.try_revoke("POLICY-r4", "first")
    before = storage_policy.get_policy("POLICY-r4")
    storage_policy.try_revoke("POLICY-r4", "second attempt")
    after = storage_policy.get_policy("POLICY-r4")
    assert after == before


def test_find_active_policy_returns_empty_when_none_active():
    assert storage_policy.find_active_policy("EURUSD", "H4", "TRENDING") == []


def test_find_active_policy_finds_exactly_the_matching_scope():
    _draft(policy_id="POLICY-f1", symbol="EURUSD", timeframe="H4", regime_profile="TRENDING")
    storage_policy.try_set_validated("POLICY-f1")
    storage_policy.try_set_active("POLICY-f1")

    found = storage_policy.find_active_policy("EURUSD", "H4", "TRENDING")
    assert len(found) == 1
    assert found[0]["policy_id"] == "POLICY-f1"
    assert storage_policy.find_active_policy("EURUSD", "H4", "RANGING") == []
    assert storage_policy.find_active_policy("GBPUSD", "H4", "TRENDING") == []


def test_every_transition_against_revoked_is_a_no_op():
    _draft(policy_id="POLICY-term1")
    storage_policy.try_revoke("POLICY-term1", "cancelled")
    before = storage_policy.get_policy("POLICY-term1")

    storage_policy.try_set_validated("POLICY-term1")
    storage_policy.try_set_active("POLICY-term1")
    storage_policy.try_revoke("POLICY-term1", "again")

    after = storage_policy.get_policy("POLICY-term1")
    assert after == before
    assert after["status"] == storage_policy.REVOKED
