"""tests/test_matrix_ai_recommendations_storage.py -- D1 round-trip tests
for storage/matrix_ai_recommendations.py (Phase 3B audit trail)."""
from __future__ import annotations

import json

import pytest

from storage import matrix_ai_recommendations as recs


def _record(recommendation_id="MATRIX-AI-abc123", **overrides):
    kwargs = {
        "provider": "anthropic", "requested_model": "claude-sonnet-4-6", "actual_model": "claude-sonnet-4-6",
        "input_family_ids": ["fam1"], "input_cell_ids": None,
        "evidence_snapshot": {"families": []}, "evidence_snapshot_hash": "deadbeef",
        "constraints_used": {"frozen_engines": ["smc"]}, "focus_hint": "crypto",
        "reasoning_summary": "gap in XAUUSD coverage", "coverage_gaps": ["XAUUSD/nnfx untested"],
        "proposed_next_cells": [{"symbol": "XAUUSD", "bundle_name": "NNFX trend", "timeframes": ["H4"], "engines": ["nnfx"], "risk_preset": "balanced", "rationale": "coverage gap"}],
        "distinct_from_dead_list": "not a liquidity sweep idea", "priority": "MEDIUM",
    }
    kwargs.update(overrides)
    recs.record_recommendation(recommendation_id, **kwargs)


def test_record_and_get_recommendation_round_trip():
    _record()
    row = recs.get_recommendation("MATRIX-AI-abc123")
    assert row is not None
    assert row["provider"] == "anthropic"
    assert row["requested_model"] == "claude-sonnet-4-6"
    assert row["actual_model"] == "claude-sonnet-4-6"
    assert row["status"] == recs.DRAFT
    assert row["reasoning_summary"] == "gap in XAUUSD coverage"
    assert row["priority"] == "MEDIUM"
    assert json.loads(row["input_family_ids_json"]) == ["fam1"]
    assert json.loads(row["proposed_next_cells_json"])[0]["symbol"] == "XAUUSD"


def test_get_recommendation_returns_none_for_unknown_id():
    assert recs.get_recommendation("nope") is None


def test_list_recommendations_empty_by_default():
    assert recs.list_recommendations() == []


def test_list_recommendations_newest_first_and_respects_limit():
    _record("MATRIX-AI-1")
    _record("MATRIX-AI-2")
    _record("MATRIX-AI-3")
    all_recs = recs.list_recommendations(limit=50)
    assert [r["recommendation_id"] for r in all_recs] == ["MATRIX-AI-3", "MATRIX-AI-2", "MATRIX-AI-1"]
    assert len(recs.list_recommendations(limit=1)) == 1


def test_list_recommendations_filters_by_status():
    _record("MATRIX-AI-1")
    _record("MATRIX-AI-2")
    recs.review_recommendation("MATRIX-AI-1", status=recs.APPROVED, reviewed_by="op", review_note="looks good")
    approved = recs.list_recommendations(status=recs.APPROVED)
    assert [r["recommendation_id"] for r in approved] == ["MATRIX-AI-1"]
    drafts = recs.list_recommendations(status=recs.DRAFT)
    assert [r["recommendation_id"] for r in drafts] == ["MATRIX-AI-2"]


def test_review_recommendation_approves_and_records_reviewer():
    _record()
    recs.review_recommendation("MATRIX-AI-abc123", status=recs.APPROVED, reviewed_by="alice", review_note="promising gap")
    row = recs.get_recommendation("MATRIX-AI-abc123")
    assert row["status"] == recs.APPROVED
    assert row["reviewed_by"] == "alice"
    assert row["review_note"] == "promising gap"
    assert row["reviewed_at"] is not None


def test_review_recommendation_never_touches_the_snapshot_or_scope_fields():
    """Phase 3B-H, property 1 -- storage-level proof: review_recommendation()
    only ever changes status/reviewed_by/reviewed_at/review_note. Every
    other column, including the evidence snapshot itself, is byte-
    identical before and after."""
    _record()
    before = recs.get_recommendation("MATRIX-AI-abc123")
    recs.review_recommendation("MATRIX-AI-abc123", status=recs.APPROVED, reviewed_by="alice", review_note="promising gap")
    after = recs.get_recommendation("MATRIX-AI-abc123")

    immutable_fields = (
        "provider", "requested_model", "actual_model", "input_family_ids_json", "input_cell_ids_json",
        "evidence_snapshot_json", "evidence_snapshot_hash", "constraints_used_json", "focus_hint",
        "reasoning_summary", "coverage_gaps_json", "proposed_next_cells_json",
        "distinct_from_dead_list", "priority", "created_at",
    )
    for field in immutable_fields:
        assert before[field] == after[field], f"{field} changed after review_recommendation()"


def test_review_recommendation_rejects():
    _record()
    recs.review_recommendation("MATRIX-AI-abc123", status=recs.REJECTED, reviewed_by="bob", review_note=None)
    assert recs.get_recommendation("MATRIX-AI-abc123")["status"] == recs.REJECTED


def test_review_recommendation_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown recommendation_id"):
        recs.review_recommendation("nope", status=recs.APPROVED, reviewed_by="op", review_note=None)


def test_review_recommendation_rejects_invalid_target_status():
    """Never DRAFT (creation-only) and never a Matrix-cell-style status
    like VALIDATED -- a recommendation's review vocabulary is strictly
    DRAFT/APPROVED/REJECTED."""
    _record()
    with pytest.raises(ValueError):
        recs.review_recommendation("MATRIX-AI-abc123", status="VALIDATED", reviewed_by="op", review_note=None)
    with pytest.raises(ValueError):
        recs.review_recommendation("MATRIX-AI-abc123", status=recs.DRAFT, reviewed_by="op", review_note=None)


