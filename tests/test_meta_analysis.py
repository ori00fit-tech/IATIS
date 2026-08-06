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
    compute_effective_configuration_summary,
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
    sl_atr_multiplier: float = 2.0, trades: int = 50, metrics: dict | None = None,
) -> dict:
    params = {
        _TF_IDX_KEY: tf_idx, _ENGINES_IDX_KEY: engine_idx, _INDICATORS_IDX_KEY: indicator_idx,
        "sl_atr_multiplier": sl_atr_multiplier,
    }
    return {
        "mission_id": "m1", "trial_number": trial_number, "symbol": "EURUSD", "state": state,
        "objective_value": objective_value, "params_json": json.dumps(params),
        "metrics_json": json.dumps(metrics) if metrics is not None else None,
        "trades": trades, "error": None,
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
    sl_atr_multiplier: float = 2.0, trades: int = 50, metrics: dict | None = None,
) -> dict:
    params = {"__hypothesis_idx": hypothesis_idx, "sl_atr_multiplier": sl_atr_multiplier}
    return {
        "mission_id": "m1", "trial_number": trial_number, "symbol": "EURUSD", "state": "COMPLETE",
        "objective_value": objective_value, "params_json": json.dumps(params),
        "metrics_json": json.dumps(metrics) if metrics is not None else None,
        "trades": trades, "error": None,
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


# ── Edge Discovery (2026-07-31) — cross-trial consensus, pooled 3-way
# breakdown, ranked opportunities ─────────────────────────────────────────

def _direction_bucket(win_rate: float, trades: int = 20) -> dict:
    wins = round(win_rate / 100 * trades)
    return {"trades": trades, "wins": wins, "win_rate": win_rate, "pnl": 1.0}


def _consensus_trial(i: int, buy_wr: float, sell_wr: float) -> dict:
    return _row(i, objective_value=1.0, metrics={
        "by_direction": {"BUY": _direction_bucket(buy_wr), "SELL": _direction_bucket(sell_wr)},
    })


def test_cross_trial_consensus_direction_claim_correct_k_and_n():
    # 8 trials favor BUY, 2 favor SELL -> k=8, n=10.
    trials = [_consensus_trial(i, 70.0, 40.0) for i in range(8)]
    trials += [_consensus_trial(8 + i, 30.0, 60.0) for i in range(2)]
    trials += [_row(100 + i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 10)]
    space = _space()
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.insufficient_data is False
    claim = next(
        c for c in result.cross_trial_consensus if c.dimension == "direction" and c.metric == "win_rate"
    )
    assert claim.dominant_value == "BUY"
    assert claim.other_value == "SELL"
    assert claim.n_trials_compared == 10
    assert claim.trials_favor_dominant == 8
    assert claim.fraction_favor_dominant == pytest.approx(0.8)
    assert claim.p_value is not None
    assert claim.significance in ("SURVIVES_CORRECTION", "NOMINAL_ONLY", "NOT_SIGNIFICANT")
    assert "8/10" in claim.claim_text
    assert "BUY" in claim.claim_text


def test_cross_trial_consensus_excludes_unknown_regime_and_session():
    space = _space()
    trials = [
        _row(i, objective_value=1.0, metrics={"by_regime": {"Unknown": _direction_bucket(60.0)}})
        for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)
    ]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    regime_claim = next(c for c in result.cross_trial_consensus if c.dimension == "regime" and c.metric == "win_rate")
    # Every trial's by_regime only has an "Unknown" bucket; TRENDING/RANGING
    # (the only pair regime claims are computed over) are never present, so
    # no trial can contribute -> the claim degrades to insufficient data
    # rather than fabricating a comparison against a gate-off fallback.
    assert regime_claim.n_trials_compared == 0
    assert regime_claim.significance == "INSUFFICIENT_DATA"


