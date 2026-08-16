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

from backtest.metrics import TradeRecord
from backtest.optimizer import (
    EvalResult,
    MissionSearchSpace,
    _finite_objective,
    _reliability_adjusted_objective,
    classify_search_space_variation,
    distributions_for,
    evaluate_point,
    make_sampler,
    resolve_point,
    search_space_from_dict,
    search_space_has_signal_variation,
    suggest_point,
    trade_stream_fingerprint,
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


def test_search_space_rejects_unknown_engine_in_engine_variant_choices():
    with pytest.raises(ValueError, match="unknown engine"):
        _small_space(engine_variant_choices=({"not_a_real_engine": "v2"},))


def test_search_space_rejects_engine_with_no_variants():
    with pytest.raises(ValueError, match="no variant"):
        _small_space(engine_variant_choices=({"nnfx": "v2"},))


def test_search_space_rejects_empty_engine_variant_choices():
    with pytest.raises(ValueError, match="engine_variant_choices"):
        _small_space(engine_variant_choices=())


def test_search_space_engine_variant_choices_defaults_to_all_v1():
    space = _small_space()
    assert space.engine_variant_choices == ({},)


def test_search_space_rejects_unknown_confluence_overrides_key():
    with pytest.raises(ValueError, match="confluence_overrides"):
        _small_space(confluence_overrides={"not_a_real_key": 1})


def test_search_space_rejects_out_of_bounds_min_engines_agreeing():
    with pytest.raises(ValueError, match="min_engines_agreeing"):
        _small_space(confluence_overrides={"min_engines_agreeing": 0})


def test_search_space_rejects_out_of_bounds_min_informative_weight_share():
    with pytest.raises(ValueError, match="min_informative_weight_share"):
        _small_space(confluence_overrides={"min_informative_weight_share": 1.5})


def test_search_space_confluence_overrides_defaults_to_none():
    assert _small_space().confluence_overrides is None


def test_search_space_accepts_valid_confluence_overrides():
    space = _small_space(confluence_overrides={"min_engines_agreeing": 1})
    assert space.confluence_overrides == {"min_engines_agreeing": 1}


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


def test_resolve_point_picks_correct_engine_variant_by_index():
    space = _small_space(engine_variant_choices=({}, {"price_action": "v2"}, {"wyckoff": "v2"}))
    raw = {"__timeframes_idx": 0, "__engines_idx": 0, "__indicators_idx": 0, "__engine_variants_idx": 1}
    resolved = resolve_point(space, raw)
    assert resolved["engine_variants"] == {"price_action": "v2"}


def test_resolve_point_includes_mission_wide_confluence_overrides():
    space = _small_space(confluence_overrides={"min_engines_agreeing": 1})
    raw = {"__timeframes_idx": 0, "__engines_idx": 0, "__indicators_idx": 0, "sl_atr_multiplier": 2.0}
    resolved = resolve_point(space, raw)
    assert resolved["confluence_overrides"] == {"min_engines_agreeing": 1}


def test_resolve_point_confluence_overrides_defaults_to_empty_dict_when_unset():
    space = _small_space()
    raw = {"__timeframes_idx": 0, "__engines_idx": 0, "__indicators_idx": 0, "sl_atr_multiplier": 2.0}
    resolved = resolve_point(space, raw)
    assert resolved["confluence_overrides"] == {}


def test_search_space_from_dict_round_trips_confluence_overrides():
    raw = {
        "timeframes_choices": [["H1"]], "engine_set_choices": [["nnfx"]],
        "indicator_set_choices": [[]], "confluence_overrides": {"min_engines_agreeing": 1},
    }
    space = search_space_from_dict(raw)
    assert space.confluence_overrides == {"min_engines_agreeing": 1}


def test_search_space_from_dict_backward_compatible_without_confluence_overrides():
    """A mission created before this shipped has no confluence_overrides
    key in its stored search_space_json — must default to None, not
    KeyError."""
    raw = {
        "timeframes_choices": [["H1"]], "engine_set_choices": [["nnfx"]],
        "indicator_set_choices": [[]],
    }
    space = search_space_from_dict(raw)
    assert space.confluence_overrides is None


def test_resolve_point_defaults_engine_variants_to_index_0_when_key_missing():
    """Resume/replay backward-compat: a trial recorded before engine
    variants existed has no __engine_variants_idx key in its stored
    params_json — must default to index 0 (the all-v1 entry)."""
    space = _small_space(engine_variant_choices=({}, {"price_action": "v2"}))
    raw = {"__timeframes_idx": 0, "__engines_idx": 0, "__indicators_idx": 0}
    resolved = resolve_point(space, raw)
    assert resolved["engine_variants"] == {}


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


def test_evaluate_point_surfaces_gate_rejections_on_the_pruned_path(monkeypatch):
    """Prune Forensic Audit (2026-08-04, reports/forensic/21_...)
    regression: BacktestResult.gate_rejections/context_rejections/
    indicator_rejections are computed by run_backtest() but, before this
    fix, evaluate_point() silently discarded all three — so Mission
    Center had zero visibility into WHY a low-trade-count trial was
    starved (quorum "votes" vs "neutral_bias" vs "score" vs an
    indicator/context filter). Pins that they now flow through
    unmutated on the PRUNED (insufficient) path."""
    import backtest.optimizer as optimizer_module

    class _FakeBacktestResult:
        trades: list = []
        gate_rejections = {"votes": 42, "score": 3}
        context_rejections = {"session": 5}
        indicator_rejections = {"rsi": 2}

    monkeypatch.setattr(
        optimizer_module, "run_backtest",
        lambda df, cfg, engine_config=None: _FakeBacktestResult(),
    )

    df = _ohlcv(50)
    point = {"timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "risk_overrides": {}}
    result = evaluate_point("EURUSD", df, point, min_trades=1, objective_metric="profit_factor")

    assert result.insufficient is True  # 0 trades < min_trades=1
    assert result.gate_rejections == {"votes": 42, "score": 3}
    assert result.context_rejections == {"session": 5}
    assert result.indicator_rejections == {"rsi": 2}


def test_evaluate_point_surfaces_gate_rejections_on_the_complete_path(monkeypatch):
    """Same regression as above, on the COMPLETE (not insufficient) path —
    the fields must not be dropped there either."""
    import backtest.optimizer as optimizer_module

    class _FakeBacktestResult:
        trades: list = []
        gate_rejections = {"neutral_bias": 7}
        context_rejections: dict = {}
        indicator_rejections: dict = {}

    monkeypatch.setattr(
        optimizer_module, "run_backtest",
        lambda df, cfg, engine_config=None: _FakeBacktestResult(),
    )

    df = _ohlcv(50)
    point = {"timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "risk_overrides": {}}
    result = evaluate_point("EURUSD", df, point, min_trades=0, objective_metric="profit_factor")

    assert result.insufficient is False  # 0 trades >= min_trades=0
    assert result.gate_rejections == {"neutral_bias": 7}


def test_finite_objective_clips_inf_but_preserves_real_metrics_value():
    assert _finite_objective(float("inf")) == pytest.approx(1e6)
    assert _finite_objective(float("-inf")) == pytest.approx(-1e6)
    assert _finite_objective(1.75) == pytest.approx(1.75)


# ── _reliability_adjusted_objective (Mission Center Research Rigor Phase 1) ─

def test_reliability_adjusted_objective_caps_pf_regardless_of_inf_sentinel():
    # Without the cap, inf -> 1e6 -> full confidence at 30+ trades would
    # stay 1e6, dwarfing any real PF. With the cap it must land at exactly
    # the 5.0 ceiling (full confidence, 30 trades).
    value = _reliability_adjusted_objective(float("inf"), trades=30, metric="profit_factor")
    assert value == pytest.approx(5.0)


def test_reliability_adjusted_objective_discounts_low_trade_count():
    high_trades = _reliability_adjusted_objective(2.0, trades=30, metric="profit_factor")
    low_trades = _reliability_adjusted_objective(2.0, trades=3, metric="profit_factor")
    assert low_trades < high_trades
    assert high_trades == pytest.approx(2.0)  # full confidence -> uncapped-by-discount raw value


def test_reliability_adjusted_objective_a_lucky_few_trade_pf_never_beats_a_well_sampled_moderate_pf():
    """The exact regression this fix targets: PF=inf from 11 trades must
    NOT look better to the sampler than PF=2.4 from 120 trades."""
    lucky_few_trades = _reliability_adjusted_objective(float("inf"), trades=11, metric="profit_factor")
    well_sampled = _reliability_adjusted_objective(2.4, trades=120, metric="profit_factor")
    assert lucky_few_trades < well_sampled


def test_reliability_adjusted_objective_leaves_genuinely_bounded_metrics_uncapped():
    # sharpe_ratio/sortino_ratio/expectancy_r/win_rate have no cap in
    # _OBJECTIVE_CAP_BY_METRIC — a large-but-finite value passes through
    # uncapped, only trade-count-discounted.
    for metric in ("sharpe_ratio", "sortino_ratio", "expectancy_r", "win_rate"):
        value = _reliability_adjusted_objective(12.0, trades=30, metric=metric)
        assert value == pytest.approx(12.0), metric


def test_reliability_adjusted_objective_never_exceeds_finite_objective_cap_on_negative_inf():
    value = _reliability_adjusted_objective(float("-inf"), trades=30, metric="profit_factor")
    assert value == pytest.approx(-5.0)


# ── MC-1 (2026-08-15 red-team audit): recovery_factor/calmar_ratio/sqn
# were previously left uncapped on the false claim they're "already
# naturally bounded" — all three can blow up from a handful of lucky
# trades with a tiny drawdown/near-identical R-multiples. ──

@pytest.mark.parametrize("metric,cap", [
    ("recovery_factor", 20.0), ("calmar_ratio", 20.0), ("sqn", 7.0),
])
def test_reliability_adjusted_objective_caps_the_newly_bounded_metrics(metric, cap):
    huge_value = _reliability_adjusted_objective(9999.0, trades=30, metric=metric)
    assert huge_value == pytest.approx(cap)  # full trade-count confidence, capped value passes through


def test_reliability_adjusted_objective_a_lucky_few_trade_sqn_never_beats_a_well_sampled_moderate_sqn():
    """The same regression class test_..._a_lucky_few_trade_pf_never_beats_...
    already pins for profit_factor, now proven for sqn too: an
    implausibly huge SQN from a handful of trades must not outrank a
    realistic SQN from a well-sampled trial."""
    lucky_few_trades = _reliability_adjusted_objective(500.0, trades=8, metric="sqn")
    well_sampled = _reliability_adjusted_objective(3.5, trades=150, metric="sqn")
    assert lucky_few_trades < well_sampled


# ── trade_stream_fingerprint (Mission Center Research Rigor, item 2) ────────

def _tr(trade_id: str, entry_time: str, direction: str, exit_time: str | None) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id, symbol="EURUSD", direction=direction,
        entry_time=pd.Timestamp(entry_time, tz="UTC"),
        exit_time=pd.Timestamp(exit_time, tz="UTC") if exit_time else None,
        entry_price=1.1, exit_price=1.11, stop_loss=1.09, take_profit=1.12,
        position_size=1000.0,
    )


def test_trade_stream_fingerprint_empty_list_is_canonical_and_stable():
    assert trade_stream_fingerprint([]) == trade_stream_fingerprint([])


def test_trade_stream_fingerprint_deterministic_for_identical_records():
    a = [_tr("1", "2024-01-01T00:00", "BUY", "2024-01-02T00:00")]
    b = [_tr("1", "2024-01-01T00:00", "BUY", "2024-01-02T00:00")]
    assert trade_stream_fingerprint(a) == trade_stream_fingerprint(b)


def test_trade_stream_fingerprint_order_independent():
    t1 = _tr("1", "2024-01-01T00:00", "BUY", "2024-01-02T00:00")
    t2 = _tr("2", "2024-01-03T00:00", "SELL", "2024-01-04T00:00")
    assert trade_stream_fingerprint([t1, t2]) == trade_stream_fingerprint([t2, t1])


def test_trade_stream_fingerprint_differs_on_different_exit_time():
    # A risk override that genuinely moves SL/TP and changes WHEN a trade
    # exits must produce a DIFFERENT fingerprint — a real, distinct stream.
    a = [_tr("1", "2024-01-01T00:00", "BUY", "2024-01-02T00:00")]
    b = [_tr("1", "2024-01-01T00:00", "BUY", "2024-01-03T00:00")]
    assert trade_stream_fingerprint(a) != trade_stream_fingerprint(b)


def test_trade_stream_fingerprint_differs_on_different_direction():
    a = [_tr("1", "2024-01-01T00:00", "BUY", "2024-01-02T00:00")]
    b = [_tr("1", "2024-01-01T00:00", "SELL", "2024-01-02T00:00")]
    assert trade_stream_fingerprint(a) != trade_stream_fingerprint(b)


def test_trade_stream_fingerprint_ignores_pnl_and_cost_derived_fields():
    # Two records with the identical entry/direction/exit but different
    # pnl_usd (e.g. only commission/slippage differed) must fingerprint
    # identically — same realized trade stream, different cost applied.
    a = TradeRecord(
        trade_id="1", symbol="EURUSD", direction="BUY",
        entry_time=pd.Timestamp("2024-01-01T00:00", tz="UTC"),
        exit_time=pd.Timestamp("2024-01-02T00:00", tz="UTC"),
        entry_price=1.1, exit_price=1.11, stop_loss=1.09, take_profit=1.12,
        position_size=1000.0, pnl_usd=42.0,
    )
    b = TradeRecord(
        trade_id="1", symbol="EURUSD", direction="BUY",
        entry_time=pd.Timestamp("2024-01-01T00:00", tz="UTC"),
        exit_time=pd.Timestamp("2024-01-02T00:00", tz="UTC"),
        entry_price=1.1, exit_price=1.11, stop_loss=1.09, take_profit=1.12,
        position_size=1000.0, pnl_usd=-3.0,
    )
    assert trade_stream_fingerprint([a]) == trade_stream_fingerprint([b])


def test_evaluate_point_always_populates_trade_stream_fingerprint(monkeypatch):
    """Computed unconditionally inside evaluate_point() regardless of
    return_trades — zero extra cost, no need to keep full trade objects
    to get this (see EvalResult.trade_stream_fingerprint's docstring)."""
    import backtest.optimizer as optimizer_module

    fake_records = [_tr("1", "2024-01-01T00:00", "BUY", "2024-01-02T00:00")]

    class _FakeBacktestResult:
        trades: list = [object()]
        gate_rejections: dict = {}
        context_rejections: dict = {}
        indicator_rejections: dict = {}

    monkeypatch.setattr(optimizer_module, "run_backtest", lambda df, cfg, engine_config=None: _FakeBacktestResult())
    monkeypatch.setattr(optimizer_module, "trade_to_record", lambda t, symbol: fake_records[0])

    df = _ohlcv(50)
    point = {"timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "risk_overrides": {}}
    result = evaluate_point("EURUSD", df, point, min_trades=0, objective_metric="profit_factor", return_trades=False)

    assert result.trade_records is None  # unaffected by return_trades=False
    assert result.trade_stream_fingerprint == trade_stream_fingerprint(fake_records)
    assert result.trade_stream_fingerprint != ""


def test_evaluate_point_populates_objective_raw_distinct_from_capped_objective_value(monkeypatch):
    """objective_raw must preserve the TRUE metric value (even a real inf)
    while objective_value is what the sampler actually sees — never
    fabricated, never silently identical when they should differ."""
    import backtest.optimizer as optimizer_module

    class _FakeMetrics:
        total_trades = 5
        profit_factor = float("inf")

    class _FakeBacktestResult:
        trades: list = []
        gate_rejections: dict = {}
        context_rejections: dict = {}
        indicator_rejections: dict = {}

    monkeypatch.setattr(optimizer_module, "run_backtest", lambda df, cfg, engine_config=None: _FakeBacktestResult())
    monkeypatch.setattr(optimizer_module, "calculate_metrics", lambda records, initial_capital: _FakeMetrics())

    df = _ohlcv(50)
    point = {"timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "risk_overrides": {}}
    result = evaluate_point("EURUSD", df, point, min_trades=1, objective_metric="profit_factor")

    assert result.objective_raw == float("inf")  # the real value, never touched
    assert result.objective_value is not None
    assert result.objective_value < 1e6  # capped+discounted, not the raw sentinel-clipped value


# ── Confluence quorum override (Mission Center Research Rigor Phase 1) ──────

def test_confluence_overrides_unblocks_a_single_engine_hypothesis():
    """The exact operator-reported bug: a mission with only ONE engine
    enabled PRUNEs every trial (production min_engines_agreeing=2 is
    mathematically unreachable with agree_count<=1) unless
    confluence_overrides lowers the quorum for that one ad-hoc run."""
    df = _ohlcv(2400)
    point_no_override = {
        "timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "risk_overrides": {},
        "confluence_overrides": {},
    }
    without_override = evaluate_point("EURUSD", df, point_no_override, min_trades=1, objective_metric="profit_factor")
    assert without_override.trades == 0

    point_with_override = {
        "timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "risk_overrides": {},
        "confluence_overrides": {"min_engines_agreeing": 1},
    }
    with_override = evaluate_point("EURUSD", df, point_with_override, min_trades=1, objective_metric="profit_factor")
    assert with_override.trades > 0


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


def test_grid_size_multiplies_by_engine_variant_choices_count():
    space = _small_space(
        risk_param_ranges={},
        risk_param_grid={"sl_atr_multiplier": (1.5, 2.0)},
        engine_variant_choices=({}, {"price_action": "v2"}, {"wyckoff": "v2"}),
    )
    # timeframes(2) x engines(2) x indicators(2) x engine_variants(3) x risk grid(2)
    assert space.grid_size() == 2 * 2 * 2 * 3 * 2


def test_distributions_for_covers_every_suggested_param():
    space = _small_space(
        risk_param_ranges={},
        risk_param_grid={"sl_atr_multiplier": (1.5, 2.0)},
    )
    dists = distributions_for(space, grid_mode=True)
    assert set(dists.keys()) == {
        "__timeframes_idx", "__engines_idx", "__indicators_idx", "__context_idx",
        "__engine_variants_idx", "sl_atr_multiplier",
    }


# ── Hypothesis Bundles (2026-07-30) ──────────────────────────────────────
# Regression coverage for the live-diagnosed bug: an operator's mission
# only ever varied risk params because timeframes/engines/indicators/
# context each had exactly one choice, so the sampler always picked index
# 0 for all four. hypothesis_bundle_choices lets the operator define named,
# atomic combinations searched as ONE dimension instead.

_BUNDLE_SMC = {
    "name": "SMC only", "timeframes": ["H1"], "engines": ["smc"],
    "indicators": [], "context_filters": [],
}
_BUNDLE_NNFX = {
    "name": "NNFX + Wyckoff", "timeframes": ["H4"], "engines": ["nnfx", "wyckoff"],
    "indicators": [], "context_filters": [],
}


def _bundle_space(**kwargs) -> MissionSearchSpace:
    defaults = dict(
        timeframes_choices=(("H1",),), engine_set_choices=(("nnfx",),),
        indicator_set_choices=((),),
        hypothesis_bundle_choices=(_BUNDLE_SMC, _BUNDLE_NNFX),
        risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
    )
    defaults.update(kwargs)
    return MissionSearchSpace(**defaults)


def test_hypothesis_bundle_choices_defaults_to_none_and_is_backward_compatible():
    space = _small_space()
    assert space.hypothesis_bundle_choices is None


def test_hypothesis_bundle_rejects_blank_name():
    with pytest.raises(ValueError, match="name"):
        _bundle_space(hypothesis_bundle_choices=({"name": "", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []},))


def test_hypothesis_bundle_rejects_duplicate_names():
    dup = {**_BUNDLE_NNFX, "name": _BUNDLE_SMC["name"]}
    with pytest.raises(ValueError, match="unique"):
        _bundle_space(hypothesis_bundle_choices=(_BUNDLE_SMC, dup))


def test_hypothesis_bundle_rejects_unknown_engine():
    bad = {**_BUNDLE_SMC, "engines": ["not_a_real_engine"]}
    with pytest.raises(ValueError, match="engine"):
        _bundle_space(hypothesis_bundle_choices=(bad,))


def test_hypothesis_bundle_rejects_unknown_timeframe():
    bad = {**_BUNDLE_SMC, "timeframes": ["M5"]}
    with pytest.raises(ValueError, match="timeframe"):
        _bundle_space(hypothesis_bundle_choices=(bad,))


def test_hypothesis_bundle_rejects_unknown_engine_variant():
    bad = {**_BUNDLE_SMC, "engine_variants": {"price_action": "v3"}}
    with pytest.raises(ValueError, match="no variant"):
        _bundle_space(hypothesis_bundle_choices=(bad,))


def test_resolve_point_reads_engine_variants_from_bundle():
    bundle_with_variant = {**_BUNDLE_NNFX, "engine_variants": {"wyckoff": "v2"}}
    space = _bundle_space(hypothesis_bundle_choices=(_BUNDLE_SMC, bundle_with_variant))
    resolved = resolve_point(space, {"__hypothesis_idx": 1, "sl_atr_multiplier": 2.0})
    assert resolved["engine_variants"] == {"wyckoff": "v2"}


def test_resolve_point_bundle_engine_variants_defaults_to_empty_when_absent():
    space = _bundle_space()  # _BUNDLE_SMC/_BUNDLE_NNFX carry no "engine_variants" key
    resolved = resolve_point(space, {"__hypothesis_idx": 0, "sl_atr_multiplier": 2.0})
    assert resolved["engine_variants"] == {}


def test_hypothesis_bundle_rejects_empty_tuple():
    with pytest.raises(ValueError, match="hypothesis_bundle_choices"):
        _bundle_space(hypothesis_bundle_choices=())


def test_suggest_point_hypothesis_mode_only_suggests_hypothesis_idx():
    space = _bundle_space()
    study = optuna.create_study(sampler=make_sampler("random", 42, space, grid_mode=False))
    trial = study.ask()
    raw = suggest_point(trial, space, grid_mode=False)
    assert "__hypothesis_idx" in raw
    assert raw["__hypothesis_idx"] in (0, 1)
    assert "__timeframes_idx" not in raw
    assert "__engines_idx" not in raw
    assert "__indicators_idx" not in raw
    assert "__context_idx" not in raw
    assert "sl_atr_multiplier" in raw


def test_resolve_point_hypothesis_mode_pulls_atomic_bundle():
    space = _bundle_space()
    raw = {"__hypothesis_idx": 1, "sl_atr_multiplier": 2.2}
    point = resolve_point(space, raw)
    assert point["timeframes"] == ["H4"]
    assert point["engines"] == ["nnfx", "wyckoff"]
    assert point["indicators"] == []
    assert point["context_filters"] == []
    assert point["risk_overrides"] == {"sl_atr_multiplier": 2.2}


def test_resolve_point_output_shape_identical_in_both_modes():
    # Load-bearing design property: evaluate_point()/mission_validator.py/
    # the meta-analysis endpoint must never need to know which branch
    # produced this dict.
    bundle_point = resolve_point(_bundle_space(), {"__hypothesis_idx": 0, "sl_atr_multiplier": 2.0})
    flat_point = resolve_point(_small_space(), {"__timeframes_idx": 0, "__engines_idx": 0, "__indicators_idx": 0, "sl_atr_multiplier": 2.0})
    assert set(bundle_point.keys()) == set(flat_point.keys()) == {
        "timeframes", "engines", "indicators", "context_filters", "engine_variants",
        "risk_overrides", "confluence_overrides",
    }


def test_distributions_for_hypothesis_mode():
    space = _bundle_space(risk_param_ranges={}, risk_param_grid={"sl_atr_multiplier": (1.5, 2.0)})
    dists = distributions_for(space, grid_mode=True)
    assert set(dists.keys()) == {"__hypothesis_idx", "sl_atr_multiplier"}


def test_grid_size_hypothesis_mode_uses_bundle_count_not_cartesian_product():
    space = _bundle_space(risk_param_ranges={}, risk_param_grid={"sl_atr_multiplier": (1.5, 2.0)})
    assert space.grid_size() == 2 * 2  # 2 bundles x 2 risk-grid values, NOT 1x1x1x2x2


def test_grid_search_space_dict_hypothesis_mode():
    space = _bundle_space(risk_param_ranges={}, risk_param_grid={"sl_atr_multiplier": (1.5, 2.0)})
    d = space.grid_search_space_dict()
    assert d["__hypothesis_idx"] == [0, 1]
    assert "__timeframes_idx" not in d


def test_hypothesis_bundle_choices_round_trips_through_search_space_from_dict():
    from backtest.optimizer import search_space_from_dict
    space = _bundle_space()
    raw = {
        "timeframes_choices": [list(c) for c in space.timeframes_choices],
        "engine_set_choices": [list(c) for c in space.engine_set_choices],
        "indicator_set_choices": [list(c) for c in space.indicator_set_choices],
        "context_filter_set_choices": [list(c) for c in space.context_filter_set_choices],
        "hypothesis_bundle_choices": [dict(b) for b in space.hypothesis_bundle_choices],
        "risk_param_ranges": space.risk_param_ranges,
        "risk_param_grid": space.risk_param_grid,
    }
    reconstructed = search_space_from_dict(raw)
    assert reconstructed.hypothesis_bundle_choices == space.hypothesis_bundle_choices


def test_search_space_from_dict_backward_compatible_without_hypothesis_bundle_choices():
    from backtest.optimizer import search_space_from_dict
    raw = {
        "timeframes_choices": [["H1"]], "engine_set_choices": [["nnfx"]],
        "indicator_set_choices": [[]],
        # no hypothesis_bundle_choices key at all — a mission stored before this shipped.
        "risk_param_ranges": {"sl_atr_multiplier": [1.0, 3.0]},
        "risk_param_grid": {},
    }
    space = search_space_from_dict(raw)
    assert space.hypothesis_bundle_choices is None


def test_hypothesis_bundles_are_actually_sampled_not_stuck_on_index_zero():
    # The authoritative behavior-change proof — the literal bug the
    # operator found: with real bundles that differ in engines, a real
    # sampler run across several trials must pick BOTH bundles, not
    # always index 0 (which is exactly what happened when every dimension
    # had only one choice).
    space = _bundle_space()
    study = optuna.create_study(sampler=make_sampler("random", 42, space, grid_mode=False), direction="maximize")
    seen_indices = set()
    for _ in range(20):
        trial = study.ask()
        raw = suggest_point(trial, space, grid_mode=False)
        seen_indices.add(raw["__hypothesis_idx"])
        study.tell(trial, 1.0)
    assert seen_indices == {0, 1}


# ── search_space_has_signal_variation (2026-08-02, dependence detection) ──
# Real, operator-found bug this guards: a mission whose search space only
# varies risk/cost params (every entry-signal dimension pinned to exactly
# one choice) makes every trial run the identical entry-signal stream —
# backtest/meta_analysis.py uses this to detect that case and stop
# reporting cross-trial-consensus p-values as if trials were independent.

def _risk_only_space(**kwargs) -> MissionSearchSpace:
    defaults = dict(
        timeframes_choices=(("H4",),),
        engine_set_choices=(("nnfx", "price_action"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
    )
    defaults.update(kwargs)
    return MissionSearchSpace(**defaults)


def test_no_signal_variation_when_every_dimension_pinned_to_one_choice():
    assert search_space_has_signal_variation(_risk_only_space()) is False


def test_signal_variation_true_when_timeframes_vary():
    space = _risk_only_space(timeframes_choices=(("H1",), ("H4",)))
    assert search_space_has_signal_variation(space) is True


def test_signal_variation_true_when_engine_set_varies():
    space = _small_space()  # 2 engine_set_choices by default
    assert search_space_has_signal_variation(space) is True


def test_signal_variation_true_when_indicator_set_varies():
    space = _risk_only_space(indicator_set_choices=((), ({"name": "rsi", "mode": "entry_filter", "params": {}, "weight": 0.0},)))
    assert search_space_has_signal_variation(space) is True


def test_signal_variation_true_when_context_filter_set_varies():
    space = _risk_only_space(context_filter_set_choices=((), ({"name": "session", "mode": "entry_filter", "params": {}, "weight": 0.0},)))
    assert search_space_has_signal_variation(space) is True


def test_signal_variation_true_when_engine_variants_vary():
    space = _risk_only_space(engine_variant_choices=({}, {"price_action": "v2"}))
    assert search_space_has_signal_variation(space) is True


def test_signal_variation_hypothesis_bundle_mode_single_bundle_is_dependent():
    bundle = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    space = _risk_only_space(hypothesis_bundle_choices=(bundle,))
    assert search_space_has_signal_variation(space) is False


def test_signal_variation_hypothesis_bundle_mode_multiple_bundles_is_independent():
    bundle_a = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    bundle_b = {"name": "NNFX", "timeframes": ["H4"], "engines": ["nnfx"], "indicators": [], "context_filters": []}
    space = _risk_only_space(hypothesis_bundle_choices=(bundle_a, bundle_b))
    assert search_space_has_signal_variation(space) is True


def test_signal_variation_hypothesis_bundle_mode_ignores_vestigial_flat_dimensions():
    # In bundle mode the 4 flat *_choices fields are vestigial (never
    # consulted by resolve_point) — even if they vary, a single-bundle
    # mission is still dependent.
    bundle = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    space = _risk_only_space(
        timeframes_choices=(("H1",), ("H4",)),
        engine_set_choices=(("nnfx",), ("smc",)),
        hypothesis_bundle_choices=(bundle,),
    )
    assert search_space_has_signal_variation(space) is False


# ── Forensic Audit Phase 1, item A (2026-08-02) — Mission Search-Space
# Claim reproduction. The operator observed a real mission whose UI implied
# it searched engine+timeframe+context combinations, but whose 22 trials
# all shared one entry-signal stream (only risk/cost params varied). Root
# cause, confirmed by direct read of MissionCenter.tsx's MissionBuilder.
# submit(): flat mode (hypothesisMode=false, the UI's default) ALWAYS
# wraps every signal dimension in a single-element array —
# `timeframes_choices: [timeframes]`, `engine_set_choices: [engines]`,
# `indicator_set_choices: [indicatorSpecs]`,
# `context_filter_set_choices: [contextSpecs]` — regardless of how many
# timeframes/engines/indicators/context filters the operator picked inside
# that one combo. This test pins that exact request shape and proves it
# structurally can never produce signal variation, independent of contents.

# ── classify_search_space_variation (Forensic Audit Phase 1, item C) ──────
# Real coverage gap closed here (2026-08-04): this function is wired into
# execution/routes/missions.py's GET /research/missions/{id} response
# (`search_space_kind`, surfaced as a frontend badge) but had ZERO tests
# anywhere before this pass — despite being the exact mechanism meant to
# proactively label a mission like the operator-reported "22 trials,
# all risk-only" case, rather than requiring a human to notice it after
# the fact in Meta-Analysis.

def test_classify_risk_only_variation():
    assert classify_search_space_variation(_risk_only_space()) == "RISK_ONLY_VARIATION"


def test_classify_signal_variation_no_risk_sweep():
    space = _risk_only_space(
        timeframes_choices=(("H1",), ("H4",)),
        risk_param_ranges={},
    )
    assert classify_search_space_variation(space) == "SIGNAL_VARIATION"


def test_classify_mixed_when_both_signal_and_risk_vary():
    space = _risk_only_space(timeframes_choices=(("H1",), ("H4",)))
    assert classify_search_space_variation(space) == "MIXED"


def test_classify_none_when_nothing_varies_at_all():
    space = _risk_only_space(risk_param_ranges={}, risk_param_grid={})
    assert classify_search_space_variation(space) == "NONE"


def test_classify_hypothesis_bundle_mode_single_bundle_is_risk_only_when_swept():
    bundle = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    space = _risk_only_space(hypothesis_bundle_choices=(bundle,))
    assert classify_search_space_variation(space) == "RISK_ONLY_VARIATION"


def test_classify_hypothesis_bundle_mode_multiple_bundles_is_mixed_when_risk_also_swept():
    bundle_a = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    bundle_b = {"name": "NNFX", "timeframes": ["H4"], "engines": ["nnfx"], "indicators": [], "context_filters": []}
    space = _risk_only_space(hypothesis_bundle_choices=(bundle_a, bundle_b))
    assert classify_search_space_variation(space) == "MIXED"


def test_flat_mode_request_shape_has_no_signal_variation():
    # Mirrors MissionCenter.tsx:571-574 verbatim: every dimension wrapped
    # in a SINGLE-element tuple, even with multiple values inside it.
    space = MissionSearchSpace(
        timeframes_choices=(("H1", "H4", "D1"),),
        engine_set_choices=(("nnfx", "price_action", "smc", "wyckoff"),),
        indicator_set_choices=(({"name": "rsi", "mode": "confirmation", "params": {}, "weight": 0.0},),),
        context_filter_set_choices=(({"name": "session", "mode": "entry_filter", "params": {}, "weight": 0.0},),),
        risk_param_ranges={"sl_atr_multiplier": (1.0, 3.0)},
    )
    assert search_space_has_signal_variation(space) is False
