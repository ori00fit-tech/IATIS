"""
tests/test_backtest_engine_symbol_wiring.py
-----------------------------------------------
2026-08-15 red-team audit (TE-3): backtesting/backtest_engine.py's
run_backtest() engine-construction loop set engine.decision_tf and
engine.thresholds on every constructed engine (gate/vote parity with
main.py's live build_active_engines loop) but never engine._symbol —
the one live loop already sets (main.py:142). SentimentEngine reads
self._symbol for its per-symbol COT cache lookup (engines/
sentiment_engine.py:279); absent, it silently resolved to "UNKNOWN" in
every backtest instead of the real symbol under test.

BUG-005's own bar-time gate already means this made no observable
difference for a genuinely historical backtest (COT/MarketAux are
skipped whenever bar_time is far from wall-clock now, which is true for
almost all backtest bars regardless of what `symbol` resolves to) — but
a backtest run against very recent bars (bar_time within tolerance_hours
of now) would reach the live-data path with the wrong symbol. This test
pins the fix directly: the constructed engine really does receive the
real symbol, regardless of whether any downstream code path currently
depends on it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting.backtest_engine import BacktestConfig, build_engine_config_override, run_backtest
from engines.sentiment_engine import SentimentEngine


def _ohlcv(n: int = 1600, seed: int = 11) -> pd.DataFrame:
    # 1600 H1 bars -> ~400 H4 bars after run_backtest()'s own resample
    # (config.warmup_bars defaults to 210 H4 bars) — enough bars past
    # warmup for the per-bar loop to actually reach SentimentEngine.
    # analyze() at least once, which every assertion here depends on.
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.10 + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


def test_run_backtest_sets_symbol_on_every_constructed_engine(monkeypatch):
    """Direct regression for the TE-3 fix: run_backtest()'s engine loop
    must set engine._symbol == config.symbol on every engine it
    constructs, mirroring main.py's live build_active_engines loop."""
    captured: list[str | None] = []
    real_analyze = SentimentEngine.analyze

    def spy_analyze(self, mtf_data):
        captured.append(getattr(self, "_symbol", None))
        return real_analyze(self, mtf_data)

    monkeypatch.setattr(SentimentEngine, "analyze", spy_analyze)

    df = _ohlcv()
    cfg = BacktestConfig.from_profile("USDJPY")
    # Force Sentiment on regardless of config/engines.yaml's default
    # enabled set, so the spy is guaranteed to fire at least once.
    engine_config = build_engine_config_override(
        engines_enabled={"sentiment": True, "smc": False, "price_action": False,
                          "ict": False, "nnfx": False, "quant": False, "wyckoff": False,
                          "divergence": False, "market_structure": False},
    )
    run_backtest(df, cfg, engine_config)

    assert captured, "SentimentEngine.analyze() was never called — test setup didn't exercise the engine loop"
    assert all(sym == "USDJPY" for sym in captured), (
        f"expected every call to see _symbol == 'USDJPY', got {captured}"
    )


def test_run_backtest_symbol_wiring_matches_config_symbol_for_a_different_symbol(monkeypatch):
    """Same proof with a second symbol, confirming the wiring reads
    config.symbol dynamically rather than a hardcoded/stale value."""
    captured: list[str | None] = []
    real_analyze = SentimentEngine.analyze

    def spy_analyze(self, mtf_data):
        captured.append(getattr(self, "_symbol", None))
        return real_analyze(self, mtf_data)

    monkeypatch.setattr(SentimentEngine, "analyze", spy_analyze)

    df = _ohlcv(seed=99)
    cfg = BacktestConfig.from_profile("XAUUSD")
    engine_config = build_engine_config_override(
        engines_enabled={"sentiment": True, "smc": False, "price_action": False,
                          "ict": False, "nnfx": False, "quant": False, "wyckoff": False,
                          "divergence": False, "market_structure": False},
    )
    run_backtest(df, cfg, engine_config)

    assert captured, "SentimentEngine.analyze() was never called — test setup didn't exercise the engine loop"
    assert all(sym == "XAUUSD" for sym in captured)
