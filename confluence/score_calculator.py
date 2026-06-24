"""
confluence/score_calculator.py
----------------------------------
Weighted confluence score.

Formula: score = weighted avg of AGREEING engines' scores only.

Only engines that voted for the MAJORITY direction are included in the
score. Opposing engines are handled by check_contradictions(), not here.

This means:
  - 3 engines BULLISH, 1 BEARISH → score = avg of the 3 BULLISH engines
  - All NEUTRAL → score = 0
  - The contradiction_engine handles the BEARISH case separately

Why separate concerns:
  - score_calculator asks: "how confident are the agreeing engines?"
  - contradiction_engine asks: "is there meaningful opposition?"
  - These are different questions that should not cancel each other out.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from engines.base_engine import Bias, EngineOutput


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
}


def calculate_score(outputs: list[EngineOutput], weights: dict[str, float]) -> ScoreResult:
    """Score = weighted average of MAJORITY-side engines' scores.

    Step 1: find the majority direction (BULLISH or BEARISH)
    Step 2: score = sum(weight_i × score_i) / sum(weight_i) for majority engines
    Step 3: directional_score = positive for bullish, negative for bearish

    Opposition is handled by check_contradictions(), not by this function.
    """
    full_weight_total = sum(weights.values()) or 1.0
    contributions: dict[str, float] = {}

    bull_engines = [o for o in outputs if o.bias == Bias.BULLISH]
    bear_engines = [o for o in outputs if o.bias == Bias.BEARISH]

    def _weighted_score(engines_subset):
        total_w = 0.0
        total_ws = 0.0
        for o in engines_subset:
            key = _ENGINE_NAME_TO_CONFIG_KEY.get(o.engine_name)
            w = weights.get(key, 0.0) if key else 0.0
            total_w += w
            total_ws += w * o.score
        return total_ws / total_w if total_w > 0 else 0.0, total_w

    bull_score, bull_weight = _weighted_score(bull_engines)
    bear_score, bear_weight = _weighted_score(bear_engines)

    for o in outputs:
        key = _ENGINE_NAME_TO_CONFIG_KEY.get(o.engine_name)
        w = weights.get(key, 0.0) if key else 0.0
        sign = 1 if o.bias == Bias.BULLISH else -1 if o.bias == Bias.BEARISH else 0
        contributions[o.engine_name] = round(w * o.score * sign / 100, 4)

    # Majority direction
    if len(bull_engines) > len(bear_engines):
        final_score = round(min(bull_score, 100.0), 2)
        directional_score = final_score
        participating = len(bull_engines)
        participating_weight = bull_weight
    elif len(bear_engines) > len(bull_engines):
        final_score = round(min(bear_score, 100.0), 2)
        directional_score = -final_score
        participating = len(bear_engines)
        participating_weight = bear_weight
    elif len(bull_engines) == len(bear_engines) and len(bull_engines) > 0:
        # Tie — use higher scoring side
        if bull_score >= bear_score:
            final_score = round(min(bull_score, 100.0), 2)
            directional_score = final_score
            participating = len(bull_engines)
            participating_weight = bull_weight
        else:
            final_score = round(min(bear_score, 100.0), 2)
            directional_score = -final_score
            participating = len(bear_engines)
            participating_weight = bear_weight
    else:
        return ScoreResult(
            final_score=0.0, directional_score=0.0,
            contributions=contributions, engines_participating=0,
            engines_total=len(outputs), participating_weight_share=0.0,
        )

    return ScoreResult(
        final_score=final_score,
        directional_score=round(directional_score, 2),
        contributions=contributions,
        engines_participating=participating,
        engines_total=len(outputs),
        participating_weight_share=round(participating_weight / full_weight_total, 3),
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
