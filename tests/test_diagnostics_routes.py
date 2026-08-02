"""
tests/test_diagnostics_routes.py
------------------------------------
Forensic System Audit Phase 1, item B (2026-08-02) — contract tests for
execution/routes/diagnostics.py, matching tests/test_missions.py's
established client/HDR fixtures.
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


def test_direction_symmetry_requires_auth(client):
    r = client.get("/research/diagnostics/direction-symmetry")
    assert r.status_code == 401


def test_direction_symmetry_response_shape(client):
    r = client.get("/research/diagnostics/direction-symmetry", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert "findings" in body
    assert "files_scanned" in body
    assert "caveat" in body
    assert "generated_at" in body
    assert isinstance(body["findings"], list)
    assert isinstance(body["files_scanned"], list)
    assert len(body["files_scanned"]) >= 10


def test_direction_symmetry_every_finding_has_required_fields(client):
    r = client.get("/research/diagnostics/direction-symmetry", headers=HDR)
    body = r.json()
    for finding in body["findings"]:
        assert set(finding.keys()) >= {"file", "line", "function", "kind", "token", "detail", "severity"}
