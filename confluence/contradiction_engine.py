"""
confluence/contradiction_engine.py
--------------------------------------
Blocks trades when engines actively disagree.

Two types of contradiction:
1. Standard: any engine with score>=40 disagrees with majority
2. Group (H013): 3+ reversal engines (Divergence+Wyckoff+Sentiment)
   all agree on opposite direction to trend engines
   → Lower threshold (score>=20) because reversal signals are naturally weaker
"""
from __future__ import annotations
from dataclasses import dataclass, field
from engines.base_engine import Bias, EngineOutput

# Engines that specialize in REVERSALS (naturally lower scores)
REVERSAL_ENGINES = {"Divergence", "Wyckoff", "Sentiment"}

# Engines that specialize in TREND FOLLOWING (higher scores)
TREND_ENGINES = {"SMC", "PriceAction", "NNFX", "ICT", "Quant", "MarketStructure"}


@dataclass
class ContradictionResult:
    blocked: bool
    reasons: list[str] = field(default_factory=list)


def check_contradictions(
    outputs: list[EngineOutput],
    upcoming_high_impact_news: bool = False,
) -> ContradictionResult:
    """Block trade if meaningful engines actively disagree.

    Standard threshold = 40: catches moderate opposing signals.
    Group threshold = 20: catches reversal engine consensus (H013).
    """
    reasons: list[str] = []
    THRESHOLD = 40
    REVERSAL_THRESHOLD = 40  # raised from 20 — lower was blocking 38% of signals

    # 1. Standard contradiction — TREND engines only
    # Reversal engines (Divergence, Wyckoff, Sentiment) are DESIGNED to disagree
    # with trend — that's their purpose. Only block when TREND engines disagree.
    trend_bullish = [o for o in outputs
                     if o.engine_name in TREND_ENGINES
                     and o.bias == Bias.BULLISH and o.score >= THRESHOLD]
    trend_bearish = [o for o in outputs
                     if o.engine_name in TREND_ENGINES
                     and o.bias == Bias.BEARISH and o.score >= THRESHOLD]

    if trend_bullish and trend_bearish:
        reasons.append(
            f"Trend engine disagreement: {[o.engine_name for o in trend_bullish]} bullish "
            f"vs {[o.engine_name for o in trend_bearish]} bearish (score>={THRESHOLD})"
        )

    # 2. H013: Reversal engine group contradiction
    # Requires ALL 3 reversal engines (not just 2) to agree on opposite direction
    # AND each must have score >= 40 (genuine signal, not noise)
    winning_bias = None
    trend_votes = [o for o in outputs if o.engine_name in TREND_ENGINES
                   and o.bias != Bias.NEUTRAL and o.score >= THRESHOLD]
    if trend_votes:
        bull_trend = sum(1 for o in trend_votes if o.bias == Bias.BULLISH)
        bear_trend = sum(1 for o in trend_votes if o.bias == Bias.BEARISH)
        winning_bias = Bias.BULLISH if bull_trend > bear_trend else Bias.BEARISH

    if winning_bias:
        opposite = Bias.BEARISH if winning_bias == Bias.BULLISH else Bias.BULLISH
        reversal_agrees = [
            o for o in outputs
            if o.engine_name in REVERSAL_ENGINES
            and o.bias == opposite
            and o.score >= REVERSAL_THRESHOLD
        ]
        if len(reversal_agrees) >= 3:  # ALL 3 reversal engines must agree (was 2)
            names = [o.engine_name for o in reversal_agrees]
            reasons.append(
                f"H013 Reversal consensus: {names} all signal {opposite.value} "
                f"vs trend {winning_bias.value} with score>={REVERSAL_THRESHOLD}"
            )

    if upcoming_high_impact_news:
        reasons.append("High-impact news event upcoming")

    return ContradictionResult(blocked=len(reasons) > 0, reasons=reasons)
