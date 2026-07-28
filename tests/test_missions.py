"""
tests/test_missions.py
--------------------------
AI Research Lab / Mission Center Phase 2 (2026-07-28) — contract tests
for execution/routes/missions.py, matching tests/test_api_contract.py's
established fixtures/conventions (client/HDR, the _FakeProc pattern that
avoids spawning a real subprocess for argv-shape assertions).
"""
from __future__ import annotations

import os
import time

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

_VALID_BODY = {
    "symbols": ["EURUSD"],
    "sampler": "random",
    "n_trials_per_symbol": 2,
    "timeframes_choices": [["H1"]],
    "engine_set_choices": [["nnfx", "price_action"]],
}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


class _FakeProc:
    """Matches tests/test_api_contract.py's own fake — avoids spawning a
    real `python3 -m backtest.mission_runner` subprocess for tests that
    only assert on argv shape or job-state transitions."""

    def __init__(self, argv, **kwargs):
        _FakeProc.captured_argv = argv
        self.stdout = iter([])
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _FakeBlockingProc:
    """Never exits on its own (stdout blocks forever) — for cancel tests
    that need a job to still be "running" when cancel() is called."""

    def __init__(self, argv, **kwargs):
        _FakeBlockingProc.captured_argv = argv
        self.returncode = None
        self.killed = False

    @property
    def stdout(self):
        import queue
        # A generator that blocks forever (never yields, never raises) —
        # simulates a subprocess whose output loop never reaches EOF
        # until kill() sets the flag.
        def _gen():
            q = queue.Queue()
            while not self.killed:
                try:
                    yield q.get(timeout=0.05)
                except queue.Empty:
                    continue
        return _gen()

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9


def _wait_for_terminal(client, mission_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/research/missions/{mission_id}", headers=HDR).json()
        job_status = body.get("job_status")
        if job_status not in ("queued", "running"):
            return body
        time.sleep(0.05)
    return body


# ── Auth ──────────────────────────────────────────────────────────────────

def test_missions_create_requires_auth(client):
    assert client.post("/research/missions", json=_VALID_BODY).status_code == 401


def test_missions_list_requires_auth(client):
    assert client.get("/research/missions").status_code == 401


def test_missions_status_requires_auth(client):
    assert client.get("/research/missions/does-not-exist").status_code == 401


def test_missions_leaderboard_requires_auth(client):
    assert client.get("/research/missions/does-not-exist/leaderboard").status_code == 401


def test_missions_cancel_requires_auth(client):
    assert client.post("/research/missions/does-not-exist/cancel").status_code == 401


# ── Validation ────────────────────────────────────────────────────────────

def test_missions_create_rejects_unknown_symbol(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "symbols": ["ZZZFAKE"]}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown symbol" in r.json()["detail"]


def test_missions_create_rejects_empty_symbols(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "symbols": []}, headers=HDR)
    assert r.status_code == 400


def test_missions_create_rejects_bad_sampler(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "sampler": "not_a_sampler"}, headers=HDR)
    assert r.status_code == 400
    assert "sampler" in r.json()["detail"]


def test_missions_create_rejects_bad_objective_metric(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "objective_metric": "made_up_metric"}, headers=HDR)
    assert r.status_code == 400


def test_missions_create_rejects_n_trials_out_of_bounds(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "n_trials_per_symbol": 0}, headers=HDR)
    assert r.status_code == 400
    r2 = client.post("/research/missions", json={**_VALID_BODY, "n_trials_per_symbol": 999_999}, headers=HDR)
    assert r2.status_code == 400


def test_missions_create_rejects_unknown_engine_in_search_space(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "engine_set_choices": [["not_a_real_engine"]]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_unknown_timeframe_in_search_space(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "timeframes_choices": [["M5"]]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_risk_param_out_of_bounds(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "risk_param_ranges": {"sl_atr_multiplier": [-5.0, 999.0]}},
        headers=HDR,
    )
    assert r.status_code == 400
    assert "risk_param_ranges" in r.json()["detail"]


def test_missions_create_rejects_both_risk_forms_set(client):
    r = client.post(
        "/research/missions",
        json={
            **_VALID_BODY,
            "risk_param_ranges": {"sl_atr_multiplier": [1.0, 3.0]},
            "risk_param_grid": {"sl_atr_multiplier": [1.5, 2.0]},
        },
        headers=HDR,
    )
    assert r.status_code == 400
    assert "XOR" in r.json()["detail"]


def test_missions_create_rejects_bad_start_end_order(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "start": "2024-06-01", "end": "2024-01-01"},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_bad_name(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "name": "bad;name"}, headers=HDR)
    assert r.status_code == 400


# ── Dispatch / argv ────────────────────────────────────────────────────────

def test_missions_create_builds_expected_argv_and_returns_mission_id(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "mission_id" in body and len(body["mission_id"]) > 0

    _wait_for_terminal(client, body["mission_id"])
    argv = _FakeProc.captured_argv
    assert "backtest.mission_runner" in argv
    assert "--mission-id" in argv and body["mission_id"] in argv
    assert "--symbols" in argv and "EURUSD" in argv
    assert "--sampler" in argv and "random" in argv


def test_missions_list_includes_created_mission(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    mission_id = r.json()["mission_id"]
    _wait_for_terminal(client, mission_id)

    listing = client.get("/research/missions", headers=HDR)
    assert listing.status_code == 200
    ids = [m["job_id"] for m in listing.json()["missions"]]
    assert mission_id in ids


def test_missions_status_404_when_unknown(client):
    r = client.get("/research/missions/does-not-exist-at-all", headers=HDR)
    assert r.status_code == 404


def test_missions_leaderboard_returns_empty_for_unknown_mission(client):
    r = client.get("/research/missions/does-not-exist-at-all/leaderboard", headers=HDR)
    assert r.status_code == 200
    assert r.json()["trials"] == []


# ── Cancel ────────────────────────────────────────────────────────────────

def test_missions_cancel_404_when_unknown(client):
    r = client.post("/research/missions/does-not-exist-at-all/cancel", headers=HDR)
    assert r.status_code == 404


def test_missions_cancel_kills_running_job(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeBlockingProc)

    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    mission_id = r.json()["mission_id"]

    # Give _run_job's background thread a moment to actually reach the
    # subprocess.Popen call and flip status to "running".
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        status = client.get(f"/research/missions/{mission_id}", headers=HDR).json()
        if status.get("job_status") == "running":
            break
        time.sleep(0.02)

    cancel = client.post(f"/research/missions/{mission_id}/cancel", headers=HDR)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
