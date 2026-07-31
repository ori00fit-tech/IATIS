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
