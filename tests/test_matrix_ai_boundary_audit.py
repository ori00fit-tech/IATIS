"""
tests/test_matrix_ai_boundary_audit.py
-------------------------------------------
Hypothesis Discovery Engine, Phase 3B-H — AI Boundary Forensic Audit.

Four properties the operator required verified, in writing, before any
Phase 3C (Controlled Recommendation Conversion) work may begin:

  1. Snapshot immutability — a recommendation's evidence_snapshot/hash/
     input scope/constraints never change after creation, regardless of
     what happens to the underlying Matrix state afterward.
  2. Approval != research action — moving a recommendation to APPROVED
     creates zero research_matrix_cells/families rows and touches zero
     live-trading config.
  3. proposed_next_cells remain UNTRUSTED input — a hallucinated/invalid
     AI-proposed symbol is rejected by POST /research/matrix/generate
     exactly like any hand-typed one; the AI layer itself never validates
     domain semantics (that boundary lives at /generate, not /propose).
  4. Constraint provenance — constraints_used carries a real, content-
     sensitive research_code_commit/dead_list_hash, not just a boolean.

Matches tests/test_matrix_ai_routes.py's client/HDR fixture convention.
"""
from __future__ import annotations

import hashlib
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

# A plan that hallucinates a symbol/engine/risk_preset nothing in this
# codebase has ever configured — proves the AI layer performs no domain
# validation of its own (property 3's other half: /propose must accept
# this as structurally complete; /generate is the only place that may
# ever reject it).
_HALLUCINATED_PLAN_JSON = (
    '{"reasoning_summary": "A completely fabricated combination.", '
    '"coverage_gaps": [], '
    '"proposed_next_cells": [{"symbol": "ZZZFAKEUSD", "bundle_name": "Imaginary bundle", '
    '"timeframes": ["H4"], "engines": ["not_a_real_engine"], "risk_preset": "yolo", '
    '"rationale": "hallucinated"}], '
    '"distinct_from_dead_list": "n/a", '
    '"priority": "LOW"}'
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


def _propose(client, monkeypatch, family_id: str, plan_json: str = _FULL_VALID_PLAN_JSON) -> dict:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with patch.object(GeminiProvider, "_chat", return_value=plan_json):
        resp = client.post("/research/matrix/ai/propose", headers=HDR, json={"family_ids": [family_id]})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok", resp.json()
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Snapshot immutability
# ---------------------------------------------------------------------------


def test_review_never_touches_the_snapshot_scope_or_constraint_fields(client, monkeypatch):
    fam, _ = _generate(client)
    rec = _propose(client, monkeypatch, fam)
    rec_id = rec["recommendation_id"]

    before = client.get(f"/research/matrix/ai/recommendations/{rec_id}", headers=HDR).json()
    client.post(f"/research/matrix/ai/recommendations/{rec_id}/review", headers=HDR, json={"status": "APPROVED"})
    after = client.get(f"/research/matrix/ai/recommendations/{rec_id}", headers=HDR).json()

    immutable_fields = (
        "evidence_snapshot_json", "evidence_snapshot_hash", "input_family_ids_json", "input_cell_ids_json",
        "constraints_used_json", "reasoning_summary", "coverage_gaps_json", "proposed_next_cells_json",
        "distinct_from_dead_list", "priority", "provider", "model", "created_at",
    )
    for field in immutable_fields:
        assert before[field] == after[field], f"{field} changed after review — snapshot is not immutable"
    # only the review-workflow fields are allowed to change
    assert after["status"] == "APPROVED"
    assert before["status"] == "DRAFT"


def test_later_cell_status_changes_never_retroactively_alter_a_recommendations_snapshot(client, monkeypatch):
    """A recommendation's evidence_snapshot is a point-in-time COPY, never
    a live reference -- proving this requires actually mutating the
    underlying cell after the recommendation was created and confirming
    the persisted snapshot still shows the OLD state."""
    from storage import research_matrix as storage

    fam, cell_id = _generate(client)
    rec = _propose(client, monkeypatch, fam)
    rec_id = rec["recommendation_id"]

    row = client.get(f"/research/matrix/ai/recommendations/{rec_id}", headers=HDR).json()
    import json
    snapshot_before = json.loads(row["evidence_snapshot_json"])
    original_status_in_snapshot = snapshot_before["families"][0]["coverage"]["by_symbol"]["EURUSD"]["QUEUED"]
    assert original_status_in_snapshot == 1  # the cell was QUEUED when the snapshot was taken

    # Mutate the real, live cell state well after the recommendation exists.
    storage.update_cell(cell_id, status="SCREENED", stage_a_p_value=0.01)

    row_after = client.get(f"/research/matrix/ai/recommendations/{rec_id}", headers=HDR).json()
    assert row_after["evidence_snapshot_json"] == row["evidence_snapshot_json"]  # byte-identical, untouched
    snapshot_after = json.loads(row_after["evidence_snapshot_json"])
    assert snapshot_after["families"][0]["coverage"]["by_symbol"]["EURUSD"]["QUEUED"] == 1  # still says QUEUED, not SCREENED


# ---------------------------------------------------------------------------
# 2. Approval must not mutate research state
# ---------------------------------------------------------------------------


def test_approving_a_recommendation_creates_zero_new_cells_or_families(client, monkeypatch):
    from storage import research_matrix as storage

    fam, _ = _generate(client)
    rec = _propose(client, monkeypatch, fam)

    cells_before = storage.list_cells(limit=5000)
    families_before = storage.list_families(limit=5000)

    client.post(
        f"/research/matrix/ai/recommendations/{rec['recommendation_id']}/review", headers=HDR,
        json={"status": "APPROVED", "reviewed_by": "operator", "review_note": "looks promising"},
    )

    cells_after = storage.list_cells(limit=5000)
    families_after = storage.list_families(limit=5000)
    assert {c["cell_id"] for c in cells_after} == {c["cell_id"] for c in cells_before}
    assert {f["family_id"] for f in families_after} == {f["family_id"] for f in families_before}


def test_matrix_ai_module_never_calls_any_research_matrix_write_function():
    """Static proof, not just a runtime observation: neither the router nor
    the storage module powering AI recommendations references any of the
    functions that actually mutate research_matrix_cells/families. Mirrors
    tests/test_matrix_orchestrator.py's own forbidden-target source scan."""
    import inspect

    from execution.routes import matrix_ai as router_module
    from storage import matrix_ai_recommendations as recs_module

    forbidden_calls = (
        "upsert_cells(", "upsert_family(", "update_cell(", "claim_queued_cells(",
        "claim_candidate_cells(", "requeue_stale_running_cells(", "requeue_stale_validating_cells(",
    )
    for module in (router_module, recs_module):
        source = inspect.getsource(module)
        for call in forbidden_calls:
            assert call not in source, f"{module.__name__} references forbidden research-state mutator {call!r}"


def test_approval_endpoint_response_never_mentions_hxxx_or_registry():
    """A structural guard against ever wiring registry-style language into
    the review response -- APPROVED must always read as 'human reviewed
    this recommendation,' never 'hypothesis approved.'"""
    import inspect

    from execution.routes import matrix_ai as router_module

    source = inspect.getsource(router_module.matrix_ai_review_recommendation)
    for forbidden in ("HXXX", "registry.json", "PASSED", "hypothesis"):
        assert forbidden not in source


def test_config_and_registry_untouched_by_a_full_propose_approve_cycle(client, monkeypatch):
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("research/results/registry.json")]
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in watched if p.exists()}

    fam, _ = _generate(client)
    rec = _propose(client, monkeypatch, fam)
    client.post(f"/research/matrix/ai/recommendations/{rec['recommendation_id']}/review", headers=HDR, json={"status": "APPROVED"})

    for p in watched:
        if p in before:
            content, mtime = before[p]
            assert p.read_bytes() == content
            assert p.stat().st_mtime == mtime


