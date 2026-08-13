"""
tests/test_engine_refinement_versioning.py
----------------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — §5 Engine
Versioning. Pins EngineOutput.engine_version, BaseEngine.version's
None-by-default safety, and both real construction sites
(main.build_active_engines / backtesting.backtest_engine.run_backtest)
threading config/engines.yaml's versions: block onto every engine
instance — purely observational tagging, never consulted by any
gating/scoring logic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting.backtest_engine import (
    BacktestConfig, build_engine_config_override, run_backtest,
)
from engines.base_engine import BaseEngine, Bias, EngineOutput
from main import _ALL_ENGINES, build_active_engines
from utils.helpers import load_config


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


class _StubEngine(BaseEngine):
    name = "Stub"

    def analyze(self, mtf_data):
        return EngineOutput(engine_name=self.name, bias=Bias.NEUTRAL, score=0.0)


def test_engine_output_engine_version_defaults_to_none():
    out = EngineOutput(engine_name="X", bias=Bias.BULLISH, score=50.0)
    assert out.engine_version is None
    assert out.to_dict()["engine_version"] is None


def test_bare_construction_never_fabricates_a_version():
    engine = _StubEngine()
    assert engine.version is None
    out = engine.safe_analyze({})
    assert out.engine_version is None


def test_safe_analyze_fills_engine_version_from_self_version():
    engine = _StubEngine()
    engine.version = "9.9"
    out = engine.safe_analyze({})
    assert out.engine_version == "9.9"


def test_safe_analyze_never_overwrites_an_engine_supplied_version():
    class CustomVersionEngine(BaseEngine):
        name = "Custom"

        def analyze(self, mtf_data):
            return EngineOutput(
                engine_name=self.name, bias=Bias.NEUTRAL, score=0.0,
                engine_version="ENGINE_SUPPLIED",
            )

    engine = CustomVersionEngine()
    engine.version = "9.9"
    out = engine.safe_analyze({})
    assert out.engine_version == "ENGINE_SUPPLIED"


def test_crash_path_still_reports_engine_version():
    class BrokenEngine(BaseEngine):
        name = "Broken"

        def analyze(self, mtf_data):
            raise RuntimeError("boom")

    engine = BrokenEngine()
    engine.version = "3.1"
    out = engine.safe_analyze({})
    assert out.crashed is True
    assert out.engine_version == "3.1"


def test_build_active_engines_sets_version_from_config_versions_block():
    config = load_config()
    versions = config.get("engines", {}).get("versions", {})
    engines = build_active_engines(config)
    assert engines, "no engines enabled in config"
    enabled = config.get("engines", {}).get("enabled", {})
    for key, cls in _ALL_ENGINES.items():
        if enabled.get(key, False):
            matching = [e for e in engines if isinstance(e, cls)]
            assert matching, f"engine {key!r} should have been constructed"
            assert matching[0].version == versions.get(key)


def test_run_backtest_populates_engine_version_on_real_trade_decisions():
    df = _ohlcv(600)
    # _ohlcv() is genuinely H1-cadence (freq="h") — declare that explicitly
    # rather than relying on config.yaml's H4 default, which (post-BUG-006
    # fix) now really resamples mismatched data down to ~1/4 the bars
    # instead of silently mislabeling it.
    engine_config = build_engine_config_override(timeframes=["H1"])
    result = run_backtest(df, BacktestConfig.from_profile("EURUSD"), engine_config=engine_config)
    assert len(result.trades) > 0, "need at least one trade to assert on"
    config = load_config()
    versions = config.get("engines", {}).get("versions", {})
    for t in result.trades:
        engine_versions = {e["engine"]: e["engine_version"] for e in t.decision["engines"]}
        # NNFX/PriceAction are always-on per config/engines.yaml's prod4
        # set (engine_name is each class's own `name` attr, matching the
        # existing decision-snapshot test's own convention).
        assert engine_versions.get("NNFX") == versions.get("nnfx")
        assert engine_versions.get("PriceAction") == versions.get("price_action")
