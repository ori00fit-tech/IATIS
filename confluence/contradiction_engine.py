"""
confluence/contradiction_engine.py
--------------------------------------
Checks for hard contradictions that should block a trade even if the
weighted score and vote count look favorable. This is the system's
"no-trade intelligence" layer at the confluence stage (risk_engine.py
implements a second, independent gate on the risk side).

Phase 1 implements the contradiction check that's possible with current
data (engines actively disagreeing). News-based and HTF-weakness
contradiction checks are stubbed pending macro/news feed and proper
multi-timeframe SMC structure (both Phase 3/4 dependencies).
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
    """Run all contradiction checks. Any single trigger blocks the trade."""
    reasons: list[str] = []

    # 1. Direct engine disagreement: any engine actively opposing the
    #    majority bias with a meaningful score is flagged.
    bullish = [o for o in outputs if o.bias == Bias.BULLISH and o.score >= 50]
    bearish = [o for o in outputs if o.bias == Bias.BEARISH and o.score >= 50]

    if bullish and bearish:
        reasons.append(
            f"Active disagreement: {[o.engine_name for o in bullish]} bullish "
            f"vs {[o.engine_name for o in bearish]} bearish, both score>=50"
        )

    # 2. News contradiction (Phase 4 — macro engine not yet implemented,
    #    so this flag will always be False until then; left wired up so
    #    main.py / risk_engine don't need to change later).
    if upcoming_high_impact_news:
        reasons.append("High-impact news event upcoming")

    # TODO (Phase 3): HTF structure weakness check — needs a confirmed
    # multi-timeframe SMC bias (order blocks / BOS-CHOCH), not just the
    # single-timeframe swing structure Phase 1 SMC engine provides.

    return ContradictionResult(blocked=len(reasons) > 0, reasons=reasons)
