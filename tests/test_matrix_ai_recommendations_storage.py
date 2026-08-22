"""tests/test_matrix_ai_recommendations_storage.py -- D1 round-trip tests
for storage/matrix_ai_recommendations.py (Phase 3B audit trail)."""
from __future__ import annotations

import pytest

from storage import matrix_ai_recommendations as recs


def _record(recommendation_id="MATRIX-AI-abc123", **overrides):
    kwargs = {
        "provider": "anthropic", "model": "claude-sonnet-4-6",
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
    assert row["status"] == recs.DRAFT
    assert row["reasoning_summary"] == "gap in XAUUSD coverage"
    assert row["priority"] == "MEDIUM"
    import json
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
        "MATRIX-AI-minimal", provider="gemini", model="gemini-flash-latest",
        input_family_ids=["fam1"], input_cell_ids=None,
        evidence_snapshot={}, evidence_snapshot_hash="h", constraints_used={},
        focus_hint=None, reasoning_summary="r", coverage_gaps=None,
        proposed_next_cells=[{"symbol": "EURUSD"}], distinct_from_dead_list=None, priority=None,
    )
    row = recs.get_recommendation("MATRIX-AI-minimal")
    assert row["focus_hint"] is None
    assert row["coverage_gaps_json"] is None
    assert row["priority"] is None
