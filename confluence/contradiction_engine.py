"""
confluence/contradiction_engine.py
--------------------------------------
Blocks trades when engines actively disagree.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from engines.base_engine import Bias, EngineOutput

@dataclass
class ContradictionResult:
    blocked: bool
    reasons: list[str] = field(default_factory=list)


def check_contradictions(
    outputs: list[EngineOutput],
    upcoming_high_impact_news: bool = False,
) -> ContradictionResult:
    """Block trade if meaningful engines actively disagree.

    Threshold = 40: catches moderate opposing signals, not just noise.
    A score of 40+ means the engine has a real directional opinion.
    """
    reasons: list[str] = []
    THRESHOLD = 40

    bullish = [o for o in outputs if o.bias == Bias.BULLISH and o.score >= THRESHOLD]
    bearish = [o for o in outputs if o.bias == Bias.BEARISH and o.score >= THRESHOLD]

    if bullish and bearish:
        reasons.append(
            f"Active disagreement: {[o.engine_name for o in bullish]} bullish "
            f"vs {[o.engine_name for o in bearish]} bearish, both score>={THRESHOLD}"
        )

    if upcoming_high_impact_news:
        reasons.append("High-impact news event upcoming")

    return ContradictionResult(blocked=len(reasons) > 0, reasons=reasons)
