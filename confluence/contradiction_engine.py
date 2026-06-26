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
    REVERSAL_THRESHOLD = 20  # reversal engines use lower scores naturally

    # 1. Standard contradiction
    bullish = [o for o in outputs if o.bias == Bias.BULLISH and o.score >= THRESHOLD]
    bearish = [o for o in outputs if o.bias == Bias.BEARISH and o.score >= THRESHOLD]

    if bullish and bearish:
        reasons.append(
            f"Active disagreement: {[o.engine_name for o in bullish]} bullish "
            f"vs {[o.engine_name for o in bearish]} bearish, both score>={THRESHOLD}"
        )

    # 2. H013: Reversal engine group contradiction
    # If 3+ reversal engines agree on direction OPPOSITE to trend engines
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
        if len(reversal_agrees) >= 2:  # 2+ reversal engines agree on opposite
            names = [o.engine_name for o in reversal_agrees]
            reasons.append(
                f"Reversal signal: {names} all signal {opposite.value} "
                f"vs trend engines {winning_bias.value} — possible reversal (H013)"
            )

    if upcoming_high_impact_news:
        reasons.append("High-impact news event upcoming")

    return ContradictionResult(blocked=len(reasons) > 0, reasons=reasons)
