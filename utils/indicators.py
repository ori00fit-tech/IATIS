"""
utils/indicators.py
--------------------
The single home for indicator math shared across engines, gates, and the
backtester (institutional gap analysis, addendum A2).

Why this exists: ATR alone was independently reimplemented in seven
modules. Seven implementations is seven chances for a silent divergence
between what the gates see and what the backtest simulated. This module
consolidates LOCATION without changing a single number — each variant
below is the exact formula its call sites already used, and the
migration was verified bit-for-bit against a pre-change replay corpus
(research/replay.py).

TWO deliberately different "ATR" variants exist in this codebase:

  atr(df, period)        — TRUE-RANGE ATR: rolling mean of
                           max(H−L, |H−C₋₁|, |L−C₋₁|). Used by the
                           volatility classifier, the MQS volatility
                           score, the quant engine's percentile, and
                           (as its TR input) the NNFX ADX.

  range_atr(df, period)  — SIMPLIFIED RANGE MEAN: mean of (H−L) over the
                           last `period` bars, as a scalar. Used by the
                           SMC, Wyckoff, and PriceAction engines.

range_atr is NOT true ATR (it ignores gaps via prev-close). That is not
a bug to fix: the measured, validated system behavior (H4 backtests,
the frozen prod4 config) was produced WITH this variant in those
engines. Changing an engine from range_atr to atr() is a strategy
change — it requires a pre-registered hypothesis and resets the forward
sample (CLAUDE.md rule 6). Consolidating it here makes the variant
visible and greppable instead of hidden in three inline copies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Confluence Engine Overhaul Phase 3a (2026-08-01) — quant_engine.py's
# statistical rebuild needs a real ADF stationarity test (see
# adf_pvalue() below). This is the one function in this module that
# needs a dependency heavier than numpy/pandas — everything else here
# stays dependency-light on purpose.
from statsmodels.tsa.stattools import adfuller


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range per bar: max(H−L, |H−prevC|, |L−prevC|)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    return pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True-range ATR: rolling mean of true_range over `period` bars.
    NaN until `period` bars exist (min_periods=period)."""
    return true_range(df).rolling(window=period, min_periods=period).mean()


