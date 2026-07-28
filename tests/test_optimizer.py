"""
tests/test_optimizer.py
--------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — tests for
backtest/optimizer.py's joint multi-dimensional search space and
Optuna-backed samplers. Verifies the exact optuna behaviors this module
depends on (GridSampler exhaustion, TPE seed-determinism) against the
real installed optuna, not assumed.
"""
from __future__ import annotations

import numpy as np
import optuna
import pandas as pd
import pytest

from backtest.optimizer import (
    EvalResult,
    MissionSearchSpace,
    _finite_objective,
    distributions_for,
    evaluate_point,
    make_sampler,
    resolve_point,
    suggest_point,
)

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _ohlcv(n: int, seed: int = 7, trend: float = 0.10) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


def _small_space(**kwargs) -> MissionSearchSpace:
    defaults = dict(
        timeframes_choices=(("H1",), ("H4", "D1", "H1")),
        engine_set_choices=(("nnfx",), ("nnfx", "price_action")),
        indicator_set_choices=((), ()),
        risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
    )
    defaults.update(kwargs)
    return MissionSearchSpace(**defaults)


# ── MissionSearchSpace validation ────────────────────────────────────────

def test_search_space_rejects_both_risk_forms_set():
    with pytest.raises(ValueError, match="XOR"):
        _small_space(
            risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
            risk_param_grid={"sl_atr_multiplier": (1.0, 2.0, 3.0)},
        )


def test_search_space_rejects_unknown_timeframe():
    with pytest.raises(ValueError, match="timeframe"):
        _small_space(timeframes_choices=(("M5",),))


def test_search_space_rejects_unknown_engine():
    with pytest.raises(ValueError, match="engine"):
        _small_space(engine_set_choices=(("not_a_real_engine",),))


def test_search_space_rejects_unknown_risk_param():
    with pytest.raises(ValueError, match="risk param"):
        _small_space(risk_param_ranges={"not_a_real_param": (1.0, 2.0)})


def test_search_space_rejects_empty_choice_dimension():
    with pytest.raises(ValueError):
        _small_space(timeframes_choices=())


# ── suggest_point / resolve_point ────────────────────────────────────────

def test_suggest_point_grid_mode_uses_categorical_for_risk_params():
    space = _small_space(
        risk_param_ranges={},
        risk_param_grid={"sl_atr_multiplier": (1.5, 2.0, 2.5)},
    )
    study = optuna.create_study(sampler=make_sampler("grid", 42, space, grid_mode=True))
    trial = study.ask()
    raw = suggest_point(trial, space, grid_mode=True)
    assert raw["sl_atr_multiplier"] in (1.5, 2.0, 2.5)


def test_resolve_point_is_pure_and_deterministic():
    space = _small_space()
    raw = {"__timeframes_idx": 1, "__engines_idx": 0, "__indicators_idx": 0, "sl_atr_multiplier": 2.1}
    a = resolve_point(space, raw)
    b = resolve_point(space, raw)
    assert a == b
    assert a["timeframes"] == ["H4", "D1", "H1"]
    assert a["engines"] == ["nnfx"]
    assert a["risk_overrides"] == {"sl_atr_multiplier": 2.1}


# ── evaluate_point ────────────────────────────────────────────────────────

def test_evaluate_point_marks_insufficient_when_below_min_trades():
    df = _ohlcv(2400)
    point = {"timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "risk_overrides": {}}
    result = evaluate_point("EURUSD", df, point, min_trades=999_999, objective_metric="profit_factor")
    assert isinstance(result, EvalResult)
    assert result.insufficient is True
    assert result.objective_value is None


def test_evaluate_point_runs_end_to_end_and_produces_real_trades():
    df = _ohlcv(2400)
    # A single-engine set is a real, honest ablation that legitimately
    # produces few/no trades (quorum not met) — use the default prod4 set
    # so this smoke test reliably exercises a real, non-empty trade path.
    point = {
        "timeframes": ["H1"], "engines": ["nnfx", "price_action", "smc", "wyckoff"],
        "indicators": [], "risk_overrides": {},
    }
    result = evaluate_point("EURUSD", df, point, min_trades=1, objective_metric="profit_factor")
    assert result.trades > 0
    assert result.metrics.total_trades == result.trades


def test_finite_objective_clips_inf_but_preserves_real_metrics_value():
    assert _finite_objective(float("inf")) == pytest.approx(1e6)
    assert _finite_objective(float("-inf")) == pytest.approx(-1e6)
    assert _finite_objective(1.75) == pytest.approx(1.75)


# ── Sampler behavior (required property tests) ────────────────────────────

def test_different_samplers_produce_genuinely_different_trial_selection():
    # No risk-param dimension here (neither ranges nor grid) — isolates
    # the comparison to the tf/engine/indicator-set index dimensions,
    # which every sampler mode (grid or continuous) suggests identically
    # via suggest_categorical, so grid_mode's value doesn't matter for
    # this space and every sampler can be driven the same way.
    space = MissionSearchSpace(
        timeframes_choices=(("H1",), ("H4", "D1", "H1"), ("D1",)),
        engine_set_choices=(("nnfx",), ("nnfx", "price_action"), ("smc",)),
        indicator_set_choices=((), ()),
    )

    def _sequence(sampler_name: str, seed: int) -> list[dict]:
        sampler = make_sampler(sampler_name, seed, space, grid_mode=True)
        study = optuna.create_study(sampler=sampler, direction="maximize")
        seq = []
        for _ in range(6):
            trial = study.ask()
            raw = suggest_point(trial, space, grid_mode=True)
            seq.append(raw)
            try:
                study.tell(trial, 1.0)
            except RuntimeError:
                pass  # benign — grid exhausted mid-loop, see module docstring
        return seq

    grid_seq = _sequence("grid", 42)
    random_seq = _sequence("random", 42)
    assert grid_seq != random_seq

    tpe_seq_a = _sequence("tpe", 42)
    tpe_seq_b = _sequence("tpe", 42)
    assert tpe_seq_a == tpe_seq_b  # same seed -> identical (determinism/reproducibility)

    tpe_seq_c = _sequence("tpe", 7)
    assert tpe_seq_a != tpe_seq_c  # different seed -> different sequence


def test_grid_sampler_never_exceeds_cartesian_product_size():
    space = _small_space(
        risk_param_ranges={},
        risk_param_grid={"sl_atr_multiplier": (1.5, 2.0)},
    )
    grid_size = space.grid_size()
    assert grid_size == 2 * 2 * 2 * 2  # timeframes x engines x indicators x risk grid

    sampler = make_sampler("grid", 42, space, grid_mode=True)
    study = optuna.create_study(sampler=sampler, direction="maximize")
    seen = set()
    for _ in range(grid_size):
        trial = study.ask()
        raw = suggest_point(trial, space, grid_mode=True)
        seen.add(tuple(sorted(raw.items())))
        try:
            study.tell(trial, 1.0)
        except RuntimeError:
            pass
    assert len(seen) == grid_size  # every point visited exactly once, no duplicates


def test_distributions_for_covers_every_suggested_param():
    space = _small_space(
        risk_param_ranges={},
        risk_param_grid={"sl_atr_multiplier": (1.5, 2.0)},
    )
    dists = distributions_for(space, grid_mode=True)
    assert set(dists.keys()) == {
        "__timeframes_idx", "__engines_idx", "__indicators_idx", "sl_atr_multiplier",
    }