# ---------------------------------------------------------------------------
# 3. Proposed cells remain untrusted input
# ---------------------------------------------------------------------------


def test_ai_layer_accepts_a_hallucinated_symbol_as_structurally_complete(client, monkeypatch):
    """By design: AIAnalyzer only checks that required fields are PRESENT,
    never that a symbol/engine/risk_preset is REAL. This is not a gap to
    close in this router -- POST /generate is the one and only semantic
    validation boundary (see the next test)."""
    fam, _ = _generate(client)
    rec = _propose(client, monkeypatch, fam, plan_json=_HALLUCINATED_PLAN_JSON)
    assert rec["status"] == "ok"
    assert rec["proposed_next_cells"][0]["symbol"] == "ZZZFAKEUSD"


def test_generate_endpoint_rejects_a_hallucinated_ai_proposed_symbol_exactly_like_hand_typed_input(client):
    """The actual enforcement point: whether a symbol came from a human
    typing it or was copied verbatim out of an AI recommendation's
    proposed_next_cells, POST /research/matrix/generate applies the SAME
    _configured_symbol_universe() check either way -- this router has no
    special-cased bypass for AI-sourced data because it has no way to even
    know a /generate call originated from a recommendation."""
    resp = client.post(
        "/research/matrix/generate", headers=HDR,
        json={"symbols": ["ZZZFAKEUSD"], "bundles": [{"name": "Imaginary bundle", "timeframes": ["H4"], "engines": ["not_a_real_engine"]}], "risk_presets": ["balanced"]},
    )
    assert resp.status_code == 400
    assert "Unknown symbol" in resp.json()["detail"]


