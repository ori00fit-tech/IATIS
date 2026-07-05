"""
engines/nnfx_engine.py
-------------------------
NNFX (No Nonsense Forex) methodology engine — Phase 3.

The NNFX system uses a layered confirmation approach:
1. Baseline indicator (200 EMA): overall trend direction
2. Confirmation indicator (ADX): trend strength
3. Volume/momentum filter: avoid low-momentum entries
4. ATR-based exit sizing: consistent risk per trade

Simplified implementation using indicators available from OHLCV only
(no volume-based indicators since Twelve Data Free plan returns 0
for FX volume). Uses EMA stack for baseline and ADX for strength.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — trend strength (not direction)."""
    high, low, close = df["high"], df["low"], df["close"]

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    dm_plus = (high - high.shift()).clip(lower=0)
    dm_minus = (low.shift() - low).clip(lower=0)
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

    atr = tr.rolling(period).mean()
    di_plus = 100 * dm_plus.rolling(period).mean() / atr.replace(0, np.nan)
    di_minus = 100 * dm_minus.rolling(period).mean() / atr.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return dx.rolling(period).mean()


class NNFXEngine(BaseEngine):
    name = "NNFX"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        tf, df = self.decision_frame(mtf_data)

        if len(df) < 210:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data for NNFX analysis (need 210+ bars for EMA200)"],
            )

        close = df["close"]
        ema50 = _ema(close, 50)
        ema100 = _ema(close, 100)
        ema200 = _ema(close, 200)
        adx = _adx(df, 14)

        current = float(close.iloc[-1])
        e50 = float(ema50.iloc[-1])
        e100 = float(ema100.iloc[-1])
        e200 = float(ema200.iloc[-1])
        adx_val = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0

        reasons = []
        score = 0.0
        bias = Bias.NEUTRAL

        # --- Baseline: EMA200 ---
        if current > e200:
            bias = Bias.BULLISH
            score += 25.0
            reasons.append(f"Price above EMA200 ({e200:.5f}) — bullish baseline")
        elif current < e200:
            bias = Bias.BEARISH
            score += 25.0
            reasons.append(f"Price below EMA200 ({e200:.5f}) — bearish baseline")

        # --- EMA stack confirmation ---
        if bias == Bias.BULLISH and e50 > e100 > e200:
            score += 20.0
            reasons.append("EMA stack aligned bullish (50>100>200)")
        elif bias == Bias.BEARISH and e50 < e100 < e200:
            score += 20.0
            reasons.append("EMA stack aligned bearish (50<100<200)")
        elif bias != Bias.NEUTRAL:
            reasons.append("EMA stack not fully aligned — weak confirmation")
            score += 5.0

        # --- ADX strength filter ---
        if adx_val >= 25:
            score += 20.0
            reasons.append(f"ADX={adx_val:.1f} ≥ 25 — trending market, strong confirmation")
        elif adx_val >= 15:
            score += 8.0
            reasons.append(f"ADX={adx_val:.1f} — moderate trend strength")
        else:
            reasons.append(f"ADX={adx_val:.1f} < 15 — weak trend, NNFX cautions against entry")
            score = max(0, score - 15)
            if score < 15:
                bias = Bias.NEUTRAL

        # --- RSI second confirmation (NNFX methodology) ---
        rsi_period = 14
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        if bias == Bias.BULLISH and rsi_val > 50:
            score += 15.0
            reasons.append(f"RSI={rsi_val:.1f} confirms bullish (>50)")
        elif bias == Bias.BEARISH and rsi_val < 50:
            score += 15.0
            reasons.append(f"RSI={rsi_val:.1f} confirms bearish (<50)")
        elif bias != Bias.NEUTRAL:
            reasons.append(f"RSI={rsi_val:.1f} does not confirm direction — reduced confidence")
            score = max(0, score - 10)

        score = min(round(score, 1), 80.0)

        raw = {
            "timeframe_used": tf,
            "ema50": round(e50, 5),
            "ema100": round(e100, 5),
            "ema200": round(e200, 5),
            "adx": round(adx_val, 1),
            "rsi": round(rsi_val, 1),
            "price_vs_ema200_pct": round((current - e200) / e200 * 100, 3),
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
        )
