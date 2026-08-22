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


def test_run_batch_requires_auth(client):
    assert client.post("/research/matrix/run-batch", json={"batch_size": 5}).status_code == 401


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
    resp = client.post(
        "/research/matrix/generate", headers=HDR,
        json={"symbols": ["EURUSD", "GBPUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]},
    )
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
# GET /research/matrix/cells[/{id}]
# ---------------------------------------------------------------------------


def test_list_cells_empty_by_default(client):
    resp = client.get("/research/matrix/cells", headers=HDR)
    assert resp.status_code == 200
    assert resp.json() == {"cells": [], "count": 0}


def test_list_cells_reflects_generated_cells(client):
    client.post("/research/matrix/generate", headers=HDR, json={"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]})
    resp = client.get("/research/matrix/cells", headers=HDR)
    assert resp.json()["count"] == 1


def test_list_cells_rejects_unknown_status(client):
    resp = client.get("/research/matrix/cells", headers=HDR, params={"status": "NOT_A_STATUS"})
    assert resp.status_code == 400


def test_get_cell_404_for_unknown(client):
    resp = client.get("/research/matrix/cells/MATRIX-CELL-doesnotexist", headers=HDR)
    assert resp.status_code == 404


def test_get_cell_returns_the_real_row(client):
    client.post("/research/matrix/generate", headers=HDR, json={"symbols": ["EURUSD"], "bundles": [_BUNDLE], "risk_presets": ["balanced"]})
    cell_id = client.get("/research/matrix/cells", headers=HDR).json()["cells"][0]["cell_id"]
    resp = client.get(f"/research/matrix/cells/{cell_id}", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["cell_id"] == cell_id


# ---------------------------------------------------------------------------
# POST /research/matrix/run-batch
# ---------------------------------------------------------------------------


def test_run_batch_rejects_out_of_range_batch_size(client):
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"batch_size": 0})
    assert resp.status_code == 400
    resp2 = client.post("/research/matrix/run-batch", headers=HDR, json={"batch_size": 999})
    assert resp2.status_code == 400


def test_run_batch_rejects_bad_dates(client):
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"batch_size": 5, "start": "not-a-date"})
    assert resp.status_code == 400


def test_run_batch_submits_a_matrix_batch_job(client):
    resp = client.post("/research/matrix/run-batch", headers=HDR, json={"batch_size": 5, "stage_b_batch_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["job"] == "matrix_batch"


def test_run_status_404_for_unknown_run(client):
    resp = client.get("/research/matrix/runs/nope", headers=HDR)
    assert resp.status_code == 404


def test_run_status_returns_job_and_run_after_submission(client):
    run_id = client.post("/research/matrix/run-batch", headers=HDR, json={"batch_size": 5}).json()["run_id"]
    resp = client.get(f"/research/matrix/runs/{run_id}", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["job"] is not None


def test_run_batch_appears_in_the_shared_job_catalog_whitelist():
    from execution.routes.experiments import _JOB_COMMANDS
    assert "matrix_batch" in _JOB_COMMANDS
