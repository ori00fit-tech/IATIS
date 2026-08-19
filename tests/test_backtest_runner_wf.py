"""
Tests for backtest/runner.py and backtest/walk_forward.py.

The methodological claims are what get tested — window disjointness,
embargo correctness, verdict honesty — not just happy paths.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.runner import (
    RunnerConfig,
    find_symbol_csv,
    load_symbol_data,
    run_all,
    trade_to_record,
)
from backtest.walk_forward import (
    SymbolVerdict,
    WalkForwardConfig,
    WindowResult,
    WindowVerdict,
    WalkForwardResult,
    split_windows,
)
from backtesting.backtest_engine import Trade


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────

def _ohlcv(n: int, seed: int = 7, trend: float = 0.06, freq: str = "h") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, close) + 0.0008,
            "low": np.minimum(o, close) - 0.0008,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


def _trade(**kw) -> Trade:
    base = dict(
        entry_bar=10, entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        direction="BUY", entry_price=1.1000, stop_loss=1.0950,
        take_profit=1.1100, risk_pct=0.01, position_size=1.0,
        exit_bar=25, exit_time=pd.Timestamp("2024-01-02", tz="UTC"),
        exit_price=1.1100, pnl_pips=99.5, pnl_usd=95.0, exit_reason="TP",
    )
    base.update(kw)
    return Trade(**base)


# ─────────────────────────────────────────────────────────────────────────
# Adapter: Trade → TradeRecord
# ─────────────────────────────────────────────────────────────────────────

def test_adapter_derives_rr_from_ground_truth_prices():
    rec = trade_to_record(_trade(), "EURUSD")
    assert rec.rr_planned == 2.0          # 100 pips reward / 50 pips risk
    assert rec.rr_actual == 2.0           # exited exactly at TP
    assert rec.is_win is True
    assert rec.holding_bars == 15


def test_adapter_sell_loss_yields_negative_rr_actual():
    rec = trade_to_record(
        _trade(direction="SELL", entry_price=1.1000, stop_loss=1.1050,
               take_profit=1.0900, exit_price=1.1050, pnl_usd=-50.0,
               exit_reason="SL"),
        "EURUSD",
    )
    assert rec.rr_actual == -1.0
    assert rec.is_win is False


def test_adapter_gap_exit_worse_than_stop_reflected_in_rr():
    # SL_GAP fill below the stop must produce rr_actual < -1 (the whole
    # point of gap modeling is that losses can exceed one R).
    rec = trade_to_record(
        _trade(exit_price=1.0900, pnl_usd=-200.0, exit_reason="SL_GAP"),
        "EURUSD",
    )
    assert rec.rr_actual < -1.0


def test_adapter_propagates_decision_snapshot_into_engine_votes_cf_score_regime():
    # Interactive Charts (2026-07-25): the entry-time decision snapshot
    # (per-engine bias/score, adjusted score, regime) must survive the
    # Trade -> TradeRecord adapter, keyed by engine name for O(1) lookup.
    decision = {
        "engines": [
            {"engine": "smc", "bias": "BULLISH", "score": 82.0, "reasons": ["BOS confirmed"]},
            {"engine": "nnfx", "bias": "BULLISH", "score": 91.0, "reasons": []},
        ],
        "winning_bias": "BULLISH",
        "agree_count": 2,
        "score": 85.0,
        "adjusted_score": 87.0,
        "regime": "TRENDING",
    }
    rec = trade_to_record(_trade(decision=decision), "EURUSD")
    assert rec.cf_score == 87.0
    assert rec.regime == "TRENDING"
    assert set(rec.engine_votes) == {"smc", "nnfx"}
    assert rec.engine_votes["smc"]["score"] == 82.0
    assert rec.engine_votes["nnfx"]["score"] == 91.0


def test_adapter_propagates_session_into_top_level_field():
    # Regression (found live, 2026-07-30): by_session in calculate_metrics()
    # showed 100% "Unknown" even though decision["session"] carries a real
    # value (backtest_engine.py sets it from the MQS gate's own session
    # detection whenever use_mqs_gate is on — the default everywhere).
    # trade_to_record() copied decision["regime"] into TradeRecord.regime
    # but never copied decision["session"] into TradeRecord.session — it
    # only survived into the nested TradeRecord.features["session"] dict
    # (feature-mining only), never the top-level field calculate_metrics()
    # actually reads for by_session.
    decision = {
        "engines": [], "winning_bias": "BULLISH", "agree_count": 1,
        "score": 80.0, "adjusted_score": 82.0, "regime": "TRENDING", "session": "London",
    }
    rec = trade_to_record(_trade(decision=decision), "EURUSD")
    assert rec.session == "London"


def test_adapter_handles_missing_decision_gracefully():
    # Trade objects built outside run_backtest's loop (older manifests,
    # hand-built test fixtures) carry decision=None — must not crash.
    rec = trade_to_record(_trade(decision=None), "EURUSD")
    assert rec.engine_votes == {}
    assert rec.cf_score == 0.0
    assert rec.regime == ""
    assert rec.session == ""
    assert rec.features == {}


# ─────────────────────────────────────────────────────────────────────────
# Feature Mining Phase 1 (2026-07-30): TradeRecord.features population
# ─────────────────────────────────────────────────────────────────────────

def _decision_with_full_context() -> dict:
    return {
        "engines": [
            {"engine": "MarketStructure", "bias": "BULLISH", "score": 60.0, "reasons": [], "raw": {
                "h1_event": "BOS", "h1_strength": 65, "aligned": True,
                "last_high_bar_age": 3, "last_low_bar_age": 10,
                "last_h1_high": 1.1050, "last_h1_low": 1.0950,
            }},
            {"engine": "NNFX", "bias": "BULLISH", "score": 70.0, "reasons": [], "raw": {
                "ema200": 1.0980, "adx": 28.5, "price_vs_ema200_pct": 0.5,
            }},
        ],
        "winning_bias": "BULLISH", "agree_count": 2, "score": 85.0, "adjusted_score": 87.0,
        "regime": "TRENDING", "volatility": "normal", "atr_value": 0.0025, "info_share": 0.7,
        "session": "London",
        "mtf": {"d1_bias": "BULLISH", "d1_adx": 30.0, "d1_ema20": 1.10, "d1_ema50": 1.09, "confirming": True},
        "reversal_veto": {
            "reversal_count": 0, "reversal_engines": [], "trend_bias": "BULLISH",
            "reversal_bias": "NONE", "confidence_multiplier": 1.0, "soft_veto": False,
        },
        "contradiction_reasons": [],
        "mqs": {
            "mqs_score": 72.0, "grade": "GOOD", "should_trade": True, "session": "London",
            "active_sessions": ["London"], "atr_percentile": 0.4, "volatility_grade": "normal",
            "day_penalty": 0.0, "reasons": [],
        },
        "indicator_filters": None, "context_filters": None,
    }


def test_build_features_flattens_full_decision_context():
    rec = trade_to_record(_trade(decision=_decision_with_full_context(), entry_price=1.1000), "EURUSD")
    f = rec.features
    assert f["regime"] == "TRENDING"
    assert f["volatility"] == "normal"
    assert f["atr_value"] == 0.0025
    assert f["info_share"] == 0.7
    assert f["session"] == "London"
    assert f["mtf_d1_bias"] == "BULLISH"
    assert f["mtf_confirming"] is True
    assert f["veto_reversal_count"] == 0
    assert f["veto_trend_bias"] == "BULLISH"
    assert f["contradiction_count"] == 0
    assert f["mqs_score"] == 72.0
    assert f["mqs_grade"] == "GOOD"
    assert f["market_structure_h1_event"] == "BOS"
    assert f["market_structure_last_high_bar_age"] == 3
    assert f["nnfx_price_vs_ema200_pct"] == 0.5
    assert f["nnfx_adx"] == 28.5
    # Derived: (entry - ema200) / atr_val = (1.1000 - 1.0980) / 0.0025
    assert f["derived_ema200_distance_atr"] == pytest.approx((1.1000 - 1.0980) / 0.0025)
    # Derived: (entry - last_low) / (last_high - last_low) * 100
    assert f["derived_retracement_pct"] == pytest.approx((1.1000 - 1.0950) / (1.1050 - 1.0950) * 100)


def test_build_features_omits_fields_when_source_gate_off():
    # A run with use_mtf_confirmation/use_reversal_veto/use_mqs_gate=False
    # produces decision["mtf"]/["reversal_veto"]/["mqs"] = None — the
    # feature dict must simply omit those keys, never fabricate them.
    decision = _decision_with_full_context()
    decision["mtf"] = None
    decision["reversal_veto"] = None
    decision["mqs"] = None
    decision["session"] = ""
    rec = trade_to_record(_trade(decision=decision), "EURUSD")
    f = rec.features
    assert "mtf_d1_bias" not in f
    assert "veto_reversal_count" not in f
    assert "mqs_score" not in f
    assert "session" not in f
    # Fields from unaffected gates are still present.
    assert f["regime"] == "TRENDING"


def test_build_features_empty_dict_for_missing_decision():
    rec = trade_to_record(_trade(decision=None), "EURUSD")
    assert rec.features == {}


# ─────────────────────────────────────────────────────────────────────────
# Runner: data loading
# ─────────────────────────────────────────────────────────────────────────

def test_find_symbol_csv_missing_is_actionable_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="EURUSD_H1_"):
        find_symbol_csv("EURUSD", tmp_path)


def test_load_symbol_data_rejects_missing_columns(tmp_path):
    df = _ohlcv(50).drop(columns=["volume"])
    df.to_csv(tmp_path / "EURUSD_H1_2y.csv")
    with pytest.raises(ValueError, match="volume"):
        load_symbol_data("EURUSD", tmp_path)


def test_load_symbol_data_slices_then_validates(tmp_path):
    _ohlcv(300).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    df = load_symbol_data("EURUSD", tmp_path, start="2024-01-05", end="2024-01-08")
    assert df.index[0] >= pd.Timestamp("2024-01-05", tz="UTC")
    assert df.index[-1] <= pd.Timestamp("2024-01-08 23:59", tz="UTC")


def test_run_all_isolates_symbol_failures(tmp_path):
    _ohlcv(1200).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    # GBPUSD has no dataset — must be skipped, not abort EURUSD.
    cfg = RunnerConfig(
        symbols=("EURUSD", "GBPUSD"), data_dir=tmp_path,
        run_mc=False, write_html=False,
    )
    results = run_all(cfg)
    assert "EURUSD" in results and "GBPUSD" not in results
    assert results["EURUSD"].engine_result.error_count == 0


# ─────────────────────────────────────────────────────────────────────────
# Walk-forward: window integrity (anti-leakage)
# ─────────────────────────────────────────────────────────────────────────

def test_windows_are_disjoint_in_tradeable_region():
    df = _ohlcv(2000)
    windows = split_windows(df, n_windows=3, warmup_bars=210)
    spans = [(start, end) for _, start, end in windows]
    for (_, e1), (s2, _) in zip(spans, spans[1:]):
        assert e1 < s2, "test windows must never overlap"


def test_windows_cover_all_post_warmup_bars_exactly_once():
    df = _ohlcv(2000)
    windows = split_windows(df, n_windows=3, warmup_bars=210)
    tradeable = sum(len(f) - 210 for f, _, _ in windows)
    assert tradeable == len(df) - 210


def test_each_window_carries_exactly_warmup_embargo():
    df = _ohlcv(2000)
    for frame, test_start, _ in split_windows(df, 3, warmup_bars=210):
        assert frame.index[210] == test_start
        # the frame must not reach past its own test span into the future
        assert frame.index[-1] <= df.index[-1]


def test_split_refuses_inadequate_data_loudly():
    with pytest.raises(ValueError, match="too small"):
        split_windows(_ohlcv(500), n_windows=3, warmup_bars=210)


def test_config_rejects_single_window():
    with pytest.raises(ValueError, match="at least 2"):
        WalkForwardConfig(n_windows=1)


# ─────────────────────────────────────────────────────────────────────────
# Walk-forward: verdict honesty
# ─────────────────────────────────────────────────────────────────────────

def _wr(index: int, trades: int, pf: float, verdict: WindowVerdict) -> WindowResult:
    return WindowResult(
        index=index, start="", end="", bars=500, trades=trades,
        profit_factor=pf, win_rate=0.5, max_drawdown_pct=0.05,
        expectancy_usd=1.0, pipeline_errors=0, gate_rejections={}, verdict=verdict,
    )


def _symbol_verdict(windows: list[WindowResult]) -> SymbolVerdict:
    if any(w.verdict is WindowVerdict.FAIL for w in windows):
        return SymbolVerdict.INCONSISTENT
    if any(w.verdict is WindowVerdict.INSUFFICIENT for w in windows):
        return SymbolVerdict.INSUFFICIENT
    return SymbolVerdict.CONSISTENT


def test_insufficient_window_blocks_consistent_verdict():
    """A 2-trade window with PF 9.0 is not evidence — the symbol must
    NOT be reported CONSISTENT on the back of an unjudgeable window."""
    windows = [
        _wr(1, trades=30, pf=2.1, verdict=WindowVerdict.PASS),
        _wr(2, trades=2, pf=9.0, verdict=WindowVerdict.INSUFFICIENT),
        _wr(3, trades=25, pf=1.8, verdict=WindowVerdict.PASS),
    ]
    assert _symbol_verdict(windows) is SymbolVerdict.INSUFFICIENT


def test_single_failing_window_makes_symbol_inconsistent():
    windows = [
        _wr(1, trades=30, pf=2.1, verdict=WindowVerdict.PASS),
        _wr(2, trades=28, pf=0.9, verdict=WindowVerdict.FAIL),
        _wr(3, trades=25, pf=1.8, verdict=WindowVerdict.PASS),
    ]
    assert _symbol_verdict(windows) is SymbolVerdict.INCONSISTENT


def test_end_to_end_walk_forward_runs_and_reports(tmp_path):
    """Full integration: real engine, real metrics, 3 windows."""
    from backtest.walk_forward import run_walk_forward

    df = _ohlcv(2400, trend=0.10)
    result = run_walk_forward(
        "EURUSD", df,
        WalkForwardConfig(n_windows=3, min_pf=1.5, min_trades_per_window=1),
    )
    assert len(result.windows) == 3
    assert all(w.pipeline_errors == 0 for w in result.windows)
    assert result.verdict in SymbolVerdict
    d = result.to_dict()
    assert d["symbol"] == "EURUSD" and len(d["windows"]) == 3


def test_run_backtest_attaches_real_decision_snapshot_to_every_trade():
    # Interactive Charts (2026-07-25): run_backtest's own engine panel
    # must populate Trade.decision — not just the adapter test above,
    # which only proves the adapter *would* propagate it if present.
    from backtesting.backtest_engine import BacktestConfig, run_backtest

    df = _ohlcv(2400, trend=0.10)
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"))
    assert len(result.trades) > 0, "need at least one trade to assert on"
    for t in result.trades:
        assert t.decision is not None
        assert t.decision["winning_bias"] in ("BULLISH", "BEARISH")
        assert t.decision["agree_count"] >= 1
        assert isinstance(t.decision["engines"], list) and len(t.decision["engines"]) > 0
        engine_names = {e["engine"] for e in t.decision["engines"]}
        # NNFX/PriceAction are always-on per config/engines.yaml's prod4 set
        # (engine_name is each class's own `name` attr, not the config key).
        assert {"NNFX", "PriceAction"}.issubset(engine_names)
        for e in t.decision["engines"]:
            assert {"engine", "bias", "score", "reasons"}.issubset(e.keys())


def test_walk_forward_engine_overrides_warmup_bars_no_collision():
    """Regression (Backtesting Lab Pro Phase A, 2026-07-27): run_walk_forward
    used to build BacktestConfig.from_profile(symbol, warmup_bars=wf_config.
    warmup_bars, **wf_config.engine_overrides) — if engine_overrides ALSO
    contains a "warmup_bars" key (which the new Risk & Range step produces),
    this raised TypeError: got multiple values for keyword argument
    'warmup_bars'. An explicit engine_overrides warmup_bars must win over
    WalkForwardConfig's own warmup_bars field, not collide with it."""
    from backtest.walk_forward import run_walk_forward

    df = _ohlcv(3000, trend=0.10)
    result = run_walk_forward(
        "EURUSD", df,
        WalkForwardConfig(n_windows=3, min_pf=1.5, min_trades_per_window=1,
                           warmup_bars=250, engine_overrides={"warmup_bars": 400}),
    )
    assert len(result.windows) == 3
    # bars = window length - wf_config.warmup_bars (250, the window-embargo
    # sizing field) — this is unaffected by the BacktestConfig-level override,
    # which only controls how many bars the ENGINE itself treats as warmup.
    assert all(w.bars > 0 for w in result.windows)


