"""
tests/test_post_trade_routes.py
------------------------------------
Contract tests for execution/routes/post_trade.py, matching
tests/test_kill_switch_routes.py's established client/HDR fixture
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


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _make_incident(**overrides):
    from storage.post_trade_incidents import CRITICAL, RECONCILIATION_MISMATCH, upsert_incident
    kwargs = dict(
        fingerprint="FP:route-test", control_type=RECONCILIATION_MISMATCH,
        severity=CRITICAL, source_component="test", symbol="EURUSD",
    )
    kwargs.update(overrides)
    return upsert_incident(**kwargs)


# ── auth required on every endpoint ─────────────────────────────────────


def test_summary_requires_auth(client):
    assert client.get("/post-trade/summary").status_code == 401


def test_list_incidents_requires_auth(client):
    assert client.get("/post-trade/incidents").status_code == 401


def test_get_incident_requires_auth(client):
    assert client.get("/post-trade/incidents/anything").status_code == 401


def test_acknowledge_requires_auth(client):
    assert client.post("/post-trade/incidents/x/acknowledge", json={}).status_code == 401


def test_resolve_requires_auth(client):
    assert client.post("/post-trade/incidents/x/resolve", json={"reason": "x"}).status_code == 401


def test_waive_requires_auth(client):
    assert client.post("/post-trade/incidents/x/waive", json={"reason": "x"}).status_code == 401


# ── GET /post-trade/summary ─────────────────────────────────────────────


def test_summary_shape(client):
    resp = client.get("/post-trade/summary", headers=HDR)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("generated_at", "incident_counts_by_status", "incident_counts_by_severity",
                "reconciliation", "kill_switch", "execution_quality"):
        assert key in body


# ── GET /post-trade/incidents ────────────────────────────────────────────


def test_list_incidents_empty_by_default(client):
    resp = client.get("/post-trade/incidents", headers=HDR)
    assert resp.status_code == 200
    assert resp.json() == {"incidents": [], "count": 0}


def test_list_incidents_reflects_a_real_incident(client):
    _make_incident()
    resp = client.get("/post-trade/incidents", headers=HDR)
    body = resp.json()
    assert body["count"] == 1
    assert body["incidents"][0]["fingerprint"] == "FP:route-test"


def test_list_incidents_filters_by_status(client):
    from storage.post_trade_incidents import ACKNOWLEDGED
    incident_id = _make_incident()
    client.post(f"/post-trade/incidents/{incident_id}/acknowledge", headers=HDR, json={})
    resp = client.get("/post-trade/incidents", headers=HDR, params={"status": ACKNOWLEDGED})
    assert resp.json()["count"] == 1
    resp_open = client.get("/post-trade/incidents", headers=HDR, params={"status": "OPEN"})
    assert resp_open.json()["count"] == 0


# ── GET /post-trade/incidents/{id} ───────────────────────────────────────


def test_get_incident_404_for_unknown(client):
    resp = client.get("/post-trade/incidents/does-not-exist", headers=HDR)
    assert resp.status_code == 404


def test_get_incident_returns_the_row(client):
    incident_id = _make_incident()
    resp = client.get(f"/post-trade/incidents/{incident_id}", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["incident_id"] == incident_id


# ── POST .../acknowledge ─────────────────────────────────────────────────


def test_acknowledge_success_and_audit_logged(client, tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", audit_path)
    incident_id = _make_incident()
    resp = client.post(f"/post-trade/incidents/{incident_id}/acknowledge", headers=HDR, json={"note": "investigating"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACKNOWLEDGED"
    from storage.audit_log import read_actions
    actions = read_actions(path=audit_path)
    assert any(a["action"] == "post_trade_incident.acknowledge" for a in actions)


def test_acknowledge_already_acknowledged_is_409(client):
    incident_id = _make_incident()
    client.post(f"/post-trade/incidents/{incident_id}/acknowledge", headers=HDR, json={})
    resp = client.post(f"/post-trade/incidents/{incident_id}/acknowledge", headers=HDR, json={})
    assert resp.status_code == 409


def test_acknowledge_unknown_incident_is_409(client):
    resp = client.post("/post-trade/incidents/does-not-exist/acknowledge", headers=HDR, json={})
    assert resp.status_code == 409


# ── POST .../resolve ──────────────────────────────────────────────────────


def test_resolve_requires_a_reason(client):
    incident_id = _make_incident()
    resp = client.post(f"/post-trade/incidents/{incident_id}/resolve", headers=HDR, json={"reason": "   "})
    assert resp.status_code == 400


def test_resolve_success_and_audit_logged(client, tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", audit_path)
    incident_id = _make_incident()
    resp = client.post(
        f"/post-trade/incidents/{incident_id}/resolve", headers=HDR,
        json={"reason": "root-caused, broker fill delayed", "evidence": {"note": "confirmed via ledger"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "RESOLVED"
    assert body["resolution"]["reason"] == "root-caused, broker fill delayed"
    from storage.audit_log import read_actions
    actions = read_actions(path=audit_path)
    assert any(a["action"] == "post_trade_incident.resolve" for a in actions)


def test_resolve_already_resolved_is_409(client):
    incident_id = _make_incident()
    client.post(f"/post-trade/incidents/{incident_id}/resolve", headers=HDR, json={"reason": "first"})
    resp = client.post(f"/post-trade/incidents/{incident_id}/resolve", headers=HDR, json={"reason": "second"})
    assert resp.status_code == 409


# ── POST .../waive ────────────────────────────────────────────────────────


def test_waive_requires_a_reason(client):
    incident_id = _make_incident()
    resp = client.post(f"/post-trade/incidents/{incident_id}/waive", headers=HDR, json={"reason": ""})
    assert resp.status_code == 400


def test_waive_success_and_audit_logged(client, tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", audit_path)
    incident_id = _make_incident()
    resp = client.post(
        f"/post-trade/incidents/{incident_id}/waive", headers=HDR,
        json={"reason": "known, already-accepted one-off"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "WAIVED"
    from storage.audit_log import read_actions
    actions = read_actions(path=audit_path)
    assert any(a["action"] == "post_trade_incident.waive" for a in actions)


def test_waive_already_terminal_is_409(client):
    incident_id = _make_incident()
    client.post(f"/post-trade/incidents/{incident_id}/waive", headers=HDR, json={"reason": "one-off"})
    resp = client.post(f"/post-trade/incidents/{incident_id}/waive", headers=HDR, json={"reason": "again"})
    assert resp.status_code == 409
