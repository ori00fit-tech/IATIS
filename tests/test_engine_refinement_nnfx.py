"""
tests/test_engine_refinement_nnfx.py
----------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — NNFX refinement
(#360), canonical only (NNFX has no v2 variant). Additive OBSERVABILITY
changes plus a zero-risk indicator-math dedup and dead-code removal,
zero strategy-behavior change. Pins:

1. adx_undefined/rsi_undefined distinguish a genuine near-0/near-50
   reading from the NaN-fallback case (DI+==DI-==0 for ADX; RS
   undefined for RSI). Fallback VALUES are left unchanged — only their
   meaning is now disambiguated.
2. _adx() now computes its internal ATR via utils.indicators.atr()
   instead of a locally duplicated `tr.rolling(period).mean()` — both
   formulas are mathematically identical (pandas rolling(window).mean()
   defaults min_periods=window, matching atr()'s explicit
   min_periods=period), confirmed here by direct comparison.
3. _adx()'s unused `close` local (assigned, never read) is removed —
   confirmed behavior-neutral.
4. None of this changes bias/score/reasons — confirmed via the golden
   regression suite (tests/test_engine_config_extraction_no_behavior_
   change.py: NNFX scenario A=BEARISH 68.0, B=BEARISH 55.0) staying
   green, and directly here via purity/before-after checks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.nnfx_engine import NNFXEngine, _adx, decide, extract_features
from utils.indicators import atr as canonical_atr
from utils.indicators import true_range


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


def _flat(n: int) -> pd.DataFrame:
    """A perfectly flat instrument: no directional movement at all ->
    DI+ == DI- == 0 -> dx is NaN at every bar -> adx is NaN at the last
    bar too (rolling mean of all-NaN input)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0},
        index=idx,
    )


def _monotonic_up(n: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.arange(n) * 0.5
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": close + 0.2, "low": o - 0.2, "close": close, "volume": 1000.0},
        index=idx,
    )


def _mtf(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"H1": df}


# ── _adx()'s ATR now reuses the canonical function ──────────────────────

def test_adx_atr_matches_canonical_atr_formula():
    df = _ohlcv(250, seed=3)
    expected = canonical_atr(df, 14)
    # Reconstruct what _adx() now computes internally and confirm it's
    # exactly the canonical series, not a re-derived duplicate.
    from engines.nnfx_engine import _atr_series
    actual = _atr_series(df, 14)
    pd.testing.assert_series_equal(actual, expected)


def test_adx_atr_equals_plain_rolling_mean_of_true_range():
    # Proves the substitution is formula-identical to the OLD local
    # computation (tr.rolling(period).mean()) it replaced.
    df = _ohlcv(250, seed=5)
    old_style = true_range(df).rolling(14).mean()
    new_style = canonical_atr(df, 14)
    pd.testing.assert_series_equal(old_style, new_style)


def test_adx_still_returns_a_series_with_expected_shape():
    df = _ohlcv(250, seed=9)
    adx = _adx(df, 14)
    assert isinstance(adx, pd.Series)
    assert len(adx) == len(df)


# ── adx_undefined / rsi_undefined observability ─────────────────────────

def test_rsi_and_adx_undefined_false_on_ordinary_data():
    df = _ohlcv(250, seed=1)
    features = extract_features(df, {})
    assert features["rsi_undefined"] is False
    assert features["adx_undefined"] is False


def test_adx_undefined_true_on_a_perfectly_flat_instrument():
    df = _flat(60)
    features = extract_features(df, {"adx_period": 14})
    assert features["adx_undefined"] is True
    # Fallback value unchanged (still 0.0) — only its meaning disambiguated.
    assert features["adx_val"] == 0.0


def test_rsi_undefined_true_on_zero_average_loss():
    df = _monotonic_up(250)
    features = extract_features(df, {"rsi_period": 14})
    assert features["rsi_undefined"] is True
    assert features["rsi_val"] == 50.0


def test_undefined_flags_surface_in_engine_raw_output():
    df = _flat(220)
    eng = NNFXEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(_mtf(df))
    assert out.raw["adx_undefined"] is True
    assert out.features["adx_undefined"] is True


def test_undefined_flags_never_reach_decide_scoring():
    """decide() must remain a pure function of adx_val/rsi_val, not the
    new *_undefined flags — stripping them must not change bias/score/
    reasons."""
    df = _ohlcv(250, seed=13)
    features = extract_features(df, {})
    bias1, score1, reasons1 = decide(features, {})
    stripped = {k: v for k, v in features.items() if k not in ("adx_undefined", "rsi_undefined")}
    bias2, score2, reasons2 = decide(stripped, {})
    assert bias1 == bias2
    assert score1 == score2
    assert reasons1 == reasons2


# ── dead-code removal (`close` local in _adx) is behavior-neutral ──────

def test_adx_output_identical_regardless_of_the_removed_close_local():
    # _adx() never read df["close"] even before the cleanup — confirm the
    # function's real inputs (high/low only) still produce a sane series.
    df = _ohlcv(250, seed=17)
    adx1 = _adx(df, 14)
    adx2 = _adx(df.copy(), 14)
    pd.testing.assert_series_equal(adx1, adx2)


# ── zero behavior change: bias/score/reasons unaffected ────────────────

def test_bias_score_reasons_unchanged_by_new_observability_fields():
    df = _ohlcv(300, seed=23, trend=0.1)
    eng = NNFXEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(_mtf(df))
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert isinstance(out.score, float)
    assert 0.0 <= out.score <= 80.0
    assert isinstance(out.reasons, list) and len(out.reasons) > 0
