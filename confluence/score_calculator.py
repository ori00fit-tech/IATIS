"""
confluence/score_calculator.py
----------------------------------
Computes the final weighted confluence score (0-100) from individual
engine outputs, using the weights defined in config.yaml.

IMPORTANT — re-normalization policy (changed after a real bug was found):
weights are re-normalized across only the engines that actually
produced a non-NEUTRAL output, so the score's 0-100 scale always means
"how much of the engines that actually voted agree," not "how much of
a theoretical six-engine system agreed." The previous version weighted
against the full fixed weight table regardless of how many engines were
enabled, which made `final_score` mathematically incapable of reaching
typical thresholds (e.g. 75) whenever fewer than ~4 engines were active
— EXECUTE was unreachable by construction, not by design. See
research/results/registry.json / config.yaml history for context.

This does NOT hide how few engines participated — `engines_participating`
and `engines_total_weighted` are returned explicitly precisely so a
high score from 2 engines is never mistaken for a high score from 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.base_engine import Bias, EngineOutput


class ConfluenceConfigError(Exception):
    """Raised when confluence config is internally inconsistent, e.g.
    requiring more agreeing engines than are even enabled — which makes
    EXECUTE mathematically unreachable rather than just rare.
    """


def validate_confluence_config(config: dict) -> None:
    """Sanity-check confluence settings against the enabled engine count.

    Checks:
    1. min_engines_agreeing <= enabled engine count
       (otherwise EXECUTE is unreachable — too few engines to form a majority)
    2. min_score_to_trade <= max achievable score with enabled engines
       (otherwise EXECUTE is unreachable — score ceiling is below threshold)

    Call this once at startup (main.py does) so misconfigured values
    fail loudly at boot instead of silently guaranteeing NO_TRADE forever.
    """
    enabled = config.get("engines", {}).get("enabled", {})
    enabled_count = sum(1 for v in enabled.values() if v)
    min_engines = config.get("confluence", {}).get("min_engines_agreeing", 0)
    min_score = config.get("confluence", {}).get("min_score_to_trade", 0)

    # Check 1: enough engines to form a majority
    if min_engines > enabled_count:
        raise ConfluenceConfigError(
            f"confluence.min_engines_agreeing ({min_engines}) exceeds the number of "
            f"enabled engines ({enabled_count}). EXECUTE would be mathematically "
            f"unreachable. Lower min_engines_agreeing or enable more engines."
        )

    # Check 2: min_score_to_trade is achievable given engine weights and max scores.
    # SMC max score = 65 (majority vote cap), PA max score = 80 (sigmoid cap).
    # Other engines cap at 80. Re-normalized over enabled engines only.
    if min_score > 0:
        weights = config.get("confluence", {}).get("weights", {})
        # Map config weight keys to engine names
        _KEY_TO_ENGINE = {
            "smc": "SMC", "price_action": "PriceAction",
            "ict": "ICT", "nnfx": "NNFX", "quant": "Quant", "macro": "Macro",
        }
        # Per-engine max score caps
        _MAX_SCORE = {"SMC": 65.0}  # SMC majority-vote formula caps at 65
        _DEFAULT_MAX = 80.0

        enabled_keys = [k for k, v in enabled.items() if v]
        participating_weight = sum(weights.get(k, 0) for k in enabled_keys)

        if participating_weight > 0:
            max_weighted = sum(
                weights.get(k, 0) * _MAX_SCORE.get(_KEY_TO_ENGINE.get(k, ""), _DEFAULT_MAX)
                for k in enabled_keys
            )
            max_achievable = max_weighted / participating_weight

            if min_score > max_achievable:
                raise ConfluenceConfigError(
                    f"confluence.min_score_to_trade ({min_score}) exceeds the maximum "
                    f"achievable score with current enabled engines "
                    f"({max_achievable:.1f}). EXECUTE would be mathematically "
                    f"unreachable. Lower min_score_to_trade to ≤{int(max_achievable)}."
                )


@dataclass
class ScoreResult:
    final_score: float                  # 0-100, re-normalized over participating engines
    directional_score: float            # signed: positive = bullish lean, negative = bearish
    contributions: dict[str, float]      # raw (non-renormalized) weighted contribution per engine
    engines_participating: int = 0       # how many engines voted non-NEUTRAL
    engines_total: int = 0               # how many engines were passed in at all
    participating_weight_share: float = 0.0   # fraction of the FULL weight table covered by participants


# maps engine.name (as set in each engine class) to the config.yaml weight key
_ENGINE_NAME_TO_CONFIG_KEY = {
    "SMC": "smc",
    "ICT": "ict",
    "NNFX": "nnfx",
    "PriceAction": "price_action",
    "Quant": "quant",
    "Macro": "macro",
}


def calculate_score(outputs: list[EngineOutput], weights: dict[str, float]) -> ScoreResult:
    """Combine engine outputs into one weighted, re-normalized confluence score.

    Each engine's raw contribution = weight * score, signed by bias
    (BULLISH = +, BEARISH = -, NEUTRAL = 0). The final 0-100 score is
    re-normalized by dividing by the total weight of engines that
    actually voted (non-NEUTRAL), so a 2-engine system can still reach
    100 if both agree strongly. Engines that abstained (NEUTRAL) or
    weren't passed in at all contribute 0 and are excluded from the
    normalization denominator — but their absence is reported via
    `engines_participating` / `participating_weight_share` so it's never
    silently hidden.
    """
    contributions: dict[str, float] = {}
    directional_total = 0.0
    participating_weight = 0.0
    full_weight_total = sum(weights.values()) or 1.0
    engines_participating = 0

    for out in outputs:
        config_key = _ENGINE_NAME_TO_CONFIG_KEY.get(out.engine_name)
        weight = weights.get(config_key, 0.0) if config_key else 0.0

        sign = {Bias.BULLISH: 1, Bias.BEARISH: -1, Bias.NEUTRAL: 0}[out.bias]
        contribution = weight * out.score * sign

        contributions[out.engine_name] = round(contribution, 3)
        directional_total += contribution

        if out.bias != Bias.NEUTRAL:
            participating_weight += weight
            engines_participating += 1

    if participating_weight > 0:
        normalized_directional = directional_total / participating_weight
    else:
        normalized_directional = 0.0

    return ScoreResult(
        final_score=round(min(abs(normalized_directional), 100.0), 2),
        directional_score=round(normalized_directional, 2),
        contributions=contributions,
        engines_participating=engines_participating,
        engines_total=len(outputs),
        participating_weight_share=round(participating_weight / full_weight_total, 3),
    )
