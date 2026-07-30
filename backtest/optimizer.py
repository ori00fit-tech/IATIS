"""
backtest/optimizer.py
------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — joint,
multi-dimensional sweep space (timeframes x engine-set x indicator-set x
risk params), sampled by Optuna instead of exhaustive grid search.

Generalizes backtest/robustness.py's `_run_point`/`run_param_sweep`,
which sweeps exactly ONE parameter at a time from a fixed baseline
(never a joint grid across params, and never varies timeframes/engines/
indicators per point — those are fixed once per whole sweep there). This
module's `evaluate_point()` is the joint-space equivalent: every
dimension can vary per trial.

Reuses backtesting.backtest_engine.build_engine_config_override() and
run_backtest() exactly as-is — no new evaluation primitive, only a new
way of choosing WHICH points in that same space to evaluate.

backtest.walk_forward.ParameterSelector (a Protocol for train-data-only
BacktestConfig selection during walk-forward windows) is a narrower,
separate interface — it exists but has zero implementations/callers in
this repo. This module's sampler is not that Protocol: it also varies
timeframes/engines/indicators (not just BacktestConfig), and it drives a
single-period backtest per trial (see mission_runner.py's own docstring
for why full walk-forward per trial is out of scope for Phase 1).

Symbol is deliberately an OUTER loop in mission_runner.py (one Optuna
Study per symbol) — never a joint search dimension inside one study, so
the acquisition function is never biased toward whichever symbol yields
cheap wins first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtest.metrics import BacktestMetrics, TradeRecord, calculate_metrics
from backtest.runner import trade_to_record
from backtesting.backtest_engine import (
    ENGINE_KEYS,
    RISK_OVERRIDE_FIELDS,
    BacktestConfig,
    build_engine_config_override,
    run_backtest,
)
from confluence.context_filters import CONTEXT_KEYS
from confluence.indicator_filters import FILTER_MODES, INDICATOR_KEYS
from core.timeframe_sync import SUPPORTED_TIMEFRAMES

OPTIMIZABLE_METRICS: tuple[str, ...] = (
    "profit_factor", "sharpe_ratio", "sortino_ratio", "calmar_ratio",
    "expectancy_r", "sqn", "recovery_factor", "win_rate",
)
SAMPLER_KEYS: tuple[str, ...] = ("grid", "random", "tpe", "nsga2")

_TF_IDX_KEY = "__timeframes_idx"
_ENGINES_IDX_KEY = "__engines_idx"
_INDICATORS_IDX_KEY = "__indicators_idx"
_CONTEXT_IDX_KEY = "__context_idx"
_INTERNAL_KEYS = (_TF_IDX_KEY, _ENGINES_IDX_KEY, _INDICATORS_IDX_KEY, _CONTEXT_IDX_KEY)


def _validate_indicator_spec(spec: dict) -> None:
    if spec.get("name") not in INDICATOR_KEYS:
        raise ValueError(f"unknown indicator {spec.get('name')!r} — choose from {INDICATOR_KEYS}")
    if spec.get("mode") not in FILTER_MODES:
        raise ValueError(f"unknown indicator mode {spec.get('mode')!r} — choose from {FILTER_MODES}")


def _validate_context_spec(spec: dict) -> None:
    if spec.get("name") not in CONTEXT_KEYS:
        raise ValueError(f"unknown context filter {spec.get('name')!r} — choose from {CONTEXT_KEYS}")
    if spec.get("mode") not in FILTER_MODES:
        raise ValueError(f"unknown context filter mode {spec.get('mode')!r} — choose from {FILTER_MODES}")


@dataclass(frozen=True)
class MissionSearchSpace:
    """One joint search space for one mission. Every *_choices tuple must
    have at least one entry (an empty choice list is a config error, not
    an "unset" sentinel — use a single-entry tuple to hold a dimension
    fixed). Exactly one of risk_param_ranges/risk_param_grid may be
    non-empty: GridSampler requires every parameter suggested via
    suggest_categorical with a matching static choice list, incompatible
    with suggest_float — mixing forms would make suggest_point() ambiguous
    about which call to make per key."""

    timeframes_choices: tuple[tuple[str, ...], ...]
    engine_set_choices: tuple[tuple[str, ...], ...]
    indicator_set_choices: tuple[tuple[dict, ...], ...]
    # Context Filters (2026-07-30) — mirrors indicator_set_choices
    # exactly: each choice is a tuple of confluence.context_filters.
    # ContextSpec-shaped dicts (session/day-of-week/volatility-regime/
    # market-regime/direction). Defaults to a single empty choice
    # (no context filter layer) so existing callers that never pass this
    # keep working unchanged.
    context_filter_set_choices: tuple[tuple[dict, ...], ...] = ((),)
    risk_param_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    risk_param_grid: dict[str, tuple[float, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timeframes_choices:
            raise ValueError("timeframes_choices must have at least one entry")
        if not self.engine_set_choices:
            raise ValueError("engine_set_choices must have at least one entry")
        if not self.indicator_set_choices:
            raise ValueError("indicator_set_choices must have at least one entry")
        if not self.context_filter_set_choices:
            raise ValueError("context_filter_set_choices must have at least one entry")
        if self.risk_param_ranges and self.risk_param_grid:
            raise ValueError("supply risk_param_ranges XOR risk_param_grid, not both")

        for tfs in self.timeframes_choices:
            unknown_tf = set(tfs) - set(SUPPORTED_TIMEFRAMES)
            if unknown_tf:
                raise ValueError(f"unknown timeframe(s) {unknown_tf} — choose from {SUPPORTED_TIMEFRAMES}")
        for engines in self.engine_set_choices:
            unknown_eng = set(engines) - set(ENGINE_KEYS)
            if unknown_eng:
                raise ValueError(f"unknown engine(s) {unknown_eng} — choose from {ENGINE_KEYS}")
        for indicator_set in self.indicator_set_choices:
            for spec in indicator_set:
                _validate_indicator_spec(spec)
        for context_set in self.context_filter_set_choices:
            for spec in context_set:
                _validate_context_spec(spec)

        allowed_risk_fields = set(RISK_OVERRIDE_FIELDS)
        for name in {**self.risk_param_ranges, **self.risk_param_grid}:
            if name not in allowed_risk_fields:
                raise ValueError(f"unknown risk param {name!r} — choose from {RISK_OVERRIDE_FIELDS}")

    def grid_size(self) -> int:
        """Cartesian product size — mission_runner.py caps a grid-sampler
        mission's n_trials_per_symbol to this, since re-asking past
        exhaustion just cycles/duplicates points (verified against the
        installed optuna's real GridSampler behavior, not assumed)."""
        n = (
            len(self.timeframes_choices) * len(self.engine_set_choices)
            * len(self.indicator_set_choices) * len(self.context_filter_set_choices)
        )
        for choices in self.risk_param_grid.values():
            n *= len(choices)
        return n

    def grid_search_space_dict(self) -> dict[str, list]:
        """The dict GridSampler's constructor needs — every param name
        that suggest_point()/suggest_categorical() will use, mapped to
        its full static choice list."""
        d: dict[str, list] = {
            _TF_IDX_KEY: list(range(len(self.timeframes_choices))),
            _ENGINES_IDX_KEY: list(range(len(self.engine_set_choices))),
            _INDICATORS_IDX_KEY: list(range(len(self.indicator_set_choices))),
            _CONTEXT_IDX_KEY: list(range(len(self.context_filter_set_choices))),
        }
        d.update({k: list(v) for k, v in self.risk_param_grid.items()})
        return d


def search_space_from_dict(raw: dict[str, Any]) -> MissionSearchSpace:
    """Inverse of the dict a mission's own `search_space_json` stores
    (built by mission_runner.py's `_search_space_dict`). Single source of
    truth for turning that stored dict back into a real MissionSearchSpace
    — used by backtest/mission_validator.py (re-resolving a candidate
    trial's exact point) and execution/routes/missions.py's meta-analysis
    endpoint (Phase 3, 2026-07-30), so both reconstruct the space
    identically rather than each hand-rolling their own tuplify logic."""
    return MissionSearchSpace(
        timeframes_choices=tuple(tuple(c) for c in raw["timeframes_choices"]),
        engine_set_choices=tuple(tuple(c) for c in raw["engine_set_choices"]),
        indicator_set_choices=tuple(tuple(c) for c in raw["indicator_set_choices"]),
        # .get() with a backward-compatible default: a mission created
        # before Context Filters shipped has no such key in its stored
        # search_space_json.
        context_filter_set_choices=tuple(tuple(c) for c in raw.get("context_filter_set_choices", [[]])),
        risk_param_ranges={k: tuple(v) for k, v in raw.get("risk_param_ranges", {}).items()},
        risk_param_grid={k: tuple(v) for k, v in raw.get("risk_param_grid", {}).items()},
    )


def suggest_point(trial: Any, space: MissionSearchSpace, grid_mode: bool) -> dict[str, Any]:
    """Calls trial.suggest_categorical for the tf/engine/indicator-set
    index and every risk param (grid_mode) or trial.suggest_float
    (continuous). Returns dict(trial.params) verbatim — the RAW optuna
    params, single source of truth stored as-is in
    research_mission_trials.params_json. resolve_point() derives the
    human-readable form on demand, so there is exactly one place params
    can drift from what was actually sampled."""
    trial.suggest_categorical(_TF_IDX_KEY, list(range(len(space.timeframes_choices))))
    trial.suggest_categorical(_ENGINES_IDX_KEY, list(range(len(space.engine_set_choices))))
    trial.suggest_categorical(_INDICATORS_IDX_KEY, list(range(len(space.indicator_set_choices))))
    trial.suggest_categorical(_CONTEXT_IDX_KEY, list(range(len(space.context_filter_set_choices))))
    if grid_mode:
        for name, choices in space.risk_param_grid.items():
            trial.suggest_categorical(name, list(choices))
    else:
        for name, (lo, hi) in space.risk_param_ranges.items():
            trial.suggest_float(name, lo, hi)
    return dict(trial.params)


def resolve_point(space: MissionSearchSpace, raw_params: dict[str, Any]) -> dict[str, Any]:
    """Pure function: raw_params -> {"timeframes", "engines", "indicators",
    "context_filters", "risk_overrides"}. Same input always produces the
    same output — called both live (mission_runner evaluating a trial)
    and when rendering a stored row's params_json back into something
    readable.

    raw_params.get(_CONTEXT_IDX_KEY, 0) tolerates a resumed/replayed
    trial recorded before Context Filters shipped (its params_json has
    no __context_idx key) — defaults to index 0, which is always the
    empty-choice entry MissionSearchSpace.context_filter_set_choices
    defaults to."""
    timeframes = list(space.timeframes_choices[raw_params[_TF_IDX_KEY]])
    engines = list(space.engine_set_choices[raw_params[_ENGINES_IDX_KEY]])
    indicators = list(space.indicator_set_choices[raw_params[_INDICATORS_IDX_KEY]])
    context_filters = list(space.context_filter_set_choices[raw_params.get(_CONTEXT_IDX_KEY, 0)])
    risk_overrides = {k: v for k, v in raw_params.items() if k not in _INTERNAL_KEYS}
    return {
        "timeframes": timeframes, "engines": engines,
        "indicators": indicators, "context_filters": context_filters,
        "risk_overrides": risk_overrides,
    }


def distributions_for(space: MissionSearchSpace, grid_mode: bool) -> dict[str, Any]:
    """optuna Distribution objects per param name, used by
    mission_runner.py's replay path to reconstruct
    optuna.trial.create_trial(params=..., distributions=..., value=...,
    state=...) for a resumed mission's already-completed trials."""
    import optuna

    dists: dict[str, Any] = {
        _TF_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.timeframes_choices)))),
        _ENGINES_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.engine_set_choices)))),
        _INDICATORS_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.indicator_set_choices)))),
        _CONTEXT_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.context_filter_set_choices)))),
    }
    if grid_mode:
        for name, choices in space.risk_param_grid.items():
            dists[name] = optuna.distributions.CategoricalDistribution(list(choices))
    else:
        for name, (lo, hi) in space.risk_param_ranges.items():
            dists[name] = optuna.distributions.FloatDistribution(lo, hi)
    return dists


