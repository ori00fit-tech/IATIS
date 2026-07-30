"""
confluence/context_filters.py
--------------------------------
AI Research Lab / Mission Center — Context Filters (2026-07-30) —
market-context FILTERS (session, day-of-week, volatility regime, market
regime, trade direction) for ad-hoc research runs, never independent
signal generators.

Same non-negotiable constraint as confluence/indicator_filters.py,
verbatim: a context filter may only (a) veto an otherwise-valid EXECUTE
decision the engine vote already produced ("entry_filter"), (b) nudge
adjusted_score up or down ("confirmation"), or (c) contribute a weighted
nudge to adjusted_score ("score_weight"). Nothing here ever sets a
direction/bias — that always comes from confluence.voting_system's vote,
upstream of every call here. Reuses FILTER_MODES and the exact same
score-magnitude constants from confluence/indicator_filters.py so the two
filter families compose on one consistent scale rather than introducing a
second arbitrary magnitude.

Context detection is NOT reimplemented here — it wraps this repo's own
existing, real classifiers (regimes/session_context.py, regimes/
volatility_classifier.py, regimes/regime_detector.py), the same
"reuse the canonical implementation" principle indicator_filters.py
applies to utils/indicators.atr.

CLAUDE.md dead-list note: a "market_regime" filter set to entry_filter
mode against `["TRENDING"]` only is structurally the same idea as H024's
hard regime gate (NULL result, 2026-07-22 — pooled TEST DPF -0.024,
worse on carriers). This module does not re-litigate that: it is
reachable ONLY through Mission Center's ephemeral, per-run,
never-promoted search space (exactly like indicator filters), never
written to config.yaml/features.regime_gate. A mission finding here is a
LEAD like any other Mission Center output, not a reopening of H024 —
CLAUDE.md rule 1 still requires a fresh pre-registered hypothesis before
anything here could become evidence.

Volume/order-flow filters are deliberately NOT included — already
measured and killed (crypto_volume A/B, ΔPF ≈ 0/-0.016). News/Fear&Greed/
Funding-Rate/Open-Interest/Liquidity/Correlation are deliberately NOT
included either: none of that data is available at decision time inside
this local-OHLCV backtest engine today — adding them would require a new
data pipeline, out of scope for a filter-layer addition.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from confluence.indicator_filters import CONFIRM_BONUS, COUNTER_PENALTY, FILTER_MODES, SCORE_WEIGHT_MAX

CONTEXT_KEYS: tuple[str, ...] = ("session", "day_of_week", "volatility_regime", "market_regime", "direction")

_ALL_SESSIONS = ("Asia", "London", "NewYork", "Overlap")
_ALL_VOLATILITY_LABELS = ("low", "normal", "high", "extreme")
_ALL_REGIME_LABELS = ("TRENDING", "RANGING")
_ALL_DIRECTIONS = ("BULLISH", "BEARISH")


@dataclass
class ContextSpec:
    """One context filter's ad-hoc configuration for a single run.

    name: one of CONTEXT_KEYS.
    mode: one of FILTER_MODES (confluence.indicator_filters). "disabled"
        is inert (evaluate_context_filters skips it entirely).
    params: filter-specific configuration, e.g. {"allowed": ["London",
        "NewYork", "Overlap"]} for session. Missing keys fall back to
        "allow everything" (a no-op default) — see
        _context_value_and_alignment.
    weight: only meaningful for mode == "score_weight", 0-100.
    """
    name: str
    mode: str
    params: dict = field(default_factory=dict)
    weight: float = 0.0


@dataclass
class ContextFilterResult:
    score_adjustment: float = 0.0
    vetoed: bool = False
    veto_context: str | None = None
    # name -> {"mode", "value", "aligned", "contribution"} — every
    # non-disabled context filter that was evaluated, mirroring
    # IndicatorFilterResult.per_indicator's exact shape for consistent
    # per-trade reporting (Interactive Chart decision panel).
    per_context: dict = field(default_factory=dict)


def _context_value_and_alignment(
    window: pd.DataFrame, direction: str, spec: ContextSpec,
) -> tuple[str | int | None, bool]:
    """Returns (latest value or None if insufficient/unknown, aligned).

    Unlike indicator filters, a context filter's own default ("allowed"
    unset) means "allow everything" — filters here gate on WHEN/HOW a
    trade happens, not a directional signal, so an unconfigured filter
    should be a no-op rather than silently vetoing everything.
    """
    p = spec.params
    if spec.name == "session":
        from regimes.session_context import detect_session_from_df

        if len(window) == 0:
            return None, False
        ctx = detect_session_from_df(window)
        allowed = p.get("allowed") or list(_ALL_SESSIONS)
        aligned = ctx.primary_session in allowed or any(s in allowed for s in ctx.active_sessions)
        return ctx.primary_session, aligned

    if spec.name == "day_of_week":
        if len(window) == 0:
            return None, False
        value = int(window.index[-1].dayofweek)  # Monday=0 .. Sunday=6
        allowed_days = p.get("allowed_days")
        allowed_days = [0, 1, 2, 3, 4] if allowed_days is None else list(allowed_days)
        return value, value in allowed_days

    if spec.name == "volatility_regime":
        from regimes.volatility_classifier import classify_volatility

        period = int(p.get("period", 14))
        lookback = int(p.get("lookback", 100))
        if len(window) < period:
            return None, False
        value = classify_volatility(window, period=period, lookback=lookback).iloc[-1]
        if value == "unknown":
            return None, False
        allowed = p.get("allowed") or list(_ALL_VOLATILITY_LABELS)
        return value, value in allowed

    if spec.name == "market_regime":
        from regimes.regime_detector import Regime, detect_regime

        atr_period = int(p.get("atr_period", 14))
        lookback = int(p.get("lookback", 100))
        result = detect_regime(window, atr_period=atr_period, lookback=lookback)
        if result.regime == Regime.UNKNOWN:
            return None, False
        allowed = p.get("allowed") or list(_ALL_REGIME_LABELS)
        return result.regime.value, result.regime.value in allowed

    if spec.name == "direction":
        allowed = p.get("allowed") or list(_ALL_DIRECTIONS)
        return direction, direction in allowed

    raise ValueError(f"Unknown context filter {spec.name!r}")


def evaluate_context_filters(
    window: pd.DataFrame, direction: str, specs: list[ContextSpec],
) -> ContextFilterResult:
    """Evaluate every non-disabled spec against `window`'s latest bar —
    identical composition rules to confluence.indicator_filters.
    evaluate_indicator_filters (first entry_filter failure vetoes;
    confirmation/score_weight contributions are summed; the caller
    clamps adjusted_score to [0, 100])."""
    result = ContextFilterResult()
    for spec in specs:
        if spec.mode == "disabled":
            continue
        value, aligned = _context_value_and_alignment(window, direction, spec)
        aligned = bool(aligned)  # numpy bool_ from a pandas comparison is not JSON-serializable
        contribution = 0.0
        if spec.mode == "entry_filter":
            if not aligned and not result.vetoed:
                result.vetoed = True
                result.veto_context = spec.name
        elif spec.mode == "confirmation" and value is not None:
            contribution = CONFIRM_BONUS if aligned else -COUNTER_PENALTY
            result.score_adjustment += contribution
        elif spec.mode == "score_weight" and value is not None:
            contribution = (spec.weight / 100.0) * SCORE_WEIGHT_MAX * (1.0 if aligned else -1.0)
            result.score_adjustment += contribution
        result.per_context[spec.name] = {
            "mode": spec.mode, "value": value, "aligned": aligned, "contribution": round(contribution, 2),
        }
    return result


def parse_context_filters_json(raw: str) -> tuple[dict, ...]:
    """Shared CLI/API parsing+validation, mirrors confluence.
    indicator_filters.parse_indicators_json exactly."""
    import json

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("context filters must be a JSON array of context filter specs.")
    for spec in parsed:
        if not isinstance(spec, dict) or spec.get("name") not in CONTEXT_KEYS or spec.get("mode") not in FILTER_MODES:
            raise ValueError(f"invalid context filter spec: {spec!r}")
    return tuple(parsed)
