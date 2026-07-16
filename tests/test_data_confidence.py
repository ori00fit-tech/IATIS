"""tests/test_data_confidence.py — runtime data-confidence layer (S1).

The comparison math itself is covered by tests/test_cross_provider_diff.py
(reused, not duplicated). These tests cover the runtime layer: rotation,
persistence, verdict summarization, failure isolation, and the endpoint.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

from core import data_confidence as dc  # noqa: E402


def _fake_diff_result(verdict="AGREE", mean=0.001, mx=0.02, pct_exceeding=0.0):
    return {
        "symbol": "EURUSD",
        "comparisons": [{
            "provider_a": "twelve_data",
            "provider_b": "fcs_api",
            "bars_common": 48,
            "close_diff_pct": {"mean": mean, "median": mean, "max": mx,
                               "worst_timestamp": "2026-07-16T00:00:00"},
            "pct_bars_exceeding_tolerance": pct_exceeding,
            "verdict": verdict,
        }],
        "fetch_errors": {},
    }


@pytest.fixture
def patched_chain(monkeypatch):
    monkeypatch.setattr(
        "core.data_providers.provider_chain_for",
        lambda sym, overrides=None: ["twelve_data", "fcs_api", "yahoo_finance"],
    )


def test_round_robin_rotation():
    dc._rr_counter = 0
    syms = ["EURUSD", "XAUUSD", "BTCUSD"]
    picked = [dc.pick_symbol(syms) for _ in range(4)]
    assert picked == ["EURUSD", "XAUUSD", "BTCUSD", "EURUSD"]
    assert dc.pick_symbol([]) is None


def test_check_records_row(monkeypatch, patched_chain, fake_d1):
    monkeypatch.setattr("scripts.cross_provider_diff.run",
                        lambda *a, **k: _fake_diff_result())
    out = dc.check_and_record("EURUSD", {"features": {}})
    assert out is not None and out["verdict"] == "AGREE"

    row = fake_d1.execute("SELECT * FROM data_confidence_checks").fetchone()
    assert row["symbol"] == "EURUSD"
    assert row["provider_a"] == "twelve_data"
    assert row["verdict"] == "AGREE"
    assert row["bars_common"] == 48


def test_material_verdict_is_summarized(monkeypatch, patched_chain, fake_d1):
    monkeypatch.setattr(
        "scripts.cross_provider_diff.run",
        lambda *a, **k: _fake_diff_result(
            verdict="MATERIAL_DISAGREEMENT — do not treat either provider as ground truth",
            mean=0.4, mx=2.1, pct_exceeding=22.0),
    )
    out = dc.check_and_record("EURUSD", {"features": {}})
    assert out["verdict"] == "MATERIAL_DISAGREEMENT"


def test_check_never_raises(monkeypatch, patched_chain):
    def boom(*a, **k):
        raise RuntimeError("provider exploded")
    monkeypatch.setattr("scripts.cross_provider_diff.run", boom)
    assert dc.check_and_record("EURUSD", {"features": {}}) is None


def test_single_provider_chain_is_skipped(monkeypatch):
    monkeypatch.setattr("core.data_providers.provider_chain_for",
                        lambda sym, overrides=None: ["twelve_data"])
    assert dc.check_and_record("EURUSD", {"features": {}}) is None


def test_recent_checks_reads_history_only(monkeypatch, patched_chain, fake_d1):
    monkeypatch.setattr("scripts.cross_provider_diff.run",
                        lambda *a, **k: _fake_diff_result())
    dc.check_and_record("EURUSD", {"features": {}})
    monkeypatch.setattr(
        "scripts.cross_provider_diff.run",
        lambda *a, **k: _fake_diff_result(verdict="MATERIAL_DISAGREEMENT — x", mean=1.0),
    )
    dc.check_and_record("EURUSD", {"features": {}})

    hist = dc.recent_checks()
    assert hist["n"] == 2
    assert hist["material_disagreements"] == 1
    assert all("verdict" in c for c in hist["checks"])


def test_endpoint_contract(monkeypatch, fake_d1):
    try:
        from fastapi.testclient import TestClient
        import execution.api_server as m
        m._ENV = "development"
        from execution.api_server import app
    except ImportError:
        pytest.skip("fastapi not installed")

    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app) as client:
        assert client.get("/data-confidence").status_code == 401
        r = client.get("/data-confidence", headers={"X-API-Key": "test-key-123"})
        assert r.status_code == 200
        body = r.json()
        assert "checks" in body and "material_disagreements" in body
