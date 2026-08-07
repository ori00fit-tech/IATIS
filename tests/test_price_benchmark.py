"""
tests/test_price_benchmark.py
---------------------------------
Provider Benchmark & Data Quality Lab Phase 1 — pure-function tests for
backtest/price_benchmark.py's scoring/consensus math, plus hard-block
safety tests proving this module never writes to config.yaml,
config/symbols.yaml, config/engines.yaml, or registry.json.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from backtest.price_benchmark import (
    _IN_SCOPE_ASSET_CLASSES,
    _in_scope_symbols,
    _is_forex_week_closure,
    build_consensus,
    classify_gaps,
    completeness_score,
    composite_score,
    correctness_vs_consensus,
    freshness_score,
    latency_score,
    ohlc_integrity_score,
    pairwise_agreement_score,
    score_symbol_timeframe,
    timestamp_integrity_score,
)


def _bars(start: str, n: int, freq: str = "1h", price: float = 1.1000) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": price, "high": price + 0.0005, "low": price - 0.0005, "close": price, "volume": 100.0},
        index=idx,
    )


# ── _is_forex_week_closure ────────────────────────────────────────────

def test_forex_week_closure_saturday_always_closed():
    assert _is_forex_week_closure(pd.Timestamp("2026-08-08 12:00", tz="UTC"))  # Saturday


def test_forex_week_closure_friday_late_is_closed():
    assert _is_forex_week_closure(pd.Timestamp("2026-08-07 23:00", tz="UTC"))  # Friday 23:00 UTC


def test_forex_week_closure_friday_afternoon_is_open():
    assert not _is_forex_week_closure(pd.Timestamp("2026-08-07 12:00", tz="UTC"))


def test_forex_week_closure_sunday_early_is_closed():
    assert _is_forex_week_closure(pd.Timestamp("2026-08-09 10:00", tz="UTC"))  # Sunday 10:00 UTC


def test_forex_week_closure_sunday_late_is_open():
    assert not _is_forex_week_closure(pd.Timestamp("2026-08-09 23:00", tz="UTC"))


def test_forex_week_closure_weekday_is_open():
    assert not _is_forex_week_closure(pd.Timestamp("2026-08-05 12:00", tz="UTC"))  # Wednesday


# ── completeness / gap classification ───────────────────────────────

def test_completeness_excludes_weekend_closure_for_fx():
    # Friday 21:00 (last open-market bar) -> Sunday 22:00 (first reopen
    # bar): every hour in between (Fri>=22:00, all Saturday, Sun<22:00)
    # is a real FX weekly closure, not a data gap.
    idx = list(pd.date_range("2026-08-07 20:00", "2026-08-07 21:00", freq="1h", tz="UTC"))
    idx += list(pd.date_range("2026-08-09 22:00", "2026-08-09 23:00", freq="1h", tz="UTC"))
    df = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1, "volume": 1.0}, index=pd.DatetimeIndex(idx))
    score, detail = completeness_score(df, "H1", "fx_major")
    assert detail["expected_closure"] > 0
    assert detail["real_gap"] == 0
    assert score == 100.0  # weekend gap fully explained, not penalized


def test_completeness_penalizes_real_gap_for_fx():
    idx = list(pd.date_range("2026-08-04 00:00", "2026-08-04 05:00", freq="1h", tz="UTC"))
    del idx[3]  # a real, non-weekend missing bar
    df = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1, "volume": 1.0}, index=pd.DatetimeIndex(idx))
    score, detail = completeness_score(df, "H1", "fx_major")
    assert detail["real_gap"] == 1
    assert score < 100.0


def test_completeness_crypto_has_zero_expected_closures():
    idx = list(pd.date_range("2026-08-08 00:00", "2026-08-08 05:00", freq="1h", tz="UTC"))
    del idx[2]
    df = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0}, index=pd.DatetimeIndex(idx))
    _score, detail = completeness_score(df, "H1", "crypto")
    assert detail["expected_closure"] == 0
    assert detail["real_gap"] == 1  # a Saturday hour is a real gap for crypto, unlike FX


def test_completeness_score_perfect_data_is_100():
    df = _bars("2026-08-04", 20)
    score, detail = completeness_score(df, "H1", "fx_major")
    assert score == 100.0
    assert detail["real_gap"] == 0


# ── timestamp integrity ──────────────────────────────────────────────

def test_timestamp_integrity_perfect_alignment():
    df = _bars("2026-08-04", 10, freq="1h")
    score, detail = timestamp_integrity_score(df, "H1")
    assert score == 100.0
    assert detail["misaligned"] == 0


def test_timestamp_integrity_flags_misaligned_bar():
    df = _bars("2026-08-04 00:00", 5, freq="1h")
    # Shift one bar 7 minutes off its H1 boundary — the exact
    # from_ms % bar_ms bug class fixed in _fetch_dukascopy_jforex.
    new_index = list(df.index)
    new_index[2] = new_index[2] + pd.Timedelta(minutes=7)
    df.index = pd.DatetimeIndex(new_index)
    score, detail = timestamp_integrity_score(df, "H1")
    assert detail["misaligned"] == 1
    assert score < 100.0


# ── OHLC integrity ────────────────────────────────────────────────────

def test_ohlc_integrity_passes_valid_data():
    df = _bars("2026-08-04", 5)
    score, reason = ohlc_integrity_score(df)
    assert score == 100.0
    assert reason is None


def test_ohlc_integrity_reuses_validate_ohlcv_reason():
    df = _bars("2026-08-04", 5)
    df.loc[df.index[1], "high"] = df.loc[df.index[1], "low"] - 0.01  # high < low
    score, reason = ohlc_integrity_score(df)
    assert score == 0.0
    assert "high" in reason.lower()


def test_ohlc_integrity_empty_frame():
    score, reason = ohlc_integrity_score(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    assert score == 0.0
    assert reason is not None


# ── consensus / correctness — the key operator-worry regression test ─

def test_build_consensus_excludes_single_provider_timestamps():
    idx = pd.date_range("2026-08-04", periods=3, freq="1h", tz="UTC")
    a = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx)
    consensus = build_consensus({"only_one": a}, min_providers=2)
    assert consensus.empty


def test_pairwise_agreement_vs_correctness_vs_consensus_genuinely_differ():
    """The load-bearing regression test: two providers (A, B) agree with
    EACH OTHER (a wrong "clique") while a majority-correct group (C, D)
    anchors the wider median near the truth. correctness_vs_consensus(A)
    is pulled toward that median and flags A as clearly wrong;
    pairwise_agreement_score(A) is dragged UP by its close match with B
    even though it's also compared against C and D — a strictly higher,
    less damning number than correctness. If these two ever converged to
    the same number, the "two providers agree with each other while both
    deviate from a wider median" failure mode the operator explicitly
    worried about would no longer be distinguishable."""
    idx = pd.date_range("2026-08-04", periods=5, freq="1h", tz="UTC")

    def frame(price: float) -> pd.DataFrame:
        return pd.DataFrame({"open": price, "high": price + 0.001, "low": price - 0.001, "close": price}, index=idx)

    fetched = {
        "A": frame(1.2000),  # wrong, but agrees with B
        "B": frame(1.2001),  # wrong, but agrees with A
        "C": frame(1.1000),  # correct
        "D": frame(1.1001),  # correct
    }
    consensus = build_consensus(fetched, min_providers=2)
    corr_a = correctness_vs_consensus(fetched["A"], consensus, tolerance_pct=0.05)
    agree_a = pairwise_agreement_score("A", fetched, tolerance_pct=0.05)

    assert corr_a["score"] is not None and agree_a is not None
    assert corr_a["score"] < 20.0  # clearly flagged vs. the (correct-majority) median
    assert agree_a > corr_a["score"]  # but pairwise-agreement-only would have understated the problem


def test_correctness_vs_consensus_no_overlap_returns_none_score():
    idx_a = pd.date_range("2026-08-04", periods=3, freq="1h", tz="UTC")
    idx_b = pd.date_range("2027-01-01", periods=3, freq="1h", tz="UTC")
    a = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx_a)
    b = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx_b)
    consensus = build_consensus({"a": a, "b": b}, min_providers=1)
    result = correctness_vs_consensus(a, consensus.loc[consensus.index.isin(idx_b)], tolerance_pct=0.05)
    assert result["score"] is None


def test_pairwise_agreement_none_with_no_peers():
    idx = pd.date_range("2026-08-04", periods=3, freq="1h", tz="UTC")
    a = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx)
    assert pairwise_agreement_score("solo", {"solo": a}) is None


# ── freshness / latency ───────────────────────────────────────────────

def test_freshness_score_fresh_data_is_100():
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=3, freq="1h")
    df = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx)
    assert freshness_score(df, "H1") == 100.0


def test_freshness_score_stale_data_is_0():
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5), periods=3, freq="1h")
    df = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx)
    assert freshness_score(df, "H1") == 0.0


def test_latency_score_boundaries():
    assert latency_score(100) == 100.0
    assert latency_score(500) == 100.0
    assert latency_score(10_000) == 0.0
    assert latency_score(20_000) == 0.0
    assert latency_score(None) is None
    mid = latency_score(5000)
    assert 0.0 < mid < 100.0


# ── composite score / weight renormalization ──────────────────────────

def test_composite_score_all_dimensions_present():
    dims = {
        "completeness": 100.0, "correctness": 100.0, "timestamp_integrity": 100.0,
        "ohlc_integrity": 100.0, "spread_quality": None, "cross_provider_agreement": 100.0,
        "freshness": 100.0, "latency": 100.0,
    }
    assert composite_score(dims) == 100.0


def test_composite_score_renormalizes_missing_dimension_not_zero():
    """spread_quality is always None in Phase 1 — must never silently
    count as 0 against the composite."""
    dims = {
        "completeness": 80.0, "correctness": 80.0, "timestamp_integrity": 80.0,
        "ohlc_integrity": 80.0, "spread_quality": None, "cross_provider_agreement": 80.0,
        "freshness": 80.0, "latency": 80.0,
    }
    # every present dimension is 80 -> renormalized composite must be 80,
    # not (80*0.9)/1.0=72 (which is what treating None as 0 would produce)
    assert composite_score(dims) == 80.0


def test_composite_score_none_when_nothing_measured():
    assert composite_score({k: None for k in ["completeness", "correctness"]}) is None


# ── in-scope symbol selection ─────────────────────────────────────────

def _fake_config(entries):
    return {"data": {"twelve_data_symbols": entries}}


def test_in_scope_symbols_filters_by_asset_class():
    config = _fake_config([
        {"internal": "EURUSD", "asset_class": "fx_major", "status": "ACTIVE"},
        {"internal": "AAPL", "asset_class": "equity", "status": "WATCHLIST"},
    ])
    assert _in_scope_symbols(config) == ["EURUSD"]


def test_in_scope_symbols_excludes_retired_by_default():
    config = _fake_config([
        {"internal": "EURGBP", "asset_class": "fx_minor", "status": "RETIRED"},
        {"internal": "XAUUSD", "asset_class": "metals", "status": "ACTIVE"},
    ])
    assert _in_scope_symbols(config) == ["XAUUSD"]
    assert _in_scope_symbols(config, include_retired=True) == ["EURGBP", "XAUUSD"]


def test_in_scope_asset_classes_matches_expected_set():
    assert _IN_SCOPE_ASSET_CLASSES == {"fx_major", "fx_minor", "metals", "crypto", "indices"}


# ── score_symbol_timeframe: never silently drops a failed fetch ──────

def test_score_symbol_timeframe_records_failed_fetch_as_a_row(monkeypatch):
    import backtest.price_benchmark as pb
    from core.data_providers import DataFetchError

    def _fake_fetch(fetch_symbol, interval, outputsize, providers):
        provider = providers[0]
        if provider == "broken":
            raise DataFetchError("simulated failure")
        return _bars("2026-08-04", 10), provider

    monkeypatch.setattr(pb, "_fetch_symbol_for", lambda internal, config: internal)
    monkeypatch.setattr("core.data_providers.fetch_with_failover", _fake_fetch)

    config = _fake_config([{"internal": "EURUSD", "asset_class": "fx_major", "status": "ACTIVE"}])
    results = pb.score_symbol_timeframe("EURUSD", "H1", ["good", "broken"], 10, config)

    assert len(results) == 2
    by_provider = {r.provider: r for r in results}
    assert by_provider["good"].fetch_ok is True
    assert by_provider["broken"].fetch_ok is False
    assert by_provider["broken"].error is not None
    assert by_provider["broken"].composite_score is None


# ── hard-block safety: never writes config/registry files ────────────

def test_price_benchmark_module_contains_no_write_call_near_config_files():
    import backtest.price_benchmark as pb
    import storage.provider_benchmark as sb

    source = inspect.getsource(pb) + inspect.getsource(sb)
    forbidden = ["config.yaml", "engines.yaml", "symbols.yaml", "registry.json"]
    write_markers = ["write_text(", "safe_dump(", "yaml.dump(", 'open(', '"w")', "'w')"]
    for line in source.splitlines():
        if any(marker in line for marker in write_markers):
            assert not any(f in line for f in forbidden), f"Suspicious write near a config/registry file: {line}"


def test_run_benchmark_never_touches_config_or_registry_files(monkeypatch, tmp_path):
    import backtest.price_benchmark as pb

    config_path = Path("config.yaml")
    symbols_path = Path("config/symbols.yaml")
    engines_path = Path("config/engines.yaml")
    registry_path = Path("research/results/registry.json")
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in [config_path, symbols_path, engines_path, registry_path] if p.exists()}

    def _fake_fetch(fetch_symbol, interval, outputsize, providers):
        return _bars("2026-08-04", 10), providers[0]

    monkeypatch.setattr("core.data_providers.fetch_with_failover", _fake_fetch)
    monkeypatch.setattr("core.data_providers.provider_chain_for", lambda symbol, overrides: ["p1", "p2"])

    results = []
    config = _fake_config([{"internal": "EURUSD", "asset_class": "fx_major", "status": "ACTIVE"}])
    pb.run_benchmark(
        "test-run", "smoke", ["EURUSD"], ["H1"], None, 10, 0.05, config,
        on_result=results.append,
    )
    assert len(results) == 2  # 2 providers x 1 symbol x 1 timeframe

    for p, (content, mtime) in before.items():
        assert p.read_bytes() == content
        assert p.stat().st_mtime == mtime
