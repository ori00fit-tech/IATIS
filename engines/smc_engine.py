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


def structural_bias(df: pd.DataFrame, window: int = 3, lookback: int = 6) -> tuple[Bias, float, list[str]]:
    """Determine directional bias from the sequence of recent swing highs/lows.

    Uses majority vote over the last `lookback` swing points rather than
    comparing only the last two. This makes the bias more robust to
    short-term noise — a single counter-swing doesn't flip the bias.

    Scoring:
        score = (agreeing_pairs / total_pairs) * 65
        e.g. 5/5 pairs agreeing → score=65 (strong)
             3/5 pairs agreeing → score=39 (weak, may not pass threshold)

    HH + HL majority → BULLISH
    LH + LL majority → BEARISH
    Mixed / insufficient → NEUTRAL
    """
    swings = find_swing_points(df, window=window)
    swing_highs = df["high"][swings["swing_high"]].tail(lookback)
    swing_lows = df["low"][swings["swing_low"]].tail(lookback)

    reasons = []

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return Bias.NEUTRAL, 0.0, ["Not enough swing points to determine structure"]

    # count consecutive pairs that are rising vs falling
    def _count_direction(series):
        rising = falling = 0
        vals = list(series)
        for i in range(1, len(vals)):
            if vals[i] > vals[i - 1]:
                rising += 1
            elif vals[i] < vals[i - 1]:
                falling += 1
        return rising, falling

    highs_rising, highs_falling = _count_direction(swing_highs)
    lows_rising, lows_falling = _count_direction(swing_lows)

    total_pairs = len(swing_highs) - 1 + len(swing_lows) - 1
    bullish_pairs = highs_rising + lows_rising
    bearish_pairs = highs_falling + lows_falling

    if total_pairs == 0:
        return Bias.NEUTRAL, 0.0, ["Not enough swing pairs to vote"]

    bull_ratio = bullish_pairs / total_pairs
    bear_ratio = bearish_pairs / total_pairs

    if bull_ratio > 0.5:
        score = round(bull_ratio * 65, 1)
        reasons.append(
            f"Bullish structure: {bullish_pairs}/{total_pairs} swing pairs rising "
            f"(HH+HL majority)"
        )
        return Bias.BULLISH, score, reasons

    if bear_ratio > 0.5:
        score = round(bear_ratio * 65, 1)
        reasons.append(
            f"Bearish structure: {bearish_pairs}/{total_pairs} swing pairs falling "
            f"(LH+LL majority)"
        )
        return Bias.BEARISH, score, reasons

    reasons.append(
        f"Mixed structure: {bullish_pairs} bullish vs {bearish_pairs} bearish pairs "
        f"out of {total_pairs} — no clear majority"
    )
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

    def _pick_timeframe(self, mtf_data: dict[str, pd.DataFrame]) -> str:
        """Pick the highest timeframe that has enough bars for reliable
        swing-point detection (minimum 100 bars after the rolling window
        consumes its lookback period).

        Why not always use H4: on Twelve Data Free plan, H4 is resampled
        from 500 H1 bars → only ~125 H4 bars. With the 7-bar rolling
        window in find_swing_points(), 125 bars is borderline and often
        produces zero detected swings ('Not enough swing points').
        H1 with 500 bars is far more reliable in this case.
        """
        MIN_BARS = 100
        preference = ["H4", "D1", "H1", "M15"]
        if self.decision_tf == "D1":
            # Decision-on-D1 mode: D1 is fetched natively (500 bars, not a
            # thin resample), and SMC's own "HTF bias first" principle puts
            # it ahead of H4. The MIN_BARS guard below still applies.
            preference = ["D1", "H4", "H1", "M15"]
        # first pass: prefer higher TFs that have enough bars
        for tf in preference:
            if tf in mtf_data and len(mtf_data[tf]) >= MIN_BARS:
                return tf
        # fallback: whatever has the most bars
        return max(mtf_data.keys(), key=lambda tf: len(mtf_data.get(tf, [])),
                   default=next(iter(mtf_data)))
