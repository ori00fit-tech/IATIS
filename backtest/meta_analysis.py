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

import json
from dataclasses import dataclass, field
from typing import Any

from backtest.optimizer import MissionSearchSpace, resolve_point
from backtesting.backtest_engine import ENGINE_KEYS

DEFAULT_TOP_FRACTION = 0.20
MIN_TOP_N = 5
MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS = 20
DEFAULT_N_BINS = 5
_PLATEAU_BAND = 0.20  # relative tolerance vs the best bin's mean objective

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
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id, "symbol": self.symbol, "sampler": self.sampler,
            "n_total_trials": self.n_total_trials, "n_complete_trials": self.n_complete_trials,
            "top_fraction_used": self.top_fraction_used, "top_n": self.top_n,
            "insufficient_data": self.insufficient_data,
            "engine_frequencies": [f.to_dict() for f in self.engine_frequencies],
            "timeframe_frequencies": [f.to_dict() for f in self.timeframe_frequencies],
            "consensus_bands": [b.to_dict() for b in self.consensus_bands],
            "note": self.note,
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


def compute_meta_analysis(
    space: MissionSearchSpace,
    trials: list[dict[str, Any]],
    sampler: str,
    mission_id: str,
    symbol: str | None = None,
    top_fraction: float = DEFAULT_TOP_FRACTION,
    min_top_n: int = MIN_TOP_N,
    n_bins: int = DEFAULT_N_BINS,
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

    return MetaAnalysisResult(
        mission_id=mission_id, symbol=symbol, sampler=sampler,
        n_total_trials=n_total, n_complete_trials=n_complete,
        top_fraction_used=top_fraction, top_n=top_n, insufficient_data=False,
        engine_frequencies=engine_freqs, timeframe_frequencies=timeframe_freqs,
        consensus_bands=consensus_bands, note=sampler_caveat(sampler),
    )
