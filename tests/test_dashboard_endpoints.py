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
    assert set(body["chains"]) == {"crypto", "metals", "energy", "indices", "fx", "stocks", "etf"}
    assert body["chains"]["crypto"][0] == "ccxt"
    # Test env strips credentials (conftest) → ctrader must show unavailable.
    assert body["availability"]["ctrader"] is False
    assert body["availability"]["ccxt"] is True
    # Native coverage is what makes the chain starvation-proof.
    assert "H4" in body["native_timeframes"]["ccxt"]
    assert "H4" not in body["native_timeframes"]["yahoo_finance"]


def test_warehouse_manifest_requires_auth(client):
    assert client.get("/warehouse-manifest").status_code == 401


def test_warehouse_manifest_empty_when_nothing_pushed(client, fake_d1):
    r = client.get("/warehouse-manifest", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["datasets"] == []
    assert "checked_at" in body


def test_warehouse_manifest_reports_real_pushed_datasets(client, fake_d1):
    from storage import market_bars

    market_bars.upsert_manifest({
        "symbol": "EURUSD", "timeframe": "H1", "source": "dukascopy",
        "row_count": 1000, "status": "READY", "coverage_pct": 99.5,
    })
    market_bars.upsert_manifest({
        "symbol": "EURUSD", "timeframe": "H4", "source": "dukascopy_resampled",
        "row_count": 250, "status": "READY", "coverage_pct": 99.5,
    })
    r = client.get("/warehouse-manifest", headers=HDR)
    assert r.status_code == 200
    datasets = {(d["symbol"], d["timeframe"]): d for d in r.json()["datasets"]}
    assert datasets[("EURUSD", "H1")]["status"] == "READY"
    assert datasets[("EURUSD", "H1")]["native"] is True
    assert datasets[("EURUSD", "H4")]["native"] is False  # "_resampled" suffix


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


def test_data_health_v2_from_provenance(client, fake_d1):
    """data-health derives from decision provenance (the live feed's
    truth), not the abandoned *_2y.csv cache — the all-MISSING regression
    observed live on 2026-07-16."""
    import json as _json
    from datetime import datetime, timezone
    from storage import migrations
    migrations.apply_migrations()

    fresh = datetime.now(timezone.utc).isoformat()
    dv = _json.dumps({
        tf: {"provider": "ctrader", "row_count": 750, "last_ts": "2026-07-16 08:00:00", "sha256": "aa"}
        for tf in ("M15", "H1", "H4", "D1")  # superset of any config TF list
    })
    fake_d1.execute(
        "INSERT INTO decisions (ts, symbol, verdict, data_versions) VALUES (?,?,?,?)",
        (fresh, "EURUSD", "NO_TRADE", dv),
    )
    starved = _json.dumps({
        tf: {"provider": "twelve_data", "row_count": 125, "last_ts": "x", "sha256": "cc"}
        for tf in ("M15", "H1", "H4", "D1")  # 125 < 210 starves the decision TF
    })
    fake_d1.execute(
        "INSERT INTO decisions (ts, symbol, verdict, data_versions) VALUES (?,?,?,?)",
        (fresh, "XAUUSD", "NO_TRADE", starved),
    )
    fake_d1.commit()

    r = client.get("/data-health", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "decision_provenance"
    by_symbol = {s["symbol"]: s for s in body["symbols"]}

    if "EURUSD" in by_symbol:
        eur = by_symbol["EURUSD"]
        tf = eur["timeframes"]
        dtf = next(iter(tf))  # decision TF = timeframes[0]
        assert tf[dtf]["provider"] == "ctrader"
        assert tf[dtf]["status"] == "OK"
        assert eur["overall_status"] == "OK"

    if "XAUUSD" in by_symbol:
        # 125 decision-TF bars < 210 → the July starvation class, visible.
        assert by_symbol["XAUUSD"]["overall_status"] == "STARVED"

    # Symbols with no provenance-carrying decision yet → MISSING, not 500.
    assert all(
        s["overall_status"] in ("OK", "STALE", "GAPS", "STARVED", "MISSING")
        for s in body["symbols"]
    )


def test_data_health_alive_pipeline_not_stale_without_provenance(client, fake_d1):
    """Regression (2026-07-27): a symbol whose most recent decision is a
    Market Quality gate NO_TRADE (main.py:_market_quality_gate returns
    BEFORE build_provenance() runs, so storage/decision_db.py writes
    data_versions=NULL) must not be reported STALE — the pipeline is
    genuinely alive and deciding this symbol every run, it just hasn't
    needed a fresh provenance snapshot. Observed live on BTCUSD: MQS
    34-39/100 for hours, a real NO_TRADE logged every scheduler tick,
    Data Center still said STALE because the OLD provenance-carrying
    decision (from before the dead-market streak) was hours old."""
    import json as _json
    from datetime import datetime, timedelta, timezone
    from storage import migrations
    migrations.apply_migrations()

    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(minutes=400)).isoformat()  # > _DH_STALE_MINUTES (360)
    fresh_ts = now.isoformat()

    old_dv = _json.dumps({
        tf: {"provider": "ccxt", "row_count": 750, "last_ts": "2026-07-20 00:00:00", "sha256": "bb"}
        for tf in ("M15", "H1", "H4", "D1")
    })
    fake_d1.execute(
        "INSERT INTO decisions (ts, symbol, verdict, data_versions) VALUES (?,?,?,?)",
        (old_ts, "BTCUSD", "NO_TRADE", old_dv),
    )
    # The Market Quality gate's early-exit report — no provenance built yet.
    fake_d1.execute(
        "INSERT INTO decisions (ts, symbol, verdict, data_versions) VALUES (?,?,?,?)",
        (fresh_ts, "BTCUSD", "NO_TRADE", None),
    )
    fake_d1.commit()

    r = client.get("/data-health", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    by_symbol = {s["symbol"]: s for s in body["symbols"]}

    if "BTCUSD" in by_symbol:
        btc = by_symbol["BTCUSD"]
        assert btc["overall_status"] == "OK"
        assert "note" in btc
        assert "provenance" in btc["note"].lower()