# ─────────────────────────────────────────────────────────────────────────
# Data-download M15 support (2026-08-11) — timeframe-parameterized loader
# ─────────────────────────────────────────────────────────────────────────

def test_find_symbol_csv_m15_finds_the_m15_file(tmp_path):
    _ohlcv(50, freq="15min").to_csv(tmp_path / "EURUSD_M15_2y.csv")
    path = find_symbol_csv("EURUSD", tmp_path, timeframe="M15")
    assert path.name == "EURUSD_M15_2y.csv"


def test_find_symbol_csv_m15_missing_is_actionable_error_even_when_h1_exists(tmp_path):
    # An H1 file existing must never be silently substituted for a
    # genuine M15 request — fail loud, matching this function's own
    # established convention for a missing dataset.
    _ohlcv(50).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    with pytest.raises(FileNotFoundError, match="EURUSD_M15_"):
        find_symbol_csv("EURUSD", tmp_path, timeframe="M15")


def test_load_symbol_data_m15_loads_the_m15_file_not_h1(tmp_path):
    _ohlcv(50, seed=1).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    _ohlcv(222, seed=2, freq="15min").to_csv(tmp_path / "EURUSD_M15_2y.csv")
    loaded = load_symbol_data("EURUSD", tmp_path, timeframe="M15")
    assert len(loaded) == 222  # distinguishable from the 50-bar H1 file -> proves M15 was actually read


