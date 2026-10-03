"""
backtest/shadow_outcome_verification.py
---------------------------------------
Hypothesis Discovery Engine -- Historical Outcome Stability, Twelve Data
Historical Targeting Implementation (first-scope: Twelve Data only).

Locked Design Gates this module implements, verbatim:
  - Historical Outcome Stability (OHLC capture + independence definition).
  - Non-Causal Walk Value Stability (#1): capture/verify every actually-
    walked bar, O/H/L/C, not just the causal one.
  - Provider Identity Semantics: observe, never engineer, provider
    sameness; OHLC comparison proceeds regardless of provider match.
  - Historical Targeting scope decision: Twelve Data ONLY this phase. A
    baseline served by any other provider stays NOT_YET_VERIFIABLE --
    deferred follow-on, never a cross-provider substitution, never a
    silent re-target.
  - Timestamp contract: canonical comparison is tz-aware UTC
    pandas.Timestamp equality, never string comparison; a tz defect
    propagates as an exception, never VERIFICATION_DATA_GAP.

MECHANISM (operator's own locked boundary): per-bar-time lookup within
one fetched historical range, never a whole-range set diff. For each
bar actually captured in `walk_ohlc` (backtest.shadow_outcome_resolver's
own controlled additive extension), this module asks only "is there a
bar at exactly this timestamp in the verification retrieval", and if
so, compares its O/H/L/C exactly. Bars the verification retrieval
returns beyond what baseline ever walked are never inspected -- they
carry no meaning here. Whether the full SET of available bars for a
range stays stable over repeated retrievals is Design Gate #2 (walk
COMPOSITION stability) -- explicitly NOT designed, and this module must
never answer that question by accident.

Per-bar states are never rolled up into one decision-level verdict --
that would be a new, unlocked aggregation judgment. `verify_historical_
stability()`'s top-level `ohlc_state` is only ever set to
NOT_YET_VERIFIABLE, and only in the three cases where no per-bar
breakdown is even possible (no baseline, pre-capture legacy
observation, or a baseline provider this phase cannot target). Once an
actual verification attempt runs, the per-bar truth lives entirely in
the returned `bars` list; the caller decides what, if anything, to do
with a mix of states -- this module never decides for them.

NON-NEGOTIABLE (operator's own locked scope boundary): this module
never imports backtest.promotion_gate, backtest.policy_health,
backtest.execution_attribution, execution.authorization,
execution.trade_executor, storage.outcome_tracker, storage.shadow_book,
scheduler.py, or main.py. It never calls resolve_decision_outcome()
(that would re-derive a decision, not check raw data -- the existing
look-ahead constraint). It never computes a threshold, n, p-value,
Bonferroni correction, catastrophic-divergence verdict, or any
governance response to OHLC_UNSTABLE -- all explicitly deferred. It
never fetches via core.data_providers.fetch_with_failover -- Twelve
Data is called directly (core.twelve_data_client.TwelveDataClient),
since this phase's scope is Twelve-Data-only and fetch_with_failover
has no date-ranged capability for any provider. Consequently
DIFFERENT_PROVIDER cannot actually occur in this phase's code (there is
no multi-provider failover at verification time to produce one) -- the
state is defined now, correctly, for a later phase that may add
targeting for other providers; it is honest, reachable vocabulary, not
dead code.

A wholesale-empty historical response (core.twelve_data_client's own
_parse_response raises TwelveDataError when the API returns no "values"
at all) is NOT converted into VERIFICATION_DATA_GAP -- it propagates
unchanged, matching the locked "genuine provider/fetch exception ->
propagate unchanged" rule. VERIFICATION_DATA_GAP is reserved for a
SPECIFIC captured bar_time being absent, malformed, or ambiguous within
an otherwise-successful, non-empty response.

PERSISTENCE: this module performs NO storage read or write of its own
(matching the locked "Schema = NO" status) -- a pure function of its
own arguments plus whatever TwelveDataClient.time_series() returns for
this call.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from backtest.shadow_outcome_resolver import (
    _EVALUATION_HORIZON_HOURS,
    _OUTPUTSIZE_BUFFER_BARS,
    _TIMEFRAME_HOURS,
    _provider_symbol,
)
from core.twelve_data_client import TwelveDataClient

OHLC_STABLE = "OHLC_STABLE"
OHLC_UNSTABLE = "OHLC_UNSTABLE"
NOT_YET_VERIFIABLE = "NOT_YET_VERIFIABLE"
VERIFICATION_DATA_GAP = "VERIFICATION_DATA_GAP"

SAME_PROVIDER = "SAME_PROVIDER"
DIFFERENT_PROVIDER = "DIFFERENT_PROVIDER"
PROVIDER_NOT_AVAILABLE = "PROVIDER_NOT_AVAILABLE"

# The only provider this phase can target (locked Historical Targeting
# scope decision). Matches the "twelve_data" chain-entry string the
# multi-provider failover abstraction uses elsewhere, verbatim -- never
# redefined.
_TWELVE_DATA = "twelve_data"

__all__ = [
    "OHLC_STABLE", "OHLC_UNSTABLE", "NOT_YET_VERIFIABLE", "VERIFICATION_DATA_GAP",
    "SAME_PROVIDER", "DIFFERENT_PROVIDER", "PROVIDER_NOT_AVAILABLE",
    "ShadowOutcomeVerificationError", "verify_historical_stability",
]


class ShadowOutcomeVerificationError(Exception):
    """Structural misuse only -- a missing API key, or a verification
    attempt whose own evaluated_at is not strictly later than the
    original observation's evaluated_at (the locked independence
    ordering requirement). Never raised for an ordinary OHLC_UNSTABLE or
    VERIFICATION_DATA_GAP finding -- those are legitimate results, not
    errors."""


def _now_utc() -> datetime:
    """Wrapped so tests can control verification T_now deterministically
    -- same pattern as backtest.shadow_outcome_resolver's own _now_utc()."""
    return datetime.now(timezone.utc)


