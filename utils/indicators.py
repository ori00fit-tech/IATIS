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
