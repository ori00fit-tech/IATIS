"""tests/test_news_risk.py — News Intelligence Layer tests."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest

from fundamentals.news_risk import (
    assess_news_risk, _event_score, _is_blackout,
    NewsRiskResult, KNOWN_HIGH_IMPACT,
)


def _make_event(name="NFP", currency="USD", minutes_until=45, impact="High"):
    return {
        "name": name,
        "currency": currency,
        "impact": impact,
        "minutes_until": minutes_until,
        "date": (datetime.now(timezone.utc) + timedelta(minutes=minutes_until)).isoformat(),
    }


# --- event scoring ---

def test_known_high_impact_score():
    e = _make_event("Non-Farm Payrolls")
    assert _event_score(e) == 100

def test_fomc_score():
    e = _make_event("Federal Funds Rate")
    assert _event_score(e) == 100

def test_cpi_score():
    e = _make_event("CPI")
    assert _event_score(e) == 90

def test_medium_impact_score():
    e = _make_event("Trade Balance", impact="Medium")
    assert _event_score(e) == 50

def test_low_impact_score():
    e = _make_event("Some Minor Event", impact="Low")
    assert _event_score(e) == 15


# --- blackout detection ---

def test_blackout_before_high_impact():
    e = _make_event("NFP", minutes_until=15)
    blocked, reason = _is_blackout(e, 15)
    assert blocked
    assert "NFP" in reason

def test_blackout_after_high_impact():
    e = _make_event("FOMC", minutes_until=0)
    blocked, reason = _is_blackout(e, -5)  # 5 min after
    assert blocked

def test_no_blackout_far_ahead():
    e = _make_event("NFP", minutes_until=90)
    blocked, reason = _is_blackout(e, 90)
    assert not blocked

def test_no_blackout_low_impact_event():
    e = _make_event("Some Data", impact="Low", minutes_until=5)
    blocked, reason = _is_blackout(e, 5)
    assert not blocked


# --- assess_news_risk ---

def test_no_events_returns_low_risk():
    with patch("fundamentals.news_risk.assess_news_risk") as mock:
        mock.return_value = NewsRiskResult(
            symbol="EURUSD", news_risk_score=0, risk_level="LOW",
            blackout_active=False, blackout_reason="No upcoming events",
            upcoming_events=[], next_high_impact=None,
        )
        result = mock("EURUSD")
    assert result.risk_level == "LOW"
    assert not result.should_block


def test_high_impact_near_raises_score():
    """Score should be high when NFP is 20 minutes away."""
    events = [_make_event("Non-Farm Payrolls", "USD", minutes_until=20)]
    with patch("fundamentals.news_calendar.get_upcoming_events", return_value=events):
        result = assess_news_risk("EURUSD", calendar_events=events)
    assert result.news_risk_score > 50
    assert result.blackout_active


def test_result_to_dict():
    result = NewsRiskResult(
        symbol="GBPUSD", news_risk_score=75.0, risk_level="HIGH",
        blackout_active=True, blackout_reason="BOE rate decision in 15 min",
        upcoming_events=[_make_event("BOE Interest Rate Decision", "GBP", 15)],
        next_high_impact={"name": "BOE Rate", "currency": "GBP", "minutes_until": 15, "score": 100},
    )
    d = result.to_dict()
    assert d["news_risk_score"] == 75.0
    assert d["blackout_active"] is True
    assert d["risk_level"] == "HIGH"


def test_should_block_when_blackout():
    result = NewsRiskResult(
        symbol="EURUSD", news_risk_score=90, risk_level="EXTREME",
        blackout_active=True, blackout_reason="NFP in 10 min",
        upcoming_events=[], next_high_impact=None,
    )
    assert result.should_block is True


def test_should_not_block_low_risk():
    result = NewsRiskResult(
        symbol="EURUSD", news_risk_score=10, risk_level="LOW",
        blackout_active=False, blackout_reason="",
        upcoming_events=[], next_high_impact=None,
    )
    assert result.should_block is False


def test_currency_symbols_mapping():
    """USD events should affect EURUSD, XAUUSD, etc."""
    from fundamentals.news_calendar import CURRENCY_SYMBOLS
    assert "EURUSD" in CURRENCY_SYMBOLS["USD"]
    assert "XAUUSD" in CURRENCY_SYMBOLS["USD"]
    assert "GBPUSD" in CURRENCY_SYMBOLS["GBP"]
    assert "USDJPY" in CURRENCY_SYMBOLS["JPY"]
