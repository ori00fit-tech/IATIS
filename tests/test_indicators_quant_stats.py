"""
tests/test_indicators_quant_stats.py
----------------------------------------
Confluence Engine Overhaul Phase 3a — hand-computed and synthetic-series
correctness tests for the statistical functions added to
utils/indicators.py for engines/quant_engine.py's regime-aware rebuild.

Correctness bar for a stats engine: real, independently-verified
formulas (hand-computed micro-examples + known-generator synthetic
series), not golden bias/score values (v1 quant_engine.py is being
fully replaced, not refactored — there is nothing to reproduce).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from statsmodels.tsa.stattools import adfuller

from utils.indicators import (
    adf_pvalue,
    autocorrelation,
    efficiency_ratio,
    half_life,
    hurst_exponent,
    realized_volatility,
    shannon_entropy,
    variance_ratio,
    zscore,
)


# ---------------------------------------------------------------------------
# A. Hand-computed micro-examples
# ---------------------------------------------------------------------------

def test_zscore_matches_hand_computed_formula():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = zscore(s, window=5)
    ma = s.rolling(5).mean()
    sd = s.rolling(5).std()
    expected = (s - ma) / sd
    pd.testing.assert_series_equal(result, expected)


def test_zscore_zero_variance_window_is_nan():
    s = pd.Series([5.0] * 10)
    result = zscore(s, window=5)
    assert pd.isna(result.iloc[-1])


def test_autocorrelation_matches_pandas_autocorr():
    s = pd.Series([1.0, 3.0, 2.0, 5.0, 4.0, 7.0, 6.0, 9.0])
    result = autocorrelation(s, lag=1)
    assert result == pytest.approx(s.autocorr(1))


def test_autocorrelation_none_on_constant_series():
    s = pd.Series([3.0] * 10)
    assert autocorrelation(s, lag=1) is None


def test_autocorrelation_none_on_too_short_series():
    assert autocorrelation(pd.Series([1.0, 2.0]), lag=5) is None


def test_efficiency_ratio_matches_hand_computed_value():
    # net change over 5 bars = |104-100| = 4; path length = sum of
    # absolute bar-to-bar changes = |2|+|3|+|-1|+|4|+|-4| = 14 -> ER = 4/14
    s = pd.Series([100.0, 102.0, 105.0, 104.0, 108.0, 104.0])
    result = efficiency_ratio(s, period=5)
    assert result.iloc[-1] == pytest.approx(4 / 14)


def test_efficiency_ratio_perfectly_directional_is_one():
    s = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    result = efficiency_ratio(s, period=5)
    assert result.iloc[-1] == pytest.approx(1.0)


def test_shannon_entropy_constant_series_is_zero():
    s = pd.Series([1.0] * 20)
    assert shannon_entropy(s, bins=10) == pytest.approx(0.0, abs=1e-9)


def test_shannon_entropy_known_two_bin_split():
    # 8 values in bin 1, 2 values in bin 2 -> H = -(0.8*log2(0.8)+0.2*log2(0.2))
    # normalized by log2(2)=1, so normalized value equals the raw value.
    s = pd.Series([0.0] * 8 + [10.0] * 2)
    expected = -(0.8 * np.log2(0.8) + 0.2 * np.log2(0.2))
    result = shannon_entropy(s, bins=2)
    assert result == pytest.approx(expected, abs=1e-4)


def test_shannon_entropy_uniform_random_is_high():
    rng = np.random.default_rng(1)
    s = pd.Series(rng.uniform(0, 1, 5000))
    result = shannon_entropy(s, bins=20)
    assert result > 0.9


def test_shannon_entropy_none_when_too_few_points():
    assert shannon_entropy(pd.Series([1.0, 2.0]), bins=10) is None


def test_variance_ratio_matches_hand_computed_value():
    prices = pd.Series([100.0, 101.0, 99.0, 102.0, 98.0, 103.0, 97.0, 104.0, 96.0, 105.0, 95.0])
    q = 2
    log_p = np.log(prices.to_numpy())
    r1 = np.diff(log_p)
    rq = log_p[q:] - log_p[:-q]
    expected = np.var(rq, ddof=1) / (q * np.var(r1, ddof=1))
    result = variance_ratio(prices, q=q)
    assert result == pytest.approx(round(expected, 4))


def test_variance_ratio_none_when_too_short():
    assert variance_ratio(pd.Series([100.0, 101.0, 102.0]), q=10) is None


def test_realized_volatility_matches_hand_computed_std():
    returns = pd.Series([0.01, -0.01, 0.01, -0.01, 0.01, -0.01])
    bars_per_year = 8760.0
    expected = returns.std(ddof=1) * np.sqrt(bars_per_year)
    result = realized_volatility(returns, bars_per_year)
    assert result == pytest.approx(round(expected, 6))


def test_realized_volatility_none_when_too_short():
    assert realized_volatility(pd.Series([0.01]), 8760.0) is None


def test_bars_per_year_arithmetic():
    from engines.quant_engine import _bars_per_year

    assert _bars_per_year("H1", {}) == pytest.approx(8760.0)
    assert _bars_per_year("H4", {}) == pytest.approx(2190.0)
    assert _bars_per_year("D1", {}) == pytest.approx(365.0)
    assert _bars_per_year("UNKNOWN_TF", {}) == pytest.approx(8760.0)
    assert _bars_per_year("UNKNOWN_TF", {"bars_per_year_default": 100.0}) == pytest.approx(100.0)


def test_adf_pvalue_matches_direct_statsmodels_call():
    rng = np.random.default_rng(3)
    s = pd.Series(np.cumsum(rng.normal(0, 1, 200)) + 1000)
    result_stat, result_p = adf_pvalue(s, min_obs=30)
    expected_stat, expected_p, *_ = adfuller(s.to_numpy(), regression="c", autolag="AIC")
    assert result_stat == pytest.approx(expected_stat)
    assert result_p == pytest.approx(expected_p)


def test_adf_pvalue_none_when_too_short():
    assert adf_pvalue(pd.Series([1.0, 2.0, 3.0]), min_obs=30) == (None, None)


def test_adf_pvalue_none_on_zero_variance():
    assert adf_pvalue(pd.Series([5.0] * 50), min_obs=30) == (None, None)


def test_half_life_none_when_no_mean_reversion():
    # constant-log-return growth (no noise, no curvature) -> delta_p is
    # exactly constant regardless of level -> OLS slope (beta) is exactly
    # 0.0, which is >= 0 -> no mean reversion detected -> None.
    s = pd.Series(np.log(100.0) + 0.01 * np.arange(50, dtype=float))
    assert half_life(s, min_obs=30) is None


def test_half_life_none_when_too_short():
    assert half_life(pd.Series([1.0, 2.0]), min_obs=30) is None


# ---------------------------------------------------------------------------
# B. Synthetic-series regime checks
# ---------------------------------------------------------------------------

def _random_walk_prices(n: int, seed: int, start: float = 1000.0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(start + np.cumsum(rng.normal(0, 1, n)))


def _ou_process_prices(n: int, seed: int, theta: float = 0.1, mu: float = 100.0, sigma: float = 1.0) -> pd.Series:
    rng = np.random.default_rng(seed)
    x = np.empty(n)
    x[0] = mu
    for t in range(1, n):
        x[t] = x[t - 1] + theta * (mu - x[t - 1]) + sigma * rng.normal()
    return pd.Series(x)


def _trending_prices(n: int, seed: int, drift: float = 0.5, noise: float = 1.0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100.0 + drift * np.arange(n) + np.cumsum(rng.normal(0, noise, n)))


def _ar1_returns(n: int, seed: int, phi: float = 0.5) -> pd.Series:
    rng = np.random.default_rng(seed)
    r = np.empty(n)
    r[0] = rng.normal()
    for t in range(1, n):
        r[t] = phi * r[t - 1] + rng.normal()
    return pd.Series(r)


def test_pure_random_walk_regime_signals():
    prices = _random_walk_prices(5000, seed=1)
    log_returns = np.log(prices).diff().dropna()

    hurst = hurst_exponent(log_returns)
    assert 0.40 <= hurst <= 0.60

    _stat, p = adf_pvalue(prices)
    assert p > 0.10

    vr = variance_ratio(prices, q=10)
    assert 0.85 <= vr <= 1.15


def test_mean_reverting_ou_process_regime_signals():
    theta = 0.1
    prices = _ou_process_prices(2000, seed=2, theta=theta)
    log_returns = np.log(prices).diff().dropna()

    hurst = hurst_exponent(log_returns)
    assert hurst < 0.45

    _stat, p = adf_pvalue(prices)
    assert p < 0.05

    vr = variance_ratio(prices, q=10)
    assert vr < 0.9

    hl = half_life(np.log(prices))
    theoretical = np.log(2) / theta
    assert hl is not None
    assert theoretical * 0.4 <= hl <= theoretical * 2.5


def test_strongly_trending_series_regime_signals():
    prices = _trending_prices(2000, seed=4, drift=0.5, noise=1.0)

    log_returns = np.log(prices).diff().dropna()
    hurst = hurst_exponent(log_returns)
    assert hurst > 0.55

    vr = variance_ratio(prices, q=10)
    assert vr > 1.1

    er = efficiency_ratio(prices, period=10).iloc[-1]
    assert er > 0.5
    # Deliberately NOT asserting autocorrelation here — i.i.d. increments
    # plus linear drift give near-zero RETURN autocorrelation even though
    # the LEVEL process is unambiguously trending. Asserting positive
    # autocorrelation on this generator would be statistically wrong.


def test_ar1_process_autocorrelation_matches_phi():
    phi = 0.5
    returns = _ar1_returns(5000, seed=5, phi=phi)
    result = autocorrelation(returns, lag=1)
    assert result == pytest.approx(phi, abs=0.05)