def make_sampler(sampler: str, seed: int, space: MissionSearchSpace, grid_mode: bool):
    """grid -> GridSampler (exact search space so it can track exhaustion
    correctly across resume, see distributions_for's replay use);
    random -> RandomSampler; tpe -> TPESampler (Optuna's own default,
    the algorithm most literature calls "Bayesian Optimization"/what
    Hyperopt's core algorithm (TPE) does — multivariate=True considers
    all params jointly, not independently, matching a genuine joint
    search rather than per-dimension optimization);
    nsga2 -> NSGAIISampler (a real genetic/evolutionary algorithm,
    single-objective here — see optimizer.py's module docstring)."""
    import optuna

    if sampler == "grid":
        return optuna.samplers.GridSampler(space.grid_search_space_dict(), seed=seed)
    if sampler == "random":
        return optuna.samplers.RandomSampler(seed=seed)
    if sampler == "tpe":
        return optuna.samplers.TPESampler(seed=seed, multivariate=True)
    if sampler == "nsga2":
        return optuna.samplers.NSGAIISampler(seed=seed)
    raise ValueError(f"unknown sampler {sampler!r} — choose from {SAMPLER_KEYS}")


@dataclass(frozen=True)
class EvalResult:
    metrics: BacktestMetrics
    objective_value: float | None   # None => trial reported as PRUNED (insufficient trades)
    insufficient: bool
    trades: int
    # AI Research Lab Phase 3 (2026-07-30) — optional, only populated when
    # return_trades=True. mission_runner.py's own two call sites (train +
    # holdout eval) never pass it, so their memory footprint across
    # thousands of trials is unaffected. backtest/mission_validator.py is
    # the one caller that needs the real closed-trade list, to feed
    # backtest/monte_carlo.py's run_monte_carlo() for one specific,
    # operator-chosen candidate.
    trade_records: list[TradeRecord] | None = None


