"""
tests/test_matrix_ai_routes.py
----------------------------------
Contract tests for execution/routes/matrix_ai.py (Hypothesis Discovery
Engine, Phase 3B — AI Research Orchestrator). Matches tests/
test_research_matrix_routes.py's established client/HDR fixture
convention, and tests/test_ai_settings.py's own AI-provider-mocking
convention (set a fake API key + patch the provider's _chat()).
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"
    from execution.api_server import app
    from ai.providers.gemini import GeminiProvider
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="fastapi not installed")

HDR = {"X-API-Key": "test-key-123"}
_BUNDLE = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"]}

_FULL_VALID_PLAN_JSON = (
    '{"reasoning_summary": "XAUUSD has no NNFX-bundle cells tested yet.", '
    '"coverage_gaps": ["XAUUSD x NNFX trend x balanced untested"], '
    '"proposed_next_cells": [{"symbol": "XAUUSD", "bundle_name": "NNFX trend", '
    '"timeframes": ["H4"], "engines": ["nnfx"], "risk_preset": "balanced", '
    '"rationale": "coverage gap, not adjacent to any dead-list idea"}], '
    '"distinct_from_dead_list": "not a liquidity sweep or SMC idea", '
    '"priority": "MEDIUM"}'
)


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _generate(client, **overrides):
    body = {"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]}
    body.update(overrides)
    resp = client.post("/research/matrix/generate", headers=HDR, json=body)
    family_id = resp.json()["family_id"]
    cell_id = client.get("/research/matrix/cells", headers=HDR, params={"family_id": family_id}).json()["cells"][0]["cell_id"]
    return family_id, cell_id


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def test_propose_requires_auth(client):
    assert client.post("/research/matrix/ai/propose", json={"family_ids": ["x"]}).status_code == 401


def test_list_recommendations_requires_auth(client):
    assert client.get("/research/matrix/ai/recommendations").status_code == 401


def test_get_recommendation_requires_auth(client):
    assert client.get("/research/matrix/ai/recommendations/MATRIX-AI-x").status_code == 401


def test_review_requires_auth(client):
    assert client.post("/research/matrix/ai/recommendations/MATRIX-AI-x/review", json={"status": "APPROVED"}).status_code == 401


# ---------------------------------------------------------------------------
# POST /research/matrix/ai/propose
# ---------------------------------------------------------------------------


def test_propose_requires_at_least_one_scope(client):
    resp = client.post("/research/matrix/ai/propose", headers=HDR, json={})
    assert resp.status_code == 400


def test_propose_rejects_unknown_family_id(client):
    resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"family_ids": ["does-not-exist"]})
    assert resp.status_code == 400


def test_propose_rejects_unknown_cell_id(client):
    resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"cell_ids": ["MATRIX-CELL-doesnotexist"]})
    assert resp.status_code == 400


def test_propose_with_no_api_key_returns_disabled_and_persists_nothing(client):
    fam, _ = _generate(client)
    resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"family_ids": [fam]})
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"

    listed = client.get("/research/matrix/ai/recommendations", headers=HDR).json()
    assert listed["count"] == 0


def test_propose_ok_persists_a_draft_recommendation_with_full_audit_trail(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fam, _ = _generate(client)

    with patch.object(GeminiProvider, "_chat", return_value=_FULL_VALID_PLAN_JSON):
        resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"family_ids": [fam], "focus_hint": "metals"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["recommendation_id"].startswith("MATRIX-AI-")
    assert body["evidence_snapshot_hash"]
    assert body["proposed_next_cells"][0]["symbol"] == "XAUUSD"

    row = client.get(f"/research/matrix/ai/recommendations/{body['recommendation_id']}", headers=HDR).json()
    assert row["status"] == "DRAFT"
    assert row["provider"] == "gemini"
    assert row["evidence_snapshot_hash"] == body["evidence_snapshot_hash"]
    assert row["reasoning_summary"] == "XAUUSD has no NNFX-bundle cells tested yet."
    import json
    assert json.loads(row["input_family_ids_json"]) == [fam]
    assert json.loads(row["proposed_next_cells_json"])[0]["symbol"] == "XAUUSD"
    assert json.loads(row["constraints_used_json"])["risk_preset_names"]  # governance constraints captured


def test_propose_accepts_scoped_cell_ids_for_rejection_explanation(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fam, cell_id = _generate(client)

    with patch.object(GeminiProvider, "_chat", return_value=_FULL_VALID_PLAN_JSON):
        resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"cell_ids": [cell_id]})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    rec_id = resp.json()["recommendation_id"]
    row = client.get(f"/research/matrix/ai/recommendations/{rec_id}", headers=HDR).json()
    import json
    assert json.loads(row["input_cell_ids_json"]) == [cell_id]
    snapshot = json.loads(row["evidence_snapshot_json"])
    assert snapshot["scoped_cells"][0]["cell_id"] == cell_id


def test_propose_malformed_ai_response_returns_error_and_persists_nothing(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fam, _ = _generate(client)

    with patch.object(GeminiProvider, "_chat", return_value="not json at all"):
        resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"family_ids": [fam]})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert client.get("/research/matrix/ai/recommendations", headers=HDR).json()["count"] == 0


def test_propose_rejects_unknown_provider_override(client):
    fam_id = _generate(client)[0]
    resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"family_ids": [fam_id], "provider": "deepseek"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /research/matrix/ai/recommendations (+ /{id})
# ---------------------------------------------------------------------------


def test_list_recommendations_empty_by_default(client):
    resp = client.get("/research/matrix/ai/recommendations", headers=HDR)
    assert resp.status_code == 200
    assert resp.json() == {"recommendations": [], "count": 0}


def test_list_recommendations_rejects_bad_status_and_limit(client):
    assert client.get("/research/matrix/ai/recommendations", headers=HDR, params={"status": "VALIDATED"}).status_code == 400
    assert client.get("/research/matrix/ai/recommendations", headers=HDR, params={"limit": 0}).status_code == 400
    assert client.get("/research/matrix/ai/recommendations", headers=HDR, params={"limit": 999}).status_code == 400


def test_get_recommendation_404_for_unknown(client):
    resp = client.get("/research/matrix/ai/recommendations/MATRIX-AI-doesnotexist", headers=HDR)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /research/matrix/ai/recommendations/{id}/review
# ---------------------------------------------------------------------------


def _propose_one(client, monkeypatch) -> str:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fam, _ = _generate(client)
    with patch.object(GeminiProvider, "_chat", return_value=_FULL_VALID_PLAN_JSON):
        resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"family_ids": [fam]})
    return resp.json()["recommendation_id"]


def test_review_approves_a_recommendation(client, monkeypatch):
    rec_id = _propose_one(client, monkeypatch)
    resp = client.post(
        f"/research/matrix/ai/recommendations/{rec_id}/review", headers=HDR,
        json={"status": "APPROVED", "reviewed_by": "alice", "review_note": "worth trying"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "APPROVED"
    assert body["reviewed_by"] == "alice"
    assert body["review_note"] == "worth trying"


def test_review_rejects_a_recommendation(client, monkeypatch):
    rec_id = _propose_one(client, monkeypatch)
    resp = client.post(f"/research/matrix/ai/recommendations/{rec_id}/review", headers=HDR, json={"status": "REJECTED"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
    assert resp.json()["reviewed_by"] == "operator"  # default


def test_review_404_for_unknown_recommendation(client):
    resp = client.post("/research/matrix/ai/recommendations/MATRIX-AI-doesnotexist/review", headers=HDR, json={"status": "APPROVED"})
    assert resp.status_code == 404


def test_review_rejects_invalid_target_status(client, monkeypatch):
    rec_id = _propose_one(client, monkeypatch)
    resp = client.post(f"/research/matrix/ai/recommendations/{rec_id}/review", headers=HDR, json={"status": "VALIDATED"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Hard-block safety -- this router may never write live-trading config.
# ---------------------------------------------------------------------------


def test_config_and_registry_files_are_byte_identical_after_a_propose_and_review_cycle(client, monkeypatch):
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("research/results/registry.json")]
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in watched if p.exists()}

    rec_id = _propose_one(client, monkeypatch)
    client.post(f"/research/matrix/ai/recommendations/{rec_id}/review", headers=HDR, json={"status": "APPROVED"})

    for p in watched:
        if p in before:
            content, mtime = before[p]
            assert p.read_bytes() == content, f"{p} content changed after a Matrix AI propose/review cycle"
            assert p.stat().st_mtime == mtime, f"{p} mtime changed after a Matrix AI propose/review cycle"
