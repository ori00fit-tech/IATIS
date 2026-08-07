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
# live API for fx majors/crosses and crypto; energy/indices use different
# entity naming on MarketAux's side and are not mapped here until
# confirmed — better to return "no signal" than a wrong mapping.
MARKETAUX_SYMBOL_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "USDCHF": "USDCHF", "AUDUSD": "AUDUSD", "USDCAD": "USDCAD",
    "NZDUSD": "NZDUSD", "EURJPY": "EURJPY", "GBPJPY": "GBPJPY",
    "AUDJPY": "AUDJPY", "EURGBP": "EURGBP", "EURCHF": "EURCHF",
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
    # Confirmed 2026-07-24 via scripts/collect_marketaux_sentiment.py
    # --probe-xauusd against the live API: "GOLD" returned real entity
    # matches (3/3); "XAUUSD", "XAU/USD", "XAU" all returned zero — those
    # candidates do not exist as MarketAux entities.
    "XAUUSD": "GOLD",
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


def get_news_articles(symbol: str, limit: int = 20, hours_back: int = 48) -> list[dict] | None:
    """Provider Benchmark Phase 2 (News Benchmark) — per-article records,
    for the fields get_news_sentiment() already fetches but discards
    (title/source, kept only as aggregate scores above). Returns None
    (not []) under the exact same conditions get_news_sentiment() does:
    no MARKETAUX_API_KEY, unmapped symbol, or a failed/errored request —
    None means "no signal available", [] means "fetched successfully,
    zero matching articles in the window".

    Each record: {"headline", "published_at" (iso), "source",
    "sentiment": float|None} — sentiment is the mean of that article's own
    entity sentiment_score(s) for `symbol`'s entity, None if the article
    matched the symbol filter but carried no scored entity (rare, but the
    live API's own filter_entities=true doesn't guarantee every entity has
    a score)."""
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
    out: list[dict] = []
    for article in data.get("data", []):
        published = article.get("published_at", "")
        try:
            ts = datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        entity_scores = [
            float(e["sentiment_score"]) for e in article.get("entities", [])
            if e.get("symbol") == ma_symbol and e.get("sentiment_score") is not None
        ]
        out.append({
            "headline": article.get("title", ""),
            "published_at": published,
            "source": article.get("source", ""),
            "sentiment": round(sum(entity_scores) / len(entity_scores), 4) if entity_scores else None,
        })
    return out
