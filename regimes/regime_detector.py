"""
regimes/regime_detector.py
-----------------------------
Classifies the current market state. This gates which engines get
activated downstream (per the IATIS design: schools of thought are
turned on/off depending on regime, not blended blindly).

Phase 1 implements a real (if simple) trend/range classifier using
directional strength + volatility, since this is foundational —
everything else depends on it. MANIPULATION / NEWS_DRIVEN detection
needs order-flow / news-feed data we don't have yet, so those remain
explicit stubs rather than fake heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from regimes.volatility_classifier import atr, classify_volatility
from utils.logger import get_logger

logger = get_logger(__name__)


class Regime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    ACCUMULATION = "ACCUMULATION"   # Phase 3+: needs volume/order-flow data
    DISTRIBUTION = "DISTRIBUTION"   # Phase 3+: needs volume/order-flow data
    MANIPULATION = "MANIPULATION"   # Phase 3+: needs liquidity-sweep detection
    NEWS_DRIVEN = "NEWS_DRIVEN"     # Phase 4: needs macro/news feed
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeResult:
    regime: Regime
    confidence: float          # 0.0 - 1.0
    volatility: str            # low | normal | high | extreme
    trend_strength: float      # -1.0 (strong down) to +1.0 (strong up)
    notes: str = ""


def _trend_strength(df: pd.DataFrame, lookback: int = 50) -> float:
    """Simple directional strength: normalized linear regression slope
    of closing price over the lookback window, scaled to [-1, 1].
    """
    closes = df["close"].tail(lookback).to_numpy()
    if len(closes) < lookback:
        return 0.0

    x = np.arange(len(closes))
    slope, _ = np.polyfit(x, closes, 1)

    # normalize slope by price level and volatility so it's comparable
    # across instruments/timeframes
    price_scale = closes.mean()
    vol_scale = closes.std() if closes.std() > 0 else 1e-9
    normalized = (slope * len(closes)) / (vol_scale + 1e-9)

    return float(np.clip(normalized / 5.0, -1.0, 1.0))


def detect_regime(
    df: pd.DataFrame,
    atr_period: int = 14,
    lookback: int = 100,
    trend_threshold: float = 0.35,
) -> RegimeResult:
    """Classify the current regime from the most recent data.

    NOTE: this is a Phase 1 heuristic (trend strength + volatility only).
    ACCUMULATION / DISTRIBUTION / MANIPULATION / NEWS_DRIVEN require data
    feeds (order flow, news) not yet wired up — they intentionally fall
    back to UNKNOWN rather than being guessed.
    """
    if len(df) < max(atr_period, lookback):
        return RegimeResult(
            regime=Regime.UNKNOWN,
            confidence=0.0,
            volatility="unknown",
            trend_strength=0.0,
            notes="Insufficient data for regime detection",
        )

    strength = _trend_strength(df, lookback=min(lookback, 50))
    vol_series = classify_volatility(df, period=atr_period, lookback=lookback)
    current_vol = vol_series.iloc[-1]

    if abs(strength) >= trend_threshold:
        regime = Regime.TRENDING
        confidence = min(abs(strength), 1.0)
    else:
        regime = Regime.RANGING
        confidence = 1.0 - abs(strength) / trend_threshold if trend_threshold > 0 else 0.5
        confidence = float(np.clip(confidence, 0.0, 1.0))

    result = RegimeResult(
        regime=regime,
        confidence=round(confidence, 3),
        volatility=current_vol,
        trend_strength=round(strength, 3),
        notes="Phase 1 heuristic: trend-strength + ATR volatility only",
    )

    logger.info(
        f"Regime detected: {result.regime.value} "
        f"(confidence={result.confidence}, volatility={result.volatility}, "
        f"trend_strength={result.trend_strength})"
    )
    return result
