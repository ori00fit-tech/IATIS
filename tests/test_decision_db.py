"""
tests/test_decision_db.py
---------------------------
Tests for storage/decision_db.py — the D1-backed analytics layer.
D1 is faked in-memory by tests/conftest.py's autouse fake_d1 fixture.
"""

from __future__ import annotations

import json

import pytest

from storage.decision_db import (
    init_db,
    log_decision_db,
    recent,
    regime_performance,
    summary,
)

SAMPLE_NO_TRADE = {
    "symbol": "EURUSD",
    "final_verdict": "NO_TRADE",
    "summary": "NO_TRADE: Confluence score 57.22 below minimum required 60",
    "regime": {"state": "TRENDING", "volatility": "normal",
                "trend_strength": -0.63, "confidence": 0.63},
    "confluence": {
        "score": 57.22,
        "engines_participating": 2,
        "fail_reasons": ["Confluence score 57.22 below minimum required 60"],
    },
    "risk": {"passed": None, "reasons": ["Risk gate not evaluated"]},
    "engine_outputs": [
        {"engine": "SMC", "bias": "BEARISH", "score": 39.0},
        {"engine": "PriceAction", "bias": "BEARISH", "score": 80.0},
    ],
}

SAMPLE_EXECUTE = {
    "symbol": "EURUSD",
    "final_verdict": "EXECUTE",
    "summary": "EXECUTE BEARISH: 2/2 engines agreed",
    "regime": {"state": "TRENDING", "volatility": "normal",
                "trend_strength": -0.80, "confidence": 0.80},
    "confluence": {
        "score": 65.0,
        "engines_participating": 2,
        "fail_reasons": [],
    },
    "risk": {"passed": True, "reasons": ["All risk checks passed"],
             "recommended_risk_pct": 0.01},
    "engine_outputs": [
        {"engine": "SMC", "bias": "BEARISH", "score": 55.0},
        {"engine": "PriceAction", "bias": "BEARISH", "score": 80.0},
    ],
}


def test_init_db_creates_tables(fake_d1):
    init_db()
    tables = {
        r["name"] for r in
        fake_d1.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"decisions", "engine_votes"} <= tables


def test_log_and_retrieve():
    log_decision_db(SAMPLE_NO_TRADE)
    rows = recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "NO_TRADE"
    assert rows[0]["symbol"] == "EURUSD"


def test_multiple_inserts():
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_EXECUTE)
    rows = recent(limit=10)
    assert len(rows) == 3


def test_recent_newest_first():
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_EXECUTE)
    rows = recent(limit=10)
    assert rows[0]["verdict"] == "EXECUTE"  # newest first


def test_recent_filter_by_verdict():
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_EXECUTE)
    rows = recent(limit=10, verdict_filter="EXECUTE")
    assert len(rows) == 1
    assert rows[0]["verdict"] == "EXECUTE"


def test_summary_counts():
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_EXECUTE)
    s = summary()
    assert s["total"] == 3
    assert s["execute"] == 1
    assert s["no_trade"] == 2
    assert s["execute_rate"] == pytest.approx(1/3, abs=0.01)


def test_summary_top_reasons():
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_NO_TRADE)
    s = summary()
    assert len(s["top_no_trade_reasons"]) >= 1
    assert "Confluence" in s["top_no_trade_reasons"][0]["reason"]


def test_regime_performance():
    log_decision_db(SAMPLE_NO_TRADE)
    log_decision_db(SAMPLE_EXECUTE)
    perf = regime_performance()
    assert len(perf) >= 1
    trending = [r for r in perf if r["regime"] == "TRENDING"]
    assert len(trending) > 0


def test_engine_votes_stored(fake_d1):
    log_decision_db(SAMPLE_NO_TRADE)
    votes = fake_d1.execute("SELECT * FROM engine_votes").fetchall()
    assert len(votes) == 2  # SMC + PriceAction
    engines = {v["engine"] for v in votes}
    assert "SMC" in engines
    assert "PriceAction" in engines


def test_log_never_raises_on_bad_report():
    # malformed report — must not crash the pipeline
    log_decision_db({})
    log_decision_db({"final_verdict": None})


def test_raw_json_stored(fake_d1):
    log_decision_db(SAMPLE_EXECUTE)
    row = fake_d1.execute("SELECT raw_json FROM decisions LIMIT 1").fetchone()
    parsed = json.loads(row["raw_json"])
    assert parsed["final_verdict"] == "EXECUTE"


def test_pipeline_logs_to_db(monkeypatch):
    """Integration: run_pipeline() must write to the DB."""
    from utils.helpers import load_config
    import main as main_module

    monkeypatch.setattr(main_module, "telegram_send", lambda r: None)
    # suppress JSONL write to avoid side effects on the real log
    monkeypatch.setattr(main_module, "log_decision", lambda report: None)

    config = load_config()
    config["data"]["source"] = "synthetic"
    config["telegram"] = {"enabled": False}

    main_module.run_pipeline(config)

    rows = recent()
    assert len(rows) == 1
    assert rows[0]["verdict"] in ("EXECUTE", "NO_TRADE")
