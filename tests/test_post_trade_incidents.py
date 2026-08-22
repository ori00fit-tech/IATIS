"""tests/test_post_trade_incidents.py
---------------------------------------
storage/post_trade_incidents.py — the Unified Post-Trade Control /
Incident Register's storage layer: dedup-by-fingerprint, the state
machine (OPEN -> ACKNOWLEDGED -> RESOLVED, or -> WAIVED), and the
never-delete / never-mutate-history invariants.
"""
from __future__ import annotations

import pytest

from storage.post_trade_incidents import (
    ACKNOWLEDGED,
    CRITICAL,
    EXECUTION_TIMEOUT_UNKNOWN,
    HIGH,
    OPEN,
    RECONCILIATION_MISMATCH,
    RESOLVED,
    WAIVED,
    acknowledge_incident,
    counts_by_severity,
    counts_by_status,
    get_incident,
    has_terminal_incident,
    list_incidents,
    resolve_incident,
    upsert_incident,
    waive_incident,
)


def _make(fingerprint="FP:1", control_type=RECONCILIATION_MISMATCH, severity=CRITICAL, **kw):
    return upsert_incident(
        fingerprint=fingerprint, control_type=control_type, severity=severity,
        source_component="test", **kw,
    )


def test_upsert_creates_a_new_open_incident():
    incident_id = _make(symbol="EURUSD", actual_state="broker only")
    assert incident_id
    row = get_incident(incident_id)
    assert row["status"] == OPEN
    assert row["symbol"] == "EURUSD"
    assert row["control_type"] == RECONCILIATION_MISMATCH
    assert row["severity"] == CRITICAL
    assert row["detected_at"] == row["last_seen_at"]


def test_upsert_same_fingerprint_bumps_last_seen_not_a_new_row():
    id1 = _make(fingerprint="FP:dup", actual_state="v1")
    id2 = _make(fingerprint="FP:dup", actual_state="v2")
    assert id1 == id2
    rows = list_incidents(control_type=RECONCILIATION_MISMATCH)
    assert len([r for r in rows if r["fingerprint"] == "FP:dup"]) == 1
    assert get_incident(id1)["actual_state"] == "v2"


def test_upsert_after_resolve_creates_a_fresh_incident_not_reopen():
    id1 = _make(fingerprint="FP:reopen")
    resolve_incident(id1, actor="tester", reason="fixed")
    id2 = _make(fingerprint="FP:reopen")
    assert id2 != id1
    assert get_incident(id1)["status"] == RESOLVED
    assert get_incident(id2)["status"] == OPEN


def test_upsert_rejects_unknown_control_type():
    with pytest.raises(ValueError):
        upsert_incident(fingerprint="x", control_type="NOT_A_TYPE", severity=CRITICAL, source_component="test")


def test_upsert_rejects_unknown_severity():
    with pytest.raises(ValueError):
        upsert_incident(fingerprint="x", control_type=RECONCILIATION_MISMATCH, severity="ULTRA", source_component="test")


def test_evidence_reference_round_trips_as_dict():
    incident_id = _make(evidence_reference={"n_broker": 3, "symbols": ["EURUSD"]})
    row = get_incident(incident_id)
    assert row["evidence_reference"] == {"n_broker": 3, "symbols": ["EURUSD"]}


def test_list_incidents_filters_by_status_severity_control_type():
    a = _make(fingerprint="A", control_type=RECONCILIATION_MISMATCH, severity=CRITICAL)
    b = _make(fingerprint="B", control_type=EXECUTION_TIMEOUT_UNKNOWN, severity=HIGH)
    acknowledge_incident(a, actor="tester")

    assert {r["incident_id"] for r in list_incidents(status=ACKNOWLEDGED)} == {a}
    assert {r["incident_id"] for r in list_incidents(severity=HIGH)} == {b}
    assert {r["incident_id"] for r in list_incidents(control_type=EXECUTION_TIMEOUT_UNKNOWN)} == {b}


