"""
engines/market_structure_engine.py
-------------------------------------
Market Structure Engine: detects structural shifts (MSS/CHoCH).

More sophisticated than the current SMC engine which uses simple
swing-pair voting. This engine identifies specific structural events:

  BOS  (Break of Structure):  confirms trend continuation
  CHoCH (Change of Character): first sign of reversal
  MSS  (Market Structure Shift): confirmed reversal

Timeframe hierarchy:
  H4: macro structure (major BOS/CHoCH)
  H1: intermediate (current trading structure)
  Bias = H4 and H1 agree? → strong signal

Scoring:
  CHoCH on H1 confirmed by H4: 75
  BOS continuation on H1+H4:   65
  CHoCH on H1 only:             50
  BOS on H1 only:               45
  Conflicting timeframes:       NEUTRAL

vs current SMC engine:
  SMC: counts swing pairs, votes (simple)
  MSS: identifies specific structural EVENTS (more nuanced)

Research status: RESEARCH
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from engines.base_engine import BaseEngine, Bias, EngineOutput


def _swing_points(df: pd.DataFrame, window: int = 3) -> tuple[list, list]:
    """Return (highs, lows) as lists of (idx, price) tuples."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    highs, lows = [], []
    for i in range(window, len(df) - window):
        if high.iloc[i] == high.iloc[i - window:i + window + 1].max():
            highs.append((i, float(high.iloc[i])))
        if low.iloc[i] == low.iloc[i - window:i + window + 1].min():
            lows.append((i, float(low.iloc[i])))

    return highs, lows


def _classify_structure(highs: list, lows: list) -> dict:
    """Classify market structure from recent swings.

    Returns dict with:
      trend: 'bullish' | 'bearish' | 'ranging'
      last_event: 'BOS' | 'CHoCH' | 'MSS' | 'none'
      last_event_bias: 'bullish' | 'bearish'
      strength: 0-100
    """
    if len(highs) < 2 or len(lows) < 2:
        return {"trend": "ranging", "last_event": "none", "strength": 0}

    # Get last 4 highs and lows
    recent_h = highs[-4:]
    recent_l = lows[-4:]

    # Determine current trend from swing structure
    hh = recent_h[-1][1] > recent_h[-2][1] if len(recent_h) >= 2 else False
    hl = recent_l[-1][1] > recent_l[-2][1] if len(recent_l) >= 2 else False
    lh = recent_h[-1][1] < recent_h[-2][1] if len(recent_h) >= 2 else False
    ll = recent_l[-1][1] < recent_l[-2][1] if len(recent_l) >= 2 else False

    bullish_structure = hh and hl
    bearish_structure = lh and ll

    # Detect structural events
    last_event = "none"
    last_event_bias = "none"

    if len(recent_h) >= 3 and len(recent_l) >= 3:
        # CHoCH Bullish: was making LH+LL, then breaks above last LH
        was_bearish = recent_h[-3][1] > recent_h[-2][1]  # LH
        broke_above = recent_h[-1][1] > recent_h[-2][1]  # now HH
        low_made = recent_l[-1][1] > recent_l[-2][1]      # HL too

        if was_bearish and broke_above:
            last_event = "CHoCH" if not low_made else "MSS"
            last_event_bias = "bullish"

        # CHoCH Bearish: was making HH+HL, then breaks below last HL
        was_bullish = recent_l[-3][1] < recent_l[-2][1]  # HL
        broke_below = recent_l[-1][1] < recent_l[-2][1]  # now LL
        high_made = recent_h[-1][1] < recent_h[-2][1]    # LH too

        if was_bullish and broke_below:
            last_event = "CHoCH" if not high_made else "MSS"
            last_event_bias = "bearish"

        # BOS: trend continuation break
        if bullish_structure and last_event == "none":
            last_event = "BOS"
            last_event_bias = "bullish"
        elif bearish_structure and last_event == "none":
            last_event = "BOS"
            last_event_bias = "bearish"

    # Calculate strength based on consistency
    if bullish_structure:
        trend = "bullish"
        strength = 65 if last_event == "BOS" else (75 if last_event in ("CHoCH", "MSS") else 45)
    elif bearish_structure:
        trend = "bearish"
        strength = 65 if last_event == "BOS" else (75 if last_event in ("CHoCH", "MSS") else 45)
    else:
        trend = "ranging"
        strength = 20

    return {
        "trend": trend,
        "last_event": last_event,
        "last_event_bias": last_event_bias,
        "structure_hh": hh, "structure_hl": hl,
        "structure_lh": lh, "structure_ll": ll,
        "strength": strength,
    }


