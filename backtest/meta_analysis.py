"""
backtest/meta_analysis.py
--------------------------
AI Research Lab / Mission Center Phase 3 (2026-07-30) — retrospective,
computed-on-read pattern-spotting over an ALREADY-COMPLETED mission's
stored trials. Answers "what do the best trials share?" instead of "what
is the single best trial?" — pure functions only, no D1 access, no new
backtests, operates entirely on rows the caller already fetched via
storage.research_missions.leaderboard() plus the mission's own
MissionSearchSpace (to decode params_json back into human-readable
timeframes/engines/indicators/risk values via backtest.optimizer's
existing resolve_point()).

This is explicitly NOT a robustness/sensitivity sweep (see
backtest/robustness.py for that — a controlled, one-parameter-at-a-time
perturbation around a fixed baseline). A sampler-driven search (tpe/
nsga2/random) concentrates trials wherever looked promising early, so
bin population and the "all trials" baseline here are NOT comparable to
a designed grid — every response carries an explicit, sampler-aware
caveat saying so. Treat this module's output as a LEAD pointing at what
to test properly with backtest/mission_validator.py's dedicated
Cross-Symbol/Walk-Forward/Monte-Carlo/Robustness validation, never as
confirmation by itself.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field, replace
from typing import Any

from backtest.multiple_testing import binomial_sign_test_p_value, classify_significance
from backtest.optimizer import MissionSearchSpace, resolve_point, search_space_has_signal_variation
from backtesting.backtest_engine import ENGINE_KEYS

DEFAULT_TOP_FRACTION = 0.20
MIN_TOP_N = 5
MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS = 20
DEFAULT_N_BINS = 5
_PLATEAU_BAND = 0.20  # relative tolerance vs the best bin's mean objective

# Edge Discovery (2026-07-31) — cross-trial consensus + pooled 3-way
# breakdown + ranked opportunities. "Unknown" (the gate-off fallback
# by_regime/by_session/by_direction_regime_session use) is excluded from
# every one of these constants — it is not a real category and is not a
# legal confluence.context_filters.ContextSpec value, so it must never
# reach a claim or an opportunity candidate.
_DIRECTION_PAIR = ("BUY", "SELL")
_REGIME_PAIR = ("TRENDING", "RANGING")
_SESSION_VALUES = ("Asia", "London", "NewYork", "Overlap")
_MIN_TRIALS_PER_CLAIM = 5
# Fixed family size for Bonferroni correction — 1 direction pair + 1
# regime pair + C(4,2)=6 session pairs, x2 metrics (win_rate, profit_
# factor). Fixed so it never drifts with how many claims happened to
# resolve on a given mission.
_N_CLAIMS_ATTEMPTED = 16
MIN_TRADES_FOR_POOLED_ROW = 10

# Dependence detection (2026-08-02) — an operator-identified, real
# statistical flaw: when a mission's search space only varies risk/cost
# parameters (backtest.optimizer.search_space_has_signal_variation()
# returns False), every trial ran the SAME entry-signal stream, so
# treating each trial as an independent observation for a binomial sign
# test is wrong — Bonferroni correction (a multiple-COMPARISONS fix)
# does not and cannot repair a violated independence assumption; these
# are two separate statistical problems. When detected, every
# ConsensusClaim that would otherwise report a real significance verdict
# is overridden to this label instead, and its p_value/confidence_pct are
# nulled out (computing them under a known-violated assumption would be
# actively misleading, not just optimistic). The Pooled 3-way Breakdown
# has the identical flaw (it sums the same overlapping, non-independent
# trial data) — MetaAnalysisResult.dependence_warning covers both.
DEPENDENT_TRIALS_SIGNIFICANCE = "DEPENDENT_TRIALS_LEAD_ONLY"

_DEPENDENCE_WARNING = (
    "⚠ DEPENDENCE DETECTED — LEAD ONLY: this mission's search space only varied "
    "risk/cost parameters (SL/ATR multiplier, min_rr, sizing, warmup/step bars) across "
    "trials — timeframes, engine set, indicators, and context filters were held fixed. "
    "Every trial therefore evaluated the SAME entry-signal stream (risk params only affect "
    "stop distance and the min_rr admission gate, never which bars generate a candidate "
    "signal), so trials are NOT independent observations. Cross-Trial Consensus p-values "
    "below are nulled out, and the Pooled 3-way Breakdown sums heavily-overlapping, "
    "non-independent trials — read every number here as a single lead about this one "
    "configuration, not confirmed evidence from N separate tests. Insufficient independent "
    "evidence. Bonferroni correction fixes multiple-comparisons risk, not this."
)

_SAMPLER_CAVEAT: dict[str, str] = {
    "grid": (
        "Grid search: each bin/frequency count below corresponds to an "
        "equal number of designed points, so this table is a genuine, "
        "even coverage of the search space."
    ),
    "_default": (
        "Retrospective pattern-spotting over an already-completed, "
        "sampler-driven search — not a controlled sweep. TPE/NSGA2/random "
        "samplers concentrate trials near whatever looked promising early, "
        "so bin population and the 'all trials' baseline below are NOT "
        "comparable to a designed grid. Treat shape/lift as a LEAD for "
        "what to test properly (POST /research/missions/{id}/validate's "
        "dedicated robustness sweep), never as confirmation by itself."
    ),
}


def sampler_caveat(sampler: str) -> str:
    return _SAMPLER_CAVEAT.get(sampler, _SAMPLER_CAVEAT["_default"])


@dataclass(frozen=True)
class EffectiveConfigSummary:
    """Forensic Audit follow-up (2026-08-03) — mission fff9806b90c2's
    "50 trials, 1 effective configuration" report surfaced a real gap:
    the Mission Center had no way to show how many of a mission's trials
    are genuinely distinct EXECUTABLE configurations, only a total trial
    count. This answers that directly, independent of whether the
    degenerate case was caused by a genuine single-hypothesis mission
    (the confirmed root cause for that mission) or would ever be caused
    by something else in the future — the fingerprint doesn't care why
    two trials collapsed, only whether they did."""
    total_complete_trials: int
    unique_effective_configurations: int
    duplicate_trials: int
    # Trade Stream Fingerprint (Mission Center Research Rigor, item 2,
    # 2026-08-06) — a deeper check than the config-fingerprint dedup
    # above: two DIFFERENT effective configurations (e.g. "SMC+H1" vs
    # "SMC+H1+RSI(confirmation)") can still fire the exact same realized
    # sequence of trades, meaning the differing knob never actually
    # changed which trades executed and the two configs are NOT
    # independent evidence, even though they count as 2 "unique
    # configurations" above. All three fields are None (not 0 — never
    # fabricated) when no COMPLETE trial in this mission carries a
    # trade_stream_fingerprint yet (trials recorded before this field
    # existed omit it from their metrics_json).
    unique_trade_streams: int | None
    trade_stream_duplicate_trials: int | None
    distinct_configs_sharing_a_trade_stream: int | None

    def to_dict(self) -> dict:
        return {
            "total_complete_trials": self.total_complete_trials,
            "unique_effective_configurations": self.unique_effective_configurations,
            "duplicate_trials": self.duplicate_trials,
            "unique_trade_streams": self.unique_trade_streams,
            "trade_stream_duplicate_trials": self.trade_stream_duplicate_trials,
            "distinct_configs_sharing_a_trade_stream": self.distinct_configs_sharing_a_trade_stream,
        }


def _effective_config_fingerprint(symbol: str | None, resolved: dict[str, Any]) -> str:
    """A stable key representing the ACTUAL executable configuration a
    trial ran — deliberately excludes trial_id/created_at/seed (per the
    operator's own Invariant 3: 'two trials may only be considered
    distinct if their effective executable configurations differ').
    Indicator/context-filter lists are sorted by their own canonical JSON
    so two functionally-identical bundles listed in a different order
    still fingerprint identically."""
    return json.dumps(
        {
            "symbol": symbol,
            "timeframes": sorted(resolved["timeframes"]),
            "engines": sorted(resolved["engines"]),
            "indicators": sorted(
                (json.dumps(d, sort_keys=True) for d in resolved["indicators"]),
            ),
            "context_filters": sorted(
                (json.dumps(d, sort_keys=True) for d in resolved["context_filters"]),
            ),
            "engine_variants": resolved["engine_variants"],
            "risk_overrides": resolved["risk_overrides"],
        },
        sort_keys=True,
    )


def compute_effective_configuration_summary(
    space: MissionSearchSpace, trials: list[dict[str, Any]],
) -> EffectiveConfigSummary:
    """Pure function over already-fetched leaderboard() rows — mirrors
    this module's own no-D1-access convention. Only COMPLETE trials are
    counted (a PRUNED/FAILED trial never ran a real backtest, so it has
    no 'effective configuration' to compare). Malformed/unparseable
    params_json (should not happen for a real row, but this is read-only
    reporting, never worth crashing a mission-detail page over) is
    silently skipped rather than raising."""
    complete = [t for t in trials if t.get("state") == "COMPLETE"]
    fingerprints: set[str] = set()
    # config fingerprint -> trade stream fingerprint, first occurrence
    # only. Post-dedup (see mission_runner.py's live duplicate-
    # configuration detection), a given config fingerprint should appear
    # at most once among COMPLETE trials going forward; tolerate stale
    # data (pre-dedup resumes) by keeping only the first.
    stream_by_config: dict[str, str] = {}
    trade_streams_seen: set[str] = set()
    trials_with_stream = 0
    for t in complete:
        try:
            raw_params = json.loads(t["params_json"])
            resolved = resolve_point(space, raw_params)
        except (TypeError, ValueError, KeyError):
            continue
        cfp = _effective_config_fingerprint(t.get("symbol"), resolved)
        fingerprints.add(cfp)

        stream_fp = None
        metrics_raw = t.get("metrics_json")
        if metrics_raw:
            try:
                stream_fp = json.loads(metrics_raw).get("trade_stream_fingerprint")
            except (TypeError, ValueError):
                stream_fp = None
        if stream_fp:
            trials_with_stream += 1
            trade_streams_seen.add(stream_fp)
            stream_by_config.setdefault(cfp, stream_fp)

    total = len(complete)
    unique = len(fingerprints)

    unique_trade_streams = None
    trade_stream_duplicate_trials = None
    distinct_configs_sharing_a_trade_stream = None
    if trials_with_stream > 0:
        unique_trade_streams = len(trade_streams_seen)
        trade_stream_duplicate_trials = max(0, trials_with_stream - unique_trade_streams)
        stream_config_counts: dict[str, int] = {}
        for s in stream_by_config.values():
            stream_config_counts[s] = stream_config_counts.get(s, 0) + 1
        distinct_configs_sharing_a_trade_stream = sum(
            n for n in stream_config_counts.values() if n > 1
        )

    return EffectiveConfigSummary(
        total_complete_trials=total,
        unique_effective_configurations=unique,
        duplicate_trials=max(0, total - unique),
        unique_trade_streams=unique_trade_streams,
        trade_stream_duplicate_trials=trade_stream_duplicate_trials,
        distinct_configs_sharing_a_trade_stream=distinct_configs_sharing_a_trade_stream,
    )


# ── Research Accounting (Mission Center Research Rigor, item 3, 2026-08-06) ──
# "150 trials != 150 pieces of evidence" — composes numbers already computed
# elsewhere (mission_progress's per-state counts, count_orphaned_attempts's
# lost-attempt count, EffectiveConfigSummary's config/trade-stream dedup)
# into one answer, instead of leaving an operator to mentally combine three
# separate widgets. Pure function — no D1 access, mirrors this module's own
# "route validates, module computes" split.

@dataclass(frozen=True)
class ResearchAccountingSummary:
    requested_trials: int
    recorded_attempts: int
    abandoned_attempts: int
    completed: int
    pruned: int
    failed: int
    duplicate: int
    unique_effective_configurations: int | None
    unique_trade_streams: int | None
    # The best available "how much independent evidence do we actually
    # have" number — unique_trade_streams when available (the deepest,
    # most honest count), falling back to unique_effective_configurations
    # (config-level dedup only) for missions that predate trade-stream
    # fingerprinting, and None (never fabricated) when neither is
    # available yet (e.g. zero COMPLETE trials so far).
    effective_evidence_count: int | None
    effective_evidence_source: str  # "trade_stream" | "config" | "unavailable"

    def to_dict(self) -> dict:
        return {
            "requested_trials": self.requested_trials,
            "recorded_attempts": self.recorded_attempts,
            "abandoned_attempts": self.abandoned_attempts,
            "completed": self.completed, "pruned": self.pruned,
            "failed": self.failed, "duplicate": self.duplicate,
            "unique_effective_configurations": self.unique_effective_configurations,
            "unique_trade_streams": self.unique_trade_streams,
            "effective_evidence_count": self.effective_evidence_count,
            "effective_evidence_source": self.effective_evidence_source,
        }


def compute_research_accounting(
    n_trials_per_symbol: int,
    n_symbols: int,
    progress_by_symbol: dict[str, dict[str, int]],
    abandoned_attempts: int,
    effective_config_summary: dict[str, Any] | None,
) -> ResearchAccountingSummary:
    state_counts: dict[str, int] = {}
    for sym_counts in progress_by_symbol.values():
        for state, n in sym_counts.items():
            state_counts[state] = state_counts.get(state, 0) + n
    completed = state_counts.get("COMPLETE", 0)
    pruned = state_counts.get("PRUNED", 0)
    failed = state_counts.get("FAIL", 0)
    duplicate = state_counts.get("DUPLICATE", 0)

    unique_configs = None
    unique_streams = None
    if effective_config_summary is not None:
        unique_configs = effective_config_summary.get("unique_effective_configurations")
        unique_streams = effective_config_summary.get("unique_trade_streams")

    if unique_streams is not None:
        effective_evidence_count, effective_evidence_source = unique_streams, "trade_stream"
    elif unique_configs is not None:
        effective_evidence_count, effective_evidence_source = unique_configs, "config"
    else:
        effective_evidence_count, effective_evidence_source = None, "unavailable"

    return ResearchAccountingSummary(
        requested_trials=n_trials_per_symbol * n_symbols,
        recorded_attempts=completed + pruned + failed + duplicate,
        abandoned_attempts=abandoned_attempts,
        completed=completed, pruned=pruned, failed=failed, duplicate=duplicate,
        unique_effective_configurations=unique_configs,
        unique_trade_streams=unique_streams,
        effective_evidence_count=effective_evidence_count,
        effective_evidence_source=effective_evidence_source,
    )


@dataclass(frozen=True)
class DimensionFrequency:
    dimension: str  # "engine" | "timeframe"
    value: str
    top_count: int
    top_fraction: float
    all_count: int
    all_fraction: float
    lift: float | None  # top_fraction / all_fraction; None if all_fraction == 0

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "value": self.value,
            "top_count": self.top_count, "top_fraction": round(self.top_fraction, 4),
            "all_count": self.all_count, "all_fraction": round(self.all_fraction, 4),
            "lift": round(self.lift, 3) if self.lift is not None else None,
        }


@dataclass(frozen=True)
class ConsensusBin:
    bin_lo: float
    bin_hi: float
    n_trials: int
    mean_objective: float | None
    mean_trades: float | None

    def to_dict(self) -> dict:
        return {
            "bin_lo": round(self.bin_lo, 6), "bin_hi": round(self.bin_hi, 6),
            "n_trials": self.n_trials,
            "mean_objective": round(self.mean_objective, 4) if self.mean_objective is not None else None,
            "mean_trades": round(self.mean_trades, 1) if self.mean_trades is not None else None,
        }


@dataclass(frozen=True)
class ConsensusBand:
    risk_param: str
    bins: list[ConsensusBin]
    shape: str  # PLATEAU | SPIKE | INCONCLUSIVE

    def to_dict(self) -> dict:
        return {
            "risk_param": self.risk_param, "shape": self.shape,
            "bins": [b.to_dict() for b in self.bins],
        }


@dataclass(frozen=True)
class ConsensusClaim:
    """A real, computed cross-trial claim, e.g. 'BUY win_rate exceeded
    SELL win_rate in 43 of 50 trials' — an exact binomial sign test over
    trial-level outcomes (backtest.multiple_testing.
    binomial_sign_test_p_value), NOT a narrative. Always emitted, even
    below _MIN_TRIALS_PER_CLAIM (significance="INSUFFICIENT_DATA" then),
    matching ConsensusBand's own "always emit, mark why unusable"
    convention."""
    dimension: str          # "direction" | "regime" | "session"
    metric: str              # "win_rate" | "profit_factor"
    dominant_value: str
    other_value: str
    n_trials_compared: int   # ties excluded
    trials_favor_dominant: int
    fraction_favor_dominant: float
    p_value: float | None
    confidence_pct: float | None
    # INSUFFICIENT_DATA | SURVIVES_CORRECTION | NOMINAL_ONLY | NOT_SIGNIFICANT |
    # DEPENDENT_TRIALS_LEAD_ONLY (dependence detection, see module-level comment above)
    significance: str
    claim_text: str

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "metric": self.metric,
            "dominant_value": self.dominant_value, "other_value": self.other_value,
            "n_trials_compared": self.n_trials_compared,
            "trials_favor_dominant": self.trials_favor_dominant,
            "fraction_favor_dominant": round(self.fraction_favor_dominant, 4),
            "p_value": round(self.p_value, 6) if self.p_value is not None else None,
            "confidence_pct": self.confidence_pct,
            "significance": self.significance, "claim_text": self.claim_text,
        }


@dataclass(frozen=True)
class PooledBreakdownRow:
    """One row of the pooled 3-way cross (backtest.metrics.BacktestMetrics.
    by_direction_regime_session), summed across ALL completed trials in a
    mission, then optionally re-grouped onto a coarser 1-way/2-way
    subset. `level` names which dimensions are fixed on this row."""
    direction: str | None
    regime: str | None
    session: str | None
    level: str  # e.g. "direction" | "direction+regime" | "direction+regime+session"
    trades: int
    wins: int
    win_rate: float
    pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float  # may be float('inf')

    def to_dict(self) -> dict:
        # Raw floats (including a possible inf profit_factor) returned
        # as-is — sanitized once, uniformly, by backtest.metrics.json_safe
        # at the API route layer (see execution/routes/missions.py),
        # matching this codebase's single-sanitization-point convention.
        return {
            "direction": self.direction, "regime": self.regime, "session": self.session,
            "level": self.level, "trades": self.trades, "wins": self.wins,
            "win_rate": round(self.win_rate, 2), "pnl": round(self.pnl, 2),
            "gross_profit": round(self.gross_profit, 2), "gross_loss": round(self.gross_loss, 2),
            "profit_factor": round(self.profit_factor, 3),
        }


@dataclass(frozen=True)
class OpportunityCandidate:
    """A ranked SUGGESTION only — never auto-selected, never auto-run.
    Ranked by real, honestly-labeled criteria (observed effect size
    weighted by pooled sample size), not a fabricated 'information gain'
    statistic (no Bayesian prior model exists in this codebase to justify
    that term)."""
    direction: str | None
    regime: str | None
    session: str | None
    level: str
    trades: int
    profit_factor: float
    effect_size: float  # abs(profit_factor - 1.0); may be inf
    rank_score: float   # effect_size * trades
    label: str           # e.g. "BUY + RANGING + London"

    def to_dict(self) -> dict:
        # Raw floats (any of which may be inf) returned as-is — see
        # PooledBreakdownRow.to_dict()'s comment: sanitized once,
        # uniformly, by json_safe() at the API route layer.
        return {
            "direction": self.direction, "regime": self.regime, "session": self.session,
            "level": self.level, "trades": self.trades,
            "profit_factor": round(self.profit_factor, 3),
            "effect_size": round(self.effect_size, 4),
            "rank_score": round(self.rank_score, 2),
            "label": self.label,
        }


@dataclass(frozen=True)
class MetaAnalysisResult:
    mission_id: str
    symbol: str | None
    sampler: str
    n_total_trials: int
    n_complete_trials: int
    top_fraction_used: float
    top_n: int
    insufficient_data: bool
    engine_frequencies: list[DimensionFrequency] = field(default_factory=list)
    timeframe_frequencies: list[DimensionFrequency] = field(default_factory=list)
    consensus_bands: list[ConsensusBand] = field(default_factory=list)
    cross_trial_consensus: list[ConsensusClaim] = field(default_factory=list)
    pooled_breakdown: list[PooledBreakdownRow] = field(default_factory=list)
    opportunity_candidates: list[OpportunityCandidate] = field(default_factory=list)
    note: str = ""
    # Dependence detection (2026-08-02) — True when this mission's search
    # space never varied any entry-signal-affecting dimension (see
    # backtest.optimizer.search_space_has_signal_variation), meaning every
    # trial ran the same entry-signal stream and cross_trial_consensus/
    # pooled_breakdown are NOT independent-trial statistics. dependence_
    # warning carries the human-readable explanation; None when not
    # detected (or when insufficient_data already short-circuited before
    # any of these fields were computed).
    dependence_detected: bool = False
    dependence_warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id, "symbol": self.symbol, "sampler": self.sampler,
            "n_total_trials": self.n_total_trials, "n_complete_trials": self.n_complete_trials,
            "top_fraction_used": self.top_fraction_used, "top_n": self.top_n,
            "insufficient_data": self.insufficient_data,
            "engine_frequencies": [f.to_dict() for f in self.engine_frequencies],
            "timeframe_frequencies": [f.to_dict() for f in self.timeframe_frequencies],
            "consensus_bands": [b.to_dict() for b in self.consensus_bands],
            "cross_trial_consensus": [c.to_dict() for c in self.cross_trial_consensus],
            "pooled_breakdown": [r.to_dict() for r in self.pooled_breakdown],
            "opportunity_candidates": [o.to_dict() for o in self.opportunity_candidates],
            "note": self.note,
            "dependence_detected": self.dependence_detected,
            "dependence_warning": self.dependence_warning,
        }


def _frequency(
    dimension: str, value: str, top_resolved: list[dict], all_resolved: list[dict], key: str,
) -> DimensionFrequency:
    top_count = sum(1 for p in top_resolved if value in p[key])
    all_count = sum(1 for p in all_resolved if value in p[key])
    top_fraction = top_count / len(top_resolved) if top_resolved else 0.0
    all_fraction = all_count / len(all_resolved) if all_resolved else 0.0
    lift = (top_fraction / all_fraction) if all_fraction > 0 else None
    return DimensionFrequency(
        dimension=dimension, value=value, top_count=top_count, top_fraction=top_fraction,
        all_count=all_count, all_fraction=all_fraction, lift=lift,
    )


def _consensus_band(
    param: str, complete_rows: list[dict], resolved: list[dict], n_bins: int,
) -> ConsensusBand:
    pairs: list[tuple[float, float, int]] = []  # (value, objective_value, trades)
    for row, point in zip(complete_rows, resolved):
        value = point["risk_overrides"].get(param)
        if value is None:
            continue
        pairs.append((float(value), float(row["objective_value"]), int(row["trades"])))

    if len(pairs) < 3:
        return ConsensusBand(risk_param=param, bins=[], shape="INCONCLUSIVE")

    values = [p[0] for p in pairs]
    lo, hi = min(values), max(values)
    if hi <= lo:
        # A fixed (never actually sampled) param — cannot bin one point.
        return ConsensusBand(risk_param=param, bins=[], shape="INCONCLUSIVE")

    width = (hi - lo) / n_bins
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for value, obj, trades in pairs:
        idx = min(int((value - lo) / width), n_bins - 1)
        buckets[idx].append((obj, trades))

    bins: list[ConsensusBin] = []
    for i, bucket in enumerate(buckets):
        bin_lo, bin_hi = lo + i * width, lo + (i + 1) * width
        if bucket:
            mean_obj = sum(o for o, _ in bucket) / len(bucket)
            mean_trades = sum(t for _, t in bucket) / len(bucket)
        else:
            mean_obj, mean_trades = None, None
        bins.append(ConsensusBin(
            bin_lo=bin_lo, bin_hi=bin_hi, n_trials=len(bucket),
            mean_objective=mean_obj, mean_trades=mean_trades,
        ))

    populated = [(i, b) for i, b in enumerate(bins) if b.n_trials > 0]
    if len(populated) < 3:
        return ConsensusBand(risk_param=param, bins=bins, shape="INCONCLUSIVE")

    best_idx, best_bin = max(populated, key=lambda ib: ib[1].mean_objective)
    tolerance = abs(best_bin.mean_objective) * _PLATEAU_BAND
    shape = "SPIKE"
    for i, b in populated:
        if abs(i - best_idx) != 1:
            continue
        if abs(b.mean_objective - best_bin.mean_objective) <= tolerance:
            shape = "PLATEAU"
            break

    return ConsensusBand(risk_param=param, bins=bins, shape=shape)


def _compare_dimension_pair(
    complete_rows: list[dict], dimension: str, bucket_field: str,
    value_a: str, value_b: str, metric: str,
) -> ConsensusClaim:
    """Counts, across ALL complete trials, how many favor value_a vs.
    value_b on `metric` within `bucket_field` (e.g. by_direction's
    'win_rate') — ties and trials missing either bucket excluded. A
    profit_factor comparison additionally skips any trial recorded
    before that key existed on the bucket (degrade gracefully, never
    fabricate a value)."""
    favor_a = 0
    favor_b = 0
    for row in complete_rows:
        try:
            metrics = json.loads(row.get("metrics_json") or "{}")
        except (TypeError, ValueError):
            continue
        buckets = metrics.get(bucket_field) or {}
        bucket_a, bucket_b = buckets.get(value_a), buckets.get(value_b)
        if not isinstance(bucket_a, dict) or not isinstance(bucket_b, dict):
            continue
        if bucket_a.get("trades", 0) <= 0 or bucket_b.get("trades", 0) <= 0:
            continue
        if metric not in bucket_a or metric not in bucket_b:
            continue
        val_a, val_b = bucket_a[metric], bucket_b[metric]
        if val_a == val_b:
            continue
        if val_a > val_b:
            favor_a += 1
        else:
            favor_b += 1

    n = favor_a + favor_b
    dominant_value, other_value = (value_a, value_b) if favor_a >= favor_b else (value_b, value_a)
    trials_favor_dominant = max(favor_a, favor_b)
    fraction = (trials_favor_dominant / n) if n > 0 else 0.0

    if n < _MIN_TRIALS_PER_CLAIM:
        return ConsensusClaim(
            dimension=dimension, metric=metric, dominant_value=dominant_value, other_value=other_value,
            n_trials_compared=n, trials_favor_dominant=trials_favor_dominant,
            fraction_favor_dominant=fraction, p_value=None, confidence_pct=None,
            significance="INSUFFICIENT_DATA",
            claim_text=(
                f"{dimension} {metric}: only {n} trial(s) had both '{value_a}' and '{value_b}' "
                f"comparable — need at least {_MIN_TRIALS_PER_CLAIM} before this claim means anything."
            ),
        )

    p = binomial_sign_test_p_value(trials_favor_dominant, n)
    significance = classify_significance(p, _N_CLAIMS_ATTEMPTED)
    confidence_pct = round((1 - p) * 100, 1) if p is not None else None
    claim_text = (
        f"Observed in {trials_favor_dominant}/{n} trials: {dominant_value} {metric} exceeded "
        f"{other_value} {metric}. p={p:.4f} ({significance}), ~{confidence_pct}% confidence."
        if p is not None else
        f"Observed in {trials_favor_dominant}/{n} trials: {dominant_value} {metric} exceeded {other_value} {metric}."
    )
    return ConsensusClaim(
        dimension=dimension, metric=metric, dominant_value=dominant_value, other_value=other_value,
        n_trials_compared=n, trials_favor_dominant=trials_favor_dominant,
        fraction_favor_dominant=fraction, p_value=p, confidence_pct=confidence_pct,
        significance=significance, claim_text=claim_text,
    )


def _cross_trial_consensus(complete_rows: list[dict]) -> list[ConsensusClaim]:
    """Real, computed cross-trial consensus claims (e.g. 'BUY win_rate
    exceeded SELL win_rate in 43 of 50 trials') — an exact binomial sign
    test over trial-level outcomes, never AI narrative. Fixed family of
    _N_CLAIMS_ATTEMPTED = 16 comparisons (1 direction pair + 1 regime
    pair + 6 session pairs, x2 metrics) for a stable Bonferroni
    denominator that never drifts with which claims happened to resolve."""
    claims: list[ConsensusClaim] = []
    for metric in ("win_rate", "profit_factor"):
        claims.append(_compare_dimension_pair(
            complete_rows, "direction", "by_direction", _DIRECTION_PAIR[0], _DIRECTION_PAIR[1], metric,
        ))
        claims.append(_compare_dimension_pair(
            complete_rows, "regime", "by_regime", _REGIME_PAIR[0], _REGIME_PAIR[1], metric,
        ))
        for value_a, value_b in itertools.combinations(_SESSION_VALUES, 2):
            claims.append(_compare_dimension_pair(complete_rows, "session", "by_session", value_a, value_b, metric))
    return claims


def _apply_dependence_override(claims: list[ConsensusClaim]) -> list[ConsensusClaim]:
    """Overrides every claim that would otherwise report a real
    significance verdict (SURVIVES_CORRECTION/NOMINAL_ONLY/NOT_SIGNIFICANT)
    to DEPENDENT_TRIALS_SIGNIFICANCE and nulls p_value/confidence_pct —
    called only when search_space_has_signal_variation(space) is False
    (see module-level comment). INSUFFICIENT_DATA claims pass through
    unchanged: they already say nothing, and dependence is irrelevant when
    there isn't enough data to begin with. The raw k/n counts
    (trials_favor_dominant/n_trials_compared/fraction_favor_dominant) are
    real, valid data either way — only the independence-assuming p-value/
    confidence and the significance verdict are unsound and thus
    withheld."""
    overridden: list[ConsensusClaim] = []
    for c in claims:
        if c.significance == "INSUFFICIENT_DATA":
            overridden.append(c)
            continue
        overridden.append(replace(
            c,
            p_value=None,
            confidence_pct=None,
            significance=DEPENDENT_TRIALS_SIGNIFICANCE,
            claim_text=(
                f"Observed in {c.trials_favor_dominant}/{c.n_trials_compared} trials: "
                f"{c.dominant_value} {c.metric} exceeded {c.other_value} {c.metric}. "
                f"DEPENDENCE DETECTED — this mission never varied any entry-signal "
                f"dimension (only risk/cost params), so trials are not independent "
                f"observations; no p-value/confidence claim is valid here."
            ),
        ))
    return overridden


def _pool_breakdown_buckets(complete_rows: list[dict]) -> dict[str, dict]:
    """Sums every complete trial's by_direction_regime_session bucket-
    for-bucket (same compound key) across ALL of them (not just the
    top-N slice — distinct from engine/timeframe frequency's top-vs-all
    split). Trials recorded before this field existed contribute
    nothing to any key — degrade gracefully, never fabricate."""
    pooled: dict[str, dict] = {}
    for row in complete_rows:
        try:
            metrics = json.loads(row.get("metrics_json") or "{}")
        except (TypeError, ValueError):
            continue
        buckets = metrics.get("by_direction_regime_session") or {}
        for key, bucket in buckets.items():
            if not isinstance(bucket, dict):
                continue
            acc = pooled.setdefault(
                key, {"trades": 0, "wins": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
            )
            acc["trades"] += bucket.get("trades", 0)
            acc["wins"] += bucket.get("wins", 0)
            acc["pnl"] += bucket.get("pnl", 0.0)
            acc["gross_profit"] += bucket.get("gross_profit", 0.0)
            acc["gross_loss"] += bucket.get("gross_loss", 0.0)
    return pooled


# Every non-empty subset of {direction, regime, session} — lets all tree
# levels (1-way/2-way/3-way) be derived from the ONE pooled 3-way dict by
# grouping on key parts and summing, rather than needing 7 separate
# BacktestMetrics fields.
_LEVEL_SUBSETS: tuple[tuple[str, ...], ...] = (
    ("direction",), ("regime",), ("session",),
    ("direction", "regime"), ("direction", "session"), ("regime", "session"),
    ("direction", "regime", "session"),
)


def _expand_pooled_levels(pooled_3way: dict[str, dict]) -> list[PooledBreakdownRow]:
    """Re-groups the pooled 3-way dict onto every non-empty dimension
    subset, re-summing and recomputing win_rate/profit_factor per group
    with the same convention backtest.metrics.calculate_metrics uses.
    Bounded, descriptive display data — 'Unknown' stays in here for
    completeness (excluded later, at the opportunity-ranking stage, since
    it is not a legal pre-fill value)."""
    rows: list[PooledBreakdownRow] = []
    for subset in _LEVEL_SUBSETS:
        grouped: dict[tuple[str, ...], dict] = {}
        for key, bucket in pooled_3way.items():
            parts = key.split("|")
            if len(parts) != 3:
                continue
            values_by_dim = dict(zip(("direction", "regime", "session"), parts))
            group_key = tuple(values_by_dim[dim] for dim in subset)
            acc = grouped.setdefault(
                group_key, {"trades": 0, "wins": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
            )
            acc["trades"] += bucket.get("trades", 0)
            acc["wins"] += bucket.get("wins", 0)
            acc["pnl"] += bucket.get("pnl", 0.0)
            acc["gross_profit"] += bucket.get("gross_profit", 0.0)
            acc["gross_loss"] += bucket.get("gross_loss", 0.0)

        for group_key, acc in grouped.items():
            if acc["trades"] <= 0:
                continue
            values = dict(zip(subset, group_key))
            win_rate = acc["wins"] / acc["trades"] * 100
            gp, gl = acc["gross_profit"], acc["gross_loss"]
            pf = (gp / gl) if gl > 0 else float("inf")
            rows.append(PooledBreakdownRow(
                direction=values.get("direction"), regime=values.get("regime"), session=values.get("session"),
                level="+".join(subset), trades=acc["trades"], wins=acc["wins"], win_rate=win_rate,
                pnl=acc["pnl"], gross_profit=gp, gross_loss=gl, profit_factor=pf,
            ))
    return rows


def _rank_opportunities(
    pooled_rows: list[PooledBreakdownRow],
    min_trades: int = MIN_TRADES_FOR_POOLED_ROW,
    top_n: int = 15,
) -> list[OpportunityCandidate]:
    """Ranked SUGGESTIONS ONLY — never auto-selected, never auto-run (the
    caller decides what to do with these; nothing here ever launches a
    mission). Ranked by real, honestly-labeled effect_size x trades, not
    a fabricated 'information gain' statistic. Excludes any row tagged
    'Unknown' in any dimension — not a legal confluence.context_filters.
    ContextSpec value, so it must never reach a pre-fill action."""
    eligible = [
        r for r in pooled_rows
        if r.trades >= min_trades
        and (r.direction is None or r.direction in _DIRECTION_PAIR)
        and (r.regime is None or r.regime in _REGIME_PAIR)
        and (r.session is None or r.session in _SESSION_VALUES)
    ]
    candidates: list[OpportunityCandidate] = []
    for r in eligible:
        effect_size = abs(r.profit_factor - 1.0)
        rank_score = effect_size * r.trades
        label = " + ".join(v for v in (r.direction, r.regime, r.session) if v)
        candidates.append(OpportunityCandidate(
            direction=r.direction, regime=r.regime, session=r.session, level=r.level,
            trades=r.trades, profit_factor=r.profit_factor, effect_size=effect_size,
            rank_score=rank_score, label=label,
        ))
    candidates.sort(key=lambda c: c.rank_score, reverse=True)
    return candidates[:top_n]


def compute_meta_analysis(
    space: MissionSearchSpace,
    trials: list[dict[str, Any]],
    sampler: str,
    mission_id: str,
    symbol: str | None = None,
    top_fraction: float = DEFAULT_TOP_FRACTION,
    min_top_n: int = MIN_TOP_N,
    n_bins: int = DEFAULT_N_BINS,
    min_trades_for_pooled: int = MIN_TRADES_FOR_POOLED_ROW,
) -> MetaAnalysisResult:
    """`trials` = raw storage.research_missions.leaderboard() rows (any
    state). Only COMPLETE trials with a real objective_value contribute —
    PRUNED/FAIL trials have no meaningful metrics to weight by, same
    "insufficient excluded from verdict" convention backtest/robustness.py
    and backtest/walk_forward.py already use."""
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    if not (0.0 < top_fraction <= 1.0):
        raise ValueError("top_fraction must be in (0, 1]")

    complete_rows = [
        t for t in trials if t.get("state") == "COMPLETE" and t.get("objective_value") is not None
    ]
    n_total = len(trials)
    n_complete = len(complete_rows)

    if n_complete < MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS:
        return MetaAnalysisResult(
            mission_id=mission_id, symbol=symbol, sampler=sampler,
            n_total_trials=n_total, n_complete_trials=n_complete,
            top_fraction_used=top_fraction, top_n=0, insufficient_data=True,
            note=(
                f"Only {n_complete} COMPLETE trial(s) — need at least "
                f"{MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS} before frequency/"
                "consensus tables mean anything more than noise."
            ),
        )

    complete_rows = sorted(complete_rows, key=lambda r: r["objective_value"], reverse=True)
    resolved = [resolve_point(space, json.loads(r["params_json"])) for r in complete_rows]

    top_n = min(max(min_top_n, round(n_complete * top_fraction)), n_complete)
    top_resolved = resolved[:top_n]

    engine_freqs = [_frequency("engine", e, top_resolved, resolved, "engines") for e in ENGINE_KEYS]
    # Hypothesis Bundles (2026-07-30): timeframes_choices is vestigial in
    # bundle mode (each bundle carries its own timeframes) — enumerate
    # from the bundles actually used, not the unused flat field, or every
    # timeframe outside the placeholder entry would silently get zero
    # frequency rows.
    if space.hypothesis_bundle_choices:
        all_timeframes = sorted({
            tf for b in space.hypothesis_bundle_choices for tf in b.get("timeframes", [])
        })
    else:
        all_timeframes = sorted({tf for choice in space.timeframes_choices for tf in choice})
    timeframe_freqs = [
        _frequency("timeframe", tf, top_resolved, resolved, "timeframes") for tf in all_timeframes
    ]

    # Grid-search risk params are categorical, not continuous — a binned
    # "consensus band" view is less meaningful for them, so this is
    # deliberately scoped to risk_param_ranges only (see module docstring).
    consensus_bands = [
        _consensus_band(param, complete_rows, resolved, n_bins) for param in space.risk_param_ranges
    ]

    cross_trial_consensus = _cross_trial_consensus(complete_rows)
    pooled_3way = _pool_breakdown_buckets(complete_rows)
    pooled_breakdown = _expand_pooled_levels(pooled_3way)
    opportunity_candidates = _rank_opportunities(pooled_breakdown, min_trades=min_trades_for_pooled)

    # Dependence detection (2026-08-02, see module-level comment): a
    # mission that never varied any entry-signal-affecting dimension ran
    # the identical entry-signal stream on every trial, so the two
    # statistics above cannot be read as independent-trial evidence.
    dependence_detected = not search_space_has_signal_variation(space)
    if dependence_detected:
        cross_trial_consensus = _apply_dependence_override(cross_trial_consensus)

    return MetaAnalysisResult(
        mission_id=mission_id, symbol=symbol, sampler=sampler,
        n_total_trials=n_total, n_complete_trials=n_complete,
        top_fraction_used=top_fraction, top_n=top_n, insufficient_data=False,
        engine_frequencies=engine_freqs, timeframe_frequencies=timeframe_freqs,
        consensus_bands=consensus_bands, cross_trial_consensus=cross_trial_consensus,
        pooled_breakdown=pooled_breakdown, opportunity_candidates=opportunity_candidates,
        note=sampler_caveat(sampler),
        dependence_detected=dependence_detected,
        dependence_warning=_DEPENDENCE_WARNING if dependence_detected else None,
    )
