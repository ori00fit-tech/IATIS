"""tests/test_marketaux_client.py — MarketAux news-sentiment client tests."""
from __future__ import annotations
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
import pytest

from fundamentals.marketaux_client import get_news_sentiment


def _article(symbol="EURUSD", sentiment=0.3, hours_ago=1):
    published = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {
        "title": "test", "published_at": published, "source": "test.com",
        "entities": [{"symbol": symbol, "sentiment_score": sentiment}],
    }


def _mock_response(json_data, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_data
    if status_ok:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = Exception("HTTP error")
    return resp


def test_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    assert get_news_sentiment("EURUSD") is None


def test_returns_none_for_unmapped_symbol(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    assert get_news_sentiment("USOIL") is None  # energy/indices not mapped yet


def test_xauusd_maps_to_the_confirmed_gold_entity(monkeypatch):
    """XAUUSD -> "GOLD" confirmed 2026-07-24 via --probe-xauusd against
    the live API (3/3 real entity matches; XAUUSD/XAU/XAU-USD all
    returned zero). Regression pin: get_news_sentiment must actually
    query MarketAux for XAUUSD now, not silently return None."""
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    data = {"meta": {}, "data": [_article(symbol="GOLD", sentiment=0.5, hours_ago=1)]}
    with patch("requests.get", return_value=_mock_response(data)) as mocked:
        result = get_news_sentiment("XAUUSD")
    assert mocked.call_args.kwargs["params"]["symbols"] == "GOLD"
    assert result["article_count"] == 1


def test_aggregates_sentiment_from_recent_articles(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    data = {
        "meta": {"found": 2, "returned": 2, "limit": 20, "page": 1},
        "data": [_article(sentiment=0.4, hours_ago=1), _article(sentiment=0.2, hours_ago=2)],
    }
    with patch("requests.get", return_value=_mock_response(data)):
        result = get_news_sentiment("EURUSD")
    assert result["article_count"] == 2
    assert result["mean_sentiment"] == pytest.approx(0.3)


def test_excludes_stale_articles_outside_window(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    data = {
        "meta": {}, "data": [_article(sentiment=0.9, hours_ago=100)],  # older than default 48h window
    }
    with patch("requests.get", return_value=_mock_response(data)):
        result = get_news_sentiment("EURUSD", hours_back=48)
    assert result["article_count"] == 0
    assert result["mean_sentiment"] == 0.0


def test_returns_zero_signal_when_no_matching_entities(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    data = {"meta": {}, "data": []}
    with patch("requests.get", return_value=_mock_response(data)):
        result = get_news_sentiment("EURUSD")
    assert result == {"symbol": "EURUSD", "article_count": 0, "mean_sentiment": 0.0, "scores": []}


def test_returns_none_on_api_error_body(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "bad_key")
    data = {"error": {"code": "invalid_api_token", "message": "An invalid API token was supplied."}}
    with patch("requests.get", return_value=_mock_response(data)):
        result = get_news_sentiment("EURUSD")
    assert result is None


def test_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    with patch("requests.get", side_effect=Exception("timeout")):
        result = get_news_sentiment("EURUSD")
    assert result is None
