"""
confluence/voting_system.py
------------------------------
Weight-based voting system.

v0.5.0: Changed from raw count to WEIGHT-BASED voting.

Previous problem:
  ICT (weight=0.065) voted BEARISH with score=25
  NNFX (weight=0.227) voted BULLISH with score=70
  → Both counted as 1 vote each. This is wrong.

New approach:
  1. Minimum conviction threshold: engines with score < 20 are treated as NEUTRAL
     (a score=5 "BULLISH" is noise, not a signal)
  2. Weighted majority: sum(weight × score) for each direction
     The side with higher weighted conviction wins.
  3. agree_count still counts raw engines (for min_engines_agreeing check)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from engines.base_engine import Bias, EngineOutput


# Engines with score below this threshold are treated as NEUTRAL
# (too weak to be a real signal — just noise)
MIN_CONVICTION_SCORE = 20


@dataclass
class VoteResult:
    winning_bias: Bias
    agree_count: int
    total_engines: int
    breakdown: dict[str, int]   # {"BULLISH": 2, "BEARISH": 0, "NEUTRAL": 1}
    bull_conviction: float       # weighted conviction for BULLISH
    bear_conviction: float       # weighted conviction for BEARISH


# Engine name → config weight key mapping
_NAME_TO_KEY = {
    "SMC": "smc", "ICT": "ict", "NNFX": "nnfx",
    "PriceAction": "price_action", "Quant": "quant",
    "Wyckoff": "wyckoff", "Macro": "macro",
    "Divergence": "divergence", "MarketStructure": "market_structure",
    "Sentiment": "sentiment",
}


def tally_votes(
    outputs: list[EngineOutput],
    weights: dict[str, float] | None = None,
) -> VoteResult:
    """Tally engine biases using weight-based conviction.

    Majority direction is determined by weighted conviction
    (sum of weight × score), not by raw engine count.

    Engines with score < MIN_CONVICTION_SCORE are treated as NEUTRAL
    regardless of their stated bias.
    """
    # Effective biases (after conviction threshold)
    effective_biases = []
    for o in outputs:
        if o.bias != Bias.NEUTRAL and o.score >= MIN_CONVICTION_SCORE:
            effective_biases.append(o.bias)
        else:
            effective_biases.append(Bias.NEUTRAL)

    counts = Counter(b.value for b in effective_biases)
    bullish_count = counts.get(Bias.BULLISH.value, 0)
    bearish_count = counts.get(Bias.BEARISH.value, 0)
    neutral_count = counts.get(Bias.NEUTRAL.value, 0)

    # Weighted conviction: sum(weight × score) for each direction
    bull_conviction = 0.0
    bear_conviction = 0.0

    for o, eff_bias in zip(outputs, effective_biases):
        if weights:
            key = _NAME_TO_KEY.get(o.engine_name, o.engine_name.lower())
            w = weights.get(key, 0.01)
        else:
            w = 1.0  # fallback: equal weight

        if eff_bias == Bias.BULLISH:
            bull_conviction += w * o.score
        elif eff_bias == Bias.BEARISH:
            bear_conviction += w * o.score

    # Majority by WEIGHTED CONVICTION (not raw count)
    if bull_conviction > bear_conviction and bullish_count > 0:
        winning = Bias.BULLISH
        agree_count = bullish_count
    elif bear_conviction > bull_conviction and bearish_count > 0:
        winning = Bias.BEARISH
        agree_count = bearish_count
    elif bullish_count > 0 and bull_conviction == bear_conviction:
        # Tie in conviction — fall back to count
        if bullish_count >= bearish_count:
            winning = Bias.BULLISH
            agree_count = bullish_count
        else:
            winning = Bias.BEARISH
            agree_count = bearish_count
    else:
        winning = Bias.NEUTRAL
        agree_count = 0

    breakdown = {
        "BULLISH": bullish_count,
        "BEARISH": bearish_count,
        "NEUTRAL": neutral_count,
    }

    return VoteResult(
        winning_bias=winning,
        agree_count=agree_count,
        total_engines=len(outputs),
        breakdown=breakdown,
        bull_conviction=round(bull_conviction, 2),
        bear_conviction=round(bear_conviction, 2),
    )
