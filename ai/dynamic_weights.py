"""
ai/dynamic_weights.py
-----------------------
Dynamic Weights AI — uses Claude API to analyze engine performance
and suggest optimized weights based on live outcome data.

Unlike simple rule-based weight adjustment (storage/calibration.py),
this AI layer:
  1. Analyzes multi-dimensional engine performance
  2. Considers regime-specific performance
  3. Detects correlation between engines
  4. Recommends weights with reasoning
  5. Applies safety constraints (no engine < 5% or > 35%)

Usage:
    from ai.dynamic_weights import analyze_and_suggest_weights
    result = await analyze_and_suggest_weights(engine_stats, outcome_data)

Triggers (when to update weights):
  - Every 50 closed trades
  - After significant drawdown (>10%)
  - Weekly review
"""
from __future__ import annotations

import json
from typing import Any

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# Weight constraints
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.35
TOTAL_WEIGHT = 1.0  # must sum to 1.0 (macro excluded = 0)

ENGINE_GROUPS = {
    "trend_following": ["smc", "price_action", "nnfx", "quant"],
    "reversal": ["divergence", "wyckoff", "sentiment"],
    "contextual": ["ict", "market_structure"],
}


def _build_analysis_prompt(
    engine_stats: list[dict],
    outcome_summary: dict,
    current_weights: dict[str, float],
    regime_data: list[dict] | None = None,
) -> str:
    """Build prompt for Claude to analyze and suggest weights."""

    # Format engine stats
    engine_lines = []
    for s in engine_stats:
        engine_lines.append(
            f"  {s['engine']}: agreement={s.get('agreement_rate', 0):.0f}% "
            f"avg_score={s.get('avg_score_when_voting', 0):.0f} "
            f"neutral={s.get('neutral_pct', 0):.0f}% "
            f"current_weight={current_weights.get(s['engine'].lower().replace(' ','_'), 0):.3f}"
        )

    # Format outcome summary
    wf_lines = []
    if outcome_summary.get("calibration"):
        for c in outcome_summary["calibration"]:
            wf_lines.append(
                f"  Score {c['score_range']}: n={c['n']}, "
                f"actual_WR={c['actual_win_rate']}%"
            )

    # Format regime data
    regime_lines = []
    if regime_data:
        for r in regime_data:
            regime_lines.append(
                f"  {r['regime']}: WR={r.get('win_rate','?')}% "
                f"PF={r.get('profit_factor','?')} n={r.get('trades',0)}"
            )

    prompt = f"""You are a quantitative analyst optimizing trading system engine weights for IATIS.

## Current Engine Performance (from {outcome_summary.get('total_closed', 0)} live trades):

### Engine Statistics (292 live votes):
{chr(10).join(engine_lines)}

### Win Rate by Confluence Score:
{chr(10).join(wf_lines) if wf_lines else "  No calibration data yet (need 200+ trades)"}

### Performance by Market Regime:
{chr(10).join(regime_lines) if regime_lines else "  No regime data yet"}

### Current Portfolio Performance:
- Total closed trades: {outcome_summary.get('total_closed', 0)}
- Win rate: {outcome_summary.get('win_rate', 0):.1f}%
- Known issues: 
  * ICT agreement=20.8% (contrarian — trend filter applied)
  * Wyckoff agreement=25.7% (reversal engine — orthogonal to trend)
  * NNFX has highest contribution (agreement=84.7%, avg_score=58)
  * H013: reversal engines (Div+Wyckoff+Sentiment) correctly predicted 5 market reversals

## Engine Groups:
- Trend-following: SMC, PriceAction, NNFX, Quant
- Reversal/Counter-trend: Divergence, Wyckoff, Sentiment
- Contextual: ICT, MarketStructure

## Constraints:
- All weights must be between {MIN_WEIGHT} and {MAX_WEIGHT}
- Sum of all weights (excluding macro=disabled) must equal {TOTAL_WEIGHT}
- No single group should exceed 70% of total weight
- Reversal engines should collectively be 15-30% (they provide H013 protection)

## Task:
Analyze the engine performance data and suggest optimized weights.
Consider:
1. Engines with high agreement + high score = more weight
2. Reversal engines (low agreement but correct when market reverses) = moderate weight for H013
3. Engines with very low contribution = consider reducing to minimum

Respond ONLY with valid JSON in this exact format:
{{
  "suggested_weights": {{
    "smc": 0.XX,
    "price_action": 0.XX,
    "ict": 0.XX,
    "nnfx": 0.XX,
    "quant": 0.XX,
    "wyckoff": 0.XX,
    "divergence": 0.XX,
    "market_structure": 0.XX,
    "sentiment": 0.XX,
    "macro": 0.0
  }},
  "reasoning": {{
    "smc": "brief reason",
    "price_action": "brief reason",
    "ict": "brief reason",
    "nnfx": "brief reason",
    "quant": "brief reason",
    "wyckoff": "brief reason",
    "divergence": "brief reason",
    "market_structure": "brief reason",
    "sentiment": "brief reason"
  }},
  "confidence": "high|medium|low",
  "note": "overall strategy note",
  "requires_more_data": true_or_false
}}"""

    return prompt


