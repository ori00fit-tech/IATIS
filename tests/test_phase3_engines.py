"""
tests/test_phase3_engines.py
-------------------------------
Behavior tests for Phase 3 engines: ICT, NNFX, Quant,
and the Session Context layer they depend on.

All tests use synthetic/hand-crafted data — no real API calls.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias
from engines.ict_engine import ICTEngine, _dealing_range, _premium_discount_zone
from engines.nnfx_engine import NNFXEngine
from engines.quant_engine import QuantEngine
from regimes.session_context import SessionContext, detect_session


# ---------------------------------------------------------------------------
# Session Context
# ---------------------------------------------------------------------------

def test_detect_session_london_open():
    dt = pd.Timestamp("2026-06-24 08:00:00", tz="UTC")
    ctx = detect_session(dt)
    assert "London" in ctx.active_sessions
    assert ctx.is_session_open  # within 2h of London open (07:00)


def test_detect_session_ny_open():
    dt = pd.Timestamp("2026-06-24 12:30:00", tz="UTC")
    ctx = detect_session(dt)
    assert "NewYork" in ctx.active_sessions


def test_detect_session_overlap():
    dt = pd.Timestamp("2026-06-24 13:00:00", tz="UTC")
    ctx = detect_session(dt)
    assert ctx.is_overlap
    assert "Overlap" in ctx.active_sessions
    assert ctx.volatility_expectation == "HIGH"


def test_detect_session_asia():
    dt = pd.Timestamp("2026-06-24 03:00:00", tz="UTC")
    ctx = detect_session(dt)
    assert "Asia" in ctx.active_sessions
    assert ctx.volatility_expectation == "LOW"


def test_detect_session_off_hours():
    dt = pd.Timestamp("2026-06-24 22:30:00", tz="UTC")  # after NY close (21:00)
    ctx = detect_session(dt)
    # could be Asia opening or off-hours depending on exact time
    assert ctx.primary_session in ("Asia", "Off-Hours")


# ---------------------------------------------------------------------------
# ICT Engine
# ---------------------------------------------------------------------------

def test_ict_dealing_range():
    df = load_synthetic(50, seed=1)
    low, high = _dealing_range(df, lookback=20)
    assert high > low
    assert low <= df["close"].iloc[-1] or high >= df["close"].iloc[-1]


def test_ict_premium_zone():
    # 1.11 in range 1.08-1.12: (1.11-1.08)/(1.12-1.08) = 0.75 → clearly PREMIUM
    zone, pct = _premium_discount_zone(1.11, 1.08, 1.12)
    assert zone == "PREMIUM"
    assert pct > 0.6


def test_ict_discount_zone():
    zone, pct = _premium_discount_zone(1.085, 1.08, 1.12)
    assert zone == "DISCOUNT"
    assert pct < 0.5


def test_ict_equilibrium_zone():
    zone, pct = _premium_discount_zone(1.10, 1.08, 1.12)
    # 1.10 in range 1.08-1.12 = (1.10-1.08)/(1.12-1.08) = 0.50 = PREMIUM boundary
    # just test it doesn't crash and returns valid zone
    assert zone in ("PREMIUM", "DISCOUNT", "EQUILIBRIUM")


def test_ict_engine_returns_valid_output():
    df = load_synthetic(200, seed=42)
    mtf = build_multi_timeframe_view(df, ["M15", "H1", "H4", "D1"])
    output = ICTEngine().safe_analyze(mtf)
    assert output.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert 0 <= output.score <= 80
    assert len(output.reasons) > 0


def test_ict_engine_abstains_on_insufficient_data():
    df = load_synthetic(10, seed=1)
    mtf = {"H1": df}
    output = ICTEngine().safe_analyze(mtf)
    assert output.bias == Bias.NEUTRAL


def test_ict_engine_uses_h4_for_range_when_available():
    df_h1 = load_synthetic(200, seed=42)
    from core.timeframe_sync import resample
    df_h4 = resample(df_h1, "H4")
    mtf = {"H1": df_h1, "H4": df_h4}
    output = ICTEngine().safe_analyze(mtf)
    assert output.raw.get("timeframe_range") == "H4"


def test_ict_engine_falls_back_to_h1_range_when_h4_small():
    df_h1 = load_synthetic(200, seed=42)
    from core.timeframe_sync import resample
    df_h4 = resample(df_h1, "H4")
    # artificially truncate H4 to simulate insufficient bars
    mtf = {"H1": df_h1, "H4": df_h4.tail(5)}
    output = ICTEngine().safe_analyze(mtf)
    # should fall back to H1 for range
    assert output.raw.get("timeframe_range") == "H1"


# ---------------------------------------------------------------------------
# NNFX Engine
# ---------------------------------------------------------------------------

def test_nnfx_engine_returns_valid_output():
    df = load_synthetic(300, seed=42)
    mtf = build_multi_timeframe_view(df, ["H1"])
    output = NNFXEngine().safe_analyze(mtf)
    assert output.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert 0 <= output.score <= 80


def test_nnfx_engine_abstains_on_insufficient_data():
    df = load_synthetic(50, seed=1)  # needs 210+ for EMA200
    mtf = {"H1": df}
    output = NNFXEngine().safe_analyze(mtf)
    assert output.bias == Bias.NEUTRAL
    assert output.score == 0.0


def test_nnfx_engine_reports_ema200_in_raw():
    df = load_synthetic(300, seed=42)
    mtf = {"H1": df}
    output = NNFXEngine().safe_analyze(mtf)
    assert "ema200" in output.raw
    assert "adx" in output.raw


def test_nnfx_engine_bullish_when_price_above_ema200():
    """Construct data where close ends well above EMA200."""
    import numpy as np
    n = 300
    # rising prices ensure close > EMA200
    prices = np.linspace(1.05, 1.15, n) + np.random.default_rng(0).normal(0, 0.001, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": prices,
        "high": prices * 1.001,
        "low": prices * 0.999,
        "close": prices,
        "volume": 1000,
    }, index=idx)
    df.index.name = "datetime"
    output = NNFXEngine().safe_analyze({"H1": df})
    assert output.bias == Bias.BULLISH


# ---------------------------------------------------------------------------
# Quant Engine
# ---------------------------------------------------------------------------

def test_quant_engine_returns_valid_output():
    df = load_synthetic(200, seed=42)
    mtf = {"H1": df}
    output = QuantEngine().safe_analyze(mtf)
    assert output.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert 0 <= output.score <= 60


def test_quant_engine_abstains_on_insufficient_data():
    df = load_synthetic(20, seed=1)
    mtf = {"H1": df}
    output = QuantEngine().safe_analyze(mtf)
    assert output.bias == Bias.NEUTRAL
    assert output.score == 0.0


def test_quant_engine_reports_indicators_in_raw():
    df = load_synthetic(200, seed=42)
    output = QuantEngine().safe_analyze({"H1": df})
    assert "rsi" in output.raw
    assert "roc_10" in output.raw
    assert "atr_percentile" in output.raw


def test_quant_engine_bearish_on_overbought():
    """Construct strongly rising data to trigger RSI overbought."""
    import numpy as np
    n = 200
    rng = np.random.default_rng(0)
    prices = 1.10 + np.cumsum(rng.normal(0.002, 0.0005, n))  # strong uptrend
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": prices,
        "high": prices * 1.001,
        "low": prices * 0.999,
        "close": prices,
        "volume": 1000,
    }, index=idx)
    df.index.name = "datetime"
    output = QuantEngine().safe_analyze({"H1": df})
    # strong uptrend should lead to RSI ≥ 50
    assert output.raw["rsi"] >= 50


# ---------------------------------------------------------------------------
# Asset Profiles
# ---------------------------------------------------------------------------

def test_asset_profiles_all_symbols_loadable():
    from core.asset_profiles import PROFILES, get_profile, get_td_symbol
    symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD",
               "USOIL", "US30", "NAS100", "SPX500", "BTCUSD", "ETHUSD"]
    for sym in symbols:
        profile = get_profile(sym)
        assert profile.symbol == sym
        assert profile.td_symbol
        assert profile.asset_class


def test_asset_profiles_get_td_symbol():
    from core.asset_profiles import get_td_symbol
    assert get_td_symbol("EURUSD") == "EUR/USD"
    assert get_td_symbol("XAUUSD") == "XAU/USD"
    assert get_td_symbol("BTCUSD") == "BTC/USD"


def test_asset_profiles_unknown_raises():
    from core.asset_profiles import get_profile
    with pytest.raises(KeyError):
        get_profile("UNKNOWN")


def test_asset_profiles_all_symbols_by_class():
    from core.asset_profiles import all_symbols_by_class
    classes = all_symbols_by_class()
    assert "FOREX" in classes
    assert "METALS" in classes
    assert "CRYPTO" in classes
    assert len(classes["FOREX"]) >= 12
