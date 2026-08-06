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

import hashlib
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtest.metrics import BacktestMetrics, TradeRecord, calculate_metrics
from backtest.runner import trade_to_record
from backtesting.backtest_engine import (
    ENGINE_KEYS,
    ENGINE_VARIANT_KEYS,
    RISK_OVERRIDE_FIELDS,
    BacktestConfig,
    build_engine_config_override,
    run_backtest,
)
from backtesting.backtest_engine import _CONFLUENCE_OVERRIDE_BOUNDS
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
# Track C (Phase 4, 2026-08-01) — ad-hoc engine variants (PriceAction v2/
# Wyckoff v2), one shared index over COMPLETE variant-selection dicts —
# same "index a list of complete choices" convention as
# indicator_set_choices/context_filter_set_choices, not 9 independent
# per-engine categoricals (an engine_variants map is small and already
# atomic — no Cartesian-product concern like the 4-independent-dimension
# bug hypothesis_bundle_choices was built to fix).
_ENGINE_VARIANTS_IDX_KEY = "__engine_variants_idx"
_INTERNAL_KEYS = (_TF_IDX_KEY, _ENGINES_IDX_KEY, _INDICATORS_IDX_KEY, _CONTEXT_IDX_KEY, _ENGINE_VARIANTS_IDX_KEY)

# Hypothesis Bundles (2026-07-30) — an alternate, opt-in indexing mode. The
# 4 keys above are sampled INDEPENDENTLY (see suggest_point()), so giving
# every one of them multiple choices explores their full Cartesian product,
# not N discrete "hypotheses" (a real gap found live: an operator's mission
# only ever varied risk params because every one of those 4 dimensions had
# exactly one choice — PF clustered tightly around 1.0 as a direct result).
# hypothesis_bundle_choices lets an operator define N complete, named
# bundles (timeframes+engines+indicators+context together) and samples ONE
# shared index across all of them atomically instead.
_HYPOTHESIS_IDX_KEY = "__hypothesis_idx"
_HYPOTHESIS_INTERNAL_KEYS = (_HYPOTHESIS_IDX_KEY,)


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


def _validate_engine_variant_map(variant_map: dict) -> None:
    unknown_engines = set(variant_map) - set(ENGINE_KEYS)
    if unknown_engines:
        raise ValueError(f"unknown engine(s) in engine_variants: {unknown_engines} — choose from {ENGINE_KEYS}")
    for eng_key, variant in variant_map.items():
        allowed = ENGINE_VARIANT_KEYS.get(eng_key, ("v1",))
        if variant not in allowed:
            raise ValueError(f"engine {eng_key!r} has no variant {variant!r} — choose from {allowed}")


def _validate_confluence_overrides(overrides: dict) -> None:
    """Mission Center Research Rigor Phase 1 (2026-08-06) — same two-key
    bounds table build_engine_config_override() itself validates against,
    reused here so a bad confluence_overrides payload fails at
    MissionSearchSpace construction time, not deep inside a trial's
    evaluate_point() call."""
    unknown_keys = set(overrides) - set(_CONFLUENCE_OVERRIDE_BOUNDS)
    if unknown_keys:
        raise ValueError(
            f"unknown confluence_overrides key(s): {sorted(unknown_keys)} — "
            f"choose from {sorted(_CONFLUENCE_OVERRIDE_BOUNDS)}"
        )
    for key, value in overrides.items():
        lo, hi = _CONFLUENCE_OVERRIDE_BOUNDS[key]
        if not (lo <= value <= hi):
            raise ValueError(f"confluence_overrides.{key} must be between {lo} and {hi}, got {value}")


