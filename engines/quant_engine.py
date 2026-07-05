"""
engines/quant_engine.py
---------------------------
Quantitative/statistical confirmation engine — Phase 3.

Computes objective, math-based indicators that confirm or contradict
the directional bias from SMC and ICT:

1. RSI (14): momentum — oversold (<30) = bullish, overbought (>70) = bearish
2. ATR percentile: volatility regime — low vol = breakout potential
3. Momentum (ROC): rate of change over 10 bars
4. Price vs VWAP-proxy: mean reversion signal

These are NOT edge-proven individually — they serve as confirmation
filters when SMC+ICT already agree. Quant activation requires H003
hypothesis (or later) to pass edge_gate.py once enough decision data
accumulates to test whether quant confirmation improves precision.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _roc(series: pd.Series, period: int = 10) -> pd.Series:
    return series.pct_change(periods=period) * 100


def _atr_percentile(df: pd.DataFrame, period: int = 14, lookback: int = 100) -> float:
    """Current ATR as percentile of its own recent history."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    current_atr = atr.iloc[-1]
    historical = atr.dropna().tail(lookback)
    if len(historical) < 10:
        return 0.5
    pct = float((historical <= current_atr).mean())
    return round(pct, 3)


class QuantEngine(BaseEngine):
    name = "Quant"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        tf, df = self.decision_frame(mtf_data)

        if len(df) < 50:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data for quant analysis (need 50+ bars)"],
            )

        close = df["close"]
        rsi_series = _rsi(close, 14)
        rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
        roc_series = _roc(close, 10)
        roc_val = float(roc_series.iloc[-1]) if not pd.isna(roc_series.iloc[-1]) else 0.0
        atr_pct = _atr_percentile(df)

        reasons = []
        bullish_signals = 0
        bearish_signals = 0
        total_signals = 0

        # RSI
        if rsi_val < 30:
            bullish_signals += 1
            reasons.append(f"RSI oversold ({rsi_val:.1f}) — bullish momentum exhaustion")
        elif rsi_val > 70:
            bearish_signals += 1
            reasons.append(f"RSI overbought ({rsi_val:.1f}) — bearish momentum exhaustion")
        elif rsi_val < 45:
            bearish_signals += 0.5
            reasons.append(f"RSI leaning bearish ({rsi_val:.1f}) but not extreme")
        elif rsi_val > 55:
            bullish_signals += 0.5
            reasons.append(f"RSI leaning bullish ({rsi_val:.1f}) but not extreme")
        total_signals += 1

        # Momentum (ROC)
        if roc_val > 0.3:
            bullish_signals += 1
            reasons.append(f"Positive momentum: ROC(10)={roc_val:+.2f}%")
        elif roc_val < -0.3:
            bearish_signals += 1
            reasons.append(f"Negative momentum: ROC(10)={roc_val:+.2f}%")
        total_signals += 1

        # ATR context
        if atr_pct < 0.25:
            reasons.append(f"Low volatility (ATR pct={atr_pct:.0%}) — potential breakout setup")
        elif atr_pct > 0.80:
            reasons.append(f"High volatility (ATR pct={atr_pct:.0%}) — elevated risk, reduce size")

        # Determine bias
        if bullish_signals > bearish_signals:
            bias = Bias.BULLISH
            ratio = bullish_signals / max(total_signals, 1)
            score = round(min(ratio * 60, 60.0), 1)
        elif bearish_signals > bullish_signals:
            bias = Bias.BEARISH
            ratio = bearish_signals / max(total_signals, 1)
            score = round(min(ratio * 60, 60.0), 1)
        else:
            bias = Bias.NEUTRAL
            score = 0.0
            reasons.append("Quant signals mixed — no clear direction")

        raw = {
            "timeframe_used": tf,
            "rsi": round(rsi_val, 1),
            "roc_10": round(roc_val, 3),
            "atr_percentile": atr_pct,
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
        )
