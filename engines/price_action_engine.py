"""
engines/price_action_engine.py
---------------------------------
Plain price-action engine: trend direction via moving averages, breakout
detection via recent range. Deliberately simple and free of any "school"
jargon — this is meant to act as an independent, low-overlap check
against the SMC engine.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


def moving_average_trend(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> tuple[Bias, float, list[str]]:
    if len(df) < slow:
        return Bias.NEUTRAL, 0.0, ["Not enough bars for MA trend check"]

    ma_fast = df["close"].rolling(fast).mean().iloc[-1]
    ma_slow = df["close"].rolling(slow).mean().iloc[-1]
    spread_pct = (ma_fast - ma_slow) / ma_slow * 100

    # Score formula: sigmoid-like scaling so even small spreads (0.05-0.2%)
    # give meaningful scores (20-50) rather than near-zero values that
    # prevent any confluence from building in ranging markets.
    # At spread_pct=0.05% -> score≈16, 0.1%->29, 0.2%->47, 0.5%->73, 1%->87
    import math
    raw_score = 100 / (1 + math.exp(-15 * (abs(spread_pct) - 0.15)))
    score = round(min(raw_score, 80.0), 2)

    if ma_fast > ma_slow:
        return Bias.BULLISH, score, [f"Fast MA({fast}) above slow MA({slow}), spread={spread_pct:.3f}%"]
    elif ma_fast < ma_slow:
        return Bias.BEARISH, score, [f"Fast MA({fast}) below slow MA({slow}), spread={spread_pct:.3f}%"]
    else:
        return Bias.NEUTRAL, 0.0, ["Fast and slow MA are equal"]


def detect_breakout(df: pd.DataFrame, lookback: int = 20) -> tuple[bool, str]:
    """Did the latest close break above/below the prior `lookback`-bar range?"""
    if len(df) < lookback + 1:
        return False, "Not enough bars for breakout check"

    prior_range = df.iloc[-(lookback + 1):-1]
    last_close = df["close"].iloc[-1]

    if last_close > prior_range["high"].max():
        return True, "upside"
    if last_close < prior_range["low"].min():
        return True, "downside"
    return False, "none"


class PriceActionEngine(BaseEngine):
    name = "PriceAction"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        tf = "H1" if "H1" in mtf_data else next(iter(mtf_data))
        df = mtf_data[tf]

        bias, score, reasons = moving_average_trend(df)
        is_breakout, direction = detect_breakout(df)

        if is_breakout:
            reasons.append(f"Breakout detected: {direction}")
            if direction == "upside" and bias != Bias.BEARISH:
                bias = Bias.BULLISH
                score = max(score, 60.0)
            elif direction == "downside" and bias != Bias.BULLISH:
                bias = Bias.BEARISH
                score = max(score, 60.0)

        raw = {"timeframe_used": tf, "breakout": direction}
        return EngineOutput(engine_name=self.name, bias=bias, score=score, reasons=reasons, raw=raw)
