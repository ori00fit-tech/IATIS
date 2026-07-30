"""
tests/test_meta_analysis.py
------------------------------
AI Research Lab / Mission Center Phase 3 (2026-07-30) — pure-function
unit tests for backtest/meta_analysis.py. No D1, no backtests: every
trial row is a hand-built dict matching storage.research_missions.
leaderboard()'s exact shape, with params_json built from the same raw
optuna-param keys backtest/optimizer.py's suggest_point() produces.
"""
from __future__ import annotations

import json

import pytest

from backtest.meta_analysis import (
    MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS,
    compute_meta_analysis,
    sampler_caveat,
)
from backtest.optimizer import (
    _ENGINES_IDX_KEY,
    _INDICATORS_IDX_KEY,
    _TF_IDX_KEY,
    MissionSearchSpace,
)


def _space(**kwargs) -> MissionSearchSpace:
    defaults = dict(
        timeframes_choices=(("H1",), ("H4", "D1", "H1")),
        engine_set_choices=(("nnfx",), ("nnfx", "price_action")),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
    )
    defaults.update(kwargs)
    return MissionSearchSpace(**defaults)


def _row(
    trial_number: int, state: str = "COMPLETE", objective_value: float | None = 1.0,
    tf_idx: int = 0, engine_idx: int = 0, indicator_idx: int = 0,
    sl_atr_multiplier: float = 2.0, trades: int = 50,
) -> dict:
    params = {
        _TF_IDX_KEY: tf_idx, _ENGINES_IDX_KEY: engine_idx, _INDICATORS_IDX_KEY: indicator_idx,
        "sl_atr_multiplier": sl_atr_multiplier,
    }
    return {
        "mission_id": "m1", "trial_number": trial_number, "symbol": "EURUSD", "state": state,
        "objective_value": objective_value, "params_json": json.dumps(params),
        "metrics_json": None, "trades": trades, "error": None,
        "started_at": "t", "finished_at": "t",
    }


def test_insufficient_data_below_threshold():
    space = _space()
    trials = [_row(i, objective_value=1.0 + i * 0.01) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 1)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.insufficient_data is True
    assert result.engine_frequencies == []
    assert result.consensus_bands == []
    assert "20" in result.note or str(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS) in result.note


def test_pruned_and_fail_trials_excluded():
    space = _space()
    good = [_row(i, objective_value=1.0 + i * 0.01) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    pruned = [_row(1000 + i, state="PRUNED", objective_value=None, trades=0) for i in range(5)]
    failed = [_row(2000 + i, state="FAIL", objective_value=None) for i in range(5)]
    result = compute_meta_analysis(space, good + pruned + failed, sampler="tpe", mission_id="m1")
    assert result.n_total_trials == len(good) + len(pruned) + len(failed)
    assert result.n_complete_trials == len(good)
    assert result.insufficient_data is False


def test_engine_frequency_and_lift_correct():
    space = _space()
    # 20 trials: the top 5 (by objective_value) all use engine_idx=1
    # (nnfx+price_action), the rest use engine_idx=0 (nnfx only) — so
    # price_action's lift over its baseline frequency should be > 1.
    trials = []
    for i in range(20):
        objective = 2.0 - i * 0.05  # trial 0 is the best, descending
        engine_idx = 1 if i < 5 else 0
        trials.append(_row(i, objective_value=objective, engine_idx=engine_idx))

    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", top_fraction=0.25)
    assert result.top_n == 5

    pa_freq = next(f for f in result.engine_frequencies if f.value == "price_action")
    assert pa_freq.top_count == 5
    assert pa_freq.top_fraction == pytest.approx(1.0)
    assert pa_freq.all_count == 5
    assert pa_freq.all_fraction == pytest.approx(0.25)
    assert pa_freq.lift == pytest.approx(4.0)

    nnfx_freq = next(f for f in result.engine_frequencies if f.value == "nnfx")
    assert nnfx_freq.top_count == 5  # nnfx is in BOTH engine sets
    assert nnfx_freq.all_count == 20
    assert nnfx_freq.lift == pytest.approx(1.0)


def test_timeframe_frequency_present():
    space = _space()
    trials = [_row(i, objective_value=float(20 - i), tf_idx=(1 if i < 10 else 0)) for i in range(20)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    h4_freq = next(f for f in result.timeframe_frequencies if f.value == "H4")
    assert h4_freq.all_count == 10
    d1_freq = next(f for f in result.timeframe_frequencies if f.value == "D1")
    assert d1_freq.all_count == 10
    h1_freq = next(f for f in result.timeframe_frequencies if f.value == "H1")
    assert h1_freq.all_count == 20  # H1 appears in BOTH timeframe choices


def test_consensus_band_bin_means_correct():
    space = _space(risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)})
    # Evenly spread sl_atr_multiplier from 1.0 to 3.0 across 20 trials,
    # objective_value constant per trial so each bin's mean is trivially
    # checkable.
    trials = []
    for i in range(20):
        value = 1.0 + (i / 19.0) * 2.0  # spans [1.0, 3.0]
        trials.append(_row(i, objective_value=1.30, sl_atr_multiplier=value))
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", n_bins=5)
    band = result.consensus_bands[0]
    assert band.risk_param == "sl_atr_multiplier"
    assert sum(b.n_trials for b in band.bins) == 20
    for b in band.bins:
        if b.n_trials > 0:
            assert b.mean_objective == pytest.approx(1.30)


