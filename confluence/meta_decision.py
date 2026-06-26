"""
confluence/meta_decision.py
-----------------------------
Meta Decision Layer — evaluates decision quality, not direction.

Answers three questions before every EXECUTE:
  1. How confident are we? (Confidence Score 0-100)
  2. How stable is the signal? (Stability — agreement across engines)
  3. What is each engine's marginal contribution? (Shapley-like)

These go into the final report and influence position sizing.

Key insight from the technical review:
  "score=78 vs score=58 should NOT produce the same position size"
  Meta Decision adds a confidence multiplier to correct this.

Decision Confidence Multiplier:
  Confidence 80-100: 1.0× (full size)
  Confidence 60-80:  0.75× 
  Confidence 40-60:  0.5×
  Confidence < 40:   0.0× (block — equivalent to NO_TRADE)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.base_engine import Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MetaDecision:
    """Complete meta-analysis of a trading decision."""

    # Core scores
    confidence: float       # 0-100: overall decision confidence
    stability: float        # 0-100: agreement consistency across engines
    data_quality: float     # 0-100: data reliability score

    # Decision output
    verdict: str            # EXECUTE / CAUTION / BLOCK
    position_multiplier: float  # 0.0 to 1.0

    # Engine contributions (Shapley-like)
    engine_contributions: dict[str, float] = field(default_factory=dict)
    dominant_engine: str = ""
    weakest_engine: str = ""

    # Uncertainty factors
    uncertainty_flags: list[str] = field(default_factory=list)

    # Summary
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 1),
            "stability": round(self.stability, 1),
            "data_quality": round(self.data_quality, 1),
            "verdict": self.verdict,
            "position_multiplier": round(self.position_multiplier, 2),
            "dominant_engine": self.dominant_engine,
            "weakest_engine": self.weakest_engine,
            "engine_contributions": {
                k: round(v, 2) for k, v in self.engine_contributions.items()
            },
            "uncertainty_flags": self.uncertainty_flags,
            "reason": self.reason,
        }


def _engine_contribution(
    outputs: list[EngineOutput],
    weights: dict[str, float],
    base_score: float,
    winning_bias: Bias,
) -> dict[str, float]:
    """
    Calculate each engine's marginal contribution to the final score.
    
    Simplified Shapley: contribution = score_with - score_without
    
    Positive contribution: engine agrees and pushes score up
    Negative contribution: engine disagrees or is neutral
    """
    contributions = {}

    for output in outputs:
        name = output.engine_name.lower().replace(" ", "_")
        w = weights.get(name, 0.0)

        if output.bias == winning_bias:
            # Engine agrees — its contribution = weight × score
            contributions[name] = round(w * output.score, 2)
        elif output.bias == Bias.NEUTRAL:
            # Engine neutral — no contribution
            contributions[name] = 0.0
        else:
            # Engine disagrees — negative contribution
            contributions[name] = round(-w * output.score * 0.5, 2)

    return contributions


def _stability_score(outputs: list[EngineOutput], winning_bias: Bias) -> float:
    """
    Measure signal stability: how consistently engines agree.
    
    High stability = engines agree strongly and clearly
    Low stability = mixed signals, some engines strongly disagree
    """
    if not outputs:
        return 0.0

    agree = [o for o in outputs if o.bias == winning_bias]
    neutral = [o for o in outputs if o.bias == Bias.NEUTRAL]
    disagree = [o for o in outputs if o.bias != winning_bias and o.bias != Bias.NEUTRAL]

    n = len(outputs)
    agree_pct = len(agree) / n

    # Penalize strong disagreers
    strong_disagree = [o for o in disagree if o.score >= 40]
    disagree_penalty = len(strong_disagree) * 15

    # Penalize high neutral count (indecisive)
    neutral_penalty = max(0, len(neutral) - 3) * 5

    # Reward high agreement scores
    avg_agree_score = sum(o.score for o in agree) / len(agree) if agree else 0

    stability = (agree_pct * 100) - disagree_penalty - neutral_penalty
    stability = max(0.0, min(100.0, stability))

    return round(stability, 1)


def _data_quality_score(report_context: dict) -> float:
    """
    Estimate data reliability based on available context.
    
    Factors:
    - MQS score (already computed)
    - Data provider (Twelve Data > Yahoo > others)
    - Number of bars available
    - Recent volatility extremes
    """
    score = 70.0  # base

    mqs = report_context.get("market_quality", {}).get("mqs_score", 60)
    if mqs >= 70:
        score += 15
    elif mqs >= 50:
        score += 5
    else:
        score -= 10

    provider = report_context.get("data_provider", "")
    if "twelve_data" in provider.lower():
        score += 10
    elif "yahoo" in provider.lower():
        score += 5

    return round(max(0, min(100, score)), 1)


def _confidence_score(
    adjusted_score: float,
    stability: float,
    agree_count: int,
    total_engines: int,
    data_quality: float,
) -> float:
    """
    Composite confidence score combining all signals.
    
    Formula:
      30% from confluence score
      30% from stability
      20% from engine agreement ratio
      20% from data quality
    """
    score_component = (adjusted_score / 100) * 30
    stability_component = (stability / 100) * 30
    agreement_component = (agree_count / max(total_engines, 1)) * 20
    data_component = (data_quality / 100) * 20

    confidence = score_component + stability_component + agreement_component + data_component
    return round(max(0, min(100, confidence)), 1)


def evaluate_meta_decision(
    outputs: list[EngineOutput],
    weights: dict[str, float],
    adjusted_score: float,
    vote_result: Any,
    report_context: dict | None = None,
) -> MetaDecision:
    """
    Run meta-decision analysis on a potential EXECUTE signal.

    Args:
        outputs: engine outputs list
        weights: current engine weights
        adjusted_score: score after MTF adjustment
        vote_result: from tally_votes()
        report_context: partial report dict for data quality

    Returns:
        MetaDecision with confidence, stability, contributions
    """
    if report_context is None:
        report_context = {}

    winning_bias = getattr(vote_result, 'winning_bias', Bias.NEUTRAL)
    agree_count = getattr(vote_result, 'agree_count', 0)

    # Engine contributions
    contributions = _engine_contribution(outputs, weights, adjusted_score, winning_bias)

    # Stability
    stability = _stability_score(outputs, winning_bias)

    # Data quality
    data_quality = _data_quality_score(report_context)

    # Composite confidence
    confidence = _confidence_score(
        adjusted_score, stability, agree_count, len(outputs), data_quality
    )

    # Identify dominant and weakest engines
    positive = {k: v for k, v in contributions.items() if v > 0}
    negative = {k: v for k, v in contributions.items() if v < 0}

    dominant = max(positive, key=positive.get) if positive else ""
    weakest = min(negative, key=negative.get) if negative else ""

    # Uncertainty flags
    flags = []
    if stability < 40:
        flags.append("LOW_STABILITY: engines disagree significantly")
    if agree_count < 3:
        flags.append(f"FEW_AGREEING: only {agree_count} engines agree")
    if adjusted_score < 60:
        flags.append(f"LOW_SCORE: confluence={adjusted_score:.0f} near minimum")
    if data_quality < 50:
        flags.append("DATA_QUALITY: low data reliability")

    # Verdict and position multiplier
    if confidence >= 70 and not flags:
        verdict = "EXECUTE"
        multiplier = 1.0
    elif confidence >= 55 and len(flags) <= 1:
        verdict = "EXECUTE"
        multiplier = 0.75
        flags.append("REDUCED_SIZE: moderate confidence")
    elif confidence >= 40:
        verdict = "CAUTION"
        multiplier = 0.5
    else:
        verdict = "BLOCK"
        multiplier = 0.0
        flags.append(f"BLOCKED: confidence={confidence:.0f} too low")

    reason = (
        f"Confidence={confidence:.0f}% Stability={stability:.0f}% "
        f"DataQ={data_quality:.0f}% | "
        f"Dominant: {dominant} | "
        f"{'No flags' if not flags else flags[0]}"
    )

    logger.debug(
        f"MetaDecision: {verdict} conf={confidence:.0f} "
        f"stab={stability:.0f} mult={multiplier}"
    )

    return MetaDecision(
        confidence=confidence,
        stability=stability,
        data_quality=data_quality,
        verdict=verdict,
        position_multiplier=multiplier,
        engine_contributions=contributions,
        dominant_engine=dominant,
        weakest_engine=weakest,
        uncertainty_flags=flags,
        reason=reason,
    )