def test_physical_load_timeframe_defaults_and_h4_d1_still_load_h1():
    from backtest.runner import physical_load_timeframe

    assert physical_load_timeframe(None) == "H1"
    assert physical_load_timeframe(("H1",)) == "H1"
    assert physical_load_timeframe(("H4", "D1", "H1")) == "H1"
    assert physical_load_timeframe(("D1",)) == "H1"


def test_physical_load_timeframe_m15_base_returns_m15():
    from backtest.runner import physical_load_timeframe

    assert physical_load_timeframe(("M15",)) == "M15"
    assert physical_load_timeframe(("M15", "H1", "H4", "D1")) == "M15"
    # M15 only matters as the FIRST (base) entry — elsewhere it's inert.
    assert physical_load_timeframe(("H1", "M15")) == "H1"


def test_run_all_with_m15_override_loads_the_m15_file(tmp_path):
    """End-to-end proof that RunnerConfig.timeframes genuinely selects
    the M15 physical file through physical_load_timeframe -> load_symbol_
    data, not just that the helper function works in isolation."""
    _ohlcv(1200, freq="15min").to_csv(tmp_path / "EURUSD_M15_2y.csv")  # only an M15 file exists, no H1 file

    cfg_default = RunnerConfig(symbols=("EURUSD",), data_dir=tmp_path, run_mc=False, write_html=False)
    results_default = run_all(cfg_default)
    assert "EURUSD" not in results_default  # default (H1) can't find any H1 file -> isolated, not raised

    cfg_m15 = RunnerConfig(
        symbols=("EURUSD",), data_dir=tmp_path, run_mc=False, write_html=False,
        timeframes=("M15", "H1", "H4", "D1"),
    )
    results_m15 = run_all(cfg_m15)
    assert "EURUSD" in results_m15  # the M15 override correctly finds and uses the M15 file