class MarketStructureEngine(BaseEngine):
    name = "MarketStructure"
    """Advanced market structure analysis: BOS, CHoCH, MSS.

    Uses H4 for macro structure and H1 for current structure.
    Both timeframes must agree for a strong signal.

    Research status: RESEARCH (H011)
    """
    name = "MarketStructure"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        df_h1 = mtf_data.get("H1", next(iter(mtf_data.values())))
        df_h4 = mtf_data.get("H4", df_h1)

        if len(df_h1) < 30:
            return EngineOutput(
                engine_name="MarketStructure",
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data"],
            )

        # Analyze H1 and H4 structure
        h1_highs, h1_lows = _swing_points(df_h1.tail(100), window=3)
        h4_highs, h4_lows = _swing_points(df_h4.tail(60), window=2)

        h1_struct = _classify_structure(h1_highs, h1_lows)
        h4_struct = _classify_structure(h4_highs, h4_lows)

        reasons = []
        score = 0.0
        bias = Bias.NEUTRAL

        h1_bias = h1_struct["trend"]
        h4_bias = h4_struct["trend"]
        h1_event = h1_struct["last_event"]
        h1_event_dir = h1_struct["last_event_bias"]

        # Both timeframes agree — strong signal
        if h1_bias == h4_bias and h1_bias != "ranging":
            if h1_bias == "bullish":
                bias = Bias.BULLISH
                score = h1_struct["strength"]
                if h1_event in ("CHoCH", "MSS"):
                    score = min(score + 10, 85)
                    reasons.append(f"H1 {h1_event} bullish confirmed by H4 bullish structure")
                else:
                    reasons.append(f"H1+H4 bullish structure (BOS continuation)")
            else:
                bias = Bias.BEARISH
                score = h1_struct["strength"]
                if h1_event in ("CHoCH", "MSS"):
                    score = min(score + 10, 85)
                    reasons.append(f"H1 {h1_event} bearish confirmed by H4 bearish structure")
                else:
                    reasons.append(f"H1+H4 bearish structure (BOS continuation)")

        # H1 CHoCH/MSS against H4 trend — high priority reversal signal
        elif h1_event in ("CHoCH", "MSS") and h1_event_dir != "none":
            if h1_event_dir == "bullish":
                bias = Bias.BULLISH
                score = 50  # lower confidence — H4 disagrees
                reasons.append(f"H1 {h1_event} bullish but H4 still {h4_bias} — early reversal")
            else:
                bias = Bias.BEARISH
                score = 50
                reasons.append(f"H1 {h1_event} bearish but H4 still {h4_bias} — early reversal")

        # Only H1 structure
        elif h1_bias != "ranging":
            if h1_bias == "bullish":
                bias = Bias.BULLISH
                score = 40
            else:
                bias = Bias.BEARISH
                score = 40
            reasons.append(f"H1 {h1_bias} structure (H4 ranging/insufficient)")

        else:
            reasons.append(f"H1 ranging (HH={h1_struct['structure_hh']}, "
                           f"HL={h1_struct['structure_hl']}, "
                           f"LH={h1_struct['structure_lh']}, "
                           f"LL={h1_struct['structure_ll']})")

        last_h1_high = h1_highs[-1][1] if h1_highs else 0
        last_h1_low = h1_lows[-1][1] if h1_lows else 0
        reasons.append(f"Last H1 swing: high={last_h1_high:.5f}, low={last_h1_low:.5f}")

        return EngineOutput(
            engine_name="MarketStructure",
            bias=bias,
            score=round(score, 1),
            reasons=reasons,
            raw={
                "timeframe_h1": "H1",
                "timeframe_h4": "H4",
                "h1_trend": h1_bias,
                "h4_trend": h4_bias,
                "h1_event": h1_event,
                "h1_event_direction": h1_event_dir,
                "h1_strength": h1_struct["strength"],
                "h4_strength": h4_struct["strength"],
                "aligned": h1_bias == h4_bias,
            },
        )