def range_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Simplified range mean: mean of (high−low) over the LAST `period`
    bars, as a scalar. See module docstring — deliberately different
    from atr(); do not "upgrade" call sites without a pre-registered
    hypothesis."""
    return float((df["high"] - df["low"]).tail(period).mean())


# ---------------------------------------------------------------------------
# Confluence Engine Overhaul Phase 1 (indicator unification) — each function
# below is byte-identical to an existing engine's own formula, moved here so
# the same math has one home instead of N independently-maintained copies.
# See CLAUDE.md's Confluence Engine Overhaul plan section: RSI here matches
# price_action_engine/quant_engine/nnfx_engine's SMA-smoothed formula exactly
# — divergence_engine's own EWM-smoothed RSI is a genuinely DIFFERENT
# formula (not a duplicate) and stays where it is until Phase 3 rebuilds it.
# ---------------------------------------------------------------------------


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """SMA-smoothed RSI — matches price_action_engine.py/quant_engine.py/
    nnfx_engine.py's previously-duplicated formula exactly."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    series: pd.Series, period: int = 20, n_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(upper, mid, lower) bands — matches price_action_engine.py's
    formula exactly. Returns SMA +/- n_std * rolling std."""
    ma = series.rolling(period).mean()
    sd = series.rolling(period).std()
    return ma + n_std * sd, ma, ma - n_std * sd


def roc(series: pd.Series, period: int = 10) -> pd.Series:
    """Rate of change (%) — matches quant_engine.py's formula exactly.
    Centralized here for consistency; quant_engine.py itself is NOT
    migrated onto this in Phase 1 (it's a Phase 3 full-rebuild target —
    see the Confluence Engine Overhaul plan)."""
    return series.pct_change(periods=period) * 100


def find_swings(df: pd.DataFrame, window: int = 3) -> tuple[pd.Series, pd.Series]:
    """(swing_high, swing_low) boolean Series aligned to df's index — a
    bar whose high/low is the max/min within +/- `window` bars on either
    side. Matches smc_engine.py's find_swing_points formula exactly
    (confirmed bit-identical to market_structure_engine.py's own
    independent loop-based implementation before this unification)."""
    highs = df["high"]
    lows = df["low"]
    swing_high = highs == highs.rolling(window=2 * window + 1, center=True).max()
    swing_low = lows == lows.rolling(window=2 * window + 1, center=True).min()
    return swing_high.fillna(False), swing_low.fillna(False)


# ---------------------------------------------------------------------------
# Confluence Engine Overhaul Phase 3a (2026-08-01) — statistics for
# engines/quant_engine.py's regime-aware rebuild (TRENDING / MEAN_REVERTING
# / RANDOM classification). Kept here rather than private to quant_engine.py
# because each is generic, self-contained math worth its own unit test,
# matching this module's own charter of preventing N independently
# maintained copies of the same formula.
#
# Contract note: unlike rsi()/roc()/bollinger_bands() (which return a
# per-bar Series), every function below returns a single scalar
# (float | None) — each is meant to be called on an already
# lookback-sliced Series, not a full history.
# ---------------------------------------------------------------------------


def zscore(series: pd.Series, window: int = 20) -> pd.Series:
    """Rolling z-score: (value - rolling mean) / rolling std. Same
    rolling mean/std machinery as bollinger_bands() above, but returns a
    standardized ratio rather than price-scaled bands."""
    ma = series.rolling(window).mean()
    sd = series.rolling(window).std()
    return (series - ma) / sd.replace(0, np.nan)


def autocorrelation(series: pd.Series, lag: int = 1) -> float | None:
    """Lag-N autocorrelation of `series`. None if too short or
    zero-variance (autocorr is undefined for a constant series)."""
    s = series.dropna()
    if len(s) < lag + 2 or s.std() == 0:
        return None
    value = s.autocorr(lag)
    return float(value) if not pd.isna(value) else None


def efficiency_ratio(series: pd.Series, period: int = 10) -> pd.Series:
    """Kaufman's Efficiency Ratio: |net change over `period` bars| /
    sum(|bar-to-bar changes|) over the same window. Range [0, 1] — 1 =
    perfectly directional, near 0 = pure chop."""
    net_change = (series - series.shift(period)).abs()
    path_length = series.diff().abs().rolling(period).sum()
    return net_change / path_length.replace(0, np.nan)


def shannon_entropy(series: pd.Series, bins: int = 10, normalize: bool = True) -> float | None:
    """Shannon entropy of `series`' value distribution (histogram-based).
    Normalized (default) to [0, 1] by dividing by log2(bins) — near 1 =
    close to uniform/max-entropy (looks random), near 0 = concentrated/
    structured. A constant series correctly returns 0.0 (all mass in one
    bin), not None. None only when there isn't enough data to bin at all."""
    if bins < 2:
        return None
    s = series.dropna()
    if len(s) < bins:
        return None
    counts, _ = np.histogram(s.to_numpy(), bins=bins)
    total = counts.sum()
    if total == 0:
        return None
    probs = counts[counts > 0] / total
    h = float(-(probs * np.log2(probs)).sum())
    if normalize:
        h = h / np.log2(bins)
    return round(h, 4)


def hurst_exponent(
    series: pd.Series, lags: list[int] | None = None, min_lags: int = 4
) -> float | None:
    """Hurst exponent via classic R/S (rescaled range) analysis:
    for each lag, split `series` into non-overlapping chunks of that
    length, compute the rescaled range (R/S) per chunk and average, then
    fit log(mean R/S) vs log(lag) with a degree-1 OLS line (np.polyfit)
    — the slope is H. H≈0.5 => random walk, H>0.5 => trending/persistent,
    H<0.5 => mean-reverting/anti-persistent.

    Caller passes LOG RETURNS (not price levels) — the standard R/S
    input. Returns None if fewer than `min_lags` lags have >=2 full
    chunks available (not enough evidence to fit a reliable slope)."""
    lags = lags or [10, 20, 40, 80, 160]
    returns = series.dropna().to_numpy()
    n = len(returns)

    valid_lags: list[int] = []
    rs_values: list[float] = []
    for lag in lags:
        n_chunks = n // lag
        if n_chunks < 2:
            continue
        chunk_rs = []
        for i in range(n_chunks):
            chunk = returns[i * lag:(i + 1) * lag]
            mean_adj = chunk - chunk.mean()
            cum = np.cumsum(mean_adj)
            r = cum.max() - cum.min()
            s = chunk.std(ddof=1)
            if s > 0:
                chunk_rs.append(r / s)
        if chunk_rs:
            rs_values.append(float(np.mean(chunk_rs)))
            valid_lags.append(lag)

    if len(valid_lags) < min_lags:
        return None

    slope, _intercept = np.polyfit(np.log(valid_lags), np.log(rs_values), 1)
    return float(round(slope, 4))


