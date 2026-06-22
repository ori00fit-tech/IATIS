"""
confluence/voting_system.py
------------------------------
Counts how many active engines agree on a bias. This is separate from
score_calculator.py (weighted score) because the IATIS design requires
BOTH a minimum score AND a minimum number of agreeing engines — a single
high-confidence engine should never be enough on its own.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from engines.base_engine import Bias, EngineOutput


@dataclass
class VoteResult:
    winning_bias: Bias
    agree_count: int
    total_engines: int
    breakdown: dict[str, int]   # {"BULLISH": 2, "BEARISH": 0, "NEUTRAL": 1}


def tally_votes(outputs: list[EngineOutput]) -> VoteResult:
    """Tally engine biases. NEUTRAL votes count toward total but never
    toward the winning side's agreement count.
    """
    biases = [o.bias for o in outputs]
    counts = Counter(b.value for b in biases)

    bullish = counts.get(Bias.BULLISH.value, 0)
    bearish = counts.get(Bias.BEARISH.value, 0)

    if bullish > bearish:
        winning = Bias.BULLISH
        agree_count = bullish
    elif bearish > bullish:
        winning = Bias.BEARISH
        agree_count = bearish
    else:
        winning = Bias.NEUTRAL
        agree_count = 0

    breakdown = {
        "BULLISH": bullish,
        "BEARISH": bearish,
        "NEUTRAL": counts.get(Bias.NEUTRAL.value, 0),
    }

    return VoteResult(
        winning_bias=winning,
        agree_count=agree_count,
        total_engines=len(outputs),
        breakdown=breakdown,
    )
