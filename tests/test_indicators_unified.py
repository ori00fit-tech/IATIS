"""
tests/test_indicators_unified.py
-----------------------------------
Confluence Engine Overhaul Phase 1 — pins the exact formulas of the
indicator functions unified into utils/indicators.py (rsi, bollinger_bands,
roc, find_swings), so a future "improvement" (e.g. Wilder-smoothed RSI)
can't silently change engine behavior without a deliberate, measured
decision.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.indicators import bollinger_bands, find_swings, roc, rsi


def _trending_series(n: int = 60, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.normal(loc=0.001, scale=0.01, size=n)
    return pd.Series(100 * np.exp(np.cumsum(returns)))


def test_rsi_matches_hand_computed_sma_formula():
    series = _trending_series(n=30, seed=1)
    result = rsi(series, period=14)

    delta = series.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    expected = 100 - (100 / (1 + rs))

    pd.testing.assert_series_equal(result, expected)


def test_rsi_all_gains_is_nan_zero_loss_division():
    """All-gains input drives loss to exactly 0 -> loss.replace(0, nan) ->
    rs=nan -> rsi=nan. Every current caller (price_action/nnfx engines)
    treats this NaN as a 50.0 (neutral) fallback at their own call site —
    pinning the raw NaN here, not a caller's fallback behavior."""
    series = pd.Series([100 + i for i in range(30)], dtype=float)
    result = rsi(series, period=14)
    assert pd.isna(result.iloc[-1])


def test_bollinger_bands_matches_hand_computed_formula():
    series = _trending_series(n=40, seed=2)
    upper, mid, lower = bollinger_bands(series, period=20, n_std=2.0)

    ma = series.rolling(20).mean()
    sd = series.rolling(20).std()
    pd.testing.assert_series_equal(upper, ma + 2.0 * sd)
    pd.testing.assert_series_equal(mid, ma)
    pd.testing.assert_series_equal(lower, ma - 2.0 * sd)


def test_bollinger_bands_upper_above_mid_above_lower():
    series = _trending_series(n=40, seed=3)
    upper, mid, lower = bollinger_bands(series, period=20)
    tail = slice(25, 40)
    assert (upper.iloc[tail] >= mid.iloc[tail]).all()
    assert (mid.iloc[tail] >= lower.iloc[tail]).all()


def test_roc_matches_hand_computed_pct_change():
    series = _trending_series(n=30, seed=4)
    result = roc(series, period=10)
    expected = series.pct_change(periods=10) * 100
    pd.testing.assert_series_equal(result, expected)


def test_roc_flat_series_is_zero():
    series = pd.Series([100.0] * 20)
    result = roc(series, period=5)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_find_swings_matches_smc_engines_original_rolling_formula():
    rng = np.random.default_rng(42)
    n = 200
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.random(n) * 0.3
    low = close - rng.random(n) * 0.3
    df = pd.DataFrame({"high": high, "low": low, "close": close})

    swing_high, swing_low = find_swings(df, window=3)

    highs = df["high"]
    lows = df["low"]
    expected_high = (highs == highs.rolling(window=7, center=True).max()).fillna(False)
    expected_low = (lows == lows.rolling(window=7, center=True).min()).fillna(False)

    pd.testing.assert_series_equal(swing_high, expected_high)
    pd.testing.assert_series_equal(swing_low, expected_low)


def test_find_swings_matches_market_structure_engines_original_loop_formula():
    """market_structure_engine.py's pre-unification _swing_points() was a
    plain positional loop, independently implemented from smc_engine's
    rolling-window version — confirmed bit-identical on real data before
    unifying onto one canonical implementation (Confluence Engine Overhaul
    Phase 1). This test pins that equivalence."""
    rng = np.random.default_rng(7)
    n = 150
    close = 50 + np.cumsum(rng.normal(0, 0.3, n))
    high = close + rng.random(n) * 0.2
    low = close - rng.random(n) * 0.2
    df = pd.DataFrame({"high": high, "low": low, "close": close})

    window = 3

    def old_swing_points(df: pd.DataFrame, window: int) -> tuple[list, list]:
        high_s = df["high"].astype(float)
        low_s = df["low"].astype(float)
        highs, lows = [], []
        for i in range(window, len(df) - window):
            if high_s.iloc[i] == high_s.iloc[i - window:i + window + 1].max():
                highs.append(i)
            if low_s.iloc[i] == low_s.iloc[i - window:i + window + 1].min():
                lows.append(i)
        return highs, lows

    old_high_idx, old_low_idx = old_swing_points(df, window)

    swing_high, swing_low = find_swings(df, window=window)
    new_high_idx = list(np.where(swing_high.to_numpy())[0])
    new_low_idx = list(np.where(swing_low.to_numpy())[0])

    assert old_high_idx == new_high_idx
    assert old_low_idx == new_low_idx


def test_find_swings_empty_at_edges():
    df = pd.DataFrame({"high": [1.0, 2.0, 3.0], "low": [0.5, 1.5, 2.5]})
    swing_high, swing_low = find_swings(df, window=3)
    assert not swing_high.any()
    assert not swing_low.any()
