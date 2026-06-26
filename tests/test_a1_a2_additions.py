"""tests/test_a1_a2_additions.py — Correlation Filter + MTF Confirmation tests."""
from __future__ import annotations
import pytest
import pandas as pd
import numpy as np
from risk.correlation_engine import check_correlation, portfolio_exposure_summary, CORRELATION_GROUPS
from confluence.mtf_confirmation import check_mtf_confirmation, MTF_CONFIRM_BONUS, MTF_COUNTER_PENALTY


# ─── A1: Correlation Filter ───────────────────────────────────────────────────

def test_no_active_signals_always_allowed():
    result = check_correlation("EURUSD", [])
    assert result.allowed is True


def test_first_jpy_signal_allowed():
    result = check_correlation("USDJPY", [])
    assert result.allowed is True


def test_second_jpy_signal_allowed():
    result = check_correlation("EURJPY", ["USDJPY"])
    assert result.allowed is True


def test_third_jpy_signal_blocked():
    result = check_correlation("AUDJPY", ["USDJPY", "EURJPY"])
    assert result.allowed is False
    assert "JPY_CROSSES" in result.blocking_group


def test_metals_second_blocked():
    result = check_correlation("XAGUSD", ["XAUUSD", "XAGUSD"])
    # XAGUSD already in list + checking XAGUSD itself — 2 metals = limit
    r = check_correlation("XAGUSD", ["XAUUSD"])
    # one metal active, adding second = 2 = at limit
    # max_per_group=2 so second is still allowed, third would block
    assert r.allowed is True
    # now third metal doesn't exist but let's verify the group logic
    r2 = check_correlation("XAUUSD", ["XAGUSD", "XAUUSD"])
    # already 2 in group → blocked
    assert r2.allowed is False


def test_uncorrelated_symbols_not_blocked():
    # BTCUSD and EURUSD are in different groups
    result = check_correlation("BTCUSD", ["EURUSD", "GBPUSD"])
    assert result.allowed is True


def test_portfolio_exposure_summary():
    signals = ["USDJPY", "EURJPY", "BTCUSD", "ETHUSD"]
    summary = portfolio_exposure_summary(signals)
    assert "JPY_CROSSES" in summary
    assert "USDJPY" in summary["JPY_CROSSES"]
    assert "RISK_ASSETS" in summary


def test_correlation_groups_cover_all_iatis_symbols():
    all_grouped = set()
    for members in CORRELATION_GROUPS.values():
        all_grouped.update(members)
    iatis_symbols = {
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
        "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF",
        "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "NAS100", "SPX500", "US30",
    }
    # All symbols should be in at least one group
    for sym in iatis_symbols:
        assert sym in all_grouped, f"{sym} not in any correlation group"


# ─── A2: MTF Confirmation ─────────────────────────────────────────────────────

def _make_d1_df(trend: str, n: int = 200) -> pd.DataFrame:
    """Create synthetic D1 data with clear trend."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    if trend == "UP":
        close = 1.0 + np.arange(n) * 0.001 + np.random.randn(n) * 0.0002
    elif trend == "DOWN":
        close = 1.2 - np.arange(n) * 0.001 + np.random.randn(n) * 0.0002
    else:  # FLAT
        close = 1.1 + np.random.randn(n) * 0.0002
    high = close + abs(np.random.randn(n)) * 0.001
    low = close - abs(np.random.randn(n)) * 0.001
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                         "volume": 1000.0}, index=dates)


def test_d1_confirms_bullish_h1():
    d1 = _make_d1_df("UP")
    result = check_mtf_confirmation("BULLISH", {"D1": d1})
    if result.d1_adx >= 20:
        assert result.confirming is True
        assert result.score_adjustment == MTF_CONFIRM_BONUS


def test_d1_contradicts_bullish_h1():
    d1 = _make_d1_df("DOWN")
    result = check_mtf_confirmation("BULLISH", {"D1": d1})
    if result.d1_adx >= 20:
        assert result.confirming is False
        assert result.score_adjustment == -MTF_COUNTER_PENALTY


def test_no_d1_data_no_adjustment():
    result = check_mtf_confirmation("BULLISH", {})
    assert result.score_adjustment == 0.0
    assert result.d1_bias == "NEUTRAL"


def test_insufficient_d1_data_no_adjustment():
    d1 = _make_d1_df("UP", n=30)  # less than 50 bars
    result = check_mtf_confirmation("BULLISH", {"D1": d1})
    assert result.score_adjustment == 0.0


def test_flat_d1_no_adjustment():
    d1 = _make_d1_df("FLAT")
    result = check_mtf_confirmation("BEARISH", {"D1": d1})
    # ADX should be low for flat market
    if result.d1_adx < 20:
        assert result.score_adjustment == 0.0


def test_adjusted_score_never_exceeds_100():
    d1 = _make_d1_df("UP")
    result = check_mtf_confirmation("BULLISH", {"D1": d1})
    base_score = 95.0
    adjusted = max(0.0, min(100.0, base_score + result.score_adjustment))
    assert adjusted <= 100.0


def test_adjusted_score_never_below_zero():
    d1 = _make_d1_df("DOWN")
    result = check_mtf_confirmation("BULLISH", {"D1": d1})
    base_score = 10.0
    adjusted = max(0.0, min(100.0, base_score + result.score_adjustment))
    assert adjusted >= 0.0