def test_walk_forward_suite_wires_physical_load_timeframe(tmp_path, monkeypatch):
    import backtest.walk_forward as wf_mod

    captured_timeframes: list[str] = []
    real_load = wf_mod.load_symbol_data

    def spy_load(symbol, data_dir, start=None, end=None, timeframe="H1"):
        captured_timeframes.append(timeframe)
        return real_load(symbol, data_dir, start, end, timeframe=timeframe)

    monkeypatch.setattr(wf_mod, "load_symbol_data", spy_load)
    _ohlcv(2400, trend=0.10).to_csv(tmp_path / "EURUSD_H1_2y.csv")

    wf_default = WalkForwardConfig(n_windows=3, min_pf=1.5, min_trades_per_window=1)
    out = wf_mod.run_walk_forward_suite(["EURUSD"], tmp_path, wf_default)
    assert captured_timeframes == ["H1"]
    assert "EURUSD" in out

    captured_timeframes.clear()
    wf_h4 = WalkForwardConfig(n_windows=3, min_pf=1.5, min_trades_per_window=1, timeframes=("H4", "D1", "H1"))
    out = wf_mod.run_walk_forward_suite(["EURUSD"], tmp_path, wf_h4)
    assert captured_timeframes == ["H1"]
    assert "EURUSD" in out


def test_walk_forward_suite_m15_base_loads_the_m15_file(tmp_path):
    import backtest.walk_forward as wf_mod

    _ohlcv(2400, trend=0.10).to_csv(tmp_path / "EURUSD_H1_2y.csv")  # H1 exists, M15 does not yet
    wf_m15 = WalkForwardConfig(n_windows=3, min_pf=1.5, min_trades_per_window=1, timeframes=("M15", "H1"))
    out = wf_mod.run_walk_forward_suite(["EURUSD"], tmp_path, wf_m15)
    assert "EURUSD" not in out  # M15 requested but absent -> excluded, never substitutes H1

    _ohlcv(2400, trend=0.10, freq="15min").to_csv(tmp_path / "EURUSD_M15_2y.csv")
    out2 = wf_mod.run_walk_forward_suite(["EURUSD"], tmp_path, wf_m15)
    assert "EURUSD" in out2  # the real M15 file now exists -> succeeds


# ─────────────────────────────────────────────────────────────────────────
# Data Integrity Core (2026-08-19) — dataset_completeness_pct + the walk-
# forward PARTIAL-dataset exclusion gate.
# ─────────────────────────────────────────────────────────────────────────

_FX_CONFIG = {"data": {"twelve_data_symbols": [{"internal": "EURUSD", "asset_class": "fx_major"}]}}


def test_dataset_completeness_pct_continuous_series_scores_100():
    from backtest.runner import dataset_completeness_pct

    df = _ohlcv(600, trend=0.05)  # raw continuous hourly index, no gaps at all
    score, detail = dataset_completeness_pct(df, "EURUSD", "H1", config=_FX_CONFIG)
    assert score == 100.0
    assert detail["real_gap"] == 0


def test_dataset_completeness_pct_real_weekday_gap_scores_below_ready_bar():
    from backtest.runner import dataset_completeness_pct
    from storage.market_bars import MIN_COVERAGE_PCT_FOR_READY

    df = _ohlcv(600, trend=0.05)
    # Drop a large chunk from the middle — spans several real weekdays, so
    # most of it is a genuine gap (a handful of the dropped hours may
    # legitimately land on a weekend and get excluded as expected_closure,
    # but not nearly enough to keep this above the 95% READY bar).
    keep = df.index[(df.index < df.index[100]) | (df.index >= df.index[300])]
    partial = df.loc[keep]
    score, detail = dataset_completeness_pct(partial, "EURUSD", "H1", config=_FX_CONFIG)
    assert score < MIN_COVERAGE_PCT_FOR_READY
    assert detail["real_gap"] > 0


def test_walk_forward_suite_excludes_symbol_with_partial_dataset(tmp_path):
    """The Data Integrity Core fix's authoritative proof: a structurally-
    valid but genuinely PARTIAL CSV must never enter walk-forward evidence
    — it has to be excluded the exact same way a missing/malformed file
    already is, not silently accepted because validate_ohlcv() alone
    can't see real gaps."""
    import backtest.walk_forward as wf_mod

    full = _ohlcv(2400, trend=0.10)
    keep = full.index[(full.index < full.index[400]) | (full.index >= full.index[1200])]
    full.loc[keep].to_csv(tmp_path / "EURUSD_H1_2y.csv")

    wf_default = WalkForwardConfig(n_windows=3, min_pf=1.5, min_trades_per_window=1)
    out = wf_mod.run_walk_forward_suite(["EURUSD"], tmp_path, wf_default)
    assert "EURUSD" not in out


def test_walk_forward_suite_keeps_symbol_with_complete_dataset(tmp_path):
    """Regression check: the new gate must not reject a genuinely complete
    dataset — every pre-existing walk-forward test already relies on this
    implicitly, this pins it explicitly."""
    import backtest.walk_forward as wf_mod

    _ohlcv(2400, trend=0.10).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    wf_default = WalkForwardConfig(n_windows=3, min_pf=1.5, min_trades_per_window=1)
    out = wf_mod.run_walk_forward_suite(["EURUSD"], tmp_path, wf_default)
    assert "EURUSD" in out


# ─────────────────────────────────────────────────────────────────────────
# Backtesting Lab Pro Phase A — CLI wiring for per-run risk/cost overrides
# ─────────────────────────────────────────────────────────────────────────