def test_cross_trial_consensus_insufficient_data_below_min_trials_per_claim():
    space = _space()
    # Only 3 trials carry a real by_direction split (below _MIN_TRIALS_PER_CLAIM=5),
    # padded to clear the outer MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS floor.
    trials = [_consensus_trial(i, 70.0, 40.0) for i in range(3)]
    trials += [_row(100 + i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 3)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    claim = next(
        c for c in result.cross_trial_consensus if c.dimension == "direction" and c.metric == "win_rate"
    )
    assert claim.significance == "INSUFFICIENT_DATA"
    assert claim.p_value is None


def test_cross_trial_consensus_profit_factor_claim_skips_trials_missing_pf_field():
    space = _space()
    # Old-shape trials (predate this feature): BUY beats SELL on win_rate,
    # but no profit_factor key on either bucket at all.
    old_buy = {"trades": 20, "wins": 12, "win_rate": 60.0, "pnl": 1.0}
    old_sell = {"trades": 20, "wins": 8, "win_rate": 40.0, "pnl": -1.0}
    # New-shape trials: same win_rate split, plus a real profit_factor.
    new_buy = {**old_buy, "profit_factor": 3.0}
    new_sell = {**old_sell, "profit_factor": 0.5}

    old_trials = [
        _row(i, objective_value=1.0, metrics={"by_direction": {"BUY": old_buy, "SELL": old_sell}})
        for i in range(6)
    ]
    new_trials = [
        _row(6 + i, objective_value=1.0, metrics={"by_direction": {"BUY": new_buy, "SELL": new_sell}})
        for i in range(6)
    ]
    padding = [_row(100 + i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 12)]
    result = compute_meta_analysis(space, old_trials + new_trials + padding, sampler="tpe", mission_id="m1")

    pf_claim = next(c for c in result.cross_trial_consensus if c.dimension == "direction" and c.metric == "profit_factor")
    wr_claim = next(c for c in result.cross_trial_consensus if c.dimension == "direction" and c.metric == "win_rate")
    assert pf_claim.n_trials_compared == 6   # only the new-shape trials carry a profit_factor key
    assert wr_claim.n_trials_compared == 12  # win_rate is present on every trial, old and new alike


def test_pooled_breakdown_sums_across_all_complete_trials_not_just_top_n():
    space = _space()
    trials = []
    for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS):
        trials.append(_row(i, objective_value=float(20 - i), metrics={
            "by_direction_regime_session": {
                "BUY|RANGING|London": {"trades": 10, "wins": 6, "win_rate": 60.0, "pnl": 50.0,
                                         "gross_profit": 60.0, "gross_loss": 10.0, "profit_factor": 6.0},
            },
        }))
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", top_fraction=0.1)
    assert result.top_n < MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS  # confirms top-N really is a small slice
    full_combo = next(
        r for r in result.pooled_breakdown
        if r.level == "direction+regime+session" and r.direction == "BUY"
    )
    assert full_combo.trades == 10 * MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS  # pooled across ALL, not just top_n