def test_record_recommendation_tolerates_missing_optional_fields():
    recs.record_recommendation(
        "MATRIX-AI-minimal", provider="gemini", requested_model=None, actual_model="gemini-flash-latest",
        input_family_ids=["fam1"], input_cell_ids=None,
        evidence_snapshot={}, evidence_snapshot_hash="h", constraints_used={},
        focus_hint=None, reasoning_summary="r", coverage_gaps=None,
        proposed_next_cells=[{"symbol": "EURUSD"}], distinct_from_dead_list=None, priority=None,
    )
    row = recs.get_recommendation("MATRIX-AI-minimal")
    assert row["requested_model"] is None
    assert row["actual_model"] == "gemini-flash-latest"
    assert row["focus_hint"] is None
    assert row["coverage_gaps_json"] is None
    assert row["priority"] is None


def test_record_recommendation_tolerates_unknown_actual_model():
    """Phase 3B-H hardening -- never fabricate a model; None is the
    honest value when it cannot be established."""
    recs.record_recommendation(
        "MATRIX-AI-unknown-model", provider="gemini", requested_model="gemini-flash-latest", actual_model=None,
        input_family_ids=["fam1"], input_cell_ids=None,
        evidence_snapshot={}, evidence_snapshot_hash="h", constraints_used={},
        focus_hint=None, reasoning_summary="r", coverage_gaps=None,
        proposed_next_cells=[{"symbol": "EURUSD"}], distinct_from_dead_list=None, priority=None,
    )
    assert recs.get_recommendation("MATRIX-AI-unknown-model")["actual_model"] is None


# --- Phase 3B-H hardening pass 2: atomic review + append-only history ------


def test_review_recommendation_is_atomic_a_second_review_is_refused_not_overwritten():
    _record()
    recs.review_recommendation("MATRIX-AI-abc123", status=recs.APPROVED, reviewed_by="alice", review_note="first review")
    with pytest.raises(ValueError, match="already reviewed"):
        recs.review_recommendation("MATRIX-AI-abc123", status=recs.REJECTED, reviewed_by="bob", review_note="second review")
    # the FIRST review's outcome survives untouched
    row = recs.get_recommendation("MATRIX-AI-abc123")
    assert row["status"] == recs.APPROVED
    assert row["reviewed_by"] == "alice"
    assert row["review_note"] == "first review"


def test_review_history_records_every_review_action_oldest_first():
    _record()
    recs.review_recommendation("MATRIX-AI-abc123", status=recs.APPROVED, reviewed_by="alice", review_note="first review")
    history = recs.list_recommendation_reviews("MATRIX-AI-abc123")
    assert len(history) == 1
    assert history[0]["old_status"] == recs.DRAFT
    assert history[0]["new_status"] == recs.APPROVED
    assert history[0]["reviewed_by"] == "alice"
    assert history[0]["review_note"] == "first review"


def test_review_history_is_empty_for_a_still_draft_recommendation():
    _record()
    assert recs.list_recommendation_reviews("MATRIX-AI-abc123") == []


def test_review_recommendation_writes_status_and_history_in_one_atomic_batch():
    """Re-audit fix (2026-08-22): storage/d1_client.py's own module
    docstring says a sequence of separate execute() calls within one
    `with d1_connection() as con:` block is NOT atomic as a group -- each
    is its own independent HTTPS request/D1 statement. review_
    recommendation() used to issue the status UPDATE and the history
    INSERT as two such separate calls, so a crash/network failure between
    them could change status with no corresponding history row. This
    proves the fix structurally: exactly one d1_client.d1_batch() call
    carries BOTH statements together (Cloudflare D1's own .batch() API --
    "all succeed or all fail" -- the same primitive storage.decision_db
    already relies on), not two independent d1_connection().execute()
    round-trips."""
    import storage.d1_client as d1_client_module
    from storage import matrix_ai_recommendations as recs_module

    _record()
    calls: list[list[str]] = []
    real_batch = d1_client_module.d1_batch

    def spy_batch(statements):
        calls.append([sql for sql, _params in statements])
        return real_batch(statements)

    original = recs_module.d1_client.d1_batch
    recs_module.d1_client.d1_batch = spy_batch
    try:
        recs.review_recommendation("MATRIX-AI-abc123", status=recs.APPROVED, reviewed_by="alice", review_note="ok")
    finally:
        recs_module.d1_client.d1_batch = original

    assert len(calls) == 1, "status UPDATE + history INSERT must travel in exactly one d1_batch() call"
    assert len(calls[0]) == 2
    assert "UPDATE research_matrix_ai_recommendations" in calls[0][0]
    assert "INSERT INTO research_matrix_ai_recommendation_reviews" in calls[0][1]
    assert "changes() = 1" in calls[0][1]


def test_review_history_accumulates_across_a_refused_second_attempt():
    """The REFUSED second review attempt (rejected by the atomic CAS) must
    NOT itself appear in history -- only review actions that actually
    changed status are recorded."""
    _record()
    recs.review_recommendation("MATRIX-AI-abc123", status=recs.APPROVED, reviewed_by="alice", review_note="ok")
    with pytest.raises(ValueError):
        recs.review_recommendation("MATRIX-AI-abc123", status=recs.REJECTED, reviewed_by="bob", review_note="too late")
    history = recs.list_recommendation_reviews("MATRIX-AI-abc123")
    assert len(history) == 1  # the refused attempt left no trace
    assert history[0]["reviewed_by"] == "alice"
