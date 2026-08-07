"""
tests/test_news_benchmark.py
--------------------------------
Provider Benchmark & Data Quality Lab Phase 2 (News Benchmark) — pure-
function tests for backtest/news_benchmark.py, mirroring
tests/test_price_benchmark.py's hand-built-fixture discipline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backtest.news_benchmark import (
    NewsBenchmarkResult,
    composite_score,
    coverage_score,
    cross_provider_coverage_agreement,
    duplicate_rate_score,
    freshness_score,
    mean_sentiment,
    score_symbol,
    sentiment_availability_score,
    source_diversity_score,
)


def _article(headline="test", hours_ago=1.0, source="reuters", sentiment=None):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"headline": headline, "published_at": ts, "source": source, "sentiment": sentiment}


# ── coverage_score ────────────────────────────────────────────────────

def test_coverage_score_100_when_articles_present():
    assert coverage_score([_article()]) == 100.0


def test_coverage_score_0_when_empty():
    assert coverage_score([]) == 0.0


# ── source_diversity_score ────────────────────────────────────────────

def test_source_diversity_none_when_no_articles():
    assert source_diversity_score([]) is None


def test_source_diversity_full_marks_at_target_distinct_sources():
    articles = [_article(source="a"), _article(source="b"), _article(source="c")]
    assert source_diversity_score(articles) == 100.0


def test_source_diversity_partial_below_target():
    articles = [_article(source="a"), _article(source="a")]
    assert source_diversity_score(articles) == pytest.approx(100.0 / 3, abs=0.1)


# ── duplicate_rate_score ───────────────────────────────────────────────

def test_duplicate_rate_100_when_no_articles():
    score, detail = duplicate_rate_score([])
    assert score is None
    assert detail["n"] == 0


def test_duplicate_rate_100_for_single_article():
    score, _ = duplicate_rate_score([_article()])
    assert score == 100.0


def test_duplicate_rate_100_when_all_distinct():
    articles = [_article(headline="Fed hikes rates"), _article(headline="ECB holds steady")]
    score, detail = duplicate_rate_score(articles)
    assert score == 100.0
    assert detail["distinct_headlines"] == 2


def test_duplicate_rate_detects_exact_normalized_duplicates():
    articles = [
        _article(headline="Fed hikes rates!!"),
        _article(headline="fed hikes rates"),
        _article(headline="ECB holds steady"),
    ]
    score, detail = duplicate_rate_score(articles)
    # 1 duplicate pair out of C(3,2)=3 total pairs -> 100 * (1 - 1/3)
    assert score == pytest.approx(66.67, abs=0.1)
    assert detail["distinct_headlines"] == 2


# ── freshness_score ────────────────────────────────────────────────────

def test_freshness_none_when_no_articles():
    assert freshness_score([]) is None


def test_freshness_100_within_one_hour():
    assert freshness_score([_article(hours_ago=0.5)]) == 100.0


def test_freshness_0_at_or_past_24_hours():
    assert freshness_score([_article(hours_ago=30)]) == 0.0


def test_freshness_uses_the_newest_article():
    articles = [_article(hours_ago=20), _article(hours_ago=0.5)]
    assert freshness_score(articles) == 100.0


def test_freshness_skips_unparseable_timestamps():
    articles = [{"headline": "x", "published_at": "not-a-date", "source": "s", "sentiment": None}]
    assert freshness_score(articles) is None


# ── sentiment_availability_score ────────────────────────────────────────

def test_sentiment_availability_none_when_no_articles():
    assert sentiment_availability_score([]) is None


def test_sentiment_availability_100_when_any_scored():
    articles = [_article(sentiment=None), _article(sentiment=0.4)]
    assert sentiment_availability_score(articles) == 100.0


def test_sentiment_availability_none_when_finnhub_shaped_no_sentiment():
    """Finnhub's articles always carry sentiment=None — this dimension
    must stay None for Finnhub, never fabricated as 0."""
    articles = [_article(sentiment=None), _article(sentiment=None)]
    assert sentiment_availability_score(articles) is None


# ── mean_sentiment (informational only) ─────────────────────────────────

def test_mean_sentiment_none_when_no_scored_articles():
    assert mean_sentiment([_article(sentiment=None)]) is None


def test_mean_sentiment_averages_scored_articles():
    articles = [_article(sentiment=0.4), _article(sentiment=0.2)]
    assert mean_sentiment(articles) == pytest.approx(0.3)


# ── cross_provider_coverage_agreement ───────────────────────────────────

def test_coverage_agreement_none_below_two_providers():
    assert cross_provider_coverage_agreement({"marketaux": [_article()]}) is None


def test_coverage_agreement_100_when_both_have_news():
    fetched = {"marketaux": [_article()], "finnhub": [_article()]}
    assert cross_provider_coverage_agreement(fetched) == 100.0


def test_coverage_agreement_100_when_both_have_no_news():
    fetched = {"marketaux": [], "finnhub": []}
    assert cross_provider_coverage_agreement(fetched) == 100.0


def test_coverage_agreement_0_when_providers_disagree():
    fetched = {"marketaux": [_article()], "finnhub": []}
    assert cross_provider_coverage_agreement(fetched) == 0.0


def test_coverage_agreement_ignores_failed_fetches():
    """A provider with None (failed fetch) doesn't count toward the >=2
    threshold — only real, successful fetches are compared."""
    fetched = {"marketaux": [_article()], "finnhub": None}
    assert cross_provider_coverage_agreement(fetched) is None


# ── composite_score renormalization ─────────────────────────────────────

def test_composite_score_none_when_all_dims_none():
    assert composite_score({"coverage": None, "source_diversity": None}) is None


def test_composite_score_renormalizes_missing_dims():
    """sentiment_availability always None for Finnhub — composite still
    computes from the remaining weighted dimensions, never treats the
    missing one as 0."""
    dims_with_sentiment = {
        "coverage": 100.0, "source_diversity": 100.0, "duplicate_rate": 100.0,
        "freshness": 100.0, "latency": 100.0, "sentiment_availability": 100.0,
        "cross_provider_coverage_agreement": 100.0,
    }
    dims_without_sentiment = {k: v for k, v in dims_with_sentiment.items() if k != "sentiment_availability"}
    assert composite_score(dims_with_sentiment) == 100.0
    assert composite_score(dims_without_sentiment) == 100.0  # renormalized, not dragged down by an implicit 0


# ── score_symbol: end-to-end orchestration ──────────────────────────────

def test_score_symbol_reports_failed_fetch_with_real_error(monkeypatch):
    def _fake_fetch(provider, symbol, hours_back, limit):
        return None
    monkeypatch.setattr("backtest.news_benchmark._fetch_provider_articles", _fake_fetch)
    results = score_symbol("EURUSD", ["marketaux"], hours_back=48, limit=20)
    assert len(results) == 1
    r = results[0]
    assert r.fetch_ok is False
    assert r.error is not None
    assert r.coverage_score is None
    assert r.composite_score is None


def test_score_symbol_never_crashes_on_provider_exception(monkeypatch):
    def _fake_fetch(provider, symbol, hours_back, limit):
        raise RuntimeError("boom")
    monkeypatch.setattr("backtest.news_benchmark._fetch_provider_articles", _fake_fetch)
    results = score_symbol("EURUSD", ["marketaux"], hours_back=48, limit=20)
    assert results[0].fetch_ok is False
    assert "boom" in results[0].error


def test_score_symbol_computes_real_dims_on_success(monkeypatch):
    def _fake_fetch(provider, symbol, hours_back, limit):
        return [_article(headline="Fed hikes rates", hours_ago=0.5, source="reuters", sentiment=0.4 if provider == "marketaux" else None)]
    monkeypatch.setattr("backtest.news_benchmark._fetch_provider_articles", _fake_fetch)
    results = score_symbol("EURUSD", ["marketaux", "finnhub"], hours_back=48, limit=20)
    assert len(results) == 2
    ma = next(r for r in results if r.provider == "marketaux")
    fh = next(r for r in results if r.provider == "finnhub")
    assert ma.fetch_ok and fh.fetch_ok
    assert ma.article_count == 1 and fh.article_count == 1
    assert ma.sentiment_availability_score == 100.0
    assert fh.sentiment_availability_score is None  # honest structural gap, never fabricated
    assert ma.cross_provider_coverage_agreement_score == 100.0
    assert fh.cross_provider_coverage_agreement_score == 100.0
    assert ma.composite_score is not None and fh.composite_score is not None


def test_news_benchmark_result_to_dict_roundtrips():
    r = NewsBenchmarkResult(
        provider="marketaux", symbol="EURUSD", fetch_ok=True, error=None, latency_ms=120,
        article_count=1, coverage_score=100.0, source_diversity_score=100.0,
        duplicate_rate_score=100.0, freshness_score=100.0, latency_score=100.0,
        sentiment_availability_score=100.0, cross_provider_coverage_agreement_score=100.0,
        composite_score=100.0, mean_sentiment=0.4, detail={"n": 1},
    )
    d = r.to_dict()
    assert d["provider"] == "marketaux"
    assert d["composite_score"] == 100.0
