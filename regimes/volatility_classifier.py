"""
regimes/volatility_classifier.py
----------------------------------
ATR-based volatility classification. Kept separate from regime_detector.py
because volatility state (low/normal/high/extreme) is a useful signal on
its own — e.g. risk_engine will want it independently of trend/range state.
"""

from __future__ import annotations

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(window=period, min_periods=period).mean()


def classify_volatility(df: pd.DataFrame, period: int = 14, lookback: int = 100) -> pd.Series:
    """Classify each bar's volatility relative to its own recent history.

    Returns a Series of labels: "low", "normal", "high", "extreme".
    Percentile thresholds are a reasonable starting point for Phase 1 and
    should be revisited once real market data is available.
    """
    atr_series = atr(df, period=period)
    rolling_rank = atr_series.rolling(window=lookback, min_periods=period).rank(pct=True)

    def label(pct: float | None) -> str:
        if pct is None or pd.isna(pct):
            return "unknown"
        if pct < 0.25:
            return "low"
        if pct < 0.75:
            return "normal"
        if pct < 0.95:
            return "high"
        return "extreme"

    return rolling_rank.apply(label)
