"""
confluence/regime_weights.py
------------------------------
Regime-aware weight adjustment for the confluence layer.

Instead of static weights across all market conditions, engines
that are naturally stronger in the current regime get boosted:

TRENDING:  SMC + ICT higher (structure + killzone work better)
RANGING:   Wyckoff + Quant higher (range/mean-reversion signals)
VOLATILE:  Quant higher, reduce all others (pure stats over patterns)

This is NOT Dynamic Weighting (that requires P&L history).
This is hand-crafted domain knowledge that makes intuitive sense
and can be validated once enough engine_tracker data accumulates.

Phase 3: hand-crafted regime multipliers
Phase 6: replaced by data-driven weights from engine_tracker
"""

from __future__ import annotations

from utils.logger import get_logger

logger = get_logger(__name__)


# Base weights (from config.yaml) — sum = 1.0
# Regime multipliers adjust these proportionally

_REGIME_MULTIPLIERS: dict[str, dict[str, float]] = {
    "TRENDING": {
        "smc": 1.3,          # swing structure most reliable in trends
        "price_action": 1.2, # MA trend signal clear in trends
        "ict": 1.2,          # killzones more relevant in trends
        "nnfx": 1.3,         # EMA200 trend filter strongest here
        "quant": 0.8,        # RSI/momentum less reliable in trends
        "wyckoff": 0.7,      # range patterns rare in trends
        "macro": 1.0,
    },
    "RANGING": {
        "smc": 0.9,          # structure breaks unreliable in ranges
        "price_action": 0.8, # MA trend signal noisy in ranges
        "ict": 1.0,          # premium/discount still valid
        "nnfx": 0.7,         # EMA200 trend filter less useful
        "quant": 1.2,        # RSI mean-reversion better in ranges
        "wyckoff": 1.4,      # spring/upthrust most relevant in ranges
        "macro": 1.0,
    },
    "VOLATILE": {
        "smc": 0.8,
        "price_action": 0.8,
        "ict": 0.9,
        "nnfx": 0.8,
        "quant": 1.3,        # statistical signals more robust
        "wyckoff": 1.0,
        "macro": 1.1,        # macro context more important in volatility
    },
}


def apply_regime_weights(
    base_weights: dict[str, float],
    regime: str,
    volatility: str = "normal",
) -> dict[str, float]:
    """Return adjusted weights for the current market regime.

    Args:
        base_weights: weights from config.yaml
        regime: 'TRENDING' | 'RANGING'
        volatility: 'low' | 'normal' | 'high' | 'extreme'

    Returns:
        New weight dict normalized to same sum as base_weights.
    """
    # Choose regime key
    if volatility in ("high", "extreme"):
        regime_key = "VOLATILE"
    else:
        regime_key = regime if regime in _REGIME_MULTIPLIERS else "TRENDING"

    multipliers = _REGIME_MULTIPLIERS[regime_key]
    total_base = sum(base_weights.values()) or 1.0

    # Apply multipliers
    adjusted = {
        engine: base_weights.get(engine, 0) * multipliers.get(engine, 1.0)
        for engine in base_weights
    }

    # Re-normalize to same total as base
    total_adjusted = sum(adjusted.values()) or 1.0
    normalized = {
        engine: round(weight * total_base / total_adjusted, 4)
        for engine, weight in adjusted.items()
    }

    logger.debug(
        f"Regime-aware weights ({regime_key}): "
        f"{', '.join(f'{k}={v:.3f}' for k, v in normalized.items() if v > 0)}"
    )

    return normalized