def _validate_hypothesis_bundle(bundle: dict) -> None:
    name = bundle.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"hypothesis bundle missing a non-blank 'name': {bundle!r}")
    unknown_tf = set(bundle.get("timeframes", [])) - set(SUPPORTED_TIMEFRAMES)
    if unknown_tf:
        raise ValueError(f"hypothesis {name!r}: unknown timeframe(s) {unknown_tf} — choose from {SUPPORTED_TIMEFRAMES}")
    unknown_eng = set(bundle.get("engines", [])) - set(ENGINE_KEYS)
    if unknown_eng:
        raise ValueError(f"hypothesis {name!r}: unknown engine(s) {unknown_eng} — choose from {ENGINE_KEYS}")
    for spec in bundle.get("indicators", []):
        _validate_indicator_spec(spec)
    for spec in bundle.get("context_filters", []):
        _validate_context_spec(spec)
    _validate_engine_variant_map(bundle.get("engine_variants", {}))


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
    # Hypothesis Bundles (2026-07-30) — opt-in, defaults to None so every
    # existing caller/stored search_space_json (missions created before
    # this shipped) constructs and behaves byte-identically. When set,
    # this REPLACES independent sampling of the 4 dimensions above with
    # one shared index over complete named bundles — see the module-level
    # comment above _HYPOTHESIS_IDX_KEY for why.
    hypothesis_bundle_choices: tuple[dict, ...] | None = None
    # Track C (Phase 4, 2026-08-01) — ad-hoc PriceAction v2/Wyckoff v2
    # selection. Each entry is a COMPLETE {engine_key: variant} map,
    # index-sampled — same convention as indicator_set_choices/
    # context_filter_set_choices. Defaults to a single empty-map choice
    # (every engine stays v1) so every existing caller/stored
    # search_space_json (missions created before this shipped) constructs
    # and behaves byte-identically.
    engine_variant_choices: tuple[dict[str, str], ...] = ({},)
    # Mission Center Research Rigor Phase 1 (2026-08-06) — a single,
    # MISSION-WIDE {"min_engines_agreeing", "min_informative_weight_share"}
    # override, applied to every trial regardless of which bundle/choice
    # it draws. Deliberately NOT a per-trial searched dimension (searching
    # quorum independently of which engines got enabled would draw
    # nonsensical combinations — e.g. quorum=2 with a single-engine choice
    # can never pass) and NOT per-hypothesis-bundle in this phase (every
    # bundle in a mission shares this one override; a mission mixing
    # single- and multi-engine bundles that each want a different quorum
    # is a real, deliberately deferred v2 refinement). None = production
    # config.yaml confluence block, unchanged — the fix that unblocks a
    # single-engine research hypothesis (e.g. "does SMC alone have edge?"),
    # which otherwise PRUNEs every trial: config.yaml's live
    # min_engines_agreeing=2 makes agree_count>=2 mathematically
    # unreachable with only one engine enabled.
    confluence_overrides: dict[str, float] | None = None
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
        if not self.engine_variant_choices:
            raise ValueError("engine_variant_choices must have at least one entry")
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
        for variant_map in self.engine_variant_choices:
            _validate_engine_variant_map(variant_map)
        if self.confluence_overrides:
            _validate_confluence_overrides(self.confluence_overrides)

        if self.hypothesis_bundle_choices is not None:
            if not self.hypothesis_bundle_choices:
                raise ValueError("hypothesis_bundle_choices must have at least one entry (or be None)")
            names = [b.get("name") for b in self.hypothesis_bundle_choices]
            if len(set(names)) != len(names):
                raise ValueError(f"hypothesis_bundle_choices names must be unique, got {names}")
            for bundle in self.hypothesis_bundle_choices:
                _validate_hypothesis_bundle(bundle)

        allowed_risk_fields = set(RISK_OVERRIDE_FIELDS)
        for name in {**self.risk_param_ranges, **self.risk_param_grid}:
            if name not in allowed_risk_fields:
                raise ValueError(f"unknown risk param {name!r} — choose from {RISK_OVERRIDE_FIELDS}")

    def grid_size(self) -> int:
        """Cartesian product size — mission_runner.py caps a grid-sampler
        mission's n_trials_per_symbol to this, since re-asking past
        exhaustion just cycles/duplicates points (verified against the
        installed optuna's real GridSampler behavior, not assumed)."""
        if self.hypothesis_bundle_choices:
            n = len(self.hypothesis_bundle_choices)
        else:
            n = (
                len(self.timeframes_choices) * len(self.engine_set_choices)
                * len(self.indicator_set_choices) * len(self.context_filter_set_choices)
                * len(self.engine_variant_choices)
            )
        for choices in self.risk_param_grid.values():
            n *= len(choices)
        return n

    def grid_search_space_dict(self) -> dict[str, list]:
        """The dict GridSampler's constructor needs — every param name
        that suggest_point()/suggest_categorical() will use, mapped to
        its full static choice list."""
        d: dict[str, list]
        if self.hypothesis_bundle_choices:
            d = {_HYPOTHESIS_IDX_KEY: list(range(len(self.hypothesis_bundle_choices)))}
        else:
            d = {
                _TF_IDX_KEY: list(range(len(self.timeframes_choices))),
                _ENGINES_IDX_KEY: list(range(len(self.engine_set_choices))),
                _INDICATORS_IDX_KEY: list(range(len(self.indicator_set_choices))),
                _CONTEXT_IDX_KEY: list(range(len(self.context_filter_set_choices))),
                _ENGINE_VARIANTS_IDX_KEY: list(range(len(self.engine_variant_choices))),
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
        # Track C — .get() with a backward-compatible default: a mission
        # created before engine variants shipped has no such key.
        engine_variant_choices=tuple(dict(c) for c in raw.get("engine_variant_choices", [{}])),
        hypothesis_bundle_choices=(
            tuple(dict(b) for b in raw["hypothesis_bundle_choices"])
            if raw.get("hypothesis_bundle_choices") else None
        ),
        # Mission Center Research Rigor Phase 1 — .get() with a
        # backward-compatible default of None: a mission created before
        # this shipped has no such key in its stored search_space_json.
        confluence_overrides=raw.get("confluence_overrides"),
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
    if space.hypothesis_bundle_choices:
        trial.suggest_categorical(_HYPOTHESIS_IDX_KEY, list(range(len(space.hypothesis_bundle_choices))))
    else:
        trial.suggest_categorical(_TF_IDX_KEY, list(range(len(space.timeframes_choices))))
        trial.suggest_categorical(_ENGINES_IDX_KEY, list(range(len(space.engine_set_choices))))
        trial.suggest_categorical(_INDICATORS_IDX_KEY, list(range(len(space.indicator_set_choices))))
        trial.suggest_categorical(_CONTEXT_IDX_KEY, list(range(len(space.context_filter_set_choices))))
        trial.suggest_categorical(_ENGINE_VARIANTS_IDX_KEY, list(range(len(space.engine_variant_choices))))
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
    defaults to.

    Hypothesis Bundles (2026-07-30): when space.hypothesis_bundle_choices
    is set, raw_params carries __hypothesis_idx instead of the 4
    individual index keys — timeframes/engines/indicators/context_filters
    all come from that one picked bundle atomically. The OUTPUT SHAPE is
    identical either way (same 5 keys) — every caller (evaluate_point,
    mission_validator.run_validation, the meta-analysis endpoint) reads
    this dict without needing to know which branch produced it."""
    if space.hypothesis_bundle_choices:
        bundle = space.hypothesis_bundle_choices[raw_params[_HYPOTHESIS_IDX_KEY]]
        timeframes = list(bundle.get("timeframes", []))
        engines = list(bundle.get("engines", []))
        indicators = list(bundle.get("indicators", []))
        context_filters = list(bundle.get("context_filters", []))
        engine_variants = dict(bundle.get("engine_variants", {}))
        internal_keys: tuple[str, ...] = _HYPOTHESIS_INTERNAL_KEYS
    else:
        timeframes = list(space.timeframes_choices[raw_params[_TF_IDX_KEY]])
        engines = list(space.engine_set_choices[raw_params[_ENGINES_IDX_KEY]])
        indicators = list(space.indicator_set_choices[raw_params[_INDICATORS_IDX_KEY]])
        context_filters = list(space.context_filter_set_choices[raw_params.get(_CONTEXT_IDX_KEY, 0)])
        engine_variants = dict(space.engine_variant_choices[raw_params.get(_ENGINE_VARIANTS_IDX_KEY, 0)])
        internal_keys = _INTERNAL_KEYS
    risk_overrides = {k: v for k, v in raw_params.items() if k not in internal_keys}
    return {
        "timeframes": timeframes, "engines": engines,
        "indicators": indicators, "context_filters": context_filters,
        "engine_variants": engine_variants, "risk_overrides": risk_overrides,
        # Mission-wide, not per-trial-sampled (see MissionSearchSpace.
        # confluence_overrides' own docstring) — identical for every
        # trial regardless of which branch above produced the rest.
        "confluence_overrides": dict(space.confluence_overrides) if space.confluence_overrides else {},
    }


def search_space_has_signal_variation(space: MissionSearchSpace) -> bool:
    """True iff at least one ENTRY-signal-affecting dimension (timeframes,
    engine set, indicators, context filters, engine variants — or, in
    hypothesis-bundle mode, the bundle choice itself) actually varies
    across this mission's trials.

    False means every trial in the mission ran the IDENTICAL entry-signal
    stream and only risk/cost parameters (SL/ATR multiplier, min_rr
    admission gate, position sizing, warmup/step bars, ...) differed
    between trials. Risk params only affect stop distance and a trade's
    admission/sizing — never WHICH bars the confluence engines vote
    EXECUTE on — so in that case every trial's trade stream overlaps
    almost entirely with every other trial's.

    Used by backtest/meta_analysis.py to detect when "N of M trials
    agree" cross-trial-consensus statistics (backtest.multiple_testing.
    binomial_sign_test_p_value) would violate that test's independence
    assumption — a real, distinct problem from multiple-comparisons risk
    (which Bonferroni correction addresses); Bonferroni cannot fix a
    violated independence assumption (2026-08-02)."""
    if space.hypothesis_bundle_choices:
        return len(space.hypothesis_bundle_choices) > 1
    return (
        len(space.timeframes_choices) > 1
        or len(space.engine_set_choices) > 1
        or len(space.indicator_set_choices) > 1
        or len(space.context_filter_set_choices) > 1
        or len(space.engine_variant_choices) > 1
    )


def classify_search_space_variation(space: MissionSearchSpace) -> str:
    """SIGNAL_VARIATION | RISK_ONLY_VARIATION | MIXED | NONE — Forensic
    Audit Phase 1, item C (2026-08-02): an explicit, UI-facing label built
    on search_space_has_signal_variation()'s existing detection, so an
    operator can see at a glance which axis a mission's trials actually
    vary across, instead of discovering "22 trials, all risk-only" after
    the fact in Meta-Analysis (the exact operator-reported case this
    labels proactively)."""
    has_signal = search_space_has_signal_variation(space)
    has_risk = bool(space.risk_param_ranges) or bool(space.risk_param_grid)
    if has_signal and has_risk:
        return "MIXED"
    if has_signal:
        return "SIGNAL_VARIATION"
    if has_risk:
        return "RISK_ONLY_VARIATION"
    return "NONE"


def distributions_for(space: MissionSearchSpace, grid_mode: bool) -> dict[str, Any]:
    """optuna Distribution objects per param name, used by
    mission_runner.py's replay path to reconstruct
    optuna.trial.create_trial(params=..., distributions=..., value=...,
    state=...) for a resumed mission's already-completed trials."""
    import optuna

    dists: dict[str, Any]
    if space.hypothesis_bundle_choices:
        dists = {
            _HYPOTHESIS_IDX_KEY: optuna.distributions.CategoricalDistribution(
                list(range(len(space.hypothesis_bundle_choices)))
            ),
        }
    else:
        dists = {
            _TF_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.timeframes_choices)))),
            _ENGINES_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.engine_set_choices)))),
            _INDICATORS_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.indicator_set_choices)))),
            _CONTEXT_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.context_filter_set_choices)))),
            _ENGINE_VARIANTS_IDX_KEY: optuna.distributions.CategoricalDistribution(list(range(len(space.engine_variant_choices)))),
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
    # Prune Forensic Audit (2026-08-04, reports/forensic/21_...) — always
    # populated (small dicts, cheap to keep, unlike trade_records). Before
    # this, a PRUNED trial's own bar-by-bar rejection reasons (quorum
    # "votes" vs "neutral_bias" vs "score" vs "info_share" vs indicator/
    # context filters) were computed by run_backtest() and then silently
    # discarded — so Mission Center had no way to tell "insufficient
    # engine agreement" apart from "insufficient signal strength" apart
    # from "filtered out by an indicator/context rule" for ANY trial,
    # pruned or complete. These three dicts carry that breakdown through
    # unmutated, straight from backtesting.backtest_engine.BacktestResult.
    gate_rejections: dict = field(default_factory=dict)
    context_rejections: dict = field(default_factory=dict)
    indicator_rejections: dict = field(default_factory=dict)
    # Mission Center Research Rigor Phase 1 (2026-08-06) — the metric
    # value BEFORE capping/reliability-discount, i.e. exactly what
    # metrics.<objective_metric> holds (still a real inf when that's the
    # true value). objective_value is what the sampler actually sees;
    # this field exists purely so a trial's leaderboard/report row can
    # show both side by side. None whenever objective_value is (PRUNED).
    objective_raw: float | None = None
    # Mission Center Research Rigor — Trade Stream Fingerprint (item 2,
    # 2026-08-06): a stable hash of the REALIZED sequence of trades this
    # configuration actually produced. Two trials whose resolved
    # configuration differs (a different indicator/engine/context was
    # "on") but whose trade stream fingerprint is IDENTICAL are not
    # independent evidence — the differing knob never actually changed
    # which trades fired (e.g. "SMC+H1" vs "SMC+H1+RSI(confirmation)"
    # producing the exact same entries/exits). Always computed from
    # `records`, which evaluate_point() builds unconditionally regardless
    # of return_trades — zero extra cost, no need to keep full trade
    # objects around to get this.
    trade_stream_fingerprint: str = ""


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


