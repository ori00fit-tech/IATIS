"""
tests/test_decision_db.py
---------------------------
Tests for storage/decision_db.py — SQLite analytics layer.
All tests use tmp_path to avoid touching the real DB.
"""

from __future__ import annotations

import json
from pathlib import Path

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


@pytest.fixture
def db(tmp_path):
    return tmp_path / "test_decisions.db"


def test_init_db_creates_tables(db):
    init_db(db)
    assert db.exists()


def test_log_and_retrieve(db):
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    rows = recent(limit=10, path=db)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "NO_TRADE"
    assert rows[0]["symbol"] == "EURUSD"


def test_multiple_inserts(db):
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_EXECUTE, path=db)
    rows = recent(limit=10, path=db)
    assert len(rows) == 3


def test_recent_newest_first(db):
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_EXECUTE, path=db)
    rows = recent(limit=10, path=db)
    assert rows[0]["verdict"] == "EXECUTE"  # newest first


def test_recent_filter_by_verdict(db):
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_EXECUTE, path=db)
    rows = recent(limit=10, verdict_filter="EXECUTE", path=db)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "EXECUTE"


def test_summary_counts(db):
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_EXECUTE, path=db)
    s = summary(path=db)
    assert s["total"] == 3
    assert s["execute"] == 1
    assert s["no_trade"] == 2
    assert s["execute_rate"] == pytest.approx(1/3, abs=0.01)


def test_summary_top_reasons(db):
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    s = summary(path=db)
    assert len(s["top_no_trade_reasons"]) >= 1
    assert "Confluence" in s["top_no_trade_reasons"][0]["reason"]


def test_regime_performance(db):
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    log_decision_db(SAMPLE_EXECUTE, path=db)
    perf = regime_performance(path=db)
    assert len(perf) >= 1
    trending = [r for r in perf if r["regime"] == "TRENDING"]
    assert len(trending) > 0


def test_engine_votes_stored(db):
    import sqlite3
    log_decision_db(SAMPLE_NO_TRADE, path=db)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    votes = con.execute("SELECT * FROM engine_votes").fetchall()
    con.close()
    assert len(votes) == 2  # SMC + PriceAction
    engines = {v["engine"] for v in votes}
    assert "SMC" in engines
    assert "PriceAction" in engines


def test_log_never_raises_on_bad_report(db):
    # malformed report — must not crash the pipeline
    log_decision_db({}, path=db)
    log_decision_db({"final_verdict": None}, path=db)


def test_raw_json_stored(db):
    import sqlite3
    log_decision_db(SAMPLE_EXECUTE, path=db)
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT raw_json FROM decisions LIMIT 1").fetchone()
    con.close()
    parsed = json.loads(row[0])
    assert parsed["final_verdict"] == "EXECUTE"


def test_pipeline_logs_to_db(tmp_path, monkeypatch):
    """Integration: run_pipeline() must write to SQLite DB."""
    from utils.helpers import load_config
    import main as main_module
    import storage.decision_db as db_module

    db_path = tmp_path / "decisions.db"

    monkeypatch.setattr(
        main_module, "log_decision_db",
        lambda report: db_module.log_decision_db(report, path=db_path)
    )
    monkeypatch.setattr(main_module, "telegram_send", lambda r: None)
    # suppress JSONL write to avoid side effects on real log
    monkeypatch.setattr(main_module, "log_decision", lambda report: None)

    config = load_config()
    config["data"]["source"] = "synthetic"
    config["telegram"] = {"enabled": False}

    main_module.run_pipeline(config)

    assert db_path.exists(), "SQLite DB should be created after pipeline run"
    rows = recent(path=db_path)
    assert len(rows) == 1
    assert rows[0]["verdict"] in ("EXECUTE", "NO_TRADE")