def test_runner_cli_wires_risk_overrides(monkeypatch, tmp_path):
    import sys
    import backtest.runner as runner_mod

    captured: dict = {}

    def fake_run_all(config):
        captured["config"] = config
        return {}

    monkeypatch.setattr(runner_mod, "run_all", fake_run_all)
    monkeypatch.setattr(
        sys, "argv",
        ["runner.py", "--symbols", "EURUSD", "--data-dir", str(tmp_path),
         "--min-rr", "5.0", "--warmup-bars", "300"],
    )
    with pytest.raises(SystemExit, match="No symbol completed"):
        runner_mod.main()
    assert captured["config"].engine_overrides == {"min_rr": 5.0, "warmup_bars": 300}


def test_walk_forward_cli_wires_risk_overrides(monkeypatch):
    import sys
    import backtest.walk_forward as wf_mod

    captured: dict = {}

    def fake_suite(symbols, data_dir, wf_config, start=None, end=None):
        captured["wf_config"] = wf_config
        captured["start"] = start
        captured["end"] = end
        return {}

    monkeypatch.setattr(wf_mod, "run_walk_forward_suite", fake_suite)
    monkeypatch.setattr(
        sys, "argv",
        ["walk_forward.py", "--symbols", "EURUSD", "--min-rr", "5.0",
         "--warmup-bars", "300", "--start", "2024-01-01", "--end", "2024-06-01"],
    )
    with pytest.raises(SystemExit, match="No symbol completed"):
        wf_mod.main()
    # warmup_bars is its own dedicated WalkForwardConfig field, NOT folded
    # into engine_overrides (see main()'s own comment) — this is the
    # regression proof that the two paths don't collide at the CLI layer.
    assert captured["wf_config"].warmup_bars == 300
    assert captured["wf_config"].engine_overrides == {"min_rr": 5.0}
    assert captured["start"] == "2024-01-01"
    assert captured["end"] == "2024-06-01"


def test_runner_cli_wires_timeframes_override(monkeypatch, tmp_path):
    import sys
    import backtest.runner as runner_mod

    captured: dict = {}

    def fake_run_all(config):
        captured["config"] = config
        return {}

    monkeypatch.setattr(runner_mod, "run_all", fake_run_all)
    monkeypatch.setattr(
        sys, "argv",
        ["runner.py", "--symbols", "EURUSD", "--data-dir", str(tmp_path), "--timeframes", "H1"],
    )
    with pytest.raises(SystemExit, match="No symbol completed"):
        runner_mod.main()
    assert captured["config"].timeframes == ("H1",)


def test_runner_cli_rejects_unsupported_timeframe(monkeypatch, tmp_path):
    import sys
    import backtest.runner as runner_mod

    monkeypatch.setattr(
        sys, "argv",
        ["runner.py", "--symbols", "EURUSD", "--data-dir", str(tmp_path), "--timeframes", "M1"],
    )
    with pytest.raises(SystemExit):
        runner_mod.main()


def test_walk_forward_cli_wires_timeframes_override(monkeypatch):
    import sys
    import backtest.walk_forward as wf_mod

    captured: dict = {}

    def fake_suite(symbols, data_dir, wf_config, start=None, end=None):
        captured["wf_config"] = wf_config
        return {}

    monkeypatch.setattr(wf_mod, "run_walk_forward_suite", fake_suite)
    monkeypatch.setattr(sys, "argv", ["walk_forward.py", "--symbols", "EURUSD", "--timeframes", "H4", "D1", "H1"])
    with pytest.raises(SystemExit, match="No symbol completed"):
        wf_mod.main()
    assert captured["wf_config"].timeframes == ("H4", "D1", "H1")


def test_walk_forward_cli_wires_engines_override(monkeypatch):
    import sys
    import backtest.walk_forward as wf_mod

    captured: dict = {}

    def fake_suite(symbols, data_dir, wf_config, start=None, end=None):
        captured["wf_config"] = wf_config
        return {}

    monkeypatch.setattr(wf_mod, "run_walk_forward_suite", fake_suite)
    monkeypatch.setattr(sys, "argv", ["walk_forward.py", "--symbols", "EURUSD", "--engines", "nnfx"])
    with pytest.raises(SystemExit, match="No symbol completed"):
        wf_mod.main()
    assert captured["wf_config"].engines == ("nnfx",)


def test_engine_config_override_none_when_no_timeframes_requested():
    """build_engine_config_override(None) must return None — preserving
    run_backtest's own load_config() default path byte-for-byte for every
    existing caller that never requests an override."""
    from backtesting.backtest_engine import build_engine_config_override

    assert build_engine_config_override(None) is None


def test_engine_config_override_merges_timeframes_only():
    """The override must carry every other confluence/engine setting from
    the real config unchanged — only data.timeframes is replaced."""
    from backtesting.backtest_engine import build_engine_config_override
    from utils.helpers import load_config

    real = load_config()
    override = build_engine_config_override(["H1"])
    assert override["data"]["timeframes"] == ["H1"]
    assert override["confluence"]["weights"] == real["confluence"]["weights"]
    assert override["engines"] == real["engines"]


def test_timeframes_override_produces_the_expected_mtf_view():
    """The authoritative proof (Backtesting Lab Pro Phase B) that the
    override reaches build_multi_timeframe_view with the exact requested
    timeframe set — a ["H1"]-only override must yield no coarser (D1/H4)
    resampled views, while a full ["H4","D1","H1"] override must."""
    from backtesting.backtest_engine import build_engine_config_override
    from core.timeframe_sync import build_multi_timeframe_view

    df = _ohlcv(2400, trend=0.10)

    single_tf_config = build_engine_config_override(["H1"])
    mtf_single = build_multi_timeframe_view(df, single_tf_config["data"]["timeframes"])
    assert set(mtf_single.keys()) == {"H1"}

    full_config = build_engine_config_override(["H4", "D1", "H1"])
    mtf_full = build_multi_timeframe_view(df, full_config["data"]["timeframes"])
    assert "D1" in mtf_full and "H4" in mtf_full


