"""
tests/test_kill_switch_routes.py
------------------------------------
Contract tests for execution/routes/kill_switch.py, matching
tests/test_missions.py's established client/HDR fixture conventions.
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


@pytest.fixture
def client(monkeypatch, tmp_path):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    monkeypatch.setattr("storage.kill_switch.STATE_PATH", tmp_path / "kill_switch.json")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_status_requires_auth(client):
    resp = client.get("/risk/kill-switch")
    assert resp.status_code == 401


def test_activate_requires_auth(client):
    resp = client.post("/risk/kill-switch/activate", json={"reason": "test"})
    assert resp.status_code == 401


def test_deactivate_requires_auth(client):
    resp = client.post("/risk/kill-switch/deactivate")
    assert resp.status_code == 401


def test_status_default_is_inactive(client):
    resp = client.get("/risk/kill-switch", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["active"] is False


def test_activate_requires_a_reason(client):
    resp = client.post("/risk/kill-switch/activate", headers=HDR, json={})
    assert resp.status_code == 400

    resp = client.post("/risk/kill-switch/activate", headers=HDR, json={"reason": "   "})
    assert resp.status_code == 400


def test_activate_then_status_reflects_active(client):
    resp = client.post("/risk/kill-switch/activate", headers=HDR, json={"reason": "anomalous fills"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["reason"] == "anomalous fills"

    resp = client.get("/risk/kill-switch", headers=HDR)
    assert resp.json()["active"] is True


def test_deactivate_without_prior_activation_is_409(client):
    resp = client.post("/risk/kill-switch/deactivate", headers=HDR)
    assert resp.status_code == 409


def test_activate_then_deactivate_round_trip(client):
    client.post("/risk/kill-switch/activate", headers=HDR, json={"reason": "halt"})
    resp = client.post("/risk/kill-switch/deactivate", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["active"] is False

    resp = client.get("/risk/kill-switch", headers=HDR)
    assert resp.json()["active"] is False


def test_activation_is_audit_logged(client, tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(
        "storage.audit_log.log_action",
        lambda action, **kw: logged.append((action, kw)),
    )
    client.post("/risk/kill-switch/activate", headers=HDR, json={"reason": "halt for review"})
    assert any(a == "kill_switch.activate" for a, _ in logged)
