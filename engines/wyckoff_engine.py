"""
engines/wyckoff_engine.py
-----------------------------
Wyckoff methodology engine — Phase 3.

Wyckoff focuses on the relationship between price, volume, and
institutional intent (Composite Operator). The full methodology
requires reliable volume data — which FX markets don't provide
(only tick volume proxy). Therefore:

- For FOREX: price-only Wyckoff (structure detection without volume)
- For METALS/INDICES/CRYPTO: full Wyckoff with volume analysis

Price-only Wyckoff concepts (usable without volume):
  1. Trading Range identification (accumulation or distribution)
  2. Spring/Upthrust detection (false breakout into key level)
  3. Phase detection (A/B/C/D/E) via price behavior patterns
  4. Effort vs Result (price bar size vs expected direction)

Volume-enhanced concepts (metals/indices only):
  5. Stopping Volume: high volume + narrow spread = absorption
  6. Climax Volume: extreme volume = potential reversal
  7. No Demand: narrow spread + low volume in uptrend
  8. No Supply: narrow spread + low volume in downtrend

The engine auto-detects whether reliable volume is available
(via the asset profile) and adjusts its analysis accordingly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from engines.smc_engine import find_swing_points
from utils.logger import get_logger

logger = get_logger(__name__)


def _identify_trading_range(
    df: pd.DataFrame,
    lookback: int = 40,
    range_atr_max: float = 8.0,
    recent_lookback: int = 10,
) -> tuple[float, float, bool]:
    """Identify if price is in a trading range (consolidation).

    Uses ATR-normalized spread instead of raw % to handle both
    low-price forex (1.08) and high-price crypto (60,000+).

    NOTE: return arity (low, high, in_range) is a shared contract —
    engines/wyckoff_engine_v2.py imports and calls this function
    directly (`range_low, range_high, _ = _identify_trading_range(...)`).
    Do not add/remove return values here without checking that call site.
    """
    window = df.tail(lookback)
    high = float(window["high"].max())
    low = float(window["low"].min())

    # ATR-normalized spread (works for any price level).
    # range_atr, NOT true ATR — deliberate variant, see utils/indicators.py.
    from utils.indicators import range_atr
    atr = range_atr(df, 14)
    price_range = high - low
    spread_in_atr = price_range / atr if atr > 0 else 99

    recent_high = float(df["high"].tail(recent_lookback).max())
    recent_low  = float(df["low"].tail(recent_lookback).min())

    # In range: price_range < range_atr_max× ATR AND recent extremes near range boundaries
    in_range = (
        spread_in_atr < range_atr_max
        and (abs(recent_high - high) / (atr + 1e-10) < 1.0
             or abs(recent_low - low) / (atr + 1e-10) < 1.0)
    )
    return low, high, bool(in_range)


def _range_atr_zero(df: pd.DataFrame) -> bool:
    """True when range_atr(df, 14) == 0 (a fully flat instrument over the
    ATR lookback — e.g. a stale/frozen feed). _identify_trading_range()
    already handles this internally via a 99 sentinel spread_in_atr that
    deterministically forces in_range=False (99 >= any sane
    range_atr_max) — correct, but silent. This helper exists purely so
    extract_features() can surface the reason without touching
    _identify_trading_range()'s return signature, which
    engines/wyckoff_engine_v2.py depends on directly (OBSERVABILITY
    only, no behavior change)."""
    from utils.indicators import range_atr
    return range_atr(df, 14) <= 0


def _detect_spring_upthrust(
    df: pd.DataFrame,
    range_low: float,
    range_high: float,
    tolerance: float = 0.002,
) -> tuple[str, float]:
    """Detect Spring (false breakdown below range) or Upthrust (false breakout above).

    Spring: price dips below range_low but closes back inside → bullish
    Upthrust: price spikes above range_high but closes back inside → bearish

    Returns (event_type, strength) where event_type is 'spring', 'upthrust', or 'none'.
    strength = how far price went beyond the range relative to ATR.
    """
    bar = df.iloc[-1]

    # Spring: wicked below range low, closed above it
    if float(bar["low"]) < range_low * (1 - tolerance) and float(bar["close"]) > range_low:
        penetration = (range_low - float(bar["low"])) / range_low
        return "spring", round(penetration * 100, 2)

    # Upthrust: wicked above range high, closed below it
    if float(bar["high"]) > range_high * (1 + tolerance) and float(bar["close"]) < range_high:
        penetration = (float(bar["high"]) - range_high) / range_high
        return "upthrust", round(penetration * 100, 2)

    return "none", 0.0


def _effort_vs_result(df: pd.DataFrame, lookback: int = 10) -> tuple[str, str]:
    """Compare bar spread (effort) to price movement (result).

    Wide spread + little net movement = absorption (effort without result)
    Narrow spread + large net movement = easy movement (efficient market)

    Returns (effort_level, result_label).
    """
    window = df.tail(lookback)
    avg_spread = float((window["high"] - window["low"]).mean())
    avg_body = float((window["close"] - window["open"]).abs().mean())

    last_spread = float(df["high"].iloc[-1] - df["low"].iloc[-1])
    effort = "high" if last_spread > avg_spread * 1.2 else "low"
    result = "strong" if avg_body > avg_spread * 0.5 else "weak"
    return effort, result


def _volume_analysis(df: pd.DataFrame, lookback: int = 20) -> dict:
    """Volume-based Wyckoff signals (only meaningful for assets with
    real volume data — metals, indices, crypto).

    Returns dict with: stopping_volume, climax, no_demand, no_supply
    """
    window = df.tail(lookback)
    if window["volume"].sum() == 0:
        return {"available": False}

    avg_vol = float(window["volume"].mean())
    last_vol = float(df["volume"].iloc[-1])
    last_spread = float(df["high"].iloc[-1] - df["low"].iloc[-1])
    avg_spread = float((window["high"] - window["low"]).mean())
    last_close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])

    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0
    spread_ratio = last_spread / avg_spread if avg_spread > 0 else 1.0

    return {
        "available": True,
        "vol_ratio": round(vol_ratio, 2),
        # Stopping volume: high volume + narrow spread = absorption at key level
        "stopping_volume": vol_ratio > 1.5 and spread_ratio < 0.8,
        # Climax: extreme volume + wide spread = potential exhaustion
        "climax": vol_ratio > 2.0 and spread_ratio > 1.3,
        # No demand: narrow spread + low volume on up bar = weak buying
        "no_demand": spread_ratio < 0.7 and vol_ratio < 0.7 and last_close > prev_close,
        # No supply: narrow spread + low volume on down bar = weak selling
        "no_supply": spread_ratio < 0.7 and vol_ratio < 0.7 and last_close < prev_close,
    }


def extract_features(df: pd.DataFrame, t: dict) -> dict:
    """Feature Extraction layer (Confluence Engine Overhaul Phase 2) —
    trading-range/spring-upthrust/volume facts decide() needs. Pure
    function of (df, thresholds), no bias/score logic."""
    range_low, range_high, in_range = _identify_trading_range(
        df,
        lookback=t.get("range_lookback", 40),
        range_atr_max=t.get("range_atr_max", 8.0),
        recent_lookback=t.get("range_recent_lookback", 10),
    )
    current = float(df["close"].iloc[-1])
    event, strength = _detect_spring_upthrust(
        df, range_low, range_high, tolerance=t.get("spring_tolerance", 0.002),
    )
    vol = _volume_analysis(df)

    return {
        "range_low": range_low, "range_high": range_high, "in_range": in_range,
        "range_atr_zero": _range_atr_zero(df),
        "current": current, "event": event, "strength": strength, "vol": vol,
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer (Confluence Engine Overhaul Phase 2) — turns
    an extract_features() snapshot into a bias/score opinion via
    Wyckoff's staged range -> spring/upthrust -> position -> volume
    confirmation sequence. Pure function of (features, thresholds)."""
    range_low, range_high, in_range = (
        features["range_low"], features["range_high"], features["in_range"],
    )
    current = features["current"]
    event, strength, vol = features["event"], features["strength"], features["vol"]

    reasons: list[str] = []
    score = 0.0
    bias = Bias.NEUTRAL

    if in_range:
        reasons.append(
            f"Price in Wyckoff trading range "
            f"[{range_low:.5f} – {range_high:.5f}] "
            f"(consolidation phase)"
        )

    spring_upthrust_score = t.get("spring_upthrust_score", 45.0)
    if event == "spring":
        bias = Bias.BULLISH
        score += spring_upthrust_score
        reasons.append(
            f"SPRING detected: false breakdown below {range_low:.5f}, "
            f"closed back inside range — Wyckoff bullish reversal signal "
            f"(penetration: {strength:.2f}%)"
        )
    elif event == "upthrust":
        bias = Bias.BEARISH
        score += spring_upthrust_score
        reasons.append(
            f"UPTHRUST detected: false breakout above {range_high:.5f}, "
            f"closed back inside range — Wyckoff bearish reversal signal "
            f"(penetration: {strength:.2f}%)"
        )

    range_position_score = t.get("range_position_score", 25.0)
    range_position_low_pct = t.get("range_position_low_pct", 0.25)
    range_position_high_pct = t.get("range_position_high_pct", 0.75)
    if event == "none" and in_range:
        range_pct = (current - range_low) / (range_high - range_low) if range_high != range_low else 0.5
        if range_pct < range_position_low_pct:
            bias = Bias.BULLISH
            score += range_position_score
            reasons.append(
                f"Price at bottom of range ({range_pct:.0%}) — "
                f"potential Wyckoff accumulation zone"
            )
        elif range_pct > range_position_high_pct:
            bias = Bias.BEARISH
            score += range_position_score
            reasons.append(
                f"Price at top of range ({range_pct:.0%}) — "
                f"potential Wyckoff distribution zone"
            )

    stopping_volume_score = t.get("stopping_volume_score", 20.0)
    climax_score = t.get("climax_score", 15.0)
    no_demand_score = t.get("no_demand_score", 15.0)
    no_supply_score = t.get("no_supply_score", 15.0)
    if vol.get("available"):
        if vol.get("stopping_volume") and bias == Bias.BULLISH:
            score += stopping_volume_score
            reasons.append(
                f"Stopping volume detected (vol_ratio={vol['vol_ratio']}x) — "
                f"absorption of selling, confirms bullish Wyckoff"
            )
        elif vol.get("climax") and event == "none":
            score += climax_score
            reasons.append(
                f"Climax volume (vol_ratio={vol['vol_ratio']}x) — "
                f"potential trend exhaustion"
            )
        elif vol.get("no_demand") and bias == Bias.BEARISH:
            score += no_demand_score
            reasons.append("No demand (low vol + narrow up bar) — weak buying, confirms bearish")
        elif vol.get("no_supply") and bias == Bias.BULLISH:
            score += no_supply_score
            reasons.append("No supply (low vol + narrow down bar) — weak selling, confirms bullish")
    else:
        reasons.append("Volume unavailable (FX) — Wyckoff analysis is price-only")

    if not reasons or (not in_range and event == "none"):
        reasons.append("No clear Wyckoff pattern — price not in identifiable structure")
        bias = Bias.NEUTRAL
        score = 0.0

    score_cap = t.get("score_cap", 75.0)
    score_neutral_floor = t.get("score_neutral_floor", 20.0)
    score = min(round(score, 1), score_cap)
    if score < score_neutral_floor:
        bias = Bias.NEUTRAL

    return bias, score, reasons


class WyckoffEngine(BaseEngine):
    name = "Wyckoff"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        min_bars = t.get("min_bars", 40)

        # Wyckoff works best on H4/D1 for range identification
        tf = next(
            (tfname for tfname in ["H4", "D1", "H1"] if tfname in mtf_data and len(mtf_data[tfname]) >= min_bars),
            next(iter(mtf_data))
        )
        df = mtf_data[tf]

        if len(df) < min_bars:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=[f"Insufficient data for Wyckoff analysis (need {min_bars}+ bars)"],
            )

        features = extract_features(df, t)
        bias, score, reasons = decide(features, t)

        raw = {
            "timeframe_used": tf,
            "trading_range": {
                "low": features["range_low"], "high": features["range_high"],
                "in_range": features["in_range"],
                "range_atr_zero": features["range_atr_zero"],
            },
            "event": features["event"],
            "event_strength_pct": features["strength"],
            "volume_analysis": features["vol"],
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
