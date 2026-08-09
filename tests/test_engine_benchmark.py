"""
tests/test_engine_benchmark.py
------------------------------------
Engine Benchmark — pure-function/orchestration tests for
backtest/engine_benchmark.py, plus the two hard-block safety tests
(mirroring tests/test_price_benchmark.py's own convention): a source-scan
for any write call near config.yaml/config/engines.yaml/registry.json,
and a live run_benchmark() integration test proving those files are
byte-identical before/after a real run against real (synthetic-file)
data.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.engine_benchmark import (
    EngineBenchmarkResult,
    PROFILES,
    _in_scope_symbols,
    run_benchmark,
    score_symbol,
)
from backtesting.backtest_engine import ENGINE_KEYS


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


def _write_dataset(data_dir: Path, symbol: str = "EURUSD", n: int = 800) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _ohlcv(n).to_csv(data_dir / f"{symbol}_H1_test.csv")


def _fake_config(entries: list[dict]) -> dict:
    return {"data": {"twelve_data_symbols": entries}}


# ── _in_scope_symbols ────────────────────────────────────────────────

def test_in_scope_symbols_filters_by_asset_class_and_status():
    config = _fake_config([
        {"internal": "EURUSD", "asset_class": "fx_major", "status": "ACTIVE"},
        {"internal": "AAPL", "asset_class": "equity", "status": "ACTIVE"},
        {"internal": "OLDPAIR", "asset_class": "fx_major", "status": "RETIRED"},
    ])
    assert _in_scope_symbols(config) == ["EURUSD"]


def test_in_scope_symbols_includes_retired_when_requested():
    config = _fake_config([
        {"internal": "OLDPAIR", "asset_class": "fx_major", "status": "RETIRED"},
    ])
    assert _in_scope_symbols(config, include_retired=True) == ["OLDPAIR"]
    assert _in_scope_symbols(config, include_retired=False) == []


# ── profiles ──────────────────────────────────────────────────────────

def test_profiles_shape():
    assert set(PROFILES) == {"smoke", "standard", "deep"}
    assert PROFILES["deep"]["include_retired"] is True
    assert PROFILES["smoke"]["include_retired"] is False


# ── score_symbol ──────────────────────────────────────────────────────

def test_score_symbol_missing_dataset_records_a_real_row_per_engine(tmp_path):
    results = score_symbol("NODATA", ["nnfx", "smc"], tmp_path)
    assert len(results) == 2
    assert all(not r.run_ok for r in results)
    assert all(r.error for r in results)
    assert all(r.total_trades == 0 for r in results)


def test_score_symbol_runs_every_requested_engine_standalone(tmp_path):
    _write_dataset(tmp_path, "EURUSD", n=800)
    results = score_symbol("EURUSD", ["nnfx", "smc", "wyckoff"], tmp_path)
    assert len(results) == 3
    assert {r.engine for r in results} == {"nnfx", "smc", "wyckoff"}
    for r in results:
        assert r.symbol == "EURUSD"
        assert r.run_ok is True
        assert r.error is None
        assert r.bars_used == 800
        assert isinstance(r.total_trades, int)
        # win_rate/profit_factor/etc. are only populated when trades occurred —
        # both a real number and None (zero trades) are legitimate outcomes.
        if r.total_trades > 0:
            assert r.win_rate is not None
            assert r.profit_factor is not None


def test_score_symbol_one_bad_engine_does_not_abort_the_rest(tmp_path, monkeypatch):
    _write_dataset(tmp_path, "EURUSD", n=800)

    real_run_backtest = None
    import backtest.engine_benchmark as eb_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated engine crash")

    # Patch run_backtest globally for this test — every engine's call
    # will fail, which is exactly what proves the per-engine isolation:
    # every one of them still gets a real, non-crashing result row.
    monkeypatch.setattr("backtesting.backtest_engine.run_backtest", _boom)

    results = score_symbol("EURUSD", ["nnfx", "smc"], tmp_path)
    assert len(results) == 2
    assert all(not r.run_ok for r in results)
    assert all("simulated engine crash" in (r.error or "") for r in results)


def test_score_symbol_never_ranks_or_sorts_results(tmp_path):
    """Deliberate non-feature: score_symbol returns results in the exact
    order engines were requested, never sorted by any KPI — there is no
    'best engine' concept anywhere in this module."""
    _write_dataset(tmp_path, "EURUSD", n=800)
    engines = ["wyckoff", "nnfx", "smc"]
    results = score_symbol("EURUSD", engines, tmp_path)
    assert [r.engine for r in results] == engines


# ── run_benchmark orchestration ─────────────────────────────────────

def test_run_benchmark_calls_on_result_for_every_symbol_engine_pair(tmp_path):
    _write_dataset(tmp_path, "EURUSD", n=800)
    _write_dataset(tmp_path, "XAUUSD", n=800)
    config = _fake_config([
        {"internal": "EURUSD", "asset_class": "fx_major", "status": "ACTIVE"},
        {"internal": "XAUUSD", "asset_class": "metals", "status": "ACTIVE"},
    ])
    results: list[EngineBenchmarkResult] = []
    run_benchmark(
        "test-run", "smoke", ["EURUSD", "XAUUSD"], ["nnfx", "smc"],
        tmp_path, None, None, config, on_result=results.append,
    )
    assert len(results) == 4  # 2 symbols x 2 engines
    assert {(r.symbol, r.engine) for r in results} == {
        ("EURUSD", "nnfx"), ("EURUSD", "smc"), ("XAUUSD", "nnfx"), ("XAUUSD", "smc"),
    }


def test_run_benchmark_defaults_engines_to_every_engine_key(tmp_path):
    _write_dataset(tmp_path, "EURUSD", n=800)
    config = _fake_config([{"internal": "EURUSD", "asset_class": "fx_major", "status": "ACTIVE"}])
    results: list[EngineBenchmarkResult] = []
    run_benchmark("test-run", "smoke", ["EURUSD"], None, tmp_path, None, None, config, on_result=results.append)
    assert {r.engine for r in results} == set(ENGINE_KEYS)


def test_run_benchmark_smoke_profile_caps_default_symbols(tmp_path):
    entries = [
        {"internal": f"SYM{i}", "asset_class": "fx_major", "status": "ACTIVE"} for i in range(10)
    ]
    for e in entries:
        _write_dataset(tmp_path, e["internal"], n=300)
    config = _fake_config(entries)
    results: list[EngineBenchmarkResult] = []
    run_benchmark("test-run", "smoke", None, ["nnfx"], tmp_path, None, None, config, on_result=results.append)
    symbols_seen = {r.symbol for r in results}
    assert len(symbols_seen) <= 5  # _SMOKE_SYMBOL_CAP


# ── hard-block safety: never writes config/registry files ────────────

def test_engine_benchmark_module_contains_no_write_call_near_config_files():
    import backtest.engine_benchmark as eb
    import storage.engine_benchmark as sb

    source = inspect.getsource(eb) + inspect.getsource(sb)
    forbidden = ["config.yaml", "engines.yaml", "symbols.yaml", "registry.json"]
    write_markers = ["write_text(", "safe_dump(", "yaml.dump(", 'open(', '"w")', "'w')"]
    for line in source.splitlines():
        if any(marker in line for marker in write_markers):
            assert not any(f in line for f in forbidden), f"Suspicious write near a config/registry file: {line}"


def test_run_benchmark_never_touches_config_or_registry_files(tmp_path):
    _write_dataset(tmp_path, "EURUSD", n=800)
    config_path = Path("config.yaml")
    symbols_path = Path("config/symbols.yaml")
    engines_path = Path("config/engines.yaml")
    registry_path = Path("research/results/registry.json")
    before = {
        p: (p.read_bytes(), p.stat().st_mtime)
        for p in [config_path, symbols_path, engines_path, registry_path] if p.exists()
    }

    fake_config = _fake_config([{"internal": "EURUSD", "asset_class": "fx_major", "status": "ACTIVE"}])
    results: list[EngineBenchmarkResult] = []
    run_benchmark(
        "test-run", "smoke", ["EURUSD"], ["nnfx", "smc"],
        tmp_path, None, None, fake_config, on_result=results.append,
    )
    assert len(results) == 2

    for p, (content, mtime) in before.items():
        assert p.read_bytes() == content
        assert p.stat().st_mtime == mtime
