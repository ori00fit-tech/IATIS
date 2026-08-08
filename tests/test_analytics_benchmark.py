"""
tests/test_analytics_benchmark.py
--------------------------------------
Provider Benchmark & Data Quality Lab Phase 4 — pure-function tests for
backtest/analytics_benchmark.py's determinism/coverage scoring, plus
hard-block safety tests proving this module never writes to config.yaml,
config/symbols.yaml, config/engines.yaml, or registry.json.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from backtest.analytics_benchmark import (
    PROFILES,
    PROVIDERS,
    coverage_score,
    composite_score,
    determinism_score,
    run_benchmark,
    score_symbol,
)


def _article(headline="Gold rallies on Fed pivot", published_at="2026-08-08T10:00:00Z",
             source="Reuters", sentiment=0.42) -> dict:
    return {"headline": headline, "published_at": published_at, "source": source, "sentiment": sentiment}


# ── PROVIDERS / PROFILES sanity ──────────────────────────────────────

def test_only_one_provider():
    # This phase's whole scope decision hinges on this — pin it as a
    # regression guard so a future addition doesn't silently reintroduce
    # cross-provider machinery this module deliberately doesn't have.
    assert PROVIDERS == ("marketaux",)


def test_profiles_shape_matches_news_benchmark_convention():
    assert set(PROFILES) == {"smoke", "standard", "deep"}
    for prof in PROFILES.values():
        assert set(prof) == {"hours_back", "limit"}


# ── coverage_score ───────────────────────────────────────────────────

def test_coverage_zero_articles():
    assert coverage_score([]) == 0.0


def test_coverage_some_articles():
    assert coverage_score([_article()]) == 100.0


# ── determinism_score ────────────────────────────────────────────────

def test_determinism_none_when_no_overlap():
    score, detail = determinism_score([_article(headline="A")], [_article(headline="B")])
    assert score is None
    assert detail["overlap_count"] == 0


def test_determinism_100_when_all_overlapping_sentiment_matches():
    a = _article(sentiment=0.42)
    b = _article(sentiment=0.42)  # same key (headline/published_at/source), same sentiment
    score, detail = determinism_score([a], [b])
    assert score == 100.0
    assert detail == {"overlap_count": 1, "matched": 1, "mismatched": 0, "mismatched_examples": []}


def test_determinism_flags_a_real_sentiment_drift():
    a = _article(sentiment=0.42)
    b = _article(sentiment=-0.10)  # same identity, different sentiment second time around
    score, detail = determinism_score([a], [b])
    assert score == 0.0
    assert detail["mismatched"] == 1
    assert detail["mismatched_examples"][0]["first_sentiment"] == 0.42
    assert detail["mismatched_examples"][0]["second_sentiment"] == -0.10


def test_determinism_partial_overlap_scores_only_the_overlap():
    stable = _article(headline="Stable story", sentiment=0.5)
    only_in_first = _article(headline="Only in first fetch")
    only_in_second = _article(headline="Only in second fetch")
    score, detail = determinism_score([stable, only_in_first], [stable, only_in_second])
    assert score == 100.0  # the one comparable article matched; the non-overlapping ones aren't penalized
    assert detail["overlap_count"] == 1


def test_determinism_none_sentiment_both_times_counts_as_match():
    # A provider that never scores sentiment for this article both times
    # (both None) is stable, not "broken" — None == None is a real match.
    a = _article(sentiment=None)
    b = _article(sentiment=None)
    score, detail = determinism_score([a], [b])
    assert score == 100.0


# ── composite_score ──────────────────────────────────────────────────

def test_composite_score_none_when_all_dims_none():
    assert composite_score({"determinism": None, "freshness": None, "coverage": None, "latency": None}) is None


def test_composite_score_renormalizes_when_determinism_missing():
    dims = {"determinism": None, "freshness": 100.0, "coverage": 100.0, "latency": 100.0}
    assert composite_score(dims) == 100.0


def test_composite_score_weighted_average_with_all_dims_present():
    dims = {"determinism": 0.0, "freshness": 100.0, "coverage": 100.0, "latency": 100.0}
    # determinism weight 0.40 dragging the average down from 100
    score = composite_score(dims)
    assert 55.0 < score < 65.0


# ── score_symbol orchestration ───────────────────────────────────────

def test_score_symbol_reports_unsupported_provider(monkeypatch):
    monkeypatch.setattr("fundamentals.marketaux_client.get_news_articles", lambda *a, **k: None)
    results = score_symbol("XAUUSD", ["marketaux"], 48, 20)
    assert len(results) == 1
    assert results[0].fetch_ok is False
    assert "API key" in results[0].error or "does not support" in results[0].error


def test_score_symbol_fetches_twice_and_scores_determinism(monkeypatch):
    calls = {"n": 0}

    def fake_get_news_articles(symbol, limit=20, hours_back=48):
        calls["n"] += 1
        return [_article(sentiment=0.3)]

    monkeypatch.setattr("fundamentals.marketaux_client.get_news_articles", fake_get_news_articles)
    results = score_symbol("EURUSD", ["marketaux"], 48, 20)
    assert calls["n"] == 2  # determinism needs two real fetches
    assert results[0].fetch_ok is True
    assert results[0].determinism_score == 100.0
    assert results[0].composite_score is not None


def test_score_symbol_second_fetch_failure_still_reports_a_partial_result(monkeypatch):
    calls = {"n": 0}

    def flaky(symbol, limit=20, hours_back=48):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_article()]
        raise ConnectionError("network dropped mid-run")

    monkeypatch.setattr("fundamentals.marketaux_client.get_news_articles", flaky)
    results = score_symbol("EURUSD", ["marketaux"], 48, 20)
    assert results[0].fetch_ok is True  # first fetch succeeded — real, partial data, not silently dropped
    assert results[0].determinism_score is None  # nothing to compare
    assert results[0].coverage_score == 100.0  # still measurable from the first fetch


def test_score_symbol_one_provider_exception_does_not_crash_the_run(monkeypatch):
    monkeypatch.setattr(
        "fundamentals.marketaux_client.get_news_articles",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("network down")),
    )
    results = score_symbol("EURUSD", ["marketaux"], 48, 20)
    assert len(results) == 1
    assert results[0].fetch_ok is False
    assert "network down" in results[0].error


# ── run_benchmark profile resolution ─────────────────────────────────

def test_run_benchmark_default_symbols_come_from_marketaux_map(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "backtest.analytics_benchmark.score_symbol",
        lambda symbol, providers, hours_back, limit: (seen.append(symbol), [])[1],
    )
    run_benchmark("run-x", "smoke", None, None, None, None)
    from fundamentals.marketaux_client import MARKETAUX_SYMBOL_MAP
    assert set(seen) == set(MARKETAUX_SYMBOL_MAP)


def test_run_benchmark_explicit_symbols_overrides_default(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "backtest.analytics_benchmark.score_symbol",
        lambda symbol, providers, hours_back, limit: (seen.append(symbol), [])[1],
    )
    run_benchmark("run-y", "deep", ["EURUSD"], None, None, None)
    assert seen == ["EURUSD"]


# ── hard-block safety: never writes config/registry files ────────────

def test_analytics_benchmark_module_contains_no_write_call_near_config_files():
    import backtest.analytics_benchmark as ab
    import storage.analytics_benchmark as sb

    source = inspect.getsource(ab) + inspect.getsource(sb)
    forbidden = ["config.yaml", "engines.yaml", "symbols.yaml", "registry.json"]
    write_markers = ["write_text(", "safe_dump(", "yaml.dump(", 'open(', '"w")', "'w')"]
    for line in source.splitlines():
        if any(marker in line for marker in write_markers):
            assert not any(f in line for f in forbidden), f"Suspicious write near a config/registry file: {line}"


def test_run_benchmark_never_touches_config_or_registry_files(monkeypatch):
    import backtest.analytics_benchmark as ab

    config_path = Path("config.yaml")
    symbols_path = Path("config/symbols.yaml")
    engines_path = Path("config/engines.yaml")
    registry_path = Path("research/results/registry.json")
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in [config_path, symbols_path, engines_path, registry_path] if p.exists()}

    monkeypatch.setattr("fundamentals.marketaux_client.get_news_articles", lambda *a, **k: [_article()])

    results = []
    ab.run_benchmark("test-run", "smoke", ["EURUSD"], None, None, None, on_result=results.append)
    assert len(results) == 1

    after = {p: (p.read_bytes(), p.stat().st_mtime) for p in before}
    assert after == before