# Finite sentinel used ONLY to feed the sampler's acquisition function —
# profit_factor is a real, correct float('inf') whenever a trial has zero
# losing trades (see backtest/metrics.py's json_safe() docstring). The
# true inf value is preserved in EvalResult.metrics / the stored
# metrics_json; only the value handed to study.tell() is clipped, since
# Optuna's internal math cannot consume a bare inf.
_OBJECTIVE_INF_SENTINEL = 1e6


def _finite_objective(raw_value: float) -> float:
    if raw_value == float("inf"):
        return _OBJECTIVE_INF_SENTINEL
    if raw_value == float("-inf"):
        return -_OBJECTIVE_INF_SENTINEL
    return float(raw_value)


def evaluate_point(
    symbol: str,
    df: pd.DataFrame,
    point: dict[str, Any],
    min_trades: int,
    objective_metric: str,
    return_trades: bool = False,
) -> EvalResult:
    """Generalizes backtest/robustness.py's `_run_point` to the full
    joint space: build_engine_config_override for timeframes/engines/
    indicators, BacktestConfig.from_profile for risk overrides,
    run_backtest, calculate_metrics — the exact same evaluation
    primitives every other backtest job in this codebase already uses.

    return_trades (Phase 3, 2026-07-30): when True, the closed-trade
    TradeRecord list is kept on the result (EvalResult.trade_records)
    instead of discarded — needed by backtest/mission_validator.py to
    feed backtest/monte_carlo.py's run_monte_carlo(). Defaults False so
    mission_runner.py's ordinary per-trial evaluation (thousands of
    trials per mission) keeps its existing, smaller memory footprint.
    """
    engine_config = build_engine_config_override(
        timeframes=point["timeframes"] or None,
        engines_enabled={e: (e in point["engines"]) for e in ENGINE_KEYS},
        indicators=point["indicators"] or None,
        context_filters=point.get("context_filters") or None,
    )
    cfg = BacktestConfig.from_profile(symbol, **point["risk_overrides"])
    bt = run_backtest(df, cfg, engine_config=engine_config)
    records = [trade_to_record(t, symbol) for t in bt.trades]
    metrics = calculate_metrics(records, initial_capital=cfg.initial_balance)
    trade_records = records if return_trades else None

    trades = metrics.total_trades
    if trades < min_trades:
        return EvalResult(
            metrics=metrics, objective_value=None, insufficient=True,
            trades=trades, trade_records=trade_records,
        )

    raw_value = getattr(metrics, objective_metric)
    return EvalResult(
        metrics=metrics,
        objective_value=_finite_objective(raw_value),
        insufficient=False,
        trades=trades,
        trade_records=trade_records,
    )
