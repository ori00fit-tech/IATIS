"""tests/test_dashboard_endpoints.py — the dashboard's new live-wiring
endpoints: /philosophy-audit (8-axis checks over the decisions DB),
/provider-chains (data-layer transparency), and the trust-audit block in
/research."""
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
    with TestClient(app) as c:
        yield c


def test_philosophy_audit_runs_all_axes(client):
    r = client.get("/philosophy-audit", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total"] >= 20
    axes = {c["axis"] for c in body["checks"]}
    assert axes == {1, 2, 3, 4, 5, 6, 7, 8, 9}
    for c in body["checks"]:
        assert c["status"] in ("PASS", "FAIL", "WARN", "INFO")


def test_philosophy_audit_requires_auth(client):
    assert client.get("/philosophy-audit").status_code == 401


def test_provider_chains_reports_classes_and_availability(client):
    r = client.get("/provider-chains", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert set(body["chains"]) == {"crypto", "metals", "energy", "indices", "fx"}
    assert body["chains"]["crypto"][0] == "ccxt"
    # Test env strips credentials (conftest) → ctrader must show unavailable.
    assert body["availability"]["ctrader"] is False
    assert body["availability"]["ccxt"] is True
    # Native coverage is what makes the chain starvation-proof.
    assert "H4" in body["native_timeframes"]["ccxt"]
    assert "H4" not in body["native_timeframes"]["yahoo_finance"]


def test_research_includes_trust_audit(client):
    r = client.get("/research", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert "trust_audit" in body
    # H009 is PASSED without a qualifying evidence block — it must be
    # flagged and its row marked untrusted, never rendered as green.
    assert any("H009" in w for w in body["trust_audit"]["warnings"])
    h009 = next(h for h in body["hypotheses"] if h["id"] == "H009")
    assert h009["trusted"] is False
    trusted_map = {h["id"]: h.get("trusted") for h in body["hypotheses"]}
    assert trusted_map.get("H001") is True  # FAILED entries are honestly labeled, not "untrusted"
