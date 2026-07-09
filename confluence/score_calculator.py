"""
confluence/score_calculator.py
----------------------------------
Weighted confluence score.

Formula: score = weighted avg of AGREEING engines' scores only.

"Agreeing" means: effectively voting (bias != NEUTRAL AND score >=
MIN_CONVICTION_SCORE — the same threshold the vote layer applies) for the
SAME side tally_votes() declared the winner by weighted conviction.

Unified with voting_system (philosophy audit, Axis 6). Previously this
module picked its own majority by raw engine COUNT (ties broken by the
higher-average side) while tally_votes picked the verdict direction by
weighted CONVICTION — the two could select opposite sides, so the stored
cf_score sometimes described the direction the verdict didn't take, and a
1-point nudge in one engine could flip the reported score between the two
sides' averages (the BTC-39 vs ETH-80 discontinuity). It also ignored the
conviction threshold, so an engine at score 19 was NEUTRAL for the quorum
yet still steered the score. Both inconsistencies are now closed:

  - calculate_score() takes the winning bias from tally_votes() (callers
    pass vote_result.winning_bias; if omitted it derives it by calling
    tally_votes itself — one definition, one place).
  - The conviction threshold is imported from voting_system and applied
    identically here.
  - winning_bias == NEUTRAL (including exact conviction ties) → score 0:
    a dead heat is no information, not "the louder side's average".

Why separate concerns (unchanged):
  - score_calculator asks: "how confident are the agreeing engines?"
  - contradiction_engine asks: "is there meaningful opposition?"
  - These are different questions that should not cancel each other out.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from engines.base_engine import Bias, EngineOutput
from confluence.voting_system import MIN_CONVICTION_SCORE, effective_bias, tally_votes


class ConfluenceConfigError(Exception):
    pass


@dataclass
class ScoreResult:
    final_score: float
    directional_score: float
    contributions: dict[str, float]
    engines_participating: int
    engines_total: int
    participating_weight_share: float


_ENGINE_NAME_TO_CONFIG_KEY = {
    "SMC": "smc", "ICT": "ict", "NNFX": "nnfx",
    "PriceAction": "price_action", "Quant": "quant",
    "Wyckoff": "wyckoff", "Macro": "macro",
    # H010-H012 engines (added in v0.4)
    "Divergence": "divergence",
    "MarketStructure": "market_structure",
    "Sentiment": "sentiment",
}


def _engine_key(engine_name: str) -> str | None:
    """Convert engine_name to config weight key.
    Handles: direct map, CamelCase→snake_case, lowercase fallback.
    """
    if engine_name in _ENGINE_NAME_TO_CONFIG_KEY:
        return _ENGINE_NAME_TO_CONFIG_KEY[engine_name]
    # Auto: CamelCase → snake_case fallback
    import re
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', engine_name).lower()
    return snake if snake else None


def calculate_score(
    outputs: list[EngineOutput],
    weights: dict[str, float],
    winning_bias: Bias | None = None,
) -> ScoreResult:
    """Score = weighted average of the WINNING side's effective votes.

    Step 1: the winning direction comes from tally_votes() — pass
            vote_result.winning_bias; when omitted it is derived here by
            the same function, so there is exactly one majority definition
            in the system (weighted conviction, philosophy audit Axis 6).
    Step 2: score = sum(weight_i × score_i) / sum(weight_i) over engines
            whose EFFECTIVE bias (conviction threshold applied) equals the
            winning bias.
    Step 3: directional_score = +score for BULLISH, -score for BEARISH.
    winning_bias == NEUTRAL (no votes, or an exact conviction tie) → 0.

    Opposition is handled by check_contradictions(), not by this function.
    """
    if winning_bias is None:
        winning_bias = tally_votes(outputs, weights).winning_bias

    full_weight_total = sum(weights.values()) or 1.0

    # Contributions use the same effective bias as the vote layer: an
    # engine below MIN_CONVICTION_SCORE contributes exactly nothing,
    # instead of being NEUTRAL for the quorum but non-zero here.
    contributions: dict[str, float] = {}
    for o in outputs:
        key = _engine_key(o.engine_name)
        w = weights.get(key, 0.0) if key else 0.0
        eff = effective_bias(o)
        sign = 1 if eff == Bias.BULLISH else -1 if eff == Bias.BEARISH else 0
        contributions[o.engine_name] = round(w * o.score * sign / 100, 4)

    if winning_bias == Bias.NEUTRAL:
        return ScoreResult(
            final_score=0.0, directional_score=0.0,
            contributions=contributions, engines_participating=0,
            engines_total=len(outputs), participating_weight_share=0.0,
        )

    agreeing = [o for o in outputs if effective_bias(o) == winning_bias]

    total_w = 0.0
    total_ws = 0.0
    for o in agreeing:
        key = _engine_key(o.engine_name)
        w = weights.get(key, 0.0) if key else 0.0
        total_w += w
        total_ws += w * o.score
    side_score = total_ws / total_w if total_w > 0 else 0.0

    final_score = round(min(side_score, 100.0), 2)
    direction = 1 if winning_bias == Bias.BULLISH else -1

    return ScoreResult(
        final_score=final_score,
        directional_score=round(direction * final_score, 2),
        contributions=contributions,
        engines_participating=len(agreeing),
        engines_total=len(outputs),
        participating_weight_share=round(total_w / full_weight_total, 3),
    )


def validate_confluence_config(config: dict) -> None:
    enabled = config.get("engines", {}).get("enabled", {})
    enabled_count = sum(1 for v in enabled.values() if v)
    min_engines = config.get("confluence", {}).get("min_engines_agreeing", 0)
    min_score = config.get("confluence", {}).get("min_score_to_trade", 0)

    if min_engines > enabled_count:
        raise ConfluenceConfigError(
            f"min_engines_agreeing ({min_engines}) > enabled engines ({enabled_count}). "
            f"EXECUTE unreachable."
        )

    if min_score > 0:
        weights = config.get("confluence", {}).get("weights", {})
        _MAX_SCORE = {"SMC": 65.0, "Quant": 60.0, "Wyckoff": 75.0, "Macro": 70.0}
        _DEFAULT_MAX = 80.0
        _KEY_TO_ENGINE = {
            "smc": "SMC", "price_action": "PriceAction", "ict": "ICT",
            "nnfx": "NNFX", "quant": "Quant", "wyckoff": "Wyckoff", "macro": "Macro",
        }
        enabled_keys = [k for k, v in enabled.items() if v]
        pw = sum(weights.get(k, 0) for k in enabled_keys)
        if pw > 0:
            max_ws = sum(
                weights.get(k, 0) * _MAX_SCORE.get(_KEY_TO_ENGINE.get(k, ""), _DEFAULT_MAX)
                for k in enabled_keys
            )
            max_achievable = max_ws / pw
            if min_score > max_achievable:
                raise ConfluenceConfigError(
                    f"min_score_to_trade ({min_score}) > max achievable "
                    f"({max_achievable:.1f}). Lower to ≤{int(max_achievable)}."
                )
