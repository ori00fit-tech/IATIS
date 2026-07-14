"""tests/test_sentiment_engine.py — Sentiment engine tests, incl. H021's
MarketAux news-sentiment wiring (config-gated infra, engine stays disabled
in config/engines.yaml regardless — see research/results/registry.json)."""
from __future__ import annotations
from unittest.mock import patch
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.sentiment_engine import SentimentEngine


def _flat_df(n=60, price=1.1000):
    """Price sits exactly mid-range so the retail-proxy fallback stays
    neutral, isolating whatever else drives bias (COT / MarketAux)."""
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": [price] * n, "high": [price + 0.0005] * n,
        "low": [price - 0.0005] * n, "close": [price] * n,
        "volume": [100.0] * n,
    }, index=idx)


def _engine(symbol="EURUSD"):
    e = SentimentEngine()
    e.decision_tf = "H1"
    e._symbol = symbol
    return e


def test_no_cot_no_marketaux_stays_neutral(monkeypatch):
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    with patch("engines.sentiment_engine._load_cot_data", return_value=None):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.NEUTRAL
    assert out.raw["marketaux_available"] is False


def test_marketaux_bullish_drives_bias_when_cot_absent(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    fake_sentiment = {"symbol": "EURUSD", "article_count": 12, "mean_sentiment": 0.45, "scores": [0.45] * 12}
    with patch("engines.sentiment_engine._load_cot_data", return_value=None), \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.BULLISH
    assert out.raw["marketaux_available"] is True
    assert out.raw["marketaux_mean_sentiment"] == 0.45
    assert any("MarketAux" in r for r in out.reasons)


def test_marketaux_bearish_drives_bias_when_cot_absent(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    fake_sentiment = {"symbol": "EURUSD", "article_count": 8, "mean_sentiment": -0.3, "scores": [-0.3] * 8}
    with patch("engines.sentiment_engine._load_cot_data", return_value=None), \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.BEARISH


def test_marketaux_confirms_cot_boosts_score(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    cot = {"large_spec_net": 20000, "net_change_4w": 3000}
    fake_sentiment = {"symbol": "EURUSD", "article_count": 10, "mean_sentiment": 0.4, "scores": [0.4] * 10}
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot), \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.BULLISH
    assert any("MarketAux confirms" in r for r in out.reasons)


def test_marketaux_unavailable_falls_back_to_existing_behavior(monkeypatch):
    """No MARKETAUX_API_KEY set — engine behaves exactly as it did before
    H021's wiring (neutral here, since COT/retail proxy are both silent)."""
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    with patch("engines.sentiment_engine._load_cot_data", return_value=None):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.NEUTRAL
    assert out.raw["marketaux_mean_sentiment"] is None