def test_acknowledge_requires_open_status():
    incident_id = _make()
    acknowledge_incident(incident_id, actor="tester", note="looking into it")
    assert get_incident(incident_id)["status"] == ACKNOWLEDGED
    with pytest.raises(ValueError):
        acknowledge_incident(incident_id, actor="tester")


def test_acknowledge_unknown_incident_raises():
    with pytest.raises(ValueError):
        acknowledge_incident("does-not-exist", actor="tester")


def test_resolve_requires_non_blank_reason():
    incident_id = _make()
    with pytest.raises(ValueError):
        resolve_incident(incident_id, actor="tester", reason="   ")


def test_resolve_from_open_and_from_acknowledged():
    id1 = _make(fingerprint="R1")
    resolve_incident(id1, actor="tester", reason="root-caused")
    assert get_incident(id1)["status"] == RESOLVED

    id2 = _make(fingerprint="R2")
    acknowledge_incident(id2, actor="tester")
    resolve_incident(id2, actor="tester", reason="fixed after triage")
    assert get_incident(id2)["status"] == RESOLVED


def test_resolve_never_mutates_detected_at_or_evidence():
    incident_id = _make(evidence_reference={"original": True})
    original = get_incident(incident_id)
    resolve_incident(incident_id, actor="tester", reason="done", evidence={"extra": 1})
    after = get_incident(incident_id)
    assert after["detected_at"] == original["detected_at"]
    assert after["evidence_reference"] == {"original": True}
    assert after["resolution"] == {"reason": "done", "evidence": {"extra": 1}}


def test_resolve_already_terminal_raises():
    incident_id = _make()
    resolve_incident(incident_id, actor="tester", reason="done")
    with pytest.raises(ValueError):
        resolve_incident(incident_id, actor="tester", reason="again")


def test_waive_requires_non_blank_reason_and_sets_fields():
    incident_id = _make()
    with pytest.raises(ValueError):
        waive_incident(incident_id, actor="tester", reason="")
    waive_incident(incident_id, actor="tester", reason="known false positive")
    row = get_incident(incident_id)
    assert row["status"] == WAIVED
    assert row["waive_reason"] == "known false positive"
    assert row["waived_by"] == "tester"
    assert row["waived_at"]


def test_waive_already_terminal_raises():
    incident_id = _make()
    waive_incident(incident_id, actor="tester", reason="one-off")
    with pytest.raises(ValueError):
        waive_incident(incident_id, actor="tester", reason="again")


def test_counts_by_status_and_severity():
    _make(fingerprint="C1", severity=CRITICAL)
    id2 = _make(fingerprint="C2", severity=HIGH)
    acknowledge_incident(id2, actor="tester")

    by_status = counts_by_status()
    assert by_status.get(OPEN) == 1
    assert by_status.get(ACKNOWLEDGED) == 1

    by_sev = counts_by_severity()
    assert by_sev.get(CRITICAL) == 1
    assert by_sev.get(HIGH) == 1


def test_counts_by_severity_excludes_terminal_by_default():
    incident_id = _make(severity=CRITICAL)
    resolve_incident(incident_id, actor="tester", reason="done")
    assert counts_by_severity().get(CRITICAL, 0) == 0
    assert counts_by_severity(status=RESOLVED).get(CRITICAL) == 1


def test_has_terminal_incident_false_for_open_and_unknown():
    assert has_terminal_incident("does-not-exist") is False
    incident_id = _make(fingerprint="HT1")
    assert has_terminal_incident("HT1") is False
    resolve_incident(incident_id, actor="tester", reason="done")
    assert has_terminal_incident("HT1") is True


def test_never_deletes_a_row_across_the_full_lifecycle():
    incident_id = _make(fingerprint="LIFECYCLE")
    acknowledge_incident(incident_id, actor="tester")
    resolve_incident(incident_id, actor="tester", reason="closed the loop")
    # Still readable, still the same row (no new incident_id minted).
    row = get_incident(incident_id)
    assert row is not None
    assert row["incident_id"] == incident_id
    assert row["status"] == RESOLVED
