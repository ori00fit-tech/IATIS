"""
fundamentals/marketaux_client.py
-----------------------------------
MarketAux news + per-entity sentiment client.

Built for H021 (research/results/registry.json — MarketAux news sentiment
as a Sentiment engine input, pre-registered before this client's output
was wired into any scoring logic). Not a data-quality upgrade on its own:
the client only becomes evidence once H021's controlled A/B test runs.

API: https://api.marketaux.com/v1/news/all (free tier: 100 requests/day —
verified 2026-07-14 against the real endpoint, not guessed from docs,
which are behind bot-protection for unauthenticated fetches).

Response shape (verified via a real request):
  {"meta": {"found", "returned", "limit", "page"},
   "data": [{"title", "published_at", "source", "url",
             "entities": [{"symbol", "sentiment_score" (-1..1), ...}]}]}
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.marketaux.com/v1/news/all"

# IATIS internal symbol -> MarketAux "symbols" param. Verified against the
# live API for fx majors/crosses and crypto only; metals/energy/indices
# use different entity naming on MarketAux's side and are not mapped here
# until confirmed — better to return "no signal" than a wrong mapping.
MARKETAUX_SYMBOL_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "USDCHF": "USDCHF", "AUDUSD": "AUDUSD", "USDCAD": "USDCAD",
    "NZDUSD": "NZDUSD", "EURJPY": "EURJPY", "GBPJPY": "GBPJPY",
    "AUDJPY": "AUDJPY", "EURGBP": "EURGBP", "EURCHF": "EURCHF",
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
}


def get_news_sentiment(symbol: str, limit: int = 20, hours_back: int = 48) -> dict | None:
    """Aggregate recent per-entity sentiment_score for `symbol`.

    Returns None (not a neutral 0.0) when MARKETAUX_API_KEY is unset, the
    symbol has no verified mapping, or the request fails — callers must
    treat None as "no signal available", distinct from a genuine neutral
    reading of 0.0 sentiment.
    """
    api_key = os.environ.get("MARKETAUX_API_KEY", "")
    if not api_key:
        return None

    ma_symbol = MARKETAUX_SYMBOL_MAP.get(symbol)
    if not ma_symbol:
        return None

    params = {
        "symbols": ma_symbol,
        "filter_entities": "true",
        "language": "en",
        "limit": limit,
        "api_token": api_key,
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning(f"MarketAux request failed for {symbol}: {exc}")
        return None

    if "error" in data:
        logger.warning(f"MarketAux error for {symbol}: {data['error'].get('message', data['error'])}")
        return None

    cutoff = time.time() - hours_back * 3600
    scores: list[float] = []
    for article in data.get("data", []):
        published = article.get("published_at", "")
        try:
            ts = datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        for entity in article.get("entities", []):
            if entity.get("symbol") == ma_symbol and entity.get("sentiment_score") is not None:
                scores.append(float(entity["sentiment_score"]))

    if not scores:
        return {"symbol": symbol, "article_count": 0, "mean_sentiment": 0.0, "scores": []}

    return {
        "symbol": symbol,
        "article_count": len(scores),
        "mean_sentiment": round(sum(scores) / len(scores), 4),
        "scores": scores,
    }