# Mission Center Research Rigor Phase 1 (2026-08-06) — an unclipped,
# unbounded metric fed straight to a sampler's acquisition function lets
# a handful of lucky/unlucky trades dominate: PF=inf (or a huge-but-finite
# PF) from 11 trades would otherwise look "better" than PF=2.4 from 120
# trades, dragging TPE/NSGA-II toward tiny-sample configurations. Only
# profit_factor gets a cap today — it's the one OPTIMIZABLE_METRICS entry
# genuinely prone to an inf/huge-from-few-trades blowup (sharpe/sortino/
# calmar/expectancy_r/sqn/recovery_factor/win_rate are already naturally
# bounded). This NEVER touches EvalResult.metrics/the real report value —
# only what the sampler sees.
_OBJECTIVE_CAP_BY_METRIC: dict[str, float] = {"profit_factor": 5.0}
# Trades at/above this get zero reliability discount; below it, the
# capped value is scaled down toward a small floor fraction of its
# magnitude as trades -> 0, so a lucky few-trade outlier can never
# outrank a well-sampled trial (e.g. PF=inf@11 trades must score below
# PF=2.4@120 trades — verified as a regression test, not just asserted).
_RELIABILITY_FULL_TRADES = 30
_RELIABILITY_FLOOR = 0.1


