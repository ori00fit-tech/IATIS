"""
engines/price_action_engine.py
---------------------------------
Pure Price Action engine — candlestick patterns + momentum + volatility.

Deliberately different from NNFX (EMA-based) and SMC (structure-based).
Focuses on: recent candle patterns, RSI momentum, Bollinger Band position,
and short-term momentum. No EMA overlap with NNFX.

Correlation with NNFX was 0.975 (redundant) — now uses completely
different indicators to add genuine diversification.
"""

from __future__ import annotations
import math
import pandas as pd
import numpy as np

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bollinger(series: pd.Series, period: int = 20, std: float = 2.0):
    ma  = series.rolling(period).mean()
    sd  = series.rolling(period).std()
    return ma + std * sd, ma, ma - std * sd


def _candle_pattern(df: pd.DataFrame) -> tuple[str, float]:
    """Detect key candlestick reversal/continuation patterns."""
    if len(df) < 3:
        return "none", 0.0

    c0 = df.iloc[-1]   # current
    c1 = df.iloc[-2]   # previous
    c2 = df.iloc[-3]   # two bars ago

    body0 = abs(float(c0["close"]) - float(c0["open"]))
    wick_top0 = float(c0["high"]) - max(float(c0["close"]), float(c0["open"]))
    wick_bot0 = min(float(c0["close"]), float(c0["open"])) - float(c0["low"])
    range0 = float(c0["high"]) - float(c0["low"]) or 1e-10

    body1 = abs(float(c1["close"]) - float(c1["open"]))

    # Hammer / Bullish Pin Bar
    if (wick_bot0 > body0 * 2 and wick_bot0 > wick_top0 * 2
            and body0 < range0 * 0.4):
        return "hammer", 0.75

    # Shooting Star / Bearish Pin Bar
    if (wick_top0 > body0 * 2 and wick_top0 > wick_bot0 * 2
            and body0 < range0 * 0.4):
        return "shooting_star", 0.75

    # Bullish Engulfing
    if (float(c0["close"]) > float(c0["open"]) and
            float(c1["close"]) < float(c1["open"]) and
            float(c0["close"]) > float(c1["open"]) and
            float(c0["open"]) < float(c1["close"])):
        return "bullish_engulfing", 0.85

    # Bearish Engulfing
    if (float(c0["close"]) < float(c0["open"]) and
            float(c1["close"]) > float(c1["open"]) and
            float(c0["close"]) < float(c1["open"]) and
            float(c0["open"]) > float(c1["close"])):
        return "bearish_engulfing", 0.85

    # Doji
    if body0 < range0 * 0.1 and range0 > 0:
        return "doji", 0.3

    return "none", 0.0