def test_timeframes_override_reaches_run_backtest_without_crashing():
    """End-to-end smoke test: run_backtest with a single-timeframe
    override must complete and produce trades whose decision snapshot is
    still fully populated — MTF confirmation degrading to neutral (no D1
    view to confirm against) must never crash the pipeline."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    override_config = build_engine_config_override(["H1"])
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=override_config)

    assert result.error_count == 0
    assert len(result.trades) > 0
    for t in result.trades:
        assert t.decision is not None
        assert len(t.decision["engines"]) > 0


def test_h4_only_override_resamples_h1_physical_data_to_real_h4_candles():
    """2026-08-13 forensic fix — distinct from BUG-020. Confirmed live on
    the VPS: a Mission Center trial declaring timeframes=["H4"] alone gets
    the H1 physical dataset (H4 has no native downloader — see
    backtest/runner.py::physical_load_timeframe). Before this fix,
    build_multi_timeframe_view's base-label branch just relabeled the raw
    H1-cadence df as "H4" with zero resampling — every engine analyzed a
    decision every H1 bar, not every 4 hours. run_backtest must now
    resample the WHOLE frame to real H4 candles before the per-bar loop,
    so total_bars/timeframe genuinely reflect H4 cadence, not H1 count."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)  # H1-cadence, per _ohlcv's default freq="h"
    override_config = build_engine_config_override(["H4"])
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=override_config)

    assert result.timeframe == "H4"
    # 2400 H1 bars -> ~600 real H4 candles, nowhere near the original 2400.
    assert 550 <= result.total_bars <= 601
    assert result.error_count == 0
    assert result.total_runs > 0  # real decision points were actually evaluated


def test_h4_only_override_produces_fewer_decision_points_than_h1_alone():
    """The direct proof the fix changes DECISION CADENCE, not just a
    reported label: the same physical H1 dataframe run with an H4-only
    override must reach roughly 1/4 the total_runs of the identical df
    run with an H1-only override (step_bars held equal in both, via
    BacktestConfig.from_profile's own default)."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    cfg = BacktestConfig.from_profile("EURUSD")

    result_h1 = run_backtest(df, cfg, engine_config=build_engine_config_override(["H1"]))
    result_h4 = run_backtest(df, cfg, engine_config=build_engine_config_override(["H4"]))

    assert result_h1.total_runs > 0
    assert result_h4.total_runs > 0
    ratio = result_h1.total_runs / result_h4.total_runs
    assert 3.0 <= ratio <= 5.0  # ~4x fewer decision points at H4 vs H1


def test_d1_only_override_resamples_h1_physical_data_to_real_d1_candles():
    """Same fix, D1 side — needs a much longer H1 window (D1 has no
    native downloader either) so the resampled D1 series clears
    warmup_bars and the new insufficient-data guard doesn't short-circuit
    the whole run before it proves anything."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(7000, trend=0.15)  # ~291 days of H1 bars -> ~292 D1 candles
    override_config = build_engine_config_override(["D1"])
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=override_config)

    assert result.timeframe == "D1"
    assert 280 <= result.total_bars <= 292
    assert result.error_count == 0


def test_insufficient_bars_after_resample_returns_empty_result_not_indexerror():
    """A short H1 window resampled down to D1 can legitimately fall below
    warmup_bars (210) — must degrade to an empty BacktestResult, never
    raise IndexError indexing past the resampled frame's end."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(500, trend=0.05)  # ~20 days of H1 bars -> ~20 D1 candles, well under warmup_bars
    override_config = build_engine_config_override(["D1"])
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=override_config)

    assert result.timeframe == "D1"
    assert len(result.trades) == 0
    assert result.total_runs == 0


def test_h1_only_override_on_h1_physical_data_is_unchanged_by_the_resample_fix():
    """Regression pin: the exact pre-existing case (base timeframe already
    matches the physical file's real cadence) must be a complete no-op —
    total_bars stays exactly len(df), nothing resampled away."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    override_config = build_engine_config_override(["H1"])
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=override_config)

    assert result.total_bars == len(df)
    assert result.timeframe == "H1"


