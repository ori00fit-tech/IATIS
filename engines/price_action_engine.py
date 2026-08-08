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
from utils.indicators import bollinger_bands, rsi as _rsi_series
from utils.logger import get_logger

logger = get_logger(__name__)


def _candle_pattern(df: pd.DataFrame) -> tuple[str, float]:
    """Detect key candlestick reversal/continuation patterns."""
    if len(df) < 3:
        return "none", 0.0

    c0 = df.iloc[-1]   # current
    c1 = df.iloc[-2]   # previous

    body0 = abs(float(c0["close"]) - float(c0["open"]))
    wick_top0 = float(c0["high"]) - max(float(c0["close"]), float(c0["open"]))
    wick_bot0 = min(float(c0["close"]), float(c0["open"])) - float(c0["low"])
    range0 = float(c0["high"]) - float(c0["low"]) or 1e-10

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


def extract_features(df: pd.DataFrame, t: dict) -> dict:
    """Feature Extraction layer (Confluence Engine Overhaul Phase 2) —
    computes every indicator/structural value decide() needs, with zero
    bias/score logic. Pure function of (df, thresholds)."""
    close = df["close"]

    rsi = _rsi_series(close, t.get("rsi_period", 14))
    # rsi() (utils/indicators.py) returns NaN when the lookback period has
    # zero average loss or zero average gain (RS undefined) — not "no
    # information." The true value there is 100 (all gains) or 0 (all
    # losses), not the neutral 50 this engine falls back to; rsi_undefined
    # makes that fallback distinguishable from a genuine ~50 reading
    # (OBSERVABILITY only — the fallback value itself is left as-is since
    # correcting it would change decide()'s RSI-threshold branching, a
    # strategy-semantics change out of scope for this refinement pass).
    rsi_undefined = bool(pd.isna(rsi.iloc[-1]))
    rsi_val = float(rsi.iloc[-1]) if not rsi_undefined else 50.0
    rsi_prev = float(rsi.iloc[-2]) if not pd.isna(rsi.iloc[-2]) else 50.0

    upper, mid, lower = bollinger_bands(close, t.get("bb_period", 20), t.get("bb_std", 2.0))
    last_close = float(close.iloc[-1])
    last_upper = float(upper.iloc[-1])
    last_lower = float(lower.iloc[-1])
    last_mid = float(mid.iloc[-1])
    bb_pct: float | None = None
    if not any(pd.isna(x) for x in [last_upper, last_lower, last_mid]):
        bb_pct = (last_close - last_lower) / (last_upper - last_lower + 1e-10)

    pattern, pattern_strength = _candle_pattern(df)

    # Clamped so a configured momentum_bars longer than the available
    # history never indexes out of range — momentum_bars_used makes a
    # silent clamp observable instead of a mismatch between the configured
    # and actually-applied lookback (OBSERVABILITY; the clamp itself is
    # pre-existing behavior, unchanged).
    momentum_bars_configured = t.get("momentum_bars", 5)
    mom_bars = min(momentum_bars_configured, len(df) - 1)
    mom = float(close.iloc[-1]) - float(close.iloc[-1 - mom_bars])
    # range_atr, NOT true ATR — deliberate variant, see utils/indicators.py.
    from utils.indicators import range_atr
    atr = range_atr(df, t.get("momentum_atr_period", 14))
    mom_r = mom / atr if atr > 0 else 0

    is_breakout, breakout_direction = detect_breakout(df, t.get("breakout_lookback", 20))

    return {
        "rsi_val": rsi_val,
        "rsi_prev": rsi_prev,
        "rsi_undefined": rsi_undefined,
        "last_close": last_close,
        "last_mid": last_mid,
        "bb_pct": bb_pct,
        "pattern": pattern,
        "pattern_strength": pattern_strength,
        "mom_r": mom_r,
        "momentum_bars_used": mom_bars,
        "momentum_bars_configured": momentum_bars_configured,
        "is_breakout": is_breakout,
        "breakout_direction": breakout_direction,
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer (Confluence Engine Overhaul Phase 2) — turns
    an extract_features() snapshot into a bias/score opinion. Pure
    function of (features, thresholds); contains all scoring, no
    indicator math."""
    reasons: list[str] = []
    bull_score = 0.0
    bear_score = 0.0

    rsi_val = features["rsi_val"]
    rsi_prev = features["rsi_prev"]

    # ── 1. RSI momentum (30 points max) ──────────────────────────
    rsi_bull = t.get("rsi_bull", 55)
    rsi_bear = t.get("rsi_bear", 45)
    rsi_momentum_score = t.get("rsi_momentum_score", 30.0)
    rsi_overbought = t.get("rsi_overbought", 70)
    rsi_overbought_score = t.get("rsi_overbought_score", 15.0)
    rsi_oversold = t.get("rsi_oversold", 30)
    rsi_oversold_score = t.get("rsi_oversold_score", 15.0)

    if rsi_val > rsi_bull and rsi_val > rsi_prev:
        bull_score += rsi_momentum_score
        reasons.append(f"RSI={rsi_val:.1f} bullish momentum (rising, >{rsi_bull})")
    elif rsi_val < rsi_bear and rsi_val < rsi_prev:
        bear_score += rsi_momentum_score
        reasons.append(f"RSI={rsi_val:.1f} bearish momentum (falling, <{rsi_bear})")
    elif rsi_val > rsi_overbought:
        bear_score += rsi_overbought_score
        reasons.append(f"RSI={rsi_val:.1f} overbought — potential reversal")
    elif rsi_val < rsi_oversold:
        bull_score += rsi_oversold_score
        reasons.append(f"RSI={rsi_val:.1f} oversold — potential reversal")
    else:
        reasons.append(f"RSI={rsi_val:.1f} neutral zone")

    # ── 2. Bollinger Band position (25 points max) ────────────────
    bb_pct = features["bb_pct"]
    bb_upper_pct = t.get("bb_upper_pct", 0.8)
    bb_upper_score = t.get("bb_upper_score", 20.0)
    bb_lower_pct = t.get("bb_lower_pct", 0.2)
    bb_lower_score = t.get("bb_lower_score", 20.0)
    bb_mid_score = t.get("bb_mid_score", 10.0)

    if bb_pct is not None:
        if bb_pct > bb_upper_pct:
            bull_score += bb_upper_score
            reasons.append(f"Price at upper Bollinger band ({bb_pct:.0%}) — strong momentum")
        elif bb_pct < bb_lower_pct:
            bear_score += bb_lower_score
            reasons.append(f"Price at lower Bollinger band ({bb_pct:.0%}) — bearish pressure")
        elif features["last_close"] > features["last_mid"]:
            bull_score += bb_mid_score
            reasons.append(f"Price above BB midline — mild bullish")
        else:
            bear_score += bb_mid_score
            reasons.append(f"Price below BB midline — mild bearish")

    # ── 3. Candlestick pattern (25 points max) ────────────────────
    pattern_score = t.get("pattern_score", 25.0)
    doji_dampen = t.get("doji_dampen", 0.7)
    pattern = features["pattern"]
    strength = features["pattern_strength"]
    if pattern in ("hammer", "bullish_engulfing"):
        bull_score += pattern_score * strength
        reasons.append(f"Bullish candle pattern: {pattern} (strength={strength:.0%})")
    elif pattern in ("shooting_star", "bearish_engulfing"):
        bear_score += pattern_score * strength
        reasons.append(f"Bearish candle pattern: {pattern} (strength={strength:.0%})")
    elif pattern == "doji":
        reasons.append("Doji detected — indecision, reducing score")
        bull_score *= doji_dampen
        bear_score *= doji_dampen

    # ── 4. Short-term momentum (20 points max) ───────────────────
    mom_r = features["mom_r"]
    momentum_threshold = t.get("momentum_threshold", 0.5)
    momentum_score = t.get("momentum_score", 20.0)
    if mom_r > momentum_threshold:
        bull_score += momentum_score
        reasons.append(f"Bullish short-term momentum (+{mom_r:.1f}× ATR)")
    elif mom_r < -momentum_threshold:
        bear_score += momentum_score
        reasons.append(f"Bearish short-term momentum ({mom_r:.1f}× ATR)")

    # ── 5. Range breakout (15 points max) ─────────────────────────
    # Restores the detect_breakout() contract: the engine must flag
    # breakouts in both `raw` and `reasons` (see tests/test_behavior.py).
    breakout_score = t.get("breakout_score", 15.0)
    is_breakout = features["is_breakout"]
    breakout_direction = features["breakout_direction"]
    if is_breakout and breakout_direction == "upside":
        bull_score += breakout_score
        reasons.append("Breakout detected: upside (20-bar range high broken)")
    elif is_breakout and breakout_direction == "downside":
        bear_score += breakout_score
        reasons.append("Breakout detected: downside (20-bar range low broken)")

    # ── Final bias ────────────────────────────────────────────────
    bull_score_min = t.get("bull_score_min", 30.0)
    score_cap = t.get("score_cap", 80.0)
    if bull_score > bear_score and bull_score >= bull_score_min:
        bias = Bias.BULLISH
        score = min(round(bull_score, 1), score_cap)
    elif bear_score > bull_score and bear_score >= bull_score_min:
        bias = Bias.BEARISH
        score = min(round(bear_score, 1), score_cap)
    else:
        bias = Bias.NEUTRAL
        score = 0.0
        reasons.append("No clear price action signal")

    return bias, score, reasons


class PriceActionEngine(BaseEngine):
    name = "PriceAction"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        tf, df = self.decision_frame(mtf_data)

        min_bars = t.get("min_bars", 30)
        if len(df) < min_bars:
            return EngineOutput(
                engine_name=self.name, bias=Bias.NEUTRAL, score=0.0,
                reasons=[f"Insufficient data (need {min_bars}+ bars)"],
            )

        features = extract_features(df, t)
        bias, score, reasons = decide(features, t)

        raw = {
            "timeframe_used": tf,
            "rsi": round(features["rsi_val"], 1),
            "rsi_undefined": features["rsi_undefined"],
            "bb_pct": round(features["bb_pct"], 3) if features["bb_pct"] is not None else None,
            "pattern": features["pattern"],
            "momentum_atr": round(features["mom_r"], 2),
            "momentum_bars_used": features["momentum_bars_used"],
            "momentum_bars_configured": features["momentum_bars_configured"],
            "breakout": features["breakout_direction"] if features["is_breakout"] else "none",
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
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
