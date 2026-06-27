"""
confluence/reversal_veto.py
------------------------------
H013: Reversal Engine Group Agreement as Counter-Signal.

Evidence (from registry.json):
    2026-06-26: 4 trend engines (SMC+NNFX+PA+Quant) voted BEARISH,
    3 reversal engines (Divergence+Wyckoff+Sentiment) voted BULLISH.
    Market reversed BULLISH — reversal engines were correct.
    Result: 5 trades hit SL = significant loss.

Logic:
    The trend engines are excellent at identifying the CURRENT direction,
    but they lag at turning points. The reversal engines (divergence,
    wyckoff springs, sentiment extremes) detect UPCOMING reversals.

    When they UNANIMOUSLY disagree with the trend engines, this is a
    strong reversal warning. The system should either:
    - BLOCK the trade (strong veto: 3/3 reversal engines agree)
    - REDUCE confidence (soft veto: 2/3 reversal engines agree)

Engine Classification:
    TREND engines: smc, price_action, nnfx, quant, ict, market_structure
    REVERSAL engines: divergence, wyckoff, sentiment

Rules:
    3/3 reversal engines disagree → BLOCK (veto)
    2/3 reversal engines disagree → reduce confidence to 0.5×
    1/3 or fewer → no effect (normal confluence)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.base_engine import Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)

# Engine classification
TREND_ENGINES = {"SMC", "PriceAction", "NNFX", "Quant", "ICT", "MarketStructure"}
REVERSAL_ENGINES = {"Divergence", "Wyckoff", "Sentiment"}

# Veto thresholds
HARD_VETO_COUNT = 3   # 3/3 reversal engines disagree → block
SOFT_VETO_COUNT = 2   # 2/3 reversal engines disagree → reduce size


@dataclass
class ReversalVetoResult:
    """Result of the reversal veto check."""
    vetoed: bool                    # True = block the trade
    soft_veto: bool                 # True = reduce position size
    confidence_multiplier: float    # 1.0 = normal, 0.5 = reduced, 0.0 = blocked
    reversal_count: int             # how many reversal engines disagree
    reversal_engines: list[str]     # which reversal engines are opposing
    trend_bias: str                 # what the trend engines say
    reversal_bias: str              # what the reversal engines say
    reason: str

    def to_dict(self) -> dict:
        return {
            "vetoed": self.vetoed,
            "soft_veto": self.soft_veto,
            "confidence_multiplier": self.confidence_multiplier,
            "reversal_count": self.reversal_count,
            "reversal_engines": self.reversal_engines,
            "trend_bias": self.trend_bias,
            "reversal_bias": self.reversal_bias,
            "reason": self.reason,
        }


def check_reversal_veto(
    outputs: list[EngineOutput],
    winning_bias: Bias,
) -> ReversalVetoResult:
    """Check if reversal engines unanimously oppose the trade direction.

    This implements H013: when reversal engines (divergence, wyckoff,
    sentiment) all agree on the OPPOSITE direction, the trade is likely
    entering a reversal zone.

    Args:
        outputs: all engine outputs from this pipeline run
        winning_bias: the majority-voted direction (BULLISH/BEARISH)

    Returns:
        ReversalVetoResult with veto decision
    """
    if winning_bias == Bias.NEUTRAL:
        return ReversalVetoResult(
            vetoed=False, soft_veto=False, confidence_multiplier=1.0,
            reversal_count=0, reversal_engines=[], trend_bias="NEUTRAL",
            reversal_bias="N/A", reason="No direction to veto (NEUTRAL).",
        )

    opposite = Bias.BEARISH if winning_bias == Bias.BULLISH else Bias.BULLISH

    # Find reversal engines that actively oppose the winning bias
    opposing_reversals = []
    active_reversals = 0

    for o in outputs:
        if o.engine_name not in REVERSAL_ENGINES:
            continue
        if o.bias == Bias.NEUTRAL:
            continue  # abstention doesn't count as opposition
        active_reversals += 1
        if o.bias == opposite:
            opposing_reversals.append(o.engine_name)

    count = len(opposing_reversals)

    # Require at least 2 active reversal engines to consider a veto
    # (if only 1 is active and it opposes, that's not enough signal)
    if active_reversals < 2:
        return ReversalVetoResult(
            vetoed=False, soft_veto=False, confidence_multiplier=1.0,
            reversal_count=count, reversal_engines=opposing_reversals,
            trend_bias=winning_bias.value,
            reversal_bias=opposite.value if count > 0 else "MIXED",
            reason=f"Only {active_reversals} reversal engine(s) active — insufficient for veto.",
        )

    # Hard veto: ALL active reversal engines oppose (and at least 3 active)
    if count >= HARD_VETO_COUNT and count == active_reversals:
        reason = (
            f"H013 HARD VETO: {count}/{active_reversals} reversal engines "
            f"({', '.join(opposing_reversals)}) unanimously {opposite.value} "
            f"against trend {winning_bias.value}. Potential reversal zone."
        )
        logger.warning(reason)
        return ReversalVetoResult(
            vetoed=True, soft_veto=False, confidence_multiplier=0.0,
            reversal_count=count, reversal_engines=opposing_reversals,
            trend_bias=winning_bias.value, reversal_bias=opposite.value,
            reason=reason,
        )

    # Soft veto: 2+ reversal engines oppose
    if count >= SOFT_VETO_COUNT:
        reason = (
            f"H013 SOFT VETO: {count}/{active_reversals} reversal engines "
            f"({', '.join(opposing_reversals)}) {opposite.value} "
            f"against trend {winning_bias.value}. Reducing position size."
        )
        logger.info(reason)
        return ReversalVetoResult(
            vetoed=False, soft_veto=True, confidence_multiplier=0.5,
            reversal_count=count, reversal_engines=opposing_reversals,
            trend_bias=winning_bias.value, reversal_bias=opposite.value,
            reason=reason,
        )

    # No veto
    return ReversalVetoResult(
        vetoed=False, soft_veto=False, confidence_multiplier=1.0,
        reversal_count=count, reversal_engines=opposing_reversals,
        trend_bias=winning_bias.value,
        reversal_bias=opposite.value if count > 0 else "AGREES",
        reason=f"Reversal engines: {count}/{active_reversals} opposing — no veto.",
    )
