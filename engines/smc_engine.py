"""
engines/smc_engine.py
------------------------
Smart Money Concepts engine.

Phase 1 implements real swing-point detection and a basic structural bias
(are we making higher-highs/higher-lows or lower-highs/lower-lows). This is
the foundation SMC builds on. The more advanced concepts — order blocks,
fair value gaps, BOS/CHOCH labeling, liquidity-sweep detection — are left
as explicit TODOs/stubs for Phase 3, because faking their output would be
worse than not having them: a NEUTRAL abstain is honest, a fabricated
order block is not.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


def find_swing_points(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """Identify swing highs/lows: a bar whose high/low is the max/min
    within +/- `window` bars on either side.

    Returns a DataFrame with boolean columns 'swing_high' and 'swing_low'
    aligned to df's index.
    """
    highs = df["high"]
    lows = df["low"]

    swing_high = (highs == highs.rolling(window=2 * window + 1, center=True).max())
    swing_low = (lows == lows.rolling(window=2 * window + 1, center=True).min())

    return pd.DataFrame({"swing_high": swing_high.fillna(False), "swing_low": swing_low.fillna(False)})


def structural_bias(df: pd.DataFrame, window: int = 3) -> tuple[Bias, float, list[str]]:
    """Determine bias from the sequence of recent swing highs/lows.

    HH + HL pattern -> bullish structure
    LH + LL pattern -> bearish structure
    Mixed/insufficient swings -> neutral
    """
    swings = find_swing_points(df, window=window)
    swing_highs = df["high"][swings["swing_high"]].tail(4)
    swing_lows = df["low"][swings["swing_low"]].tail(4)

    reasons = []

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return Bias.NEUTRAL, 0.0, ["Not enough swing points to determine structure"]

    highs_rising = swing_highs.iloc[-1] > swing_highs.iloc[-2]
    lows_rising = swing_lows.iloc[-1] > swing_lows.iloc[-2]
    highs_falling = swing_highs.iloc[-1] < swing_highs.iloc[-2]
    lows_falling = swing_lows.iloc[-1] < swing_lows.iloc[-2]

    if highs_rising and lows_rising:
        reasons.append("Higher-high and higher-low detected (bullish structure)")
        return Bias.BULLISH, 65.0, reasons

    if highs_falling and lows_falling:
        reasons.append("Lower-high and lower-low detected (bearish structure)")
        return Bias.BEARISH, 65.0, reasons

    reasons.append("Mixed swing structure — no clear higher-high/low or lower-high/low pattern")
    return Bias.NEUTRAL, 20.0, reasons


class SMCEngine(BaseEngine):
    name = "SMC"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        # Use the highest available timeframe for structural bias (more reliable
        # than the lowest timeframe, consistent with SMC's "HTF bias first" principle)
        tf = self._pick_timeframe(mtf_data)
        df = mtf_data[tf]

        bias, score, reasons = structural_bias(df)

        # --- Phase 3 TODOs (left explicit, not faked) ---
        # order_blocks = detect_order_blocks(df)
        # fvg = detect_fair_value_gaps(df)
        # bos_choch = detect_bos_choch(df)
        # liquidity_zones = detect_liquidity_zones(mtf_data)
        raw = {
            "timeframe_used": tf,
            "order_blocks": "NOT_IMPLEMENTED_PHASE_3",
            "fvg": "NOT_IMPLEMENTED_PHASE_3",
            "bos_choch": "NOT_IMPLEMENTED_PHASE_3",
            "liquidity_zones": "NOT_IMPLEMENTED_PHASE_3",
        }

        return EngineOutput(engine_name=self.name, bias=bias, score=score, reasons=reasons, raw=raw)

    @staticmethod
    def _pick_timeframe(mtf_data: dict[str, pd.DataFrame]) -> str:
        # prefer H4 if present, else the highest timeframe available
        preference = ["H4", "D1", "H1", "M15"]
        for tf in preference:
            if tf in mtf_data:
                return tf
        return next(iter(mtf_data))
