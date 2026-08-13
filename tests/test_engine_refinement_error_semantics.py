"""
tests/test_engine_refinement_error_semantics.py
--------------------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — §4 Error Semantics.
Pins BacktestResult's new crashed_engine_bars/crashed_engine_totals
counters: a single engine crashing inside safe_analyze() must be
COUNTABLE from the run's own statistics, without ever changing gating,
voting, or the pipeline-level error_count (which stays reserved for a
whole-bar exception, a structurally different failure mode).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from backtesting.backtest_engine import (
    BacktestConfig, build_engine_config_override, run_backtest,
)
from engines.smc_engine import SMCEngine
from engines.wyckoff_engine import WyckoffEngine

# _ohlcv() below is genuinely H1-cadence (freq="h"). Declaring that
# explicitly avoids relying on config.yaml's H4 default, which (post-
# BUG-006 fix) now really resamples mismatched data down to ~1/4 the bars
# instead of silently mislabeling it — at n=600 that would otherwise land
# below warmup_bars=210 and produce an empty result.
_H1_ENGINE_CONFIG = build_engine_config_override(timeframes=["H1"])


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


def test_no_crash_produces_zero_crashed_engine_bars():
    df = _ohlcv(600)
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"))
    assert result.crashed_engine_bars == 0
    assert result.crashed_engine_totals == {}
    assert result.error_count == 0


def test_engine_crash_is_counted_without_changing_gating(monkeypatch):
    df = _ohlcv(600)

    def _boom(self, mtf_data):
        raise KeyError("missing_column")

    monkeypatch.setattr(SMCEngine, "analyze", _boom)
    result = run_backtest(
        df, BacktestConfig.from_profile("EURUSD"), engine_config=_H1_ENGINE_CONFIG,
    )

    assert result.total_runs > 0
    # Every crashed bar is attributed to SMC by name, not silently dropped.
    assert result.crashed_engine_bars > 0
    assert result.crashed_engine_totals.get("SMC") == result.crashed_engine_bars
    assert result.crashed_engine_bars <= result.total_runs
    # The per-engine crash is caught inside safe_analyze() — it must never
    # escape as a whole-bar pipeline exception (a structurally different,
    # pre-existing counter this feature deliberately does not touch).
    assert result.error_count == 0
    # Accounting invariant unchanged by this feature: every processed bar
    # is still either an EXECUTE or a NO_TRADE.
    assert result.execute_count + result.no_trade_count == result.total_runs


def test_crashed_engine_totals_tracks_per_engine_breakdown(monkeypatch):
    df = _ohlcv(600)

    def _boom_smc(self, mtf_data):
        raise ValueError("smc broke")

    def _boom_wyckoff(self, mtf_data):
        raise RuntimeError("wyckoff broke")

    monkeypatch.setattr(SMCEngine, "analyze", _boom_smc)
    monkeypatch.setattr(WyckoffEngine, "analyze", _boom_wyckoff)
    result = run_backtest(
        df, BacktestConfig.from_profile("EURUSD"), engine_config=_H1_ENGINE_CONFIG,
    )

    assert set(result.crashed_engine_totals.keys()) == {"SMC", "Wyckoff"}
    assert result.crashed_engine_totals["SMC"] > 0
    assert result.crashed_engine_totals["Wyckoff"] > 0
    # A bar where BOTH crash still only counts once toward crashed_engine_bars.
    assert result.crashed_engine_bars <= result.total_runs
    assert result.crashed_engine_bars >= max(
        result.crashed_engine_totals["SMC"], result.crashed_engine_totals["Wyckoff"]
    )


def test_first_engine_crash_is_logged_at_warning_once(monkeypatch, caplog):
    df = _ohlcv(600)

    def _boom(self, mtf_data):
        raise KeyError("missing_column")

    monkeypatch.setattr(SMCEngine, "analyze", _boom)
    with caplog.at_level(logging.WARNING, logger="backtesting.backtest_engine"):
        run_backtest(
            df, BacktestConfig.from_profile("EURUSD"), engine_config=_H1_ENGINE_CONFIG,
        )

    first_crash_logs = [
        r for r in caplog.records if r.message.startswith("First engine crash at bar")
    ]
    assert len(first_crash_logs) == 1
    assert "SMC" in first_crash_logs[0].message
