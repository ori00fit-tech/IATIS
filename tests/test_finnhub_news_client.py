"""tests/test_finnhub_news_client.py — Finnhub news-client tests (Provider
Benchmark Phase 2, second news provider)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from fundamentals.finnhub_news_client import (
    FINNHUB_NEWS_SYMBOL_MAP,
    get_category_news,
    get_symbol_news,
)


def _article(headline="test headline", hours_ago=1, source="reuters"):
    return {"headline": headline, "datetime": int(time.time() - hours_ago * 3600), "source": source, "category": "forex", "id": 1}


def _mock_response(json_data, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_data
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = Exception("HTTP error")
    return resp


# ── get_category_news ────────────────────────────────────────────────

def test_get_category_news_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert get_category_news("forex") is None


def test_get_category_news_extracts_headline_source_and_timestamp(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    with patch("requests.get", return_value=_mock_response([_article(headline="Fed hikes rates", hours_ago=1)])):
        result = get_category_news("forex")
    assert len(result) == 1
    assert result[0]["headline"] == "Fed hikes rates"
    assert result[0]["source"] == "reuters"
    assert result[0]["sentiment"] is None  # Finnhub's free /news has no sentiment field — never fabricated


def test_get_category_news_excludes_stale_articles(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    with patch("requests.get", return_value=_mock_response([_article(hours_ago=100)])):
        result = get_category_news("forex", hours_back=48)
    assert result == []


def test_get_category_news_respects_limit(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    articles = [_article(headline=f"h{i}", hours_ago=1) for i in range(10)]
    with patch("requests.get", return_value=_mock_response(articles)):
        result = get_category_news("forex", limit=3)
    assert len(result) == 3


def test_get_category_news_returns_none_on_non_list_response(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "bad_key")
    with patch("requests.get", return_value=_mock_response({"error": "invalid key"})):
        result = get_category_news("forex")
    assert result is None


def test_get_category_news_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    with patch("requests.get", side_effect=Exception("timeout")):
        result = get_category_news("forex")
    assert result is None


def test_get_category_news_skips_articles_with_missing_or_bad_timestamp(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    articles = [{"headline": "no ts", "source": "x"}, _article(headline="has ts", hours_ago=1)]
    with patch("requests.get", return_value=_mock_response(articles)):
        result = get_category_news("forex")
    assert len(result) == 1
    assert result[0]["headline"] == "has ts"


# ── get_symbol_news (best-effort keyword-match proxy) ────────────────

def test_get_symbol_news_returns_none_for_unmapped_symbol():
    """Metals/indices have no dedicated Finnhub /news category — a real,
    documented gap, never a misleading "general" substitute."""
    assert get_symbol_news("XAUUSD") is None


def test_get_symbol_news_filters_to_matching_headlines(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    articles = [
        _article(headline="EUR/USD climbs on ECB comments", hours_ago=1),
        _article(headline="Bitcoin surges past resistance", hours_ago=1),
        _article(headline="Oil prices fall on supply glut", hours_ago=1),
    ]
    with patch("requests.get", return_value=_mock_response(articles)):
        result = get_symbol_news("EURUSD")
    assert len(result) == 1
    assert "EUR/USD" in result[0]["headline"]


def test_get_symbol_news_crypto_category_and_keywords(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    articles = [_article(headline="Bitcoin ETF inflows accelerate", hours_ago=1)]
    with patch("requests.get", return_value=_mock_response(articles)) as mocked:
        result = get_symbol_news("BTCUSD")
    assert mocked.call_args.kwargs["params"]["category"] == "crypto"
    assert len(result) == 1


def test_get_symbol_news_returns_none_when_category_fetch_fails(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert get_symbol_news("EURUSD") is None


def test_get_symbol_news_returns_empty_list_when_no_headline_matches(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    articles = [_article(headline="Completely unrelated market news", hours_ago=1)]
    with patch("requests.get", return_value=_mock_response(articles)):
        result = get_symbol_news("EURUSD")
    assert result == []


def test_finnhub_news_symbol_map_covers_only_fx_and_crypto():
    """No metals/indices entries — the real, documented scope boundary."""
    categories = {c for c, _ in FINNHUB_NEWS_SYMBOL_MAP.values()}
    assert categories <= {"forex", "crypto"}
    assert "XAUUSD" not in FINNHUB_NEWS_SYMBOL_MAP