def analyze_and_suggest_weights(
    engine_stats: list[dict],
    outcome_summary: dict,
    current_weights: dict[str, float],
    regime_data: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Use Claude API to analyze engine performance and suggest optimized weights.

    Returns dict with suggested_weights, reasoning, and metadata.
    """
    if outcome_summary.get("total_closed", 0) < 20:
        return {
            "status": "insufficient_data",
            "message": f"Need 20+ closed trades. Current: {outcome_summary.get('total_closed', 0)}",
            "suggested_weights": current_weights,
            "requires_more_data": True,
        }

    prompt = _build_analysis_prompt(
        engine_stats, outcome_summary, current_weights, regime_data
    )

    logger.info("Calling Claude API for dynamic weight optimization...")

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        # Extract text response
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        # Parse JSON from response
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        result = json.loads(text)

        # Validate and clamp weights
        weights = result.get("suggested_weights", {})
        validated = {}
        for engine, w in weights.items():
            if engine == "macro":
                validated[engine] = 0.0
            else:
                validated[engine] = max(MIN_WEIGHT, min(MAX_WEIGHT, float(w)))

        # Renormalize to 1.0 (excluding macro)
        active = {k: v for k, v in validated.items() if k != "macro"}
        total = sum(active.values())
        if total > 0:
            active = {k: round(v / total, 4) for k, v in active.items()}
        active["macro"] = 0.0

        result["suggested_weights"] = active
        result["status"] = "success"
        result["trades_analyzed"] = outcome_summary.get("total_closed", 0)

        logger.info(
            f"Dynamic weights AI: {result.get('confidence','?')} confidence. "
            f"Top change: NNFX {current_weights.get('nnfx',0):.3f} → {active.get('nnfx',0):.3f}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON: {e}")
        return {
            "status": "parse_error",
            "message": str(e),
            "suggested_weights": current_weights,
        }
    except Exception as exc:
        logger.error(f"Dynamic weights AI failed: {exc}")
        return {
            "status": "error",
            "message": str(exc),
            "suggested_weights": current_weights,
        }


def apply_weights_to_config(
    suggested: dict[str, float],
    config_path: str = "config.yaml",
    dry_run: bool = True,
) -> bool:
    """Apply suggested weights to config.yaml.

    Args:
        suggested: weight dict from analyze_and_suggest_weights()
        config_path: path to config.yaml
        dry_run: if True, print diff without writing

    Returns:
        True if applied successfully
    """
    import yaml
    from pathlib import Path

    path = Path(config_path)
    cfg = yaml.safe_load(path.read_text())
    current = cfg["confluence"]["weights"]

    print("\nWeight changes:")
    print(f"{'Engine':<18} {'Current':>10} {'Suggested':>10} {'Change':>10}")
    print("-" * 50)
    for engine in sorted(current.keys()):
        cur = current.get(engine, 0)
        sug = suggested.get(engine, cur)
        change = sug - cur
        arrow = "↑" if change > 0.005 else "↓" if change < -0.005 else "→"
        print(f"{engine:<18} {cur:>10.3f} {sug:>10.3f} {arrow}{abs(change):>9.3f}")

    if dry_run:
        print("\n[DRY RUN] Not applied. Set dry_run=False to apply.")
        return False

    cfg["confluence"]["weights"] = {
        k: round(v, 4) for k, v in suggested.items()
    }
    path.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
    print("\n✅ Weights applied to config.yaml")
    return True