def _parse_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _lookup_bar(verification_df: pd.DataFrame, bar_time: pd.Timestamp) -> dict[str, float] | None:
    """Exact-instant lookup only (the locked timestamp contract) -- never
    a tolerance window, never a nearest match. Returns None if the exact
    timestamp is absent, or if present but unusable (non-finite) or
    ambiguous (a duplicate timestamp resolving to more than one row) --
    all three collapse to the same caller-visible VERIFICATION_DATA_GAP,
    exactly as locked. (core.twelve_data_client's own _parse_response
    already deduplicates on read, keep-first -- so the duplicate branch
    below is inherited, defensive behavior, not something this module's
    own code can currently trigger; disclosed, not hidden.)"""
    if bar_time not in verification_df.index:
        return None
    matches = verification_df.loc[[bar_time]]
    if len(matches) != 1:
        return None
    row = matches.iloc[0]
    values = {"open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]}
    if not all(_is_finite_number(v) for v in values.values()):
        return None
    return values


def _compare_ohlc(baseline: dict[str, float], verification: dict[str, float]) -> str:
    """Exact equality on all four fields, no tolerance (locked rule).
    This answers only "did the same historical bar report the same raw
    values" -- never whether the resolver's TP/SL decision would have
    changed, which is a different, explicitly separate question."""
    for field in ("open", "high", "low", "close"):
        if baseline[field] != verification[field]:
            return OHLC_UNSTABLE
    return OHLC_STABLE


def verify_historical_stability(
    snapshot: dict[str, Any],
    resolver_result: dict[str, Any],
    *,
    base_config: dict[str, Any],
    api_key: str | None = None,
) -> dict[str, Any]:
    """The SOLE entry point. `snapshot` is the same already-persisted
    decision snapshot `resolve_decision_outcome()` itself takes (for
    `bar_time`=T_D, `symbol`, `timeframe`); `resolver_result` is the
    WHOLE dict `resolve_decision_outcome()` returns (for `walk_ohlc`,
    `provider_at_observation`, `evaluated_at`, `request_id`,
    `hypothesis_id`) -- never a hand-assembled substitute for either.

    Returns a per-bar OHLC verification breakdown plus a single,
    orthogonal provider-identity fact -- NEVER merged into one verdict
    (the locked Provider Identity Semantics rule). Never calls
    resolve_decision_outcome() itself. Never fetches via
    fetch_with_failover -- Twelve Data only, direct, per the locked
    Historical Targeting scope. Performs no storage read or write.
    """
    request_id = resolver_result["request_id"]
    hypothesis_id = resolver_result["hypothesis_id"]
    walk_ohlc = resolver_result.get("walk_ohlc")

    result: dict[str, Any] = {
        "request_id": request_id,
        "hypothesis_id": hypothesis_id,
        "provider_at_observation": resolver_result.get("provider_at_observation"),
        "provider_at_verification": None,
        "provider_state": None,
        "verification_evaluated_at": None,
        "ohlc_state": None,
        "bars": None,
    }

    if "provider_at_observation" not in resolver_result:
        # Baseline predates provider-identity capture -- permanent,
        # never backfilled (the locked PROVIDER_NOT_AVAILABLE scope).
        result["provider_state"] = PROVIDER_NOT_AVAILABLE
        result["ohlc_state"] = NOT_YET_VERIFIABLE
        return result

    provider_at_observation = resolver_result["provider_at_observation"]

    if walk_ohlc is None:
        # TIMEOUT / DATA_GAP / NOT_YET_ASSESSABLE -- no causal walk, no
        # baseline OHLC ever existed to verify. Permanent for this
        # observation (Design Gate #1's own locked scope).
        result["ohlc_state"] = NOT_YET_VERIFIABLE
        return result

    if provider_at_observation != _TWELVE_DATA:
        # This phase's scope is Twelve Data only (locked Historical
        # Targeting decision) -- never a cross-provider substitution,
        # never a silent re-target onto a different provider. Temporary:
        # deferred follow-on coverage, not permanently impossible.
        result["ohlc_state"] = NOT_YET_VERIFIABLE
        return result

    t_now = _now_utc()
    original_evaluated_at = resolver_result.get("evaluated_at")
    if original_evaluated_at is not None:
        original_dt = _parse_utc(original_evaluated_at)
        if t_now <= original_dt:
            raise ShadowOutcomeVerificationError(
                f"verify_historical_stability: verification time ({t_now.isoformat()}) is not "
                f"strictly later than the original observation's evaluated_at "
                f"({original_dt.isoformat()}) -- the locked independence ordering requirement "
                f"is not satisfied."
            )

    resolved_key = api_key if api_key is not None else os.environ.get("TWELVE_DATA_API_KEY", "")
    if not resolved_key:
        raise ShadowOutcomeVerificationError(
            "verify_historical_stability: TWELVE_DATA_API_KEY not set -- structural misuse, "
            "never converted into VERIFICATION_DATA_GAP."
        )

    timeframe = snapshot["timeframe"]
    if timeframe not in _TIMEFRAME_HOURS:
        raise ShadowOutcomeVerificationError(
            f"verify_historical_stability: unsupported timeframe {timeframe!r} "
            f"(supported: {sorted(_TIMEFRAME_HOURS)})."
        )
    interval_hours = _TIMEFRAME_HOURS[timeframe]

    bar_times = [pd.Timestamp(bar["bar_time"]) for bar in walk_ohlc]
    start_dt = _parse_utc(snapshot["bar_time"])  # T_D, exactly as locked
    end_dt = min(t_now, start_dt + timedelta(hours=_EVALUATION_HORIZON_HOURS))
    # A small, fixed safety margin on the request's end boundary only
    # (never on the comparison itself) so the last walked bar's own
    # exact timestamp is never excluded by an inclusive/exclusive
    # boundary ambiguity on the provider side -- an implementation
    # detail only, mirroring the resolver's own _OUTPUTSIZE_BUFFER_BARS
    # precedent; it never loosens the exact-equality lookup in
    # _lookup_bar().
    end_dt = max(end_dt, max(bar_times).to_pydatetime() + timedelta(hours=interval_hours))

    hours_span = max((end_dt - start_dt).total_seconds() / 3600.0, 0.0)
    bars_needed = math.ceil(hours_span / interval_hours) if hours_span > 0 else 0
    outputsize = bars_needed + _OUTPUTSIZE_BUFFER_BARS

    provider_symbol = _provider_symbol(snapshot["symbol"], base_config)
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    client = TwelveDataClient(api_key=resolved_key)
    verification_df = client.time_series(
        provider_symbol, timeframe, outputsize=outputsize, use_cache=False,
        start_date=start_str, end_date=end_str,
    )
    verification_evaluated_at = _now_utc()

    result["provider_at_verification"] = _TWELVE_DATA
    result["provider_state"] = (
        SAME_PROVIDER if provider_at_observation == _TWELVE_DATA else DIFFERENT_PROVIDER
    )
    result["verification_evaluated_at"] = verification_evaluated_at.isoformat()

    bars_result = []
    for bar in walk_ohlc:
        bar_time = pd.Timestamp(bar["bar_time"])
        baseline = {k: bar[k] for k in ("open", "high", "low", "close")}
        verification = _lookup_bar(verification_df, bar_time)
        state = VERIFICATION_DATA_GAP if verification is None else _compare_ohlc(baseline, verification)
        bars_result.append({
            "bar_time": bar["bar_time"],
            "state": state,
            "baseline": baseline,
            "verification": verification,
        })
    result["bars"] = bars_result

    return result
