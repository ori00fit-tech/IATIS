"""tests/test_live_roster_storage.py -- storage-layer tests for
storage/live_roster.py (Phase 15A): the ACTIVE/REMOVED lifecycle and the
DB-level active-hypothesis uniqueness invariant, independent of
backtest.live_roster's own promotion_gate re-validation layer."""
from __future__ import annotations

from storage import live_roster as storage_roster


def _insert(**overrides) -> dict:
    base = dict(
        roster_entry_id="ROSTER-test0001", hypothesis_id="CONFLUENCE-HYPOTHESIS-x",
        added_by="alice", added_reason="SHADOW-eligible per promotion gate",
        promotion_gate_snapshot_json='{"eligibility": "ELIGIBLE", "target_stage": "SHADOW"}',
    )
    base.update(overrides)
    return storage_roster.try_insert_active(**base)


def test_try_insert_active_creates_a_row_with_status_active():
    row = _insert()
    assert row["status"] == storage_roster.ACTIVE
    assert row["removed_at"] is None
    assert row["removed_by"] is None
    assert row["removed_reason"] is None


def test_get_roster_entry_returns_none_for_unknown_id():
    assert storage_roster.get_roster_entry("ROSTER-ghost") is None


def test_try_insert_active_blocked_by_the_real_db_level_uniqueness_index():
    """The core proof: NOT a Python len()-check -- a second ACTIVE entry
    for the EXACT SAME hypothesis_id fails to insert because the
    database's own partial UNIQUE index refuses it, not because this
    module counted anything."""
    first = _insert(roster_entry_id="ROSTER-u1", hypothesis_id="CONFLUENCE-HYPOTHESIS-dup")
    second = _insert(roster_entry_id="ROSTER-u2", hypothesis_id="CONFLUENCE-HYPOTHESIS-dup")

    assert first is not None
    assert first["status"] == storage_roster.ACTIVE
    assert second is None  # denied -- nothing inserted
    assert storage_roster.get_roster_entry("ROSTER-u2") is None


def test_try_insert_active_is_not_blocked_across_different_hypotheses():
    first = _insert(roster_entry_id="ROSTER-d1", hypothesis_id="CONFLUENCE-HYPOTHESIS-a")
    second = _insert(roster_entry_id="ROSTER-d2", hypothesis_id="CONFLUENCE-HYPOTHESIS-b")
    assert first["status"] == storage_roster.ACTIVE
    assert second["status"] == storage_roster.ACTIVE


def test_try_insert_active_after_removal_allows_re_adding():
    """REMOVED is not terminal for the hypothesis_id itself -- once a row
    is REMOVED, the partial unique index no longer blocks a fresh ACTIVE
    insert for that same hypothesis_id (a genuinely new Roster admission,
    never a revival of the old row)."""
    _insert(roster_entry_id="ROSTER-re1", hypothesis_id="CONFLUENCE-HYPOTHESIS-cycle")
    storage_roster.try_remove("ROSTER-re1", "bob", "no longer eligible")

    second = _insert(roster_entry_id="ROSTER-re2", hypothesis_id="CONFLUENCE-HYPOTHESIS-cycle")
    assert second is not None
    assert second["status"] == storage_roster.ACTIVE


def test_find_active_entry_for_hypothesis_returns_none_when_absent():
    assert storage_roster.find_active_entry_for_hypothesis("CONFLUENCE-HYPOTHESIS-none") is None


def test_find_active_entry_for_hypothesis_finds_exactly_the_matching_row():
    _insert(roster_entry_id="ROSTER-f1", hypothesis_id="CONFLUENCE-HYPOTHESIS-find")
    found = storage_roster.find_active_entry_for_hypothesis("CONFLUENCE-HYPOTHESIS-find")
    assert found is not None
    assert found["roster_entry_id"] == "ROSTER-f1"


def test_list_active_entries_excludes_removed_rows():
    _insert(roster_entry_id="ROSTER-l1", hypothesis_id="CONFLUENCE-HYPOTHESIS-l1")
    _insert(roster_entry_id="ROSTER-l2", hypothesis_id="CONFLUENCE-HYPOTHESIS-l2")
    storage_roster.try_remove("ROSTER-l2", "bob", "removed")

    active_ids = {row["roster_entry_id"] for row in storage_roster.list_active_entries()}
    assert "ROSTER-l1" in active_ids
    assert "ROSTER-l2" not in active_ids


def test_try_remove_flips_active_to_removed():
    _insert(roster_entry_id="ROSTER-r1")
    row = storage_roster.try_remove("ROSTER-r1", "bob", "evidence degraded")
    assert row["status"] == storage_roster.REMOVED
    assert row["removed_at"] is not None
    assert row["removed_by"] == "bob"
    assert row["removed_reason"] == "evidence degraded"


def test_try_remove_on_unknown_id_returns_none():
    assert storage_roster.try_remove("ROSTER-ghost", "bob", "n/a") is None


def test_try_remove_after_remove_is_a_no_op():
    _insert(roster_entry_id="ROSTER-r2")
    storage_roster.try_remove("ROSTER-r2", "bob", "first")
    before = storage_roster.get_roster_entry("ROSTER-r2")
    storage_roster.try_remove("ROSTER-r2", "carol", "second attempt")
    after = storage_roster.get_roster_entry("ROSTER-r2")
    assert after == before
    assert after["removed_by"] == "bob"
