"""
backtest/shadow_outcome_resolver.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15D — SHADOW Decision Outcome Resolver.

Implements the locked Outcome Resolution Contract (docs/
PHASE_15D_SHADOW_OUTCOME_DIVERGENCE_EVIDENCE.md, as refined by the
operator's own later chronological gap-before-hit correction) over an
already-persisted decision snapshot (Phase 15D's own Snapshot Capture
Foundation, backtest/shadow_decision_snapshot.py + storage/
shadow_decision_snapshot.py):

    snapshot (already captured, already persisted)
        -> fetch future OHLCV bars (core.data_providers.fetch_with_failover,
           the SAME shared, reusable utility every live/shadow run already
           uses -- never main.run_pipeline(), never scheduler.py)
        -> filter to bar_time > T_D (strict; T_D itself is never evidence)
        -> sort ascending
        -> walk chronologically: a DATA_GAP found before any later hit
           terminates evaluation immediately; a hit found before any gap
           is final and never re-examined against later data
        -> exactly one of TP_HIT / SL_HIT / TIMEOUT / DATA_GAP /
           NOT_YET_ASSESSABLE

NON-NEGOTIABLE (operator's own locked scope boundary for this Design
Gate): this module is a PURE, stateless function -- it performs NO
storage read or write of its own (the caller already has the snapshot
dict in hand; nothing here persists its own result). It never imports
backtest.promotion_gate, backtest.policy_health, backtest.
execution_attribution, execution.authorization, execution.trade_executor,
storage.outcome_tracker, storage.shadow_book, scheduler.py, or main.py.
It never computes or returns `diverged_catastrophically` -- that
translation is explicitly out of scope, deferred to a separate, future
Design Gate (pre-registration doc §11).

`captured_at` (the snapshot's own wall-clock persistence time) is NEVER
read anywhere in this module -- only `bar_time` (T_D) is ever used as the
decision's own temporal anchor, per the pre-registration doc's own locked
invariant.

A provider fetch failure (core.data_providers.DataFetchError) is NEVER
caught or converted into DATA_GAP -- it is a genuine operational problem,
structurally distinct from "the market itself has no data", and
propagates to the caller completely unchanged.

resolved_bar_time has exactly ONE meaning across every outcome: the
bar_time of the bar that resolved this decision. It is non-None if and
only if the outcome is TP_HIT or SL_HIT; it is None for TIMEOUT,
DATA_GAP, and NOT_YET_ASSESSABLE alike -- none of those three have a
single causal bar, so none of them are given one.

CONTROLLED ADDITIVE EXTENSION (operator's own locked Historical Outcome
Stability + Non-Causal Walk Value Stability Design Gates): two new
return keys, `provider_at_observation` and `walk_ohlc`, widen this
module's return shape without changing any existing key's value or
meaning, without touching hit detection, and without touching
resolved_bar_time semantics.

`provider_at_observation` is the provider name actually returned by
`fetch_with_failover()` for this call -- always present, never
fabricated (it is the loop variable bound at the exact successful
iteration, per `core.data_providers.fetch_with_failover`'s own
contract), captured so a later, independent verification can observe
(never engineer) whether it lands on the same provider.

`walk_ohlc` is the open/high/low/close of EVERY bar this call actually
read during the walk (not just the causal one) -- captured iff the
outcome is TP_HIT or SL_HIT (the only two outcomes with an actual
causal walk to capture), else None. Every captured bar's four fields
are validated finite at capture time; a non-finite value anywhere
(possible even on an otherwise-valid hit, since only the specific field
that triggered the hit is guaranteed non-NaN by
`_resolve_sl_before_tp`'s own comparison semantics) raises
ShadowOutcomeResolverError -- a structural capture error, never
silently converted into DATA_GAP or any other outcome.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from core.data_providers import fetch_with_failover

TP_HIT = "TP_HIT"
SL_HIT = "SL_HIT"
TIMEOUT = "TIMEOUT"
DATA_GAP = "DATA_GAP"
NOT_YET_ASSESSABLE = "NOT_YET_ASSESSABLE"

# Locked, docs/PHASE_15D_SHADOW_OUTCOME_DIVERGENCE_EVIDENCE.md §5a.
_EVALUATION_HORIZON_HOURS = 168

# A new, explicit constant table (no existing shared one was found by this
# phase's own Gate 0) -- never inferred or estimated from fetched data.
_TIMEFRAME_HOURS: dict[str, int] = {"H1": 1, "H4": 4, "D1": 24}

# A small, fixed safety margin on top of the mathematically-bounded bar
# count -- an implementation detail only (never affects outcome
# semantics, only how much data is requested).
_OUTPUTSIZE_BUFFER_BARS = 5

__all__ = [
    "TP_HIT", "SL_HIT", "TIMEOUT", "DATA_GAP", "NOT_YET_ASSESSABLE",
    "ShadowOutcomeResolverError", "resolve_decision_outcome",
]


class ShadowOutcomeResolverError(Exception):
    """Structural misuse only (an unsupported timeframe, no provider-symbol
    mapping for this snapshot's internal symbol) -- never raised for an
    ordinary outcome, and never raised in place of a provider fetch
    failure (core.data_providers.DataFetchError propagates unchanged,
    see this module's own docstring)."""


def _now_utc() -> datetime:
    """Wrapped so tests can control T_now deterministically -- the
    semantics (datetime.now(timezone.utc), called exactly once per
    resolve_decision_outcome() invocation) are unchanged."""
    return datetime.now(timezone.utc)


def _parse_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _provider_symbol(internal_symbol: str, base_config: dict[str, Any]) -> str:
    """Reads base_config["data"]["twelve_data_symbols"] -- the SAME config
    structure backtest.hypothesis_live_request.build_governed_config()
    already manipulates -- never a separate translator borrowed from
    scripts/*.py or execution/routes/*.py (the latter lives under
    execution/*.py, which this module, like Phase 8C, never imports).
    This is a transient, in-memory lookup only -- it never touches or
    alters the snapshot's own persisted (internal-label) symbol."""
    for entry in base_config.get("data", {}).get("twelve_data_symbols", []):
        if entry.get("internal") == internal_symbol:
            provider_symbol = entry.get("symbol")
            if provider_symbol:
                return provider_symbol
    raise ShadowOutcomeResolverError(
        f"resolve_decision_outcome: no provider symbol mapping found for internal symbol "
        f"{internal_symbol!r} in base_config['data']['twelve_data_symbols']."
    )


def _validate_finite_ohlc_bar(bar: dict[str, Any]) -> None:
    """Capture-time structural validation (operator's own locked rule): a
    TP_HIT/SL_HIT's own walk evidence must be finite, numeric O/H/L/C for
    EVERY bar actually walked -- not just the causal one. A non-finite
    value anywhere is a structural capture error, never silently
    converted into an outcome or a verification state."""
    for field in ("open", "high", "low", "close"):
        value = bar[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ShadowOutcomeResolverError(
                f"resolve_decision_outcome: non-finite/unusable {field}={value!r} captured for "
                f"bar at {bar['bar_time']!r} -- OHLC evidence capture failed structurally, never "
                f"converted into DATA_GAP or any other outcome."
            )


def _resolve_sl_before_tp(snapshot: dict[str, Any], bar_high: float, bar_low: float) -> str | None:
    """The EXACT tie-break convention already shipped in storage.
    outcome_tracker.auto_close_outcomes()/storage.shadow_book.
    auto_close_shadows() -- SL checked first; TP only when SL was not
    touched. Reused verbatim, never re-derived."""
    side, stop_loss, take_profit = snapshot["side"], snapshot["stop_loss"], snapshot["take_profit"]
    if side == "BUY":
        if bar_low <= stop_loss:
            return SL_HIT
        if bar_high >= take_profit:
            return TP_HIT
    elif side == "SELL":
        if bar_high >= stop_loss:
            return SL_HIT
        if bar_low <= take_profit:
            return TP_HIT
    return None


def resolve_decision_outcome(snapshot: dict[str, Any], *, base_config: dict[str, Any]) -> dict[str, Any]:
    """The SOLE entry point. `snapshot` is the full, already-persisted row
    from storage.shadow_decision_snapshot.get_snapshot_by_request_id() (or
    structurally equivalent) -- request_id, hypothesis_id, symbol,
    timeframe, bar_time, side, entry_price, stop_loss, take_profit.
    `base_config` supplies ONLY the twelve_data_symbols mapping (§13 of
    the internal<->provider symbol question) -- no engine/risk/confluence
    field of it is ever read.

    Returns EXACTLY:
        {"request_id": ..., "hypothesis_id": ...,
         "outcome": TP_HIT | SL_HIT | TIMEOUT | DATA_GAP | NOT_YET_ASSESSABLE,
         "resolved_bar_time": <ISO str> | None, "evaluated_at": <ISO str>,
         "provider_at_observation": <str>,
         "walk_ohlc": [{"bar_time": <ISO str>, "open": <float>, "high": <float>,
                        "low": <float>, "close": <float>}, ...] | None}
    (the last two keys are this module's own locked controlled additive
    extension -- see module docstring)

    Performs NO storage read or write -- a pure, stateless, deterministic
    function of its own two arguments plus whatever core.data_providers.
    fetch_with_failover() returns for this call."""
    timeframe = snapshot["timeframe"]
    if timeframe not in _TIMEFRAME_HOURS:
        raise ShadowOutcomeResolverError(
            f"resolve_decision_outcome: unsupported timeframe {timeframe!r} -- no expected_freq is "
            f"defined for it (supported: {sorted(_TIMEFRAME_HOURS)})."
        )
    expected_freq_hours = _TIMEFRAME_HOURS[timeframe]
    expected_freq = timedelta(hours=expected_freq_hours)

    t_d = _parse_utc(snapshot["bar_time"])
    horizon_boundary = t_d + timedelta(hours=_EVALUATION_HORIZON_HOURS)
    t_now = _now_utc()

    provider_symbol = _provider_symbol(snapshot["symbol"], base_config)
    hours_needed = max((min(t_now, horizon_boundary) - t_d).total_seconds() / 3600.0, 0.0)
    bars_needed = math.ceil(hours_needed / expected_freq_hours) if hours_needed > 0 else 0
    outputsize = bars_needed + _OUTPUTSIZE_BUFFER_BARS

    df, provider = fetch_with_failover(symbol=provider_symbol, interval=timeframe, outputsize=outputsize)
    eligible = df[df.index > pd.Timestamp(t_d)].sort_index()

    last_seen_time = t_d
    outcome: str | None = None
    resolved_bar_time: datetime | None = None
    walk_bars: list[dict[str, Any]] = []

    for bar_time, row in eligible.iterrows():
        if (bar_time - last_seen_time) > expected_freq:
            outcome = DATA_GAP
            break
        walk_bars.append({
            "bar_time": bar_time,
            "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
        })
        hit = _resolve_sl_before_tp(snapshot, row["high"], row["low"])
        if hit is not None:
            outcome = hit
            resolved_bar_time = bar_time
            break
        last_seen_time = bar_time

    if outcome is None:
        if last_seen_time >= horizon_boundary:
            outcome = TIMEOUT
        elif t_now >= horizon_boundary:
            outcome = DATA_GAP
        else:
            outcome = NOT_YET_ASSESSABLE

    walk_ohlc: list[dict[str, Any]] | None = None
    if outcome in (TP_HIT, SL_HIT):
        for bar in walk_bars:
            _validate_finite_ohlc_bar(bar)
        walk_ohlc = [
            {
                "bar_time": bar["bar_time"].isoformat(),
                "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"],
            }
            for bar in walk_bars
        ]

    return {
        "request_id": snapshot["request_id"],
        "hypothesis_id": snapshot["hypothesis_id"],
        "outcome": outcome,
        "resolved_bar_time": resolved_bar_time.isoformat() if resolved_bar_time is not None else None,
        "evaluated_at": t_now.isoformat(),
        "provider_at_observation": provider,
        "walk_ohlc": walk_ohlc,
    }