def test_consensus_shape_plateau_vs_spike():
    space = _space(risk_param_ranges={"sl_atr_multiplier": (1.0, 2.0)})

    # PLATEAU case: objective stays close across adjacent bins.
    plateau_trials = []
    pf_by_bin = [1.28, 1.30, 1.31, 1.29, 1.30]
    for bin_i, pf in enumerate(pf_by_bin):
        for j in range(4):
            value = 1.0 + (bin_i + 0.5) / 5.0
            plateau_trials.append(_row(bin_i * 4 + j, objective_value=pf, sl_atr_multiplier=value))
    result = compute_meta_analysis(space, plateau_trials, sampler="grid", mission_id="m1", n_bins=5)
    assert result.consensus_bands[0].shape == "PLATEAU"

    # SPIKE case: one isolated bin is far better than its neighbors.
    spike_trials = []
    pf_by_bin_spike = [0.90, 0.91, 1.55, 0.89, 0.92]
    for bin_i, pf in enumerate(pf_by_bin_spike):
        for j in range(4):
            value = 1.0 + (bin_i + 0.5) / 5.0
            spike_trials.append(_row(bin_i * 4 + j, objective_value=pf, sl_atr_multiplier=value))
    result2 = compute_meta_analysis(space, spike_trials, sampler="grid", mission_id="m1", n_bins=5)
    assert result2.consensus_bands[0].shape == "SPIKE"


