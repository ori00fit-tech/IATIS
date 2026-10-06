"""tests/test_shadow_divergence_membership_storage.py -- storage-layer
tests for storage/shadow_divergence_membership.py (Family Membership
Writer, Design Gate locked 2026-10): the insert shape, the DB-level
hypothesis_id PRIMARY KEY uniqueness invariant, and count_members(),
independent of backtest.shadow_divergence_membership's own "attempted"
decision layer."""
from __future__ import annotations

from storage import shadow_divergence_membership as storage_membership


def _insert(**overrides) -> dict | None:
    base = dict(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-x", hypothesis_fingerprint="FP-x",
        research_code_commit="abc123", tp_count_at_entry=28, sl_count_at_entry=12,
        p_value_at_entry=0.0004, baseline_p_at_entry=0.65,
        request_ids=["R1", "R2", "R3"],
    )
    base.update(overrides)
    return storage_membership.try_insert_membership(**base)


def test_try_insert_creates_a_row_with_all_fields():
    row = _insert()
    assert row["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-x"
    assert row["hypothesis_fingerprint"] == "FP-x"
    assert row["research_code_commit"] == "abc123"
    assert row["tp_count_at_entry"] == 28
    assert row["sl_count_at_entry"] == 12
    assert row["p_value_at_entry"] == 0.0004
    assert row["baseline_p_at_entry"] == 0.65
    assert row["request_ids"] == ["R1", "R2", "R3"]
    assert row["entered_at"] is not None


def test_get_membership_returns_none_when_absent():
    assert storage_membership.get_membership("CONFLUENCE-HYPOTHESIS-ghost") is None


def test_try_insert_blocked_by_the_real_db_level_hypothesis_id_uniqueness():
    """The core proof: NOT a Python len()-check -- a second membership
    for the EXACT SAME hypothesis_id fails to insert because the
    database's own PRIMARY KEY refuses it, not because this module
    counted anything."""
    first = _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-dup", tp_count_at_entry=30, sl_count_at_entry=10)
    second = _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-dup", tp_count_at_entry=5, sl_count_at_entry=5)
    assert first is not None
    assert second is None  # denied -- nothing inserted, original untouched
    assert storage_membership.get_membership("CONFLUENCE-HYPOTHESIS-dup")["tp_count_at_entry"] == 30


def test_try_insert_is_not_blocked_across_different_hypothesis_ids():
    first = _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-d1")
    second = _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-d2")
    assert first is not None
    assert second is not None


def test_get_membership_finds_the_matching_row_request_ids_decoded():
    _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-f1", request_ids=["RA", "RB"])
    found = storage_membership.get_membership("CONFLUENCE-HYPOTHESIS-f1")
    assert found is not None
    assert found["request_ids"] == ["RA", "RB"]
    assert isinstance(found["request_ids"], list)


def test_count_members_reflects_actual_rows():
    before = storage_membership.count_members()
    _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-count-a")
    _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-count-b")
    after = storage_membership.count_members()
    assert after == before + 2


def test_count_members_unaffected_by_duplicate_attempt():
    before = storage_membership.count_members()
    _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-count-dup")
    _insert(hypothesis_id="CONFLUENCE-HYPOTHESIS-count-dup")  # denied, no new row
    after = storage_membership.count_members()
    assert after == before + 1
