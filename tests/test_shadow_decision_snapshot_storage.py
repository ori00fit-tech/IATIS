"""tests/test_shadow_decision_snapshot_storage.py -- storage-layer tests
for storage/shadow_decision_snapshot.py (Phase 15D): the snapshot insert
shape and the DB-level request_id uniqueness invariant, independent of
backtest.shadow_decision_snapshot's own validation/derivation layer."""
from __future__ import annotations

from storage import shadow_decision_snapshot as storage_snapshot


def _insert(**overrides) -> dict | None:
    base = dict(
        snapshot_id="SHADOW-SNAPSHOT-test0001", request_id="LIVE-IDENTITY-REQUEST-test0001",
        hypothesis_id="CONFLUENCE-HYPOTHESIS-x", symbol="EURUSD", timeframe="H4",
        bar_time="2026-09-01T00:00:00+00:00", side="BUY",
        entry_price=1.2345, stop_loss=1.2300, take_profit=1.2450,
    )
    base.update(overrides)
    return storage_snapshot.try_insert(**base)


def test_try_insert_creates_a_row_with_all_fields():
    row = _insert()
    assert row["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-x"
    assert row["side"] == "BUY"
    assert row["entry_price"] == 1.2345
    assert row["stop_loss"] == 1.2300
    assert row["take_profit"] == 1.2450
    assert row["bar_time"] == "2026-09-01T00:00:00+00:00"
    assert row["captured_at"] is not None
    assert row["captured_at"] != row["bar_time"]  # distinct concepts, never conflated


def test_get_snapshot_by_request_id_returns_none_when_absent():
    assert storage_snapshot.get_snapshot_by_request_id("LIVE-IDENTITY-REQUEST-ghost") is None


def test_try_insert_blocked_by_the_real_db_level_request_id_uniqueness_index():
    """The core proof: NOT a Python len()-check -- a second snapshot for
    the EXACT SAME request_id fails to insert because the database's own
    UNIQUE index refuses it, not because this module counted anything."""
    first = _insert(snapshot_id="SHADOW-SNAPSHOT-u1", request_id="LIVE-IDENTITY-REQUEST-dup")
    second = _insert(snapshot_id="SHADOW-SNAPSHOT-u2", request_id="LIVE-IDENTITY-REQUEST-dup",
                      hypothesis_id="CONFLUENCE-HYPOTHESIS-y")
    assert first is not None
    assert second is None  # denied -- nothing inserted
    assert storage_snapshot.get_snapshot_by_request_id("LIVE-IDENTITY-REQUEST-dup")["snapshot_id"] == "SHADOW-SNAPSHOT-u1"


def test_try_insert_is_not_blocked_across_different_request_ids():
    first = _insert(snapshot_id="SHADOW-SNAPSHOT-d1", request_id="LIVE-IDENTITY-REQUEST-d1")
    second = _insert(snapshot_id="SHADOW-SNAPSHOT-d2", request_id="LIVE-IDENTITY-REQUEST-d2")
    assert first is not None
    assert second is not None


def test_get_snapshot_by_request_id_finds_the_matching_row():
    _insert(snapshot_id="SHADOW-SNAPSHOT-f1", request_id="LIVE-IDENTITY-REQUEST-f1")
    found = storage_snapshot.get_snapshot_by_request_id("LIVE-IDENTITY-REQUEST-f1")
    assert found is not None
    assert found["snapshot_id"] == "SHADOW-SNAPSHOT-f1"


# ── list_request_ids_for_hypothesis() -- Gate 0A: Complete Request
# Enumeration (locked 2026-10). The canonical, complete, duplicate-free
# n_T(H) enumeration source. ─────────────────────────────────────────────


def test_list_request_ids_for_hypothesis_empty_when_none_captured():
    assert storage_snapshot.list_request_ids_for_hypothesis("CONFLUENCE-HYPOTHESIS-ghost") == []


def test_list_request_ids_for_hypothesis_returns_every_request_no_limit():
    """Completeness proof: insert more rows for one hypothesis than any
    LIMIT-window convention elsewhere in this codebase defaults to (50),
    and confirm every single one still comes back -- never truncated."""
    hid = "CONFLUENCE-HYPOTHESIS-enum-complete"
    expected = set()
    for i in range(60):
        request_id = f"LIVE-IDENTITY-REQUEST-enum-{i:03d}"
        _insert(snapshot_id=f"SHADOW-SNAPSHOT-enum-{i:03d}", request_id=request_id, hypothesis_id=hid)
        expected.add(request_id)
    result = storage_snapshot.list_request_ids_for_hypothesis(hid)
    assert len(result) == 60
    assert set(result) == expected


def test_list_request_ids_for_hypothesis_is_isolated_by_hypothesis_id():
    """A different hypothesis_id's snapshots must never leak into this
    hypothesis's enumeration -- the WHERE clause, not caller-side
    filtering, is what guarantees this."""
    _insert(snapshot_id="SHADOW-SNAPSHOT-iso-a1", request_id="LIVE-IDENTITY-REQUEST-iso-a1",
            hypothesis_id="CONFLUENCE-HYPOTHESIS-iso-a")
    _insert(snapshot_id="SHADOW-SNAPSHOT-iso-b1", request_id="LIVE-IDENTITY-REQUEST-iso-b1",
            hypothesis_id="CONFLUENCE-HYPOTHESIS-iso-b")
    result_a = storage_snapshot.list_request_ids_for_hypothesis("CONFLUENCE-HYPOTHESIS-iso-a")
    result_b = storage_snapshot.list_request_ids_for_hypothesis("CONFLUENCE-HYPOTHESIS-iso-b")
    assert result_a == ["LIVE-IDENTITY-REQUEST-iso-a1"]
    assert result_b == ["LIVE-IDENTITY-REQUEST-iso-b1"]


def test_list_request_ids_for_hypothesis_returns_plain_strings_not_rows():
    """Locked return shape: list[str] of request_id values -- not
    list[dict], not the full row. The caller needs only the identifier
    to then drive get_snapshot_by_request_id() per request."""
    _insert(snapshot_id="SHADOW-SNAPSHOT-shape1", request_id="LIVE-IDENTITY-REQUEST-shape1",
            hypothesis_id="CONFLUENCE-HYPOTHESIS-shape")
    result = storage_snapshot.list_request_ids_for_hypothesis("CONFLUENCE-HYPOTHESIS-shape")
    assert result == ["LIVE-IDENTITY-REQUEST-shape1"]
    assert all(isinstance(r, str) for r in result)