def test_consensus_band_inconclusive_when_param_never_sampled():
    space = _space(risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)})
    trials = [_row(i, objective_value=1.0, sl_atr_multiplier=2.0) for i in range(20)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.consensus_bands[0].shape == "INCONCLUSIVE"


def test_grid_params_excluded_from_consensus_bands():
    space = _space(risk_param_ranges={}, risk_param_grid={"sl_atr_multiplier": (1.5, 2.0, 2.5)})
    trials = [_row(i, objective_value=1.0, sl_atr_multiplier=2.0) for i in range(20)]
    result = compute_meta_analysis(space, trials, sampler="grid", mission_id="m1")
    assert result.consensus_bands == []


def test_sampler_caveat_differs_for_grid_vs_others():
    assert "designed grid" in sampler_caveat("grid").lower() or "coverage" in sampler_caveat("grid").lower()
    assert sampler_caveat("tpe") != sampler_caveat("grid")
    assert sampler_caveat("random") == sampler_caveat("nsga2")  # both hit the default caveat


def test_rejects_bad_n_bins_and_top_fraction():
    space = _space()
    trials = [_row(i, objective_value=1.0) for i in range(20)]
    with pytest.raises(ValueError):
        compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", n_bins=0)
    with pytest.raises(ValueError):
        compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", top_fraction=0.0)
    with pytest.raises(ValueError):
        compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", top_fraction=1.5)


# ── Hypothesis Bundles (2026-07-30) ──────────────────────────────────────

_BUNDLE_SMC_H1 = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
_BUNDLE_NNFX_H4 = {"name": "NNFX + Wyckoff", "timeframes": ["H4"], "engines": ["nnfx", "wyckoff"], "indicators": [], "context_filters": []}


def _hypothesis_space(**kwargs) -> MissionSearchSpace:
    defaults = dict(
        timeframes_choices=(("H1",),), engine_set_choices=(("nnfx",),),
        indicator_set_choices=((),),
        hypothesis_bundle_choices=(_BUNDLE_SMC_H1, _BUNDLE_NNFX_H4),
        risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
    )
    defaults.update(kwargs)
    return MissionSearchSpace(**defaults)


def _hypothesis_row(
    trial_number: int, hypothesis_idx: int, objective_value: float = 1.0,
    sl_atr_multiplier: float = 2.0, trades: int = 50,
) -> dict:
    params = {"__hypothesis_idx": hypothesis_idx, "sl_atr_multiplier": sl_atr_multiplier}
    return {
        "mission_id": "m1", "trial_number": trial_number, "symbol": "EURUSD", "state": "COMPLETE",
        "objective_value": objective_value, "params_json": json.dumps(params),
        "metrics_json": None, "trades": trades, "error": None,
        "started_at": "t", "finished_at": "t",
    }


def test_engine_and_timeframe_frequencies_resolve_correctly_in_hypothesis_bundle_mode():
    # Proves resolve_point()'s unchanged output shape carries hypothesis-
    # bundle-mode trials through compute_meta_analysis() correctly with
    # zero code changes beyond the all_timeframes fix.
    space = _hypothesis_space()
    # Top 5 (best objective) all picked bundle 0 (SMC/H1); the rest bundle 1 (NNFX+Wyckoff/H4).
    trials = []
    for i in range(20):
        objective = 2.0 - i * 0.05
        idx = 0 if i < 5 else 1
        trials.append(_hypothesis_row(i, hypothesis_idx=idx, objective_value=objective))

    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", top_fraction=0.25)
    assert result.insufficient_data is False
    assert result.top_n == 5

    smc_freq = next(f for f in result.engine_frequencies if f.value == "smc")
    assert smc_freq.top_count == 5
    assert smc_freq.all_count == 5

    nnfx_freq = next(f for f in result.engine_frequencies if f.value == "nnfx")
    assert nnfx_freq.top_count == 0
    assert nnfx_freq.all_count == 15

    # The all_timeframes fix: H4 (only used by bundle 1, never in the
    # vestigial flat timeframes_choices=(("H1",),)) must still get a row.
    h4_freq = next(f for f in result.timeframe_frequencies if f.value == "H4")
    assert h4_freq.all_count == 15
    h1_freq = next(f for f in result.timeframe_frequencies if f.value == "H1")
    assert h1_freq.all_count == 5


def test_all_timeframes_enumeration_uses_bundles_not_vestigial_flat_field():
    # Direct regression pin for the one-line fix: a bundle-mode space
    # whose flat timeframes_choices contains a timeframe NO bundle uses
    # at all must not show that phantom timeframe, and must show every
    # real bundle timeframe.
    space = _hypothesis_space(timeframes_choices=(("D1",),))  # vestigial, unused in bundle mode
    trials = [_hypothesis_row(i, hypothesis_idx=i % 2, objective_value=1.0) for i in range(20)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    values = {f.value for f in result.timeframe_frequencies}
    assert values == {"H1", "H4"}
    assert "D1" not in values
