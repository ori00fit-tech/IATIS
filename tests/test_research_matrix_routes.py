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


def test_list_families_requires_auth(client):
    assert client.get("/research/matrix/families").status_code == 401


def test_get_family_requires_auth(client):
    assert client.get("/research/matrix/families/x").status_code == 401


def test_family_summary_requires_auth(client):
    assert client.get("/research/matrix/families/x/summary").status_code == 401


def test_cell_evidence_requires_auth(client):
    assert client.get("/research/matrix/cells/MATRIX-CELL-x/evidence").status_code == 401


def test_run_batch_requires_auth(client):
    assert client.post("/research/matrix/run-batch", json={"family_id": "x", "batch_size": 5}).status_code == 401


def test_run_status_requires_auth(client):
    assert client.get("/research/matrix/runs/x").status_code == 401


def test_list_runs_requires_auth(client):
    assert client.get("/research/matrix/runs").status_code == 401


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


def test_list_families_empty_by_default(client):
    resp = client.get("/research/matrix/families", headers=HDR)
    assert resp.status_code == 200
    assert resp.json() == {"families": [], "count": 0}


def test_list_families_reflects_real_generated_families(client):
    fam_a = _generate(client, symbols=["EURUSD"]).json()["family_id"]
    fam_b = _generate(client, symbols=["GBPUSD"]).json()["family_id"]
    resp = client.get("/research/matrix/families", headers=HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    ids = {f["family_id"] for f in body["families"]}
    assert ids == {fam_a, fam_b}


def test_list_families_rejects_out_of_range_limit(client):
    assert client.get("/research/matrix/families", headers=HDR, params={"limit": 0}).status_code == 400
    assert client.get("/research/matrix/families", headers=HDR, params={"limit": 501}).status_code == 400


# ---------------------------------------------------------------------------
# GET /research/matrix/families/{id}/summary -- Phase 2A Evidence Read Model
# ---------------------------------------------------------------------------


def test_family_summary_404_for_unknown(client):
    resp = client.get("/research/matrix/families/does-not-exist/summary", headers=HDR)
    assert resp.status_code == 404


def test_family_summary_reports_fixed_planned_n_and_status_breakdown(client):
    body = _generate(client, symbols=["EURUSD", "GBPUSD"]).json()
    fam = body["family_id"]

    resp = client.get(f"/research/matrix/families/{fam}/summary", headers=HDR)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["family"]["family_id"] == fam
    assert payload["family"]["planned_n"] == 2
    assert payload["family"]["cells_generated"] == 2
    # every cell is freshly QUEUED right after /generate
    assert payload["family"]["status_breakdown"]["QUEUED"] == 2
    assert payload["family"]["status_breakdown"]["VALIDATED"] == 0
    assert payload["family"]["total_requeues"] == 0
    assert payload["by_symbol"]["EURUSD"]["total"] == 1
    assert payload["by_symbol"]["GBPUSD"]["total"] == 1
    assert payload["by_bundle"]["SMC only"]["total"] == 2
    assert payload["by_risk_preset"]["balanced"]["total"] == 2


def test_family_summary_never_touches_other_families(client):
    fam_a = _generate(client, symbols=["EURUSD"]).json()["family_id"]
    _generate(client, symbols=["GBPUSD"])  # a second, independent family

    resp = client.get(f"/research/matrix/families/{fam_a}/summary", headers=HDR)
    payload = resp.json()
    assert payload["family"]["cells_generated"] == 1
    assert list(payload["by_symbol"].keys()) == ["EURUSD"]


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
# GET /research/matrix/cells/{id}/evidence -- Phase 2A Evidence Read Model
# ---------------------------------------------------------------------------


def test_cell_evidence_404_for_unknown(client):
    resp = client.get("/research/matrix/cells/MATRIX-CELL-doesnotexist/evidence", headers=HDR)
    assert resp.status_code == 404


def test_cell_evidence_decodes_json_and_has_no_stage_detail_before_any_stage_ran(client):
    _generate(client)
    cell_id = client.get("/research/matrix/cells", headers=HDR).json()["cells"][0]["cell_id"]

    resp = client.get(f"/research/matrix/cells/{cell_id}/evidence", headers=HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cell_id"] == cell_id
    assert body["bundle"] == {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    assert body["status"] == "QUEUED"
    assert body["requeue_count"] == 0
    assert body["stage_a"]["trial_detail"] is None
    assert body["stage_b"]["validation_detail"] is None


def test_cell_evidence_joins_real_stage_a_and_stage_b_detail(client):
    """A cell that has actually reached Stage A/B (simulated by writing the
    same D1 rows the real orchestrator would) must have its evidence
    endpoint return the REAL joined trial/validation rows, not just the
    cell's own denormalized summary columns."""
    from datetime import datetime, timezone

    import backtest.research_matrix as rm
    from storage import research_matrix as storage
    from storage import research_mission_validations
    from storage import research_missions

    _generate(client)
    cell_id = client.get("/research/matrix/cells", headers=HDR).json()["cells"][0]["cell_id"]
    cell = storage.get_cell(cell_id)

    now = datetime.now(timezone.utc).isoformat()
    mission_id = "mtx-testmission"
    research_missions.upsert_mission(
        mission_id=mission_id, name="matrix-cell-test", sampler="grid", objective_metric="profit_factor",
        symbols=[cell["symbol"]], n_trials_per_symbol=1, min_trades=20, seed=42,
        search_space={}, config={}, status="finished",
    )
    research_missions.record_trial(
        mission_id=mission_id, trial_number=0, symbol=cell["symbol"], state="COMPLETE",
        objective_value=1.8, params={}, metrics={"profit_factor": 1.8}, trades=50,
        error=None, started_at=now, finished_at=now,
    )
    validation_id = "mtxval-test"
    research_mission_validations.upsert_validation(
        validation_id=validation_id, mission_id=mission_id, trial_number=0,
        trial_symbol=cell["symbol"], validation_symbols=[cell["symbol"]],
        objective_metric="profit_factor", criteria={}, status="finished", validation_mode="SAME_SYMBOL",
    )
    research_mission_validations.set_validation_status(
        validation_id, "finished", finished=True, overall_verdict="SAME_SYMBOL_CONFIRMED", passing_symbols=1, total_symbols=1,
    )
    storage.update_cell(
        cell_id, status=rm.VALIDATED,
        stage_a_mission_id=mission_id, stage_a_trial_number=0,
        stage_b_validation_id=validation_id, stage_b_verdict="SAME_SYMBOL_CONFIRMED",
    )

    resp = client.get(f"/research/matrix/cells/{cell_id}/evidence", headers=HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage_a"]["trial_detail"] is not None
    assert body["stage_a"]["trial_detail"]["state"] == "COMPLETE"
    assert body["stage_a"]["trial_detail"]["trades"] == 50
    assert body["stage_b"]["validation_detail"] is not None
    assert body["stage_b"]["validation_detail"]["overall_verdict"] == "SAME_SYMBOL_CONFIRMED"


# ---------------------------------------------------------------------------
# GET /research/matrix/cells/compare -- Phase 2C Evidence Comparison
# ---------------------------------------------------------------------------


def test_compare_requires_auth(client):
    assert client.get("/research/matrix/cells/compare", params={"cell_ids": "a,b"}).status_code == 401


def test_compare_rejects_too_few_cell_ids(client):
    resp = client.get("/research/matrix/cells/compare", headers=HDR, params={"cell_ids": "only-one"})
    assert resp.status_code == 400


def test_compare_rejects_too_many_cell_ids(client):
    ids = ",".join(f"MATRIX-CELL-{i}" for i in range(11))
    resp = client.get("/research/matrix/cells/compare", headers=HDR, params={"cell_ids": ids})
    assert resp.status_code == 400


def test_compare_unknown_cell_id_is_reported_not_found_without_failing_the_request(client):
    fam = _generate(client).json()["family_id"]
    cell_id = client.get("/research/matrix/cells", headers=HDR, params={"family_id": fam}).json()["cells"][0]["cell_id"]
    resp = client.get(
        "/research/matrix/cells/compare", headers=HDR,
        params={"cell_ids": f"{cell_id},MATRIX-CELL-doesnotexist"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert any(c["found"] is True for c in body["cells"])
    assert any(c["found"] is False and c.get("cell_id") == "MATRIX-CELL-doesnotexist" for c in body["cells"])
    # provenance is computed only over the cells that were actually found
    assert body["provenance"]["cell_count"] == 1


def test_compare_returns_none_provenance_when_no_cells_found(client):
    resp = client.get(
        "/research/matrix/cells/compare", headers=HDR,
        params={"cell_ids": "MATRIX-CELL-nope1,MATRIX-CELL-nope2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provenance"] is None
    assert all(c["found"] is False for c in body["cells"])


def test_compare_within_family_same_family_true(client):
    """Comparison Type 1 -- within-family, PRIMARY: cells from one
    /generate call share family_id/planned_n/family_alpha."""
    fam = _generate(client, symbols=["EURUSD", "GBPUSD"]).json()["family_id"]
    cell_ids = [c["cell_id"] for c in client.get("/research/matrix/cells", headers=HDR, params={"family_id": fam}).json()["cells"]]
    resp = client.get(
        "/research/matrix/cells/compare", headers=HDR,
        params={"cell_ids": ",".join(cell_ids[:2])},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provenance"]["same_family"] is True
    assert len(body["provenance"]["family_ids"]) == 1
    # each returned cell carries its own evidence join, not just the raw row
    for c in body["cells"]:
        assert "stage_a" in c
        assert "stage_b" in c


def test_compare_cross_family_flags_different_families_and_commits(client):
    """Comparison Type 2 -- cross-family, DESCRIPTIVE ONLY: cells from two
    different /generate calls must never be reported as same_family, and
    the response must expose each family_id so a UI can render the
    required warning rather than a unified ranking."""
    fam_a = _generate(client, symbols=["EURUSD"]).json()["family_id"]
    fam_b = _generate(client, symbols=["GBPUSD"]).json()["family_id"]
    fam_a_cell = client.get("/research/matrix/cells", headers=HDR, params={"family_id": fam_a}).json()["cells"][0]["cell_id"]
    fam_b_cell = client.get("/research/matrix/cells", headers=HDR, params={"family_id": fam_b}).json()["cells"][0]["cell_id"]

    resp = client.get(
        "/research/matrix/cells/compare", headers=HDR,
        params={"cell_ids": f"{fam_a_cell},{fam_b_cell}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provenance"]["same_family"] is False
    assert len(body["provenance"]["family_ids"]) == 2


def test_compare_detects_same_hypothesis_lineage_across_commits(client):
    """Comparison Type 3 -- same symbol/bundle/risk_preset across
    different research_code_commit values, simulating the same hypothesis
    re-run after a code change (the operator's own EURUSD/Bundle X/
    balanced example: commit A -> commit B -> commit C)."""
    import backtest.research_matrix as rm
    from storage import research_matrix as storage

    fam = "lineage-fam"
    storage.upsert_family(fam, planned_n=3, family_alpha=0.05)
    cells = [
        rm.MatrixCellSpec(symbol="EURUSD", bundle=_BUNDLE, risk_preset="balanced", research_code_commit=f"commit{c}")
        for c in ("A", "B", "C")
    ]
    storage.upsert_cells(cells, fam)

    resp = client.get(
        "/research/matrix/cells/compare", headers=HDR,
        params={"cell_ids": ",".join(c.cell_id for c in cells)},
    )
    assert resp.status_code == 200
    body = resp.json()
    prov = body["provenance"]
    assert prov["same_hypothesis_lineage"] is True
    assert prov["lineage_key"] == {"symbol": "EURUSD", "bundle": "SMC only", "risk_preset": "balanced"}
    assert prov["same_commit"] is False
    assert sorted(prov["commits"]) == ["commitA", "commitB", "commitC"]
    for c in body["cells"]:
        assert c["research_code_commit"] in ("commitA", "commitB", "commitC")


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


def test_list_runs_empty_by_default(client):
    resp = client.get("/research/matrix/runs", headers=HDR)
    assert resp.status_code == 200
    assert resp.json() == {"runs": [], "count": 0}


def test_list_runs_scopes_by_family_id(client):
    from storage import research_matrix as storage

    fam_a = _generate(client, symbols=["EURUSD"]).json()["family_id"]
    fam_b = _generate(client, symbols=["GBPUSD"]).json()["family_id"]
    storage.upsert_run("runA", status="running", batch_size=5, family_id=fam_a)
    storage.upsert_run("runB", status="running", batch_size=5, family_id=fam_b)

    all_resp = client.get("/research/matrix/runs", headers=HDR)
    assert all_resp.json()["count"] == 2

    scoped_resp = client.get("/research/matrix/runs", headers=HDR, params={"family_id": fam_a})
    scoped_body = scoped_resp.json()
    assert scoped_body["count"] == 1
    assert scoped_body["runs"][0]["run_id"] == "runA"


def test_list_runs_rejects_out_of_range_limit(client):
    assert client.get("/research/matrix/runs", headers=HDR, params={"limit": 0}).status_code == 400
    assert client.get("/research/matrix/runs", headers=HDR, params={"limit": 501}).status_code == 400


def test_run_batch_appears_in_the_shared_job_catalog_whitelist():
    from execution.routes.experiments import _JOB_COMMANDS
    assert "matrix_batch" in _JOB_COMMANDS