def _reliability_adjusted_objective(raw_value: float, trades: int, metric: str) -> float:
    capped = _finite_objective(raw_value)
    cap = _OBJECTIVE_CAP_BY_METRIC.get(metric)
    if cap is not None:
        capped = max(-cap, min(cap, capped))
    confidence = min(1.0, trades / _RELIABILITY_FULL_TRADES)
    return capped * (_RELIABILITY_FLOOR + (1 - _RELIABILITY_FLOOR) * confidence)


# Mission Center Research Rigor — Trade Stream Fingerprint (item 2,
# 2026-08-06). Deliberately keyed on entry_time + direction + exit_time
# only — NOT pnl/score/exit_reason/anything cost-derived — so two trials
# that fired the identical set of entries/exits but differ in realized
# outcome (e.g. a commission/slippage-only override) still fingerprint
# identically (same trade stream, different cost applied to it), while a
# risk override that genuinely moves SL/TP and changes WHEN a trade exits
# correctly produces a different fingerprint (a real, distinct stream).
_EMPTY_TRADE_STREAM_FINGERPRINT = hashlib.sha256(b"EMPTY_TRADE_STREAM").hexdigest()[:16]


def trade_stream_fingerprint(records: list[TradeRecord]) -> str:
    """A stable hash of the realized sequence of trades — see EvalResult.
    trade_stream_fingerprint's docstring for why this exists and exactly
    what it deliberately does/doesn't include."""
    if not records:
        return _EMPTY_TRADE_STREAM_FINGERPRINT
    key = "|".join(
        f"{r.entry_time}:{r.direction}:{r.exit_time}"
        for r in sorted(records, key=lambda r: (str(r.entry_time), r.direction))
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


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
        engine_variants=point.get("engine_variants") or None,
        confluence_overrides=point.get("confluence_overrides") or None,
    )
    cfg = BacktestConfig.from_profile(symbol, **point["risk_overrides"])
    bt = run_backtest(df, cfg, engine_config=engine_config)
    records = [trade_to_record(t, symbol) for t in bt.trades]
    metrics = calculate_metrics(records, initial_capital=cfg.initial_balance)
    trade_records = records if return_trades else None
    gate_rejections = dict(bt.gate_rejections)
    context_rejections = dict(bt.context_rejections)
    indicator_rejections = dict(bt.indicator_rejections)
    stream_fp = trade_stream_fingerprint(records)

    trades = metrics.total_trades
    if trades < min_trades:
        return EvalResult(
            metrics=metrics, objective_value=None, insufficient=True,
            trades=trades, trade_records=trade_records,
            gate_rejections=gate_rejections,
            context_rejections=context_rejections,
            indicator_rejections=indicator_rejections,
            trade_stream_fingerprint=stream_fp,
        )

    raw_value = getattr(metrics, objective_metric)
    return EvalResult(
        metrics=metrics,
        objective_value=_reliability_adjusted_objective(raw_value, trades, objective_metric),
        objective_raw=raw_value,
        insufficient=False,
        trades=trades,
        trade_records=trade_records,
        gate_rejections=gate_rejections,
        context_rejections=context_rejections,
        indicator_rejections=indicator_rejections,
        trade_stream_fingerprint=stream_fp,
    )
