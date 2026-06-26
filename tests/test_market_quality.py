"""tests/test_market_quality.py — MQS tests."""
from __future__ import annotations
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from core.market_quality import (
    assess_market_quality, _active_sessions, _session_score,
    MQS_THRESHOLD_GOOD, MQS_THRESHOLD_FAIR
)


def _make_df(n=200, atr_level="normal"):
    """Synthetic OHLCV DataFrame."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    close = 1.1 + np.cumsum(np.random.randn(n) * 0.0005)
    if atr_level == "dead":
        noise = 0.00005
    elif atr_level == "extreme":
        noise = 0.005
    else:
        noise = 0.0008
    high = close + abs(np.random.randn(n)) * noise
    low = close - abs(np.random.randn(n)) * noise
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": 1000.0}, index=dates)


# Session detection
def test_london_session_detected():
    active = _active_sessions(9)  # 09:00 UTC
    assert "London" in active


def test_ny_session_detected():
    active = _active_sessions(14)  # 14:00 UTC
    assert "NewYork" in active


def test_london_ny_overlap():
    active = _active_sessions(14)
    assert "London" in active and "NewYork" in active


def test_no_major_session():
    active = _active_sessions(3)   # 03:00 UTC — only Tokyo/Sydney
    assert "London" not in active
    assert "NewYork" not in active


def test_session_score_overlap_highest():
    overlap_score, _ = _session_score(["London", "NewYork"])
    london_score, _ = _session_score(["London"])
    asia_score, _ = _session_score(["Tokyo"])
    assert overlap_score > london_score > asia_score


# MQS scoring
def test_good_score_london_normal_volatility():
    df = _make_df()
    now = datetime(2026, 6, 26, 10, 0, tzinfo=timezone.utc)  # London session
    result = assess_market_quality(df, "EURUSD", now=now)
    assert result.score >= MQS_THRESHOLD_GOOD
    assert result.grade == "GOOD"
    assert result.should_trade is True


def test_poor_score_dead_market():
    df = _make_df(atr_level="dead")
    now = datetime(2026, 6, 26, 3, 0, tzinfo=timezone.utc)  # Asian dead hour
    result = assess_market_quality(df, "EURUSD", now=now)
    assert result.score < MQS_THRESHOLD_GOOD


def test_monday_pre_london_penalty():
    df = _make_df()
    # Monday 05:00 UTC — should have penalty
    now = datetime(2026, 6, 22, 5, 0, tzinfo=timezone.utc)  # Monday
    result = assess_market_quality(df, "EURUSD", now=now)
    assert result.day_penalty > 0


def test_friday_late_penalty():
    df = _make_df()
    # Friday 21:00 UTC
    now = datetime(2026, 6, 26, 21, 0, tzinfo=timezone.utc)  # Friday
    result = assess_market_quality(df, "EURUSD", now=now)
    assert result.day_penalty > 0


def test_score_never_exceeds_100():
    df = _make_df()
    now = datetime(2026, 6, 26, 14, 0, tzinfo=timezone.utc)
    result = assess_market_quality(df, "EURUSD", now=now)
    assert 0 <= result.score <= 100


def test_to_dict_complete():
    df = _make_df()
    now = datetime(2026, 6, 26, 10, 0, tzinfo=timezone.utc)
    result = assess_market_quality(df, "EURUSD", now=now)
    d = result.to_dict()
    for key in ["mqs_score", "grade", "should_trade", "session",
                "active_sessions", "atr_percentile", "volatility_grade"]:
        assert key in d


def test_insufficient_data_still_returns():
    df = _make_df(n=15)
    now = datetime(2026, 6, 26, 10, 0, tzinfo=timezone.utc)
    result = assess_market_quality(df, "TEST", now=now)
    assert 0 <= result.score <= 100


def test_reasons_always_populated():
    df = _make_df()
    now = datetime(2026, 6, 26, 10, 0, tzinfo=timezone.utc)
    result = assess_market_quality(df, "EURUSD", now=now)
    assert len(result.reasons) >= 2