class PriceActionEngine(BaseEngine):
    name = "PriceAction"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        tf = "H1" if "H1" in mtf_data else next(iter(mtf_data))
        df = mtf_data[tf]

        if len(df) < 30:
            return EngineOutput(
                engine_name=self.name, bias=Bias.NEUTRAL, score=0.0,
                reasons=["Insufficient data (need 30+ bars)"],
            )

        close = df["close"]
        reasons = []
        bull_score = 0.0
        bear_score = 0.0

        # ── 1. RSI momentum (30 points max) ──────────────────────────
        rsi = _rsi(close)
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        rsi_prev = float(rsi.iloc[-2]) if not pd.isna(rsi.iloc[-2]) else 50.0

        if rsi_val > 55 and rsi_val > rsi_prev:
            bull_score += 30.0
            reasons.append(f"RSI={rsi_val:.1f} bullish momentum (rising, >55)")
        elif rsi_val < 45 and rsi_val < rsi_prev:
            bear_score += 30.0
            reasons.append(f"RSI={rsi_val:.1f} bearish momentum (falling, <45)")
        elif rsi_val > 70:
            bear_score += 15.0
            reasons.append(f"RSI={rsi_val:.1f} overbought — potential reversal")
        elif rsi_val < 30:
            bull_score += 15.0
            reasons.append(f"RSI={rsi_val:.1f} oversold — potential reversal")
        else:
            reasons.append(f"RSI={rsi_val:.1f} neutral zone")

        # ── 2. Bollinger Band position (25 points max) ────────────────
        upper, mid, lower = _bollinger(close)
        last_close = float(close.iloc[-1])
        last_upper = float(upper.iloc[-1])
        last_lower = float(lower.iloc[-1])
        last_mid   = float(mid.iloc[-1])

        if not any(pd.isna(x) for x in [last_upper, last_lower, last_mid]):
            bb_pct = (last_close - last_lower) / (last_upper - last_lower + 1e-10)
            if bb_pct > 0.8:
                bull_score += 20.0
                reasons.append(f"Price at upper Bollinger band ({bb_pct:.0%}) — strong momentum")
            elif bb_pct < 0.2:
                bear_score += 20.0
                reasons.append(f"Price at lower Bollinger band ({bb_pct:.0%}) — bearish pressure")
            elif last_close > last_mid:
                bull_score += 10.0
                reasons.append(f"Price above BB midline — mild bullish")
            else:
                bear_score += 10.0
                reasons.append(f"Price below BB midline — mild bearish")

        # ── 3. Candlestick pattern (25 points max) ────────────────────
        pattern, strength = _candle_pattern(df)
        if pattern in ("hammer", "bullish_engulfing"):
            bull_score += 25.0 * strength
            reasons.append(f"Bullish candle pattern: {pattern} (strength={strength:.0%})")
        elif pattern in ("shooting_star", "bearish_engulfing"):
            bear_score += 25.0 * strength
            reasons.append(f"Bearish candle pattern: {pattern} (strength={strength:.0%})")
        elif pattern == "doji":
            reasons.append("Doji detected — indecision, reducing score")
            bull_score *= 0.7
            bear_score *= 0.7

        # ── 4. Short-term momentum (20 points max) ───────────────────
        mom_bars = min(5, len(df) - 1)
        mom = float(close.iloc[-1]) - float(close.iloc[-1 - mom_bars])
        atr = float((df["high"] - df["low"]).tail(14).mean())
        mom_r = mom / atr if atr > 0 else 0

        if mom_r > 0.5:
            bull_score += 20.0
            reasons.append(f"Bullish short-term momentum (+{mom_r:.1f}× ATR)")
        elif mom_r < -0.5:
            bear_score += 20.0
            reasons.append(f"Bearish short-term momentum ({mom_r:.1f}× ATR)")

        # ── Final bias ────────────────────────────────────────────────
        if bull_score > bear_score and bull_score >= 30:
            bias = Bias.BULLISH
            score = min(round(bull_score, 1), 80.0)
        elif bear_score > bull_score and bear_score >= 30:
            bias = Bias.BEARISH
            score = min(round(bear_score, 1), 80.0)
        else:
            bias = Bias.NEUTRAL
            score = 0.0
            reasons.append("No clear price action signal")

        raw = {
            "timeframe_used": tf,
            "rsi": round(rsi_val, 1),
            "bb_pct": round(bb_pct, 3) if 'bb_pct' in dir() else None,
            "pattern": pattern,
            "momentum_atr": round(mom_r, 2),
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
        )



# ── Backward compatibility ─────────────────────────────────────
def detect_breakout(df: pd.DataFrame, lookback: int = 20) -> tuple[bool, str]:
    """Legacy function kept for test compatibility."""
    if len(df) < lookback + 1:
        return False, "Not enough bars for breakout check"
    prior = df.iloc[-(lookback + 1):-1]
    last = float(df["close"].iloc[-1])
    if last > float(prior["high"].max()):
        return True, "upside"
    if last < float(prior["low"].min()):
        return True, "downside"
    return False, "none"
