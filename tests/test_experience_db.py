"""
tests/test_experience_db.py
---------------------------
Tests for storage/experience_db.py (MROS Level 1 experience log).
D1 is faked in-memory by tests/conftest.py's autouse fake_d1 fixture.
"""
from __future__ import annotations

from storage.experience_db import (
    experience_summary,
    find_similar,
    pattern_analysis,
    query_experiences,
    record_experience,
    record_outcome,
)

SAMPLE_EXECUTE = {
    "symbol": "EURUSD",
    "final_verdict": "EXECUTE",
    "summary": "EXECUTE BEARISH: 2/2 engines agreed",
    "regime": {"state": "TRENDING", "volatility": "normal", "trend_strength": -0.8, "confidence": 0.8},
    "market_quality": {"mqs_score": 75.0, "grade": "A", "atr_percentile": 60.0},
    "confluence": {
        "score": 72.0,
        "raw_score": 70.0,
        "vote": {"winning_bias": "BEARISH", "agree_count": 2, "total_engines": 2,
                  "bull_conviction": 0.1, "bear_conviction": 0.9},
        "mtf": {"d1_bias": "BEARISH", "d1_adx": 30.0, "adjustment": 1.0},
    },
    "risk": {"passed": True},
    "news": {"news_risk_score": 5.0, "blackout_active": False},
    "entry_price": 1.0850,
    "stop_loss": 1.0920,
    "take_profit": 1.0640,
    "risk_reward": "1:2",
}

SAMPLE_NO_TRADE = {
    "symbol": "GBPUSD",
    "final_verdict": "NO_TRADE",
    "summary": "NO_TRADE: Confluence score 57 below minimum required 60",
    "regime": {"state": "RANGING", "volatility": "low", "trend_strength": 0.1, "confidence": 0.5},
    "confluence": {"score": 57.0, "fail_reasons": ["Confluence score 57 below minimum required 60"]},
    "risk": {"passed": None},
    "news": {"news_risk_score": 0},
}


def test_record_experience_returns_id():
    exp_id = record_experience(SAMPLE_EXECUTE)
    assert exp_id.startswith("exp_")


def test_query_experiences_filters_by_verdict():
    record_experience(SAMPLE_EXECUTE)
    record_experience(SAMPLE_NO_TRADE)
    executes = query_experiences(verdict="EXECUTE")
    assert len(executes) == 1
    assert executes[0]["symbol"] == "EURUSD"


def test_query_experiences_filters_by_min_score():
    record_experience(SAMPLE_EXECUTE)
    record_experience(SAMPLE_NO_TRADE)
    high_score = query_experiences(min_score=60)
    assert len(high_score) == 1
    assert high_score[0]["confluence_score"] == 72.0


def test_record_outcome_updates_most_recent_open_execute():
    record_experience(SAMPLE_EXECUTE)
    updated = record_outcome("EURUSD", outcome="win", exit_price=1.0640,
                              pnl_pips=210.0, pnl_usd=200.0, pnl_r=2.0)
    assert updated is True
    rows = query_experiences(symbol="EURUSD", verdict="EXECUTE")
    assert rows[0]["outcome"] == "win"
    assert rows[0]["pnl_r"] == 2.0


def test_record_outcome_no_open_experience_returns_false():
    assert record_outcome("GBPUSD", outcome="win", exit_price=1.0) is False


def test_pattern_analysis_computes_win_rate():
    record_experience(SAMPLE_EXECUTE)
    record_outcome("EURUSD", outcome="win", exit_price=1.0640, pnl_r=2.0, pnl_usd=200.0)
    result = pattern_analysis({"regime": "TRENDING"})
    assert result["trades"] == 1
    assert result["wins"] == 1
    assert result["wr"] == 100.0


def test_pattern_analysis_no_matches():
    result = pattern_analysis({"regime": "VOLATILE"})
    assert result["trades"] == 0


def test_experience_summary_counts():
    record_experience(SAMPLE_EXECUTE)
    record_experience(SAMPLE_NO_TRADE)
    record_outcome("EURUSD", outcome="win", exit_price=1.0640, pnl_r=2.0, pnl_usd=200.0)
    s = experience_summary()
    assert s["total_experiences"] == 2
    assert s["execute_count"] == 1
    assert s["closed_count"] == 1
    assert s["win_count"] == 1


def test_find_similar_matches_symbol_regime_direction():
    record_experience(SAMPLE_EXECUTE)
    record_outcome("EURUSD", outcome="win", exit_price=1.0640, pnl_r=2.0, pnl_usd=200.0)
    result = find_similar(SAMPLE_EXECUTE)
    assert result["similar_count"] == 1
    assert result["historical_wr"] == 100.0


def test_find_similar_no_history():
    result = find_similar(SAMPLE_EXECUTE)
    assert result["similar_count"] == 0