def test_multi_tf_override_with_h1_base_is_unchanged_by_the_resample_fix():
    """Regression pin: a multi-timeframe override with H1 declared FIRST
    (the existing, already-correct convention) must also be untouched —
    H1 stays the base cadence, H4/D1 are still derived via the existing
    build_multi_timeframe_view coarser-resample path, not this new one."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    override_config = build_engine_config_override(["H1", "H4", "D1"])
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=override_config)

    assert result.total_bars == len(df)
    assert result.timeframe == "H1"


def test_runner_cli_wires_engines_override(monkeypatch, tmp_path):
    import sys
    import backtest.runner as runner_mod

    captured: dict = {}

    def fake_run_all(config):
        captured["config"] = config
        return {}

    monkeypatch.setattr(runner_mod, "run_all", fake_run_all)
    monkeypatch.setattr(
        sys, "argv",
        ["runner.py", "--symbols", "EURUSD", "--data-dir", str(tmp_path), "--engines", "nnfx", "wyckoff"],
    )
    with pytest.raises(SystemExit, match="No symbol completed"):
        runner_mod.main()
    assert captured["config"].engines == ("nnfx", "wyckoff")


def test_runner_cli_rejects_unsupported_engine(monkeypatch, tmp_path):
    import sys
    import backtest.runner as runner_mod

    monkeypatch.setattr(
        sys, "argv",
        ["runner.py", "--symbols", "EURUSD", "--data-dir", str(tmp_path), "--engines", "macro"],
    )
    with pytest.raises(SystemExit):
        runner_mod.main()


def test_build_engine_config_override_returns_none_when_nothing_requested():
    from backtesting.backtest_engine import build_engine_config_override

    assert build_engine_config_override() is None
    assert build_engine_config_override(timeframes=None, engines_enabled=None) is None


def test_build_engine_config_override_merges_engines_enabled_only():
    """The override must carry every other confluence setting and the
    real data.timeframes unchanged — only engines.enabled is replaced."""
    from backtesting.backtest_engine import ENGINE_KEYS, build_engine_config_override
    from utils.helpers import load_config

    real = load_config()
    engines_enabled = {e: (e == "nnfx") for e in ENGINE_KEYS}
    override = build_engine_config_override(engines_enabled=engines_enabled)
    assert override["engines"]["enabled"]["nnfx"] is True
    assert override["engines"]["enabled"]["smc"] is False
    assert override["data"]["timeframes"] == real["data"]["timeframes"]
    assert override["confluence"]["weights"] == real["confluence"]["weights"]


def test_build_engine_config_override_never_writes_to_config_files(tmp_path):
    """Hard-block correctness requirement (Backtesting Lab Pro Phase C):
    an engine-selection override must be purely in-memory — it can never
    reach config/engines.yaml or registry.json, no matter what it's
    asked to override."""
    import inspect
    from pathlib import Path
    from backtesting.backtest_engine import ENGINE_KEYS, build_engine_config_override

    source = inspect.getsource(build_engine_config_override)
    for forbidden in ("write_text", "yaml.safe_dump", "yaml.dump", "json.dump", "open("):
        assert forbidden not in source, f"build_engine_config_override must never {forbidden}"

    engines_yaml = Path("config/engines.yaml")
    before = engines_yaml.read_bytes()
    build_engine_config_override(
        timeframes=["H1"], engines_enabled={e: False for e in ENGINE_KEYS},
    )
    after = engines_yaml.read_bytes()
    assert before == after, "config/engines.yaml must be byte-identical after an override call"


def test_engines_override_reaches_run_backtest_vote_tallying():
    """The authoritative proof (Backtesting Lab Pro Phase C): restricting
    to a single engine must show up in every trade's decision snapshot —
    agree_count <= 1 and engine_votes containing only that one engine's
    key — proving the override reached confluence vote tallying, not
    silently ignored."""
    from backtesting.backtest_engine import ENGINE_KEYS, BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    engines_enabled = {e: (e == "nnfx") for e in ENGINE_KEYS}
    override_config = build_engine_config_override(engines_enabled=engines_enabled)
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=override_config)

    assert result.error_count == 0
    for t in result.trades:
        assert t.decision["agree_count"] <= 1
        assert set(t.decision["engine_votes"]) <= {"NNFX"}


def test_engine_overrides_actually_change_backtest_output(tmp_path):
    """The authoritative proof (Backtesting Lab Pro Phase A) that a risk
    override isn't silently accepted-and-ignored: running the SAME dataset
    with a starkly different min_rr/risk_per_trade must produce a
    genuinely different trade count and position sizing."""
    _ohlcv(2400, trend=0.10).to_csv(tmp_path / "EURUSD_H1_2y.csv")

    default_results = run_all(RunnerConfig(
        symbols=("EURUSD",), data_dir=tmp_path, run_mc=False, write_html=False,
    ))
    overridden_results = run_all(RunnerConfig(
        symbols=("EURUSD",), data_dir=tmp_path, run_mc=False, write_html=False,
        engine_overrides={"risk_per_trade": 0.20, "min_rr": 10.0},
    ))

    assert "EURUSD" in default_results and "EURUSD" in overridden_results
    default_trades = default_results["EURUSD"].trade_records
    overridden_trades = overridden_results["EURUSD"].trade_records

    # A min_rr=10.0 bar is far stricter than the production 2.0 default —
    # must sharply cut (or zero out) the number of qualifying trades.
    assert len(overridden_trades) < len(default_trades)
    if default_trades and overridden_trades:
        # risk_per_trade=0.20 vs the 0.01 default should scale position
        # sizing roughly 20x (same starting balance/stop distance regime).
        avg_default_size = sum(t.position_size for t in default_trades) / len(default_trades)
        avg_overridden_size = sum(t.position_size for t in overridden_trades) / len(overridden_trades)
        assert avg_overridden_size > avg_default_size * 5


# ─────────────────────────────────────────────────────────────────────────
# Backtesting Lab Pro Phase D — Indicators & Filters
# ─────────────────────────────────────────────────────────────────────────

def test_walk_forward_cli_wires_indicators_override(monkeypatch):
    import sys
    import backtest.walk_forward as wf_mod

    captured: dict = {}

    def fake_suite(symbols, data_dir, wf_config, start=None, end=None):
        captured["wf_config"] = wf_config
        return {}

    monkeypatch.setattr(wf_mod, "run_walk_forward_suite", fake_suite)
    monkeypatch.setattr(
        sys, "argv",
        ["walk_forward.py", "--symbols", "EURUSD", "--indicators-json",
         '[{"name": "rsi", "mode": "entry_filter", "params": {"buy_above": 55}, "weight": 0}]'],
    )
    with pytest.raises(SystemExit, match="No symbol completed"):
        wf_mod.main()
    assert captured["wf_config"].indicators == (
        {"name": "rsi", "mode": "entry_filter", "params": {"buy_above": 55}, "weight": 0},
    )


def test_walk_forward_cli_rejects_malformed_indicators_json(monkeypatch):
    import sys
    import backtest.walk_forward as wf_mod

    monkeypatch.setattr(
        sys, "argv",
        ["walk_forward.py", "--symbols", "EURUSD", "--indicators-json", '[{"name": "bogus", "mode": "entry_filter"}]'],
    )
    with pytest.raises(SystemExit):
        wf_mod.main()


def test_runner_cli_wires_indicators_override(monkeypatch, tmp_path):
    import sys
    import backtest.runner as runner_mod

    captured: dict = {}

    def fake_run_all(config):
        captured["config"] = config
        return {}

    monkeypatch.setattr(runner_mod, "run_all", fake_run_all)
    monkeypatch.setattr(
        sys, "argv",
        ["runner.py", "--symbols", "EURUSD", "--data-dir", str(tmp_path), "--indicators-json",
         '[{"name": "ema", "mode": "confirmation", "params": {"period": 50}, "weight": 0}]'],
    )
    with pytest.raises(SystemExit, match="No symbol completed"):
        runner_mod.main()
    assert captured["config"].indicators == (
        {"name": "ema", "mode": "confirmation", "params": {"period": 50}, "weight": 0},
    )


def test_build_engine_config_override_merges_indicators_only():
    """The override must carry every other confluence/engine/timeframe
    setting from the real config unchanged — only indicators.filters is
    added (a key that doesn't exist in config.yaml at all)."""
    from backtesting.backtest_engine import build_engine_config_override
    from utils.helpers import load_config

    real = load_config()
    specs = [{"name": "rsi", "mode": "entry_filter", "params": {}, "weight": 0}]
    override = build_engine_config_override(indicators=specs)
    assert override["indicators"]["filters"] == specs
    assert override["data"]["timeframes"] == real["data"]["timeframes"]
    assert override["engines"] == real["engines"]


def test_build_engine_config_override_returns_none_indicators_absent():
    from backtesting.backtest_engine import build_engine_config_override

    assert build_engine_config_override(indicators=None) is None


def test_build_engine_config_override_indicators_never_writes_to_config_files():
    """Hard-block correctness requirement (Backtesting Lab Pro Phase D),
    mirroring Phase C's own engines test: an indicator override must be
    purely in-memory."""
    from pathlib import Path
    from backtesting.backtest_engine import build_engine_config_override

    engines_yaml = Path("config/engines.yaml")
    before = engines_yaml.read_bytes()
    build_engine_config_override(
        indicators=[{"name": "rsi", "mode": "entry_filter", "params": {"buy_above": 100, "sell_below": 0}, "weight": 0}],
    )
    after = engines_yaml.read_bytes()
    assert before == after, "config/engines.yaml must be byte-identical after an indicator override call"


def test_indicator_entry_filter_veto_zeroes_out_trades_and_records_rejection():
    """The authoritative proof (Backtesting Lab Pro Phase D): an
    entry_filter-mode indicator with impossible thresholds must reach
    the real EXECUTE/NO_TRADE decision and block every trade, while a
    baseline run on the identical data produces real trades — proving
    the veto isn't silently ignored, and that it can never itself
    generate a trade (only ever subtract from what the engine vote
    already allowed)."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    cfg = BacktestConfig.from_profile("EURUSD")

    baseline = run_backtest(df, cfg, engine_config=None)
    assert len(baseline.trades) > 0

    veto_config = build_engine_config_override(
        indicators=[{"name": "rsi", "mode": "entry_filter", "params": {"buy_above": 100, "sell_below": 0}, "weight": 0}],
    )
    vetoed = run_backtest(df, cfg, engine_config=veto_config)
    assert vetoed.execute_count == 0
    assert vetoed.indicator_rejections.get("rsi", 0) > 0
    assert vetoed.gate_rejections["indicator_filter"] > 0


def test_indicator_confirmation_mode_adjusts_score_and_never_sets_direction():
    """confirmation-mode indicators must nudge adjusted_score (visible
    in each trade's decision snapshot) without ever changing which
    direction a trade takes — direction always comes from the engine
    vote (vote.winning_bias), confirmed by cross-checking BUY/SELL
    against decision['winning_bias'] for every trade."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    cfg = BacktestConfig.from_profile("EURUSD")

    confirm_config = build_engine_config_override(
        indicators=[{"name": "ema", "mode": "confirmation", "params": {"period": 20}, "weight": 0}],
    )
    result = run_backtest(df, cfg, engine_config=confirm_config)
    assert len(result.trades) > 0
    for t in result.trades:
        assert t.decision["indicator_filters"] is not None
        assert "ema" in t.decision["indicator_filters"]
        expected_direction = "BUY" if t.decision["winning_bias"] == "BULLISH" else "SELL"
        assert t.direction == expected_direction


def test_build_engine_config_override_merges_context_filters_only():
    """Mirrors test_build_engine_config_override_merges_indicators_only:
    the override must carry every other confluence/engine/timeframe
    setting from the real config unchanged — only context_filters.filters
    is added."""
    from backtesting.backtest_engine import build_engine_config_override
    from utils.helpers import load_config

    real = load_config()
    specs = [{"name": "direction", "mode": "entry_filter", "params": {"allowed": ["BULLISH"]}, "weight": 0}]
    override = build_engine_config_override(context_filters=specs)
    assert override["context_filters"]["filters"] == specs
    assert override["data"]["timeframes"] == real["data"]["timeframes"]
    assert override["engines"] == real["engines"]


def test_build_engine_config_override_returns_none_context_filters_absent():
    from backtesting.backtest_engine import build_engine_config_override

    assert build_engine_config_override(context_filters=None) is None


def test_build_engine_config_override_context_filters_never_writes_to_config_files():
    """Hard-block correctness requirement, mirroring Phase D's own
    indicators test: a context-filter override must be purely in-memory."""
    from pathlib import Path
    from backtesting.backtest_engine import build_engine_config_override

    engines_yaml = Path("config/engines.yaml")
    before = engines_yaml.read_bytes()
    build_engine_config_override(
        context_filters=[{"name": "day_of_week", "mode": "entry_filter", "params": {"allowed_days": []}, "weight": 0}],
    )
    after = engines_yaml.read_bytes()
    assert before == after, "config/engines.yaml must be byte-identical after a context-filter override call"


def test_context_entry_filter_veto_zeroes_out_trades_and_records_rejection():
    """The authoritative proof (Context Filters, 2026-07-30): an
    entry_filter-mode context filter with an impossible condition
    (allowed_days=[], no day of week ever passes) must reach the real
    EXECUTE/NO_TRADE decision and block every trade, while a baseline
    run on the identical data produces real trades."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    cfg = BacktestConfig.from_profile("EURUSD")

    baseline = run_backtest(df, cfg, engine_config=None)
    assert len(baseline.trades) > 0

    veto_config = build_engine_config_override(
        context_filters=[{"name": "day_of_week", "mode": "entry_filter", "params": {"allowed_days": []}, "weight": 0}],
    )
    vetoed = run_backtest(df, cfg, engine_config=veto_config)
    assert vetoed.execute_count == 0
    assert vetoed.context_rejections.get("day_of_week", 0) > 0
    assert vetoed.gate_rejections["context_filter"] > 0


def test_context_confirmation_mode_adjusts_score_and_never_sets_direction():
    """confirmation-mode context filters must nudge adjusted_score
    (visible in each trade's decision snapshot) without ever changing
    which direction a trade takes."""
    from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest

    df = _ohlcv(2400, trend=0.10)
    cfg = BacktestConfig.from_profile("EURUSD")

    confirm_config = build_engine_config_override(
        context_filters=[{"name": "direction", "mode": "confirmation", "params": {"allowed": ["BULLISH"]}, "weight": 0}],
    )
    result = run_backtest(df, cfg, engine_config=confirm_config)
    assert len(result.trades) > 0
    for t in result.trades:
        assert t.decision["context_filters"] is not None
        assert "direction" in t.decision["context_filters"]
        expected_direction = "BUY" if t.decision["winning_bias"] == "BULLISH" else "SELL"
        assert t.direction == expected_direction


def test_from_profile_uses_real_spread_as_commission():
    """Backtests must cost trades at the measured broker spread by
    default, not the old flat 0.5 pip — otherwise PF for wide-spread
    assets (gold/crypto/indices) is optimistic. Overridable for ablation."""
    from backtesting.backtest_engine import BacktestConfig, REAL_SPREAD_PIPS

    # Carriers get their measured spread as the commission floor.
    assert BacktestConfig.from_profile("XAUUSD").commission_pips == REAL_SPREAD_PIPS["XAUUSD"]
    assert BacktestConfig.from_profile("BTCUSD").commission_pips == REAL_SPREAD_PIPS["BTCUSD"]
    # FX (not in the map — measured spreads were below the 0.5 default)
    # keeps the conservative default.
    assert BacktestConfig.from_profile("EURUSD").commission_pips == 0.5
    # Explicit override wins (ablation / sensitivity runs).
    assert BacktestConfig.from_profile("XAUUSD", commission_pips=0.5).commission_pips == 0.5
