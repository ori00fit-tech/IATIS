"""
tests/test_research_matrix_routes.py
----------------------------------------
Contract tests for execution/routes/research_matrix.py, matching
tests/test_missions.py's established client/HDR/_FakeProc fixture
conventions.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"
    from execution.api_server import app
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="fastapi not installed")

HDR = {"X-API-Key": "test-key-123"}

_BUNDLE = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"]}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


class _FakeProc:
    """Mirrors tests/test_missions.py's own fake subprocess — matrix_batch
    jobs are never actually run in these contract tests."""
    def __init__(self, argv, **kwargs):
        _FakeProc.captured_argv = argv
        self.stdout = iter([])
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


@pytest.fixture(autouse=True)
def _fake_subprocess(monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)


def _generate(client, **overrides):
    body = {"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]}
    body.update(overrides)
    return client.post("/research/matrix/generate", headers=HDR, json=body)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def test_generate_requires_auth(client):
    resp = client.post("/research/matrix/generate", json={"bundles": [_BUNDLE]})
    assert resp.status_code == 401


def test_list_cells_requires_auth(client):
    assert client.get("/research/matrix/cells").status_code == 401


def test_get_cell_requires_auth(client):
    assert client.get("/research/matrix/cells/MATRIX-CELL-x").status_code == 401


def test_get_family_requires_auth(client):
    assert client.get("/research/matrix/families/x").status_code == 401


def test_run_batch_requires_auth(client):
    assert client.post("/research/matrix/run-batch", json={"family_id": "x", "batch_size": 5}).status_code == 401


def test_run_status_requires_auth(client):
    assert client.get("/research/matrix/runs/x").status_code == 401


# ---------------------------------------------------------------------------
# POST /research/matrix/generate
# ---------------------------------------------------------------------------


def test_generate_rejects_unknown_symbol(client):
    resp = client.post("/research/matrix/generate", headers=HDR, json={"symbols": ["NOTREAL"], "bundles": [_BUNDLE]})
    assert resp.status_code == 400


def test_generate_rejects_empty_bundles(client):
    resp = client.post("/research/matrix/generate", headers=HDR, json={"symbols": ["EURUSD"], "bundles": []})
    assert resp.status_code == 400


def test_generate_rejects_unknown_risk_preset(client):
    resp = client.post(
        "/research/matrix/generate", headers=HDR,
        json={"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["yolo"]},
    )
    assert resp.status_code == 400


def test_generate_rejects_bundle_without_a_name(client):
    resp = client.post(
        "/research/matrix/generate", headers=HDR,
        json={"symbols": ["EURUSD"], "bundles": [{"timeframes": ["H1"]}]},
    )
    assert resp.status_code == 422  # Pydantic: _BundleSpec.name is required


def test_generate_succeeds_and_reports_insert_count(client):
    resp = _generate(client, symbols=["EURUSD", "GBPUSD"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] == 2
    assert body["duplicate"] == 0


def test_generate_dedupes_an_identical_second_call(client):
    body = {"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]}
    client.post("/research/matrix/generate", headers=HDR, json=body)
    resp = client.post("/research/matrix/generate", headers=HDR, json=body)
    assert resp.json()["duplicate"] == 1
    assert resp.json()["inserted"] == 0


def test_generate_defaults_to_every_configured_symbol_when_symbols_omitted(client):
    resp = client.post(
        "/research/matrix/generate", headers=HDR,
        json={"bundles": [_BUNDLE], "risk_presets": ["balanced"]},
    )
    assert resp.status_code == 200
    assert resp.json()["cells_considered"] == 24  # every configured symbol x 1 bundle x 1 preset


def test_generate_rejects_a_spec_over_the_cell_cap(client, monkeypatch):
    import execution.routes.research_matrix as rmod
    monkeypatch.setattr(rmod, "_MAX_CELLS_PER_GENERATE", 2)
    resp = client.post(
        "/research/matrix/generate", headers=HDR,
        json={"symbols": ["EURUSD", "GBPUSD", "XAUUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /research/matrix/generate -- Finding 4 (fixed research family)
# ---------------------------------------------------------------------------


def test_generate_creates_a_family_with_planned_n_fixed_to_cells_considered(client):
    resp = _generate(client, symbols=["EURUSD", "GBPUSD"])
    body = resp.json()
    assert "family_id" in body and body["family_id"]
    assert body["planned_n"] == body["cells_considered"] == 2

    fam_resp = client.get(f"/research/matrix/families/{body['family_id']}", headers=HDR)
    assert fam_resp.status_code == 200
    fam = fam_resp.json()
    assert fam["planned_n"] == 2
    assert fam["family_alpha"] == 0.05


def test_two_separate_generate_calls_mint_two_independent_families(client):
    body1 = _generate(client, symbols=["EURUSD"]).json()
    body2 = _generate(client, symbols=["GBPUSD"]).json()
    assert body1["family_id"] != body2["family_id"]


def test_get_family_404_for_unknown(client):
    resp = client.get("/research/matrix/families/does-not-exist", headers=HDR)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /research/matrix/generate -- Finding 2 (commit-sensitive fingerprint)
# ---------------------------------------------------------------------------


def test_generate_response_reports_the_resolved_research_code_commit(client, monkeypatch):
    import backtest.research_matrix as rm
    monkeypatch.setattr(rm, "resolve_research_code_commit", lambda: {"commit": "abc111", "dirty": False})
    resp = _generate(client)
    body = resp.json()
    assert body["research_code_commit"] == "abc111"
    assert body["research_code_dirty"] is False


def test_a_code_commit_change_produces_a_different_fingerprint_through_the_real_generate_route(client, monkeypatch):
    """Finding 2's real-path proof: same symbols/bundles/risk_presets, but
    the resolved research-code commit differs between two /generate calls
    -> every resulting cell_id (== MATRIX-CELL-<fingerprint>) differs too,
    exercised through the actual HTTP route, not just the low-level
    compute_cell_fingerprint()/resolve_research_code_commit() helpers."""
    import backtest.research_matrix as rm

    monkeypatch.setattr(rm, "resolve_research_code_commit", lambda: {"commit": "commitA", "dirty": False})
    cells_a = {c["cell_id"] for c in client.get("/research/matrix/cells", headers=HDR).json()["cells"]}
    _generate(client, symbols=["EURUSD"])
    cells_after_a = {c["cell_id"] for c in client.get("/research/matrix/cells", headers=HDR).json()["cells"]}
    new_from_a = cells_after_a - cells_a
    assert len(new_from_a) == 1

    monkeypatch.setattr(rm, "resolve_research_code_commit", lambda: {"commit": "commitB", "dirty": False})
    _generate(client, symbols=["EURUSD"])
    cells_after_b = {c["cell_id"] for c in client.get("/research/matrix/cells", headers=HDR).json()["cells"]}
    new_from_b = cells_after_b - cells_after_a
    assert len(new_from_b) == 1

    # different commit -> genuinely different cell_id, never a reuse of commitA's cell
    assert new_from_a != new_from_b


def test_identical_commit_across_two_calls_still_dedupes_as_before(client, monkeypatch):
    import backtest.research_matrix as rm
    monkeypatch.setattr(rm, "resolve_research_code_commit", lambda: {"commit": "same-commit", "dirty": False})
    body = {"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]}
    client.post("/research/matrix/generate", headers=HDR, json=body)
    resp = client.post("/research/matrix/generate", headers=HDR, json=body)
    assert resp.json()["duplicate"] == 1
    assert resp.json()["inserted"] == 0


# ---------------------------------------------------------------------------
# GET /research/matrix/cells[/{id}]
# ---------------------------------------------------------------------------


def test_list_cells_empty_by_default(client):
    resp = client.get("/research/matrix/cells", headers=HDR)
    assert resp.status_code == 200
    assert resp.json() == {"cells": [], "count": 0}


def test_list_cells_reflects_generated_cells(client):
    _generate(client)
    resp = client.get("/research/matrix/cells", headers=HDR)
    assert resp.json()["count"] == 1


def test_list_cells_rejects_unknown_status(client):
    resp = client.get("/research/matrix/cells", headers=HDR, params={"status": "NOT_A_STATUS"})
    assert resp.status_code == 400


def test_list_cells_filters_by_family_id(client):
    fam_a = _generate(client, symbols=["EURUSD"]).json()["family_id"]
    _generate(client, symbols=["GBPUSD"])

    resp = client.get("/research/matrix/cells", headers=HDR, params={"family_id": fam_a})
    body = resp.json()
    assert body["count"] == 1
    assert body["cells"][0]["symbol"] == "EURUSD"


def test_get_cell_404_for_unknown(client):
    resp = client.get("/research/matrix/cells/MATRIX-CELL-doesnotexist", headers=HDR)
    assert resp.status_code == 404


def test_get_cell_returns_the_real_row(client):
    _generate(client)
    cell_id = client.get("/research/matrix/cells", headers=HDR).json()["cells"][0]["cell_id"]
    resp = client.get(f"/research/matrix/cells/{cell_id}", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["cell_id"] == cell_id


# ---------------------------------------------------------------------------
# POST /research/matrix/run-batch
# ---------------------------------------------------------------------------


def test_run_batch_requires_family_id(client):
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"batch_size": 5})
    assert resp.status_code == 422  # Pydantic: family_id has no default


def test_run_batch_rejects_an_unknown_family_id(client):
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"family_id": "does-not-exist", "batch_size": 5})
    assert resp.status_code == 400


def test_run_batch_rejects_out_of_range_batch_size(client):
    fam = _generate(client).json()["family_id"]
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"family_id": fam, "batch_size": 0})
    assert resp.status_code == 400
    resp2 = client.post("/research/matrix/run-batch", headers=HDR, json={"family_id": fam, "batch_size": 999})
    assert resp2.status_code == 400


def test_run_batch_rejects_bad_dates(client):
    fam = _generate(client).json()["family_id"]
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"family_id": fam, "batch_size": 5, "start": "not-a-date"})
    assert resp.status_code == 400


def test_run_batch_submits_a_matrix_batch_job(client):
    fam = _generate(client).json()["family_id"]
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"family_id": fam, "batch_size": 5, "stage_b_batch_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["job"] == "matrix_batch"

    # Read the argv the route itself froze into the Job object synchronously
    # at request time — _FakeProc.captured_argv is set only once the actual
    # subprocess.Popen() call fires on a background ThreadPoolExecutor
    # worker (execution/routes/experiments.py::_run_job), which is
    # inherently asynchronous relative to this response and races under a
    # loaded full test-suite run; job.argv is the deterministic source of
    # truth for what the route decided to launch.
    from execution.routes.experiments import _jobs
    job = _jobs[body["run_id"]]
    assert "--family-id" in job.argv
    assert fam in job.argv


def test_run_status_404_for_unknown_run(client):
    resp = client.get("/research/matrix/runs/nope", headers=HDR)
    assert resp.status_code == 404


def test_run_status_returns_job_and_run_after_submission(client):
    fam = _generate(client).json()["family_id"]
    run_id = client.post("/research/matrix/run-batch", headers=HDR, json={"family_id": fam, "batch_size": 5}).json()["run_id"]
    resp = client.get(f"/research/matrix/runs/{run_id}", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["job"] is not None


def test_run_batch_appears_in_the_shared_job_catalog_whitelist():
    from execution.routes.experiments import _JOB_COMMANDS
    assert "matrix_batch" in _JOB_COMMANDS
