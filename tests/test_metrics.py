"""tests/test_metrics.py — /metrics observability surface (S5)."""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

from execution.metrics import render_metrics  # noqa: E402

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"
    from execution.api_server import app
    FASTAPI = True
except ImportError:
    FASTAPI = False

HDR = {"X-API-Key": "test-key-123"}


def _parse(text: str) -> dict[str, float]:
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            out[name] = float(value)
    return out


def test_render_metrics_with_healthy_d1(fake_d1):
    from storage import migrations
    from storage.decision_db import log_decision_db
    migrations.apply_migrations()
    log_decision_db({"symbol": "EURUSD", "final_verdict": "EXECUTE",
                     "summary": "t", "confluence": {}, "risk": {}, "regime": {},
                     "engine_outputs": []})

    m = _parse(render_metrics())
    assert m["iatis_d1_up"] == 1
    assert m["iatis_decisions_total"] == 1
    assert m["iatis_execute_decisions_total"] == 1
    assert m["iatis_schema_version"] == migrations.LATEST_VERSION
    assert m["iatis_d1_latency_seconds"] >= 0
    assert 0 <= m["iatis_last_decision_age_seconds"] < 3600


def test_render_metrics_never_raises_when_d1_down(monkeypatch):
    """The metrics surface must be at its most reliable when the system
    is at its least: a dead Worker yields iatis_d1_up 0, not a 500."""
    import execution.metrics as em

    def boom():
        raise RuntimeError("worker down")

    monkeypatch.setattr("storage.d1_client.d1_connection", boom)
    m = _parse(em.render_metrics())
    assert m["iatis_d1_up"] == 0
    assert "iatis_decisions_total" not in m


@pytest.mark.skipif(not FASTAPI, reason="fastapi not installed")
def test_metrics_endpoint_requires_auth():
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 401


@pytest.mark.skipif(not FASTAPI, reason="fastapi not installed")
def test_metrics_endpoint_exposition_format(monkeypatch, fake_d1):
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app) as client:
        r = client.get("/metrics", headers=HDR)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "# HELP iatis_d1_up" in r.text
        assert "# TYPE iatis_d1_up gauge" in r.text
