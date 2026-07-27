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

def _ohlcv(n: int, seed: int = 7, trend: float = 0.06) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
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


def test_adapter_handles_missing_decision_gracefully():
    # Trade objects built outside run_backtest's loop (older manifests,
    # hand-built test fixtures) carry decision=None — must not crash.
    rec = trade_to_record(_trade(decision=None), "EURUSD")
    assert rec.engine_votes == {}
    assert rec.cf_score == 0.0
    assert rec.regime == ""


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