def test_generate_endpoint_rejects_a_hallucinated_risk_preset(client):
    resp = client.post(
        "/research/matrix/generate", headers=HDR,
        json={"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["yolo"]},
    )
    assert resp.status_code == 400
    assert "Unknown risk_preset" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. Constraint provenance
# ---------------------------------------------------------------------------


def test_constraints_used_carries_research_code_commit_and_dirty_flag(client, monkeypatch):
    from backtest import research_matrix as rm

    fam, _ = _generate(client)
    rec = _propose(client, monkeypatch, fam)
    row = client.get(f"/research/matrix/ai/recommendations/{rec['recommendation_id']}", headers=HDR).json()

    import json
    constraints = json.loads(row["constraints_used_json"])
    expected = rm.resolve_research_code_commit()
    assert constraints["research_code_commit"] == expected["commit"]
    assert constraints["research_code_dirty"] == expected["dirty"]


def test_constraints_used_dead_list_hash_reflects_the_exact_text_used(client, monkeypatch):
    from execution.routes import matrix_ai as router_module

    with patch.object(router_module, "_dead_list_text", return_value="a specific dead list snapshot"):
        fam, _ = _generate(client)
        rec = _propose(client, monkeypatch, fam)

    row = client.get(f"/research/matrix/ai/recommendations/{rec['recommendation_id']}", headers=HDR).json()
    import json
    constraints = json.loads(row["constraints_used_json"])
    assert constraints["dead_list_provided"] is True
    assert constraints["dead_list_hash"] == hashlib.sha256(b"a specific dead list snapshot").hexdigest()


def test_constraints_used_dead_list_hash_changes_when_the_dead_list_content_changes(client, monkeypatch):
    from execution.routes import matrix_ai as router_module

    fam, _ = _generate(client)
    with patch.object(router_module, "_dead_list_text", return_value="version A of the dead list"):
        rec_a = _propose(client, monkeypatch, fam)
    with patch.object(router_module, "_dead_list_text", return_value="version B of the dead list"):
        rec_b = _propose(client, monkeypatch, fam)

    import json
    hash_a = json.loads(client.get(f"/research/matrix/ai/recommendations/{rec_a['recommendation_id']}", headers=HDR).json()["constraints_used_json"])["dead_list_hash"]
    hash_b = json.loads(client.get(f"/research/matrix/ai/recommendations/{rec_b['recommendation_id']}", headers=HDR).json()["constraints_used_json"])["dead_list_hash"]
    assert hash_a != hash_b


def test_constraints_used_dead_list_hash_is_none_when_dead_list_unavailable(client, monkeypatch):
    from execution.routes import matrix_ai as router_module

    with patch.object(router_module, "_dead_list_text", return_value=None):
        fam, _ = _generate(client)
        rec = _propose(client, monkeypatch, fam)

    row = client.get(f"/research/matrix/ai/recommendations/{rec['recommendation_id']}", headers=HDR).json()
    import json
    constraints = json.loads(row["constraints_used_json"])
    assert constraints["dead_list_provided"] is False
    assert constraints["dead_list_hash"] is None
