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
    df: pd.DataFrame, lookback: int = 40
) -> tuple[float, float, bool]:
    """Identify if price is in a trading range (consolidation).

    Uses ATR-normalized spread instead of raw % to handle both
    low-price forex (1.08) and high-price crypto (60,000+).
    """
    window = df.tail(lookback)
    high = float(window["high"].max())
    low = float(window["low"].min())
    close = float(df["close"].iloc[-1])

    # ATR-normalized spread (works for any price level)
    atr = float((df["high"] - df["low"]).tail(14).mean())
    price_range = high - low
    spread_in_atr = price_range / atr if atr > 0 else 99

    recent_high = float(df["high"].tail(10).max())
    recent_low  = float(df["low"].tail(10).min())

    # In range: price_range < 8× ATR AND recent extremes near range boundaries
    in_range = (
        spread_in_atr < 8.0
        and (abs(recent_high - high) / (atr + 1e-10) < 1.0
             or abs(recent_low - low) / (atr + 1e-10) < 1.0)
    )
    return low, high, bool(in_range)


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
    last = df.tail(5)
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


class WyckoffEngine(BaseEngine):
    name = "Wyckoff"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        # Wyckoff works best on H4/D1 for range identification
        tf = next(
            (t for t in ["H4", "D1", "H1"] if t in mtf_data and len(mtf_data[t]) >= 40),
            next(iter(mtf_data))
        )
        df = mtf_data[tf]

        if len(df) < 40:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data for Wyckoff analysis (need 40+ bars)"],
            )

        reasons = []
        score = 0.0
        bias = Bias.NEUTRAL

        # 1. Identify trading range
        range_low, range_high, in_range = _identify_trading_range(df)
        current = float(df["close"].iloc[-1])

        if in_range:
            reasons.append(
                f"Price in Wyckoff trading range "
                f"[{range_low:.5f} – {range_high:.5f}] "
                f"(consolidation phase)"
            )

        # 2. Detect Spring or Upthrust
        event, strength = _detect_spring_upthrust(df, range_low, range_high)

        if event == "spring":
            bias = Bias.BULLISH
            score += 45.0
            reasons.append(
                f"SPRING detected: false breakdown below {range_low:.5f}, "
                f"closed back inside range — Wyckoff bullish reversal signal "
                f"(penetration: {strength:.2f}%)"
            )
        elif event == "upthrust":
            bias = Bias.BEARISH
            score += 45.0
            reasons.append(
                f"UPTHRUST detected: false breakout above {range_high:.5f}, "
                f"closed back inside range — Wyckoff bearish reversal signal "
                f"(penetration: {strength:.2f}%)"
            )

        # 3. Position in range (if no spring/upthrust)
        if event == "none" and in_range:
            range_pct = (current - range_low) / (range_high - range_low) if range_high != range_low else 0.5
            if range_pct < 0.25:
                bias = Bias.BULLISH
                score += 25.0
                reasons.append(
                    f"Price at bottom of range ({range_pct:.0%}) — "
                    f"potential Wyckoff accumulation zone"
                )
            elif range_pct > 0.75:
                bias = Bias.BEARISH
                score += 25.0
                reasons.append(
                    f"Price at top of range ({range_pct:.0%}) — "
                    f"potential Wyckoff distribution zone"
                )

        # 4. Volume analysis (metals/indices/crypto only)
        vol = _volume_analysis(df)
        if vol.get("available"):
            if vol.get("stopping_volume") and bias == Bias.BULLISH:
                score += 20.0
                reasons.append(
                    f"Stopping volume detected (vol_ratio={vol['vol_ratio']}x) — "
                    f"absorption of selling, confirms bullish Wyckoff"
                )
            elif vol.get("climax") and event == "none":
                score += 15.0
                reasons.append(
                    f"Climax volume (vol_ratio={vol['vol_ratio']}x) — "
                    f"potential trend exhaustion"
                )
            elif vol.get("no_demand") and bias == Bias.BEARISH:
                score += 15.0
                reasons.append("No demand (low vol + narrow up bar) — weak buying, confirms bearish")
            elif vol.get("no_supply") and bias == Bias.BULLISH:
                score += 15.0
                reasons.append("No supply (low vol + narrow down bar) — weak selling, confirms bullish")
        else:
            reasons.append("Volume unavailable (FX) — Wyckoff analysis is price-only")

        if not reasons or (not in_range and event == "none"):
            reasons.append("No clear Wyckoff pattern — price not in identifiable structure")
            bias = Bias.NEUTRAL
            score = 0.0

        score = min(round(score, 1), 75.0)
        if score < 20:
            bias = Bias.NEUTRAL

        raw = {
            "timeframe_used": tf,
            "trading_range": {"low": range_low, "high": range_high, "in_range": in_range},
            "event": event,
            "event_strength_pct": strength,
            "volume_analysis": vol,
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
        )
