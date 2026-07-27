"""
Tests for backtest/robustness.py — parameter-sensitivity ("robustness")
sweeps. The methodological claims under test: baseline always uses the
symbol's real from_profile() value, INSUFFICIENT fires when the baseline
point itself lacks trades, STABLE/SENSITIVE reflects the +/-30% PF band
honestly, and one symbol's failure never aborts a suite.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backtest.robustness import (
    DEFAULT_MULTIPLIERS,
    SWEEP_PARAMS,
    RobustnessConfig,
    RobustnessResult,
    run_param_sweep,
    run_robustness,
    run_robustness_suite,
)
from backtesting.backtest_engine import BacktestConfig


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


# ─────────────────────────────────────────────────────────────────────────
# RobustnessConfig validation
# ─────────────────────────────────────────────────────────────────────────

def test_config_requires_baseline_multiplier_present():
    with pytest.raises(ValueError, match="baseline"):
        RobustnessConfig(multipliers=(0.5, 1.5))


def test_config_rejects_unsupported_param():
    with pytest.raises(ValueError, match="unsupported sweep param"):
        RobustnessConfig(params=("warmup_bars",))


def test_config_rejects_non_positive_min_trades():
    with pytest.raises(ValueError, match="min_trades"):
        RobustnessConfig(min_trades=0)


def test_config_defaults_match_documented_scenario_fields():
    # These four are exactly the cost/risk fields /research/scenario-config
    # documents as real, legitimate per-run overrides — never a gate flag.
    assert set(SWEEP_PARAMS) == {"sl_atr_multiplier", "commission_pips", "slippage_pips", "min_rr"}
    assert 1.0 in DEFAULT_MULTIPLIERS


# ─────────────────────────────────────────────────────────────────────────
# run_param_sweep / run_robustness — real engine, in-memory data
# ─────────────────────────────────────────────────────────────────────────

def test_sweep_baseline_value_matches_from_profile():
    df = _ohlcv(2400, trend=0.10)
    rc = RobustnessConfig(params=("sl_atr_multiplier",), min_trades=1)
    result = run_param_sweep("EURUSD", df, "sl_atr_multiplier", rc)

    expected_baseline = BacktestConfig.from_profile("EURUSD").sl_atr_multiplier
    assert result.baseline_value == round(expected_baseline, 6)
    assert result.verdict in ("STABLE", "SENSITIVE", "INSUFFICIENT")


def test_sweep_includes_one_point_per_multiplier():
    df = _ohlcv(2400, trend=0.10)
    multipliers = (0.5, 1.0, 1.5)
    rc = RobustnessConfig(params=("commission_pips",), multipliers=multipliers, min_trades=1)
    result = run_param_sweep("EURUSD", df, "commission_pips", rc)

    assert [p.multiplier for p in result.points] == list(multipliers)
    baseline_cfg = BacktestConfig.from_profile("EURUSD")
    for p in result.points:
        assert p.value == pytest.approx(baseline_cfg.commission_pips * p.multiplier, rel=1e-6)


def test_sweep_insufficient_when_baseline_lacks_trades():
    # Just above the 210-bar warmup floor -> only a handful of tradeable
    # bars remain, so the baseline point can never clear a high min_trades
    # bar regardless of volatility.
    df = _ohlcv(215, trend=0.0)
    rc = RobustnessConfig(params=("min_rr",), min_trades=9999)
    result = run_param_sweep("EURUSD", df, "min_rr", rc)
    assert result.verdict == "INSUFFICIENT"


def test_run_robustness_runs_every_configured_param():
    df = _ohlcv(2400, trend=0.10)
    rc = RobustnessConfig(params=("sl_atr_multiplier", "min_rr"), min_trades=1)
    result = run_robustness("EURUSD", df, rc)
    assert isinstance(result, RobustnessResult)
    assert [s.param for s in result.sweeps] == ["sl_atr_multiplier", "min_rr"]
    d = result.to_dict()
    assert d["symbol"] == "EURUSD"
    assert len(d["sweeps"]) == 2


# ─────────────────────────────────────────────────────────────────────────
# run_robustness_suite — end-to-end, real files, isolation, report write
# ─────────────────────────────────────────────────────────────────────────

def test_suite_end_to_end_writes_report(tmp_path):
    _ohlcv(2400, trend=0.10).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    out_dir = tmp_path / "reports"

    rc = RobustnessConfig(params=("sl_atr_multiplier",), min_trades=1)
    results = run_robustness_suite(["EURUSD"], tmp_path, rc, output_dir=out_dir)

    assert "EURUSD" in results
    reports = list(out_dir.glob("robustness_*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text())
    assert payload["evaluated"] == 1
    assert "EURUSD" in payload["symbols"]
    assert payload["symbols"]["EURUSD"]["sweeps"][0]["param"] == "sl_atr_multiplier"


def test_suite_isolates_symbol_failures(tmp_path):
    _ohlcv(2400, trend=0.10).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    # GBPUSD has no CSV at all -> find_symbol_csv raises FileNotFoundError,
    # must not abort EURUSD's sweep.
    rc = RobustnessConfig(params=("min_rr",), min_trades=1)
    results = run_robustness_suite(["EURUSD", "GBPUSD"], tmp_path, rc, output_dir=tmp_path / "reports")

    assert "EURUSD" in results
    assert "GBPUSD" not in results


def test_suite_writes_nothing_when_every_symbol_fails(tmp_path):
    rc = RobustnessConfig(params=("min_rr",), min_trades=1)
    out_dir = tmp_path / "reports"
    results = run_robustness_suite(["NOPE"], tmp_path, rc, output_dir=out_dir)
    assert results == {}
    assert not out_dir.exists()


def test_suite_threads_start_end_into_payload(tmp_path):
    _ohlcv(2400, trend=0.10).to_csv(tmp_path / "EURUSD_H1_2y.csv")
    rc = RobustnessConfig(params=("min_rr",), min_trades=1)
    out_dir = tmp_path / "reports"
    run_robustness_suite(
        ["EURUSD"], tmp_path, rc, output_dir=out_dir,
        start="2024-01-05", end="2024-01-15",
    )
    payload = json.loads(next(out_dir.glob("robustness_*.json")).read_text())
    assert payload["start"] == "2024-01-05"
    assert payload["end"] == "2024-01-15"


# ─────────────────────────────────────────────────────────────────────────
# Backtesting Lab Pro Phase A — CLI wiring for per-run risk/cost overrides
# ─────────────────────────────────────────────────────────────────────────

def test_robustness_cli_wires_risk_overrides(monkeypatch):
    import sys
    import backtest.robustness as robustness_mod

    captured: dict = {}

    def fake_suite(symbols, data_dir, rc, start=None, end=None):
        captured["rc"] = rc
        captured["start"] = start
        captured["end"] = end
        return {}

    monkeypatch.setattr(robustness_mod, "run_robustness_suite", fake_suite)
    monkeypatch.setattr(
        sys, "argv",
        ["robustness.py", "--symbols", "EURUSD", "--commission-pips", "2.0",
         "--start", "2024-01-01"],
    )
    with pytest.raises(SystemExit, match="No symbol completed"):
        robustness_mod.main()
    assert captured["rc"].engine_overrides == {"commission_pips": 2.0}
    assert captured["start"] == "2024-01-01"


def test_robustness_cli_wires_timeframes_override(monkeypatch):
    import sys
    import backtest.robustness as robustness_mod

    captured: dict = {}

    def fake_suite(symbols, data_dir, rc, start=None, end=None):
        captured["rc"] = rc
        return {}

    monkeypatch.setattr(robustness_mod, "run_robustness_suite", fake_suite)
    monkeypatch.setattr(sys, "argv", ["robustness.py", "--symbols", "EURUSD", "--timeframes", "H4", "H1"])
    with pytest.raises(SystemExit, match="No symbol completed"):
        robustness_mod.main()
    assert captured["rc"].timeframes == ("H4", "H1")


def test_run_param_sweep_threads_timeframes_override_into_run_backtest(monkeypatch):
    """A robustness sweep with a timeframes override must build the
    engine_config once and pass it into every sweep point's run_backtest
    call — not silently ignore it."""
    import backtest.robustness as robustness_mod

    captured_engine_configs = []
    real_run_backtest = robustness_mod.run_backtest

    def spy_run_backtest(df, cfg, engine_config=None):
        captured_engine_configs.append(engine_config)
        return real_run_backtest(df, cfg, engine_config=engine_config)

    monkeypatch.setattr(robustness_mod, "run_backtest", spy_run_backtest)

    df = _ohlcv(2400, trend=0.10)
    rc = RobustnessConfig(params=("min_rr",), multipliers=(1.0,), min_trades=1, timeframes=("H1",))
    robustness_mod.run_param_sweep("EURUSD", df, "min_rr", rc)

    assert len(captured_engine_configs) == 1
    assert captured_engine_configs[0]["data"]["timeframes"] == ["H1"]


def test_robustness_cli_wires_engines_override(monkeypatch):
    import sys
    import backtest.robustness as robustness_mod

    captured: dict = {}

    def fake_suite(symbols, data_dir, rc, start=None, end=None):
        captured["rc"] = rc
        return {}

    monkeypatch.setattr(robustness_mod, "run_robustness_suite", fake_suite)
    monkeypatch.setattr(sys, "argv", ["robustness.py", "--symbols", "EURUSD", "--engines", "nnfx", "smc"])
    with pytest.raises(SystemExit, match="No symbol completed"):
        robustness_mod.main()
    assert captured["rc"].engines == ("nnfx", "smc")


def test_run_param_sweep_threads_engines_override_into_run_backtest(monkeypatch):
    """Same proof as the timeframes-override test above, for engines."""
    import backtest.robustness as robustness_mod
    from backtesting.backtest_engine import ENGINE_KEYS

    captured_engine_configs = []
    real_run_backtest = robustness_mod.run_backtest

    def spy_run_backtest(df, cfg, engine_config=None):
        captured_engine_configs.append(engine_config)
        return real_run_backtest(df, cfg, engine_config=engine_config)

    monkeypatch.setattr(robustness_mod, "run_backtest", spy_run_backtest)

    df = _ohlcv(2400, trend=0.10)
    rc = RobustnessConfig(params=("min_rr",), multipliers=(1.0,), min_trades=1, engines=("nnfx",))
    robustness_mod.run_param_sweep("EURUSD", df, "min_rr", rc)

    assert len(captured_engine_configs) == 1
    enabled = captured_engine_configs[0]["engines"]["enabled"]
    assert enabled["nnfx"] is True
    assert all(v is False for k, v in enabled.items() if k in ENGINE_KEYS and k != "nnfx")