def test_pooled_breakdown_all_seven_levels_present_and_correctly_grouped():
    space = _space()
    trials = [_row(i, objective_value=1.0, metrics={
        "by_direction_regime_session": {
            "BUY|RANGING|London": {"trades": 10, "wins": 6, "win_rate": 60.0, "pnl": 50.0,
                                     "gross_profit": 60.0, "gross_loss": 10.0, "profit_factor": 6.0},
            "SELL|TRENDING|Asia": {"trades": 5, "wins": 1, "win_rate": 20.0, "pnl": -30.0,
                                     "gross_profit": 5.0, "gross_loss": 35.0, "profit_factor": 5 / 35},
        },
    }) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    levels = {r.level for r in result.pooled_breakdown}
    assert levels == {
        "direction", "regime", "session", "direction+regime", "direction+session",
        "regime+session", "direction+regime+session",
    }
    direction_regime_row = next(
        r for r in result.pooled_breakdown if r.level == "direction+regime" and r.direction == "BUY" and r.regime == "RANGING"
    )
    three_way_row = next(
        r for r in result.pooled_breakdown
        if r.level == "direction+regime+session" and r.direction == "BUY" and r.regime == "RANGING" and r.session == "London"
    )
    assert direction_regime_row.trades == three_way_row.trades


def test_opportunity_candidates_excludes_unknown_and_below_min_trades():
    space = _space()
    trials = [_row(i, objective_value=1.0, metrics={
        "by_direction_regime_session": {
            "BUY|RANGING|Unknown": {"trades": 500, "wins": 400, "win_rate": 80.0, "pnl": 5000.0,
                                      "gross_profit": 5100.0, "gross_loss": 10.0, "profit_factor": 510.0},
            "BUY|RANGING|London": {"trades": 3, "wins": 3, "win_rate": 100.0, "pnl": 30.0,
                                     "gross_profit": 30.0, "gross_loss": 0.0, "profit_factor": float("inf")},
        },
    }) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", min_trades_for_pooled=10)
    labels = {c.label for c in result.opportunity_candidates}
    assert not any("Unknown" in label for label in labels)
    # London combo has only 3*20=60 trades pooled but the per-trial floor
    # doesn't multiply -- confirm the actual filter is on pooled trades.
    for c in result.opportunity_candidates:
        assert c.trades >= 10


def test_opportunity_candidates_ranked_by_effect_size_times_trades_descending():
    space = _space()
    trials = [_row(i, objective_value=1.0, metrics={
        "by_direction_regime_session": {
            "BUY|RANGING|London": {"trades": 100, "wins": 60, "win_rate": 60.0, "pnl": 500.0,
                                     "gross_profit": 600.0, "gross_loss": 300.0, "profit_factor": 2.0},
            "SELL|TRENDING|Asia": {"trades": 100, "wins": 50, "win_rate": 50.0, "pnl": 10.0,
                                     "gross_profit": 100.0, "gross_loss": 90.0, "profit_factor": 100 / 90},
        },
    }) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", min_trades_for_pooled=10)
    scores = [c.rank_score for c in result.opportunity_candidates]
    assert scores == sorted(scores, reverse=True)


def test_opportunity_candidates_handles_infinite_profit_factor_without_crashing():
    space = _space()
    trials = [_row(i, objective_value=1.0, metrics={
        "by_direction_regime_session": {
            "BUY|RANGING|London": {"trades": 50, "wins": 50, "win_rate": 100.0, "pnl": 500.0,
                                     "gross_profit": 500.0, "gross_loss": 0.0, "profit_factor": float("inf")},
        },
    }) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1", min_trades_for_pooled=10)
    assert len(result.opportunity_candidates) > 0
    assert result.opportunity_candidates[0].profit_factor == float("inf")


def test_insufficient_data_leaves_new_fields_empty():
    space = _space()
    trials = [_row(i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 1)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.cross_trial_consensus == []
    assert result.pooled_breakdown == []
    assert result.opportunity_candidates == []


def test_compute_meta_analysis_backward_compatible_default_kwarg():
    space = _space()
    trials = [_row(i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    # Old 8-positional/keyword-arg call signature (no min_trades_for_pooled) still works.
    result = compute_meta_analysis(space, trials, "tpe", "m1", None, 0.20, 5, 5)
    assert result.insufficient_data is False


# ── Dependence detection (2026-08-02) — an operator-identified real flaw:
# when a mission only varies risk/cost params, every trial ran the SAME
# entry-signal stream, so cross-trial-consensus/pooled-breakdown statistics
# are not independent-trial evidence. See backtest.optimizer.
# search_space_has_signal_variation and the module-level comment above
# DEPENDENT_TRIALS_SIGNIFICANCE in backtest/meta_analysis.py. ──────────────

def _risk_only_space(**kwargs) -> MissionSearchSpace:
    # Exactly one choice in every entry-signal-affecting dimension — the
    # real, live bug case (an operator's mission that only swept
    # sl_atr_multiplier while engines/timeframes/indicators/context were
    # each pinned to a single fixed set).
    defaults = dict(
        timeframes_choices=(("H4",),),
        engine_set_choices=(("nnfx", "price_action"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
    )
    defaults.update(kwargs)
    return MissionSearchSpace(**defaults)


def test_dependence_detected_when_only_risk_params_vary():
    space = _risk_only_space()
    trials = [_row(i, objective_value=1.0 + i * 0.01) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.dependence_detected is True
    assert result.dependence_warning is not None
    assert "DEPENDENCE" in result.dependence_warning


def test_dependence_not_detected_when_engine_set_varies():
    # _space()'s default already has 2 engine_set_choices — the real
    # multi-dimensional-search case.
    space = _space()
    trials = [_row(i, objective_value=1.0 + i * 0.01) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.dependence_detected is False
    assert result.dependence_warning is None


def test_dependence_not_detected_when_insufficient_data():
    # The insufficient_data early return short-circuits before dependence
    # is ever computed — must default to False/None, not crash.
    space = _risk_only_space()
    trials = [_row(i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 1)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.insufficient_data is True
    assert result.dependence_detected is False
    assert result.dependence_warning is None


def test_dependence_override_relabels_significance_and_nulls_pvalue():
    space = _risk_only_space()
    # Same 8-vs-2 BUY/SELL split as test_cross_trial_consensus_direction_claim_correct_k_and_n,
    # which (under a real, multi-dimensional space) reaches a real
    # significance verdict with a real p_value — here it must not.
    trials = [_consensus_trial(i, 70.0, 40.0) for i in range(8)]
    trials += [_consensus_trial(8 + i, 30.0, 60.0) for i in range(2)]
    trials += [_row(100 + i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 10)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.dependence_detected is True
    claim = next(
        c for c in result.cross_trial_consensus if c.dimension == "direction" and c.metric == "win_rate"
    )
    # The real k/n counts stay intact — only the significance verdict and
    # the p-value/confidence-under-independence are withheld.
    assert claim.n_trials_compared == 10
    assert claim.trials_favor_dominant == 8
    assert claim.fraction_favor_dominant == pytest.approx(0.8)
    assert claim.significance == "DEPENDENT_TRIALS_LEAD_ONLY"
    assert claim.p_value is None
    assert claim.confidence_pct is None
    assert "8/10" in claim.claim_text
    assert "DEPENDENCE" in claim.claim_text


def test_dependence_override_leaves_insufficient_data_claims_alone():
    space = _risk_only_space()
    # Only 3 trials carry a real by_direction split — below
    # _MIN_TRIALS_PER_CLAIM=5, already INSUFFICIENT_DATA regardless of
    # dependence; the override must not relabel an already-honest claim.
    trials = [_consensus_trial(i, 70.0, 40.0) for i in range(3)]
    trials += [_row(100 + i, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS - 3)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.dependence_detected is True
    claim = next(
        c for c in result.cross_trial_consensus if c.dimension == "direction" and c.metric == "win_rate"
    )
    assert claim.significance == "INSUFFICIENT_DATA"
    assert claim.p_value is None


def test_dependence_detected_hypothesis_bundle_mode_single_bundle():
    space = _hypothesis_space(hypothesis_bundle_choices=(_BUNDLE_SMC_H1,))
    trials = [_hypothesis_row(i, hypothesis_idx=0, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.dependence_detected is True


def test_dependence_not_detected_hypothesis_bundle_mode_multiple_bundles():
    space = _hypothesis_space()  # 2 bundles by default
    trials = [_hypothesis_row(i, hypothesis_idx=i % 2, objective_value=1.0) for i in range(MIN_COMPLETE_TRIALS_FOR_META_ANALYSIS)]
    result = compute_meta_analysis(space, trials, sampler="tpe", mission_id="m1")
    assert result.dependence_detected is False


# ── compute_effective_configuration_summary (Forensic Audit follow-up, 2026-08-03) ──
# Invariant 4 from the fff9806b90c2 investigation: "N trials" alone doesn't
# say how many are genuinely distinct executable configurations.

def test_effective_config_summary_single_hypothesis_all_trials_duplicate():
    # Mirrors the actual fff9806b90c2 shape: 1 hypothesis bundle, 50 trials
    # -> all 50 collapse to exactly 1 effective configuration.
    space = _hypothesis_space(hypothesis_bundle_choices=(_BUNDLE_SMC_H1,))
    trials = [_hypothesis_row(i, hypothesis_idx=0, sl_atr_multiplier=2.0) for i in range(50)]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.total_complete_trials == 50
    assert summary.unique_effective_configurations == 1
    assert summary.duplicate_trials == 49


def test_effective_config_summary_two_hypotheses_two_unique_configs():
    space = _hypothesis_space()  # 2 bundles by default (SMC/H1, NNFX+Wyckoff/H4)
    trials = [_hypothesis_row(i, hypothesis_idx=i % 2, sl_atr_multiplier=2.0) for i in range(10)]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.total_complete_trials == 10
    assert summary.unique_effective_configurations == 2
    assert summary.duplicate_trials == 8


def test_effective_config_summary_risk_param_variation_still_counts_as_unique():
    # Same hypothesis, but a DIFFERENT sl_atr_multiplier each trial -> the
    # risk override is part of the executable config, so each is unique.
    space = _hypothesis_space(hypothesis_bundle_choices=(_BUNDLE_SMC_H1,))
    trials = [_hypothesis_row(i, hypothesis_idx=0, sl_atr_multiplier=1.0 + i * 0.1) for i in range(5)]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.unique_effective_configurations == 5
    assert summary.duplicate_trials == 0


def test_effective_config_summary_excludes_pruned_and_failed_trials():
    space = _hypothesis_space(hypothesis_bundle_choices=(_BUNDLE_SMC_H1,))
    trials = [
        _hypothesis_row(0, hypothesis_idx=0),
        {**_hypothesis_row(1, hypothesis_idx=0), "state": "PRUNED"},
        {**_hypothesis_row(2, hypothesis_idx=0), "state": "FAIL"},
    ]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.total_complete_trials == 1
    assert summary.unique_effective_configurations == 1
    assert summary.duplicate_trials == 0


def test_effective_config_summary_flat_mode_works_without_hypothesis_bundles():
    # Regression guard: the function must work for a mission that never
    # used hypothesis bundles at all (the flat/default mode).
    space = _space()  # timeframes_choices=(("H1",), ("H4","D1","H1")), etc.
    trials = [_row(0, tf_idx=0, engine_idx=0), _row(1, tf_idx=1, engine_idx=0)]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.total_complete_trials == 2
    assert summary.unique_effective_configurations == 2
    assert summary.duplicate_trials == 0


def test_effective_config_summary_to_dict_shape():
    space = _hypothesis_space(hypothesis_bundle_choices=(_BUNDLE_SMC_H1,))
    trials = [_hypothesis_row(0, hypothesis_idx=0)]
    d = compute_effective_configuration_summary(space, trials).to_dict()
    assert set(d.keys()) == {
        "total_complete_trials", "unique_effective_configurations", "duplicate_trials",
        "unique_trade_streams", "trade_stream_duplicate_trials", "distinct_configs_sharing_a_trade_stream",
    }


# ── Trade Stream Fingerprint (Mission Center Research Rigor, item 2, 2026-08-06) ──
# A deeper check than the config-fingerprint dedup above: two DIFFERENT
# effective configurations can still fire the exact same realized trade
# sequence, meaning the differing knob (e.g. an indicator that never
# actually vetoed/confirmed anything) never changed the outcome.

def test_effective_config_summary_none_when_no_trial_carries_a_trade_stream_fingerprint():
    # Every existing test above (and every trial recorded before this
    # field existed) has metrics_json=None or a metrics dict without the
    # key — must degrade to None (never a fabricated 0), matching the
    # dataclass docstring's own contract.
    space = _hypothesis_space(hypothesis_bundle_choices=(_BUNDLE_SMC_H1,))
    trials = [_hypothesis_row(0, hypothesis_idx=0, metrics={"total_trades": 50})]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.unique_trade_streams is None
    assert summary.trade_stream_duplicate_trials is None
    assert summary.distinct_configs_sharing_a_trade_stream is None


def test_effective_config_summary_two_distinct_configs_same_trade_stream_is_flagged():
    # The exact operator-cited scenario: "SMC + H1" (bundle 0) vs
    # "NNFX + Wyckoff + H4" (bundle 1) are two different effective
    # configurations, but if they happened to fire the identical realized
    # trade sequence, that's not independent evidence.
    space = _hypothesis_space()  # SMC/H1 and NNFX+Wyckoff/H4, distinct bundles
    trials = [
        _hypothesis_row(0, hypothesis_idx=0, metrics={"trade_stream_fingerprint": "SAME_STREAM"}),
        _hypothesis_row(1, hypothesis_idx=1, metrics={"trade_stream_fingerprint": "SAME_STREAM"}),
    ]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.unique_effective_configurations == 2
    assert summary.unique_trade_streams == 1
    assert summary.trade_stream_duplicate_trials == 1
    assert summary.distinct_configs_sharing_a_trade_stream == 2


def test_effective_config_summary_distinct_configs_distinct_streams_not_flagged():
    space = _hypothesis_space()
    trials = [
        _hypothesis_row(0, hypothesis_idx=0, metrics={"trade_stream_fingerprint": "STREAM_A"}),
        _hypothesis_row(1, hypothesis_idx=1, metrics={"trade_stream_fingerprint": "STREAM_B"}),
    ]
    summary = compute_effective_configuration_summary(space, trials)
    assert summary.unique_trade_streams == 2
    assert summary.trade_stream_duplicate_trials == 0
    assert summary.distinct_configs_sharing_a_trade_stream == 0


def test_effective_config_summary_tolerates_malformed_metrics_json():
    space = _hypothesis_space(hypothesis_bundle_choices=(_BUNDLE_SMC_H1,))
    trials = [{**_hypothesis_row(0, hypothesis_idx=0), "metrics_json": "not valid json{{"}]
    summary = compute_effective_configuration_summary(space, trials)  # must not raise
    assert summary.unique_trade_streams is None