def variance_ratio(series: pd.Series, q: int = 10) -> float | None:
    """Lo-MacKinlay variance ratio, simple overlapping-return point
    estimate: Var(q-period log returns, ddof=1) / (q * Var(1-period log
    returns, ddof=1)). VR≈1 => random walk, VR>1 => positive serial
    correlation/trending, VR<1 => negative serial correlation/mean-
    reverting.

    This is a magnitude/direction signal only — NOT the full
    heteroskedasticity-robust Lo-MacKinlay Z-statistic (that exists
    specifically to test statistical significance; adf_pvalue() below
    already supplies formal significance testing for this engine, so a
    second significance test is deliberately not built here).

    Caller passes PRICE LEVELS (not returns) — this function computes
    both the 1-period and q-period log returns internally."""
    prices = series.dropna()
    if len(prices) < q * 2 + 1:
        return None
    log_p = np.log(prices.to_numpy())
    r1 = np.diff(log_p)
    rq = log_p[q:] - log_p[:-q]
    var1 = np.var(r1, ddof=1)
    if var1 == 0:
        return None
    varq = np.var(rq, ddof=1)
    return float(round(varq / (q * var1), 4))


def adf_pvalue(series: pd.Series, min_obs: int = 30) -> tuple[float | None, float | None]:
    """Augmented Dickey-Fuller stationarity test on PRICE LEVELS (not
    returns), constant-only regression, AIC lag selection. Returns
    (test_statistic, p_value); (None, None) if too short or
    zero-variance. Low p-value (< threshold, decided by the caller) =>
    reject the unit-root null => series is stationary/mean-reverting.
    A HIGH p-value does NOT imply trending — ADF only tests for a unit
    root, so callers must treat this as a one-sided (mean-reversion-
    only) signal, never a trending vote."""
    s = series.dropna()
    if len(s) < min_obs or s.std() == 0:
        return None, None
    try:
        stat, pvalue, *_ = adfuller(s.to_numpy(), regression="c", autolag="AIC")
    except Exception:
        return None, None
    return float(stat), float(pvalue)


def half_life(series: pd.Series, min_obs: int = 30) -> float | None:
    """Half-life of mean reversion via an Ornstein-Uhlenbeck-style
    AR(1)-on-levels regression: delta_p[t] = alpha + beta*p[t-1] + eps,
    fit by plain OLS (np.polyfit — statsmodels is not needed for a
    single-variable slope). half_life = -ln(2)/beta when beta<0 (mean-
    reverting); None when beta>=0 (no mean reversion detected on this
    window — a one-sided signal, matching adf_pvalue()'s convention).

    Caller passes LOG PRICE LEVELS (not returns)."""
    p = series.dropna()
    if len(p) < min_obs + 1:
        return None
    lag_p = p.shift(1).dropna()
    delta_p = p.diff().dropna()
    lag_p, delta_p = lag_p.align(delta_p, join="inner")
    if len(lag_p) < min_obs or lag_p.std() == 0:
        return None
    beta, _alpha = np.polyfit(lag_p.to_numpy(), delta_p.to_numpy(), 1)
    if beta >= 0:
        return None
    return float(round(-np.log(2) / beta, 2))


def realized_volatility(returns: pd.Series, bars_per_year: float) -> float | None:
    """Annualized realized volatility: std(returns, ddof=1) *
    sqrt(bars_per_year). Caller passes an already lookback-sliced
    return series and the correct bars-per-year for the series'
    timeframe (see quant_engine.py's private _bars_per_year)."""
    r = returns.dropna()
    if len(r) < 2:
        return None
    return float(round(r.std(ddof=1) * np.sqrt(bars_per_year), 6))
