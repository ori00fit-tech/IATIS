"""
confluence/indicator_filters.py
--------------------------------
Backtesting Lab Pro Phase D (2026-07-27) — indicator-based FILTERS for
ad-hoc research runs, never independent signal generators.

Non-negotiable constraint (explicit operator requirement): an indicator
here may only (a) veto an otherwise-valid EXECUTE decision the engine
vote already produced ("entry_filter"), (b) nudge adjusted_score up or
down ("confirmation"), or (c) contribute a weighted nudge to
adjusted_score ("score_weight"). Nothing in this module ever sets a
direction/bias — that always comes from confluence.voting_system's
vote, upstream of every call here. This mirrors confluence.
reversal_veto.check_reversal_veto() (local veto flag, ANDed into the
caller's own `ok`) and confluence.mtf_confirmation.check_mtf_confirmation
(additive score_adjustment, clamped by the caller) exactly — no new
decision pattern is introduced.

Indicator math below is independently written (not imported from
engines/*_engine.py's private helpers) so this filter layer's behavior
can never silently drift if an engine author tunes its own indicator
for engine-specific reasons — each formula is verified to match the
engine it mirrors (see per-function docstring), but ownership stays
local to this module. `atr` is the one exception: reused directly from
utils/indicators.py, this codebase's own canonical, public,
cross-module indicator (regimes/volatility_classifier.py already
imports it the same way).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

INDICATOR_KEYS: tuple[str, ...] = ("rsi", "macd", "ema", "adx", "atr")
FILTER_MODES: tuple[str, ...] = ("disabled", "entry_filter", "confirmation", "score_weight")

# Magnitudes matched to confluence/mtf_confirmation.py's own constants
# (MTF_CONFIRM_BONUS/MTF_COUNTER_PENALTY = 8.0) — same order of
# influence as the existing precedent, not a new arbitrary scale.
CONFIRM_BONUS: float = 5.0
COUNTER_PENALTY: float = 5.0
SCORE_WEIGHT_MAX: float = 10.0  # weight=100 -> full +/-10 contribution


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """SMA-smoothed RSI — mirrors engines/price_action_engine.py:_rsi."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series]:
    """Mirrors engines/divergence_engine.py:_macd."""
    macd_line = series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _ema(series: pd.Series, period: int) -> pd.Series:
    """Mirrors engines/nnfx_engine.py:_ema."""
    return series.ewm(span=period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Mirrors engines/nnfx_engine.py:_adx."""
    from utils.indicators import true_range

    high, low = df["high"], df["low"]
    tr = true_range(df)

    dm_plus = (high - high.shift()).clip(lower=0)
    dm_minus = (low.shift() - low).clip(lower=0)
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

    atr_ = tr.rolling(period).mean()
    di_plus = 100 * dm_plus.rolling(period).mean() / atr_.replace(0, np.nan)
    di_minus = 100 * dm_minus.rolling(period).mean() / atr_.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return dx.rolling(period).mean()


@dataclass
class IndicatorSpec:
    """One indicator's ad-hoc filter configuration for a single run.

    name: one of INDICATOR_KEYS.
    mode: one of FILTER_MODES. "disabled" is inert (evaluate_indicator_
        filters skips it entirely).
    params: indicator-specific numeric parameters (e.g. {"period": 14,
        "buy_above": 55, "sell_below": 45} for rsi). Missing keys fall
        back to the per-indicator defaults in _indicator_value_and_alignment.
    weight: only meaningful for mode == "score_weight", 0-100.
    """
    name: str
    mode: str
    params: dict = field(default_factory=dict)
    weight: float = 0.0


@dataclass
class IndicatorFilterResult:
    score_adjustment: float = 0.0
    vetoed: bool = False
    veto_indicator: str | None = None
    # name -> {"mode", "value", "aligned", "contribution"} — every
    # non-disabled indicator that was evaluated, regardless of whether
    # it vetoed or contributed a score nudge. Used for per-trade
    # reporting (Interactive Chart decision panel) and for computing
    # per-indicator rejection counts at the call site.
    per_indicator: dict = field(default_factory=dict)


def _indicator_value_and_alignment(
    window: pd.DataFrame, direction: str, spec: IndicatorSpec,
) -> tuple[float | None, bool]:
    """Returns (latest value or None if NaN/insufficient data, aligned).

    'aligned' means: this indicator's own rule agrees with `direction`
    (rsi/macd/ema are directional checks) or the market is currently
    usable per this indicator (adx/atr are direction-agnostic strength/
    volatility gates). NaN (insufficient warmup within `window`) always
    yields aligned=False — a filter that can't be computed must never
    silently pass a trade through.
    """
    close = window["close"]
    p = spec.params
    if spec.name == "rsi":
        period = int(p.get("period", 14))
        value = _rsi(close, period).iloc[-1]
        if pd.isna(value):
            return None, False
        buy_above = float(p.get("buy_above", 55))
        sell_below = float(p.get("sell_below", 45))
        aligned = (direction == "BULLISH" and value > buy_above) or (
            direction == "BEARISH" and value < sell_below
        )
        return float(value), aligned
    if spec.name == "macd":
        fast, slow, signal = int(p.get("fast", 12)), int(p.get("slow", 26)), int(p.get("signal", 9))
        macd_line, signal_line = _macd(close, fast, slow, signal)
        hist = macd_line.iloc[-1] - signal_line.iloc[-1]
        if pd.isna(hist):
            return None, False
        aligned = (direction == "BULLISH" and hist > 0) or (direction == "BEARISH" and hist < 0)
        return float(hist), aligned
    if spec.name == "ema":
        period = int(p.get("period", 50))
        value = _ema(close, period).iloc[-1]
        if pd.isna(value):
            return None, False
        last_close = float(close.iloc[-1])
        aligned = (direction == "BULLISH" and last_close > value) or (
            direction == "BEARISH" and last_close < value
        )
        return float(value), aligned
    if spec.name == "adx":
        period = int(p.get("period", 14))
        value = _adx(window, period).iloc[-1]
        if pd.isna(value):
            return None, False
        aligned = value >= float(p.get("min_trend", 20))
        return float(value), aligned
    if spec.name == "atr":
        from utils.indicators import atr as compute_atr

        period = int(p.get("period", 14))
        value = compute_atr(window, period=period).iloc[-1]
        if pd.isna(value):
            return None, False
        lo, hi = float(p.get("min_atr", 0.0)), float(p.get("max_atr", 1e12))
        return float(value), lo <= value <= hi
    raise ValueError(f"Unknown indicator {spec.name!r}")


def evaluate_indicator_filters(
    window: pd.DataFrame, direction: str, specs: list[IndicatorSpec],
) -> IndicatorFilterResult:
    """Evaluate every non-disabled spec against `window`'s latest bar.

    Every entry_filter-mode indicator is evaluated independently — the
    first one that fails to align vetoes the trade (no AND/OR/NOT
    combination logic in this slice, matching how check_mtf_confirmation
    / check_reversal_veto already compose independently at their own
    call sites rather than through a shared boolean-expression engine).
    confirmation/score_weight contributions are summed; the caller
    clamps the resulting adjusted_score to [0, 100], same as the
    existing MTF confirmation step.
    """
    result = IndicatorFilterResult()
    for spec in specs:
        if spec.mode == "disabled":
            continue
        value, aligned = _indicator_value_and_alignment(window, direction, spec)
        aligned = bool(aligned)  # numpy bool_ from a pandas comparison is not JSON-serializable
        contribution = 0.0
        if spec.mode == "entry_filter":
            if not aligned and not result.vetoed:
                result.vetoed = True
                result.veto_indicator = spec.name
        elif spec.mode == "confirmation" and value is not None:
            contribution = CONFIRM_BONUS if aligned else -COUNTER_PENALTY
            result.score_adjustment += contribution
        elif spec.mode == "score_weight" and value is not None:
            contribution = (spec.weight / 100.0) * SCORE_WEIGHT_MAX * (1.0 if aligned else -1.0)
            result.score_adjustment += contribution
        result.per_indicator[spec.name] = {
            "mode": spec.mode, "value": value, "aligned": aligned, "contribution": round(contribution, 2),
        }
    return result


def parse_indicators_json(raw: str) -> tuple[dict, ...]:
    """Shared CLI parsing/validation for --indicators-json, used
    identically by backtest/runner.py, robustness.py, and
    walk_forward.py's main(). Raises ValueError on any malformed entry
    so each caller can surface it via argparse's own parser.error()."""
    import json

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("--indicators-json must be a JSON array of indicator specs.")
    for spec in parsed:
        if not isinstance(spec, dict) or spec.get("name") not in INDICATOR_KEYS or spec.get("mode") not in FILTER_MODES:
            raise ValueError(f"invalid indicator spec: {spec!r}")
    return tuple(parsed)
