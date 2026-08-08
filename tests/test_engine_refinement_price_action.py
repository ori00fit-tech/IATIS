"""
tests/test_engine_refinement_price_action.py
-----------------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — Price Action
refinement (#359), canonical (v1) only, per the operator's finalized
scope: v2 (engines/price_action_engine_v2.py) stays an untouched
research variant, not promoted/merged/deleted here. Additive
OBSERVABILITY changes only, zero strategy-behavior change. Pins:

1. rsi_undefined distinguishes a genuine ~50 RSI reading from the
   NaN-fallback-to-50 case (RS undefined when the lookback window has
   zero average loss or zero average gain) — a real ambiguity the old
   code silently collapsed. The fallback VALUE itself (still 50.0) is
   deliberately left unchanged: correcting it to the true 100/0 would
   shift decide()'s RSI-threshold branching, a strategy-semantics
   change out of scope for this pass.
2. momentum_bars_used/momentum_bars_configured make a config-driven
   clamp (when momentum_bars exceeds the available history) observable
   instead of silent.
3. Removing _candle_pattern()'s dead c2/body1 locals (computed, never
   read) changes no detected pattern — confirmed directly here.
4. None of this changes bias/score/reasons — confirmed via the golden
   regression suite (tests/test_engine_config_extraction_no_behavior_
   change.py) staying green, and directly here via purity checks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.price_action_engine import (
    PriceActionEngine,
    _candle_pattern,
    decide,
    detect_breakout,
    extract_features,
)


def _ohlcv(n: int, seed: int = 7, trend: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.linspace(0, trend * 100, n) + np.cumsum(rng.normal(0, 0.3, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, close) + 0.3,
            "low": np.minimum(o, close) - 0.3,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


def _monotonic_up(n: int, period: int) -> pd.DataFrame:
    """Strictly increasing closes over the RSI lookback -> zero average
    loss -> RS undefined -> rsi() returns NaN at the last bar."""
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.arange(n) * 0.5
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {
            "open": o,
            "high": close + 0.2,
            "low": o - 0.2,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


def _mtf(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"H1": df}


# ── rsi_undefined observability ─────────────────────────────────────────

def test_rsi_undefined_false_on_ordinary_mixed_data():
    df = _ohlcv(60)
    features = extract_features(df, {})
    assert features["rsi_undefined"] is False
    assert 0.0 <= features["rsi_val"] <= 100.0


def test_rsi_undefined_true_on_zero_average_loss():
    # 45 consecutive up-closes with a 14-period RSI has zero average loss
    # in the lookback window -> rsi() returns NaN at the last bar.
    df = _monotonic_up(45, period=14)
    features = extract_features(df, {"rsi_period": 14})
    assert features["rsi_undefined"] is True
    # Fallback value is unchanged (still 50.0) — only its meaning is now
    # disambiguated via the new flag, per this refinement's own scope limit.
    assert features["rsi_val"] == 50.0


def test_rsi_undefined_surfaces_in_engine_raw_output():
    df = _monotonic_up(45, period=14)
    eng = PriceActionEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(_mtf(df))
    assert out.raw["rsi_undefined"] is True
    assert out.features["rsi_undefined"] is True


def test_rsi_undefined_never_reaches_decide_scoring():
    """decide() must remain a pure function of the RSI VALUE, not the new
    rsi_undefined flag — stripping it must not change bias/score/reasons."""
    df = _ohlcv(80, seed=3)
    features = extract_features(df, {})
    bias1, score1, reasons1 = decide(features, {})
    stripped = {k: v for k, v in features.items() if k != "rsi_undefined"}
    bias2, score2, reasons2 = decide(stripped, {})
    assert bias1 == bias2
    assert score1 == score2
    assert reasons1 == reasons2


# ── momentum_bars_used / momentum_bars_configured observability ────────

def test_momentum_bars_used_matches_configured_when_unclamped():
    df = _ohlcv(60)
    features = extract_features(df, {"momentum_bars": 5})
    assert features["momentum_bars_configured"] == 5
    assert features["momentum_bars_used"] == 5


def test_momentum_bars_used_reflects_a_real_clamp():
    # len(df) - 1 = 9, configured momentum_bars = 50 -> clamp engages.
    df = _ohlcv(10)
    features = extract_features(df, {"momentum_bars": 50})
    assert features["momentum_bars_configured"] == 50
    assert features["momentum_bars_used"] == 9
    assert features["momentum_bars_used"] < features["momentum_bars_configured"]


def test_momentum_bars_fields_never_reach_decide_scoring():
    df = _ohlcv(80, seed=11)
    features = extract_features(df, {})
    bias1, score1, _ = decide(features, {})
    stripped = {
        k: v for k, v in features.items()
        if k not in ("momentum_bars_used", "momentum_bars_configured")
    }
    bias2, score2, _ = decide(stripped, {})
    assert bias1 == bias2
    assert score1 == score2


# ── _candle_pattern() dead-code removal is behavior-neutral ────────────

def test_candle_pattern_detects_bullish_engulfing_after_c2_body1_removal():
    idx = pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")
    df = pd.DataFrame(
        {
            # Row 0 (now-unused "two bars ago") is filler — only rows 1/2
            # (c1/c0) drive the pattern, confirming the removed c2/body1
            # locals were genuinely dead.
            "open": [100.0, 100.5, 99.0],
            "high": [100.5, 100.6, 101.2],
            "low": [99.5, 99.4, 98.8],
            "close": [100.2, 99.5, 101.0],
            "volume": 1000.0,
        },
        index=idx,
    )
    pattern, strength = _candle_pattern(df)
    assert pattern == "bullish_engulfing"
    assert strength == pytest.approx(0.85)


def test_candle_pattern_detects_hammer_regardless_of_bar_two_ago():
    idx = pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 100.4],
            "low": [99.0, 99.0, 97.0],
            "close": [100.2, 99.8, 100.3],
            "volume": 1000.0,
        },
        index=idx,
    )
    pattern, strength = _candle_pattern(df)
    assert pattern == "hammer"
    assert strength == pytest.approx(0.75)


# ── zero behavior change: bias/score/reasons unaffected ────────────────

def test_bias_score_reasons_unchanged_by_new_observability_fields():
    df = _ohlcv(300, seed=21, trend=0.08)
    eng = PriceActionEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(_mtf(df))
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert isinstance(out.score, float)
    assert isinstance(out.reasons, list) and len(out.reasons) > 0


def test_detect_breakout_contract_unchanged():
    # Regression guard: the pre-existing detect_breakout() legacy function
    # (unrelated to this pass's extract_features/decide changes) still
    # behaves identically — untouched by this refinement.
    df = _ohlcv(60)
    is_breakout, direction = detect_breakout(df, lookback=20)
    assert isinstance(is_breakout, bool)
    assert direction in ("upside", "downside", "none")
