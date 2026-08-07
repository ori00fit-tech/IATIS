"""
fundamentals/finnhub_news_client.py
---------------------------------------
Finnhub news client, built for Provider Benchmark Phase 2 (News Benchmark)
to give MarketAux a real second provider to compare against.

FINNHUB_API_KEY already exists in this codebase (core/data_providers.py's
_fetch_finnhub, OHLC candles only) but was never wired to any news
endpoint — confirmed by grep, zero /news or /company-news calls anywhere.

Load-bearing fact, verified before building anything here (not guessed):
Finnhub's /company-news endpoint is North-American-companies-only — not
usable for this codebase's FX/metals/crypto/indices universe. The
endpoint that actually covers this universe is /news, which is
CATEGORY-WIDE (general|forex|crypto|merger), not symbol-scoped, and
carries no sentiment score at all (headline/summary/source/datetime/
category/id/url only). This is a genuinely different shape from
MarketAux's per-entity-tagged, sentiment-scored articles — get_symbol_news()
below is an explicit, documented BEST-EFFORT keyword-match proxy over the
category feed, not real per-symbol entity tagging. Never silently treated
as equivalent to MarketAux's precision.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://finnhub.io/api/v1/news"

# IATIS internal symbol -> Finnhub /news category + the keyword tokens a
# headline/summary must contain (case-insensitive, ANY token) to count as
# "about" this symbol under the best-effort proxy. No dedicated metals/
# indices category exists on Finnhub's free /news endpoint (only
# general|forex|crypto|merger) — those symbols are deliberately NOT
# mapped here (get_symbol_news returns None, "not supported", never a
# misleading "general" substitute), matching marketaux_client.py's own
# "better to return no signal than a wrong mapping" convention.
FINNHUB_NEWS_SYMBOL_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "EURUSD": ("forex", ("EUR/USD", "EURUSD", "euro")),
    "GBPUSD": ("forex", ("GBP/USD", "GBPUSD", "pound", "sterling")),
    "USDJPY": ("forex", ("USD/JPY", "USDJPY", "yen")),
    "USDCHF": ("forex", ("USD/CHF", "USDCHF", "franc")),
    "AUDUSD": ("forex", ("AUD/USD", "AUDUSD", "aussie")),
    "USDCAD": ("forex", ("USD/CAD", "USDCAD", "loonie")),
    "NZDUSD": ("forex", ("NZD/USD", "NZDUSD", "kiwi")),
    "EURJPY": ("forex", ("EUR/JPY", "EURJPY")),
    "GBPJPY": ("forex", ("GBP/JPY", "GBPJPY")),
    "AUDJPY": ("forex", ("AUD/JPY", "AUDJPY")),
    "EURGBP": ("forex", ("EUR/GBP", "EURGBP")),
    "EURCHF": ("forex", ("EUR/CHF", "EURCHF")),
    "BTCUSD": ("crypto", ("bitcoin", "btc")),
    "ETHUSD": ("crypto", ("ethereum", "eth")),
}


def get_category_news(category: str, hours_back: int = 48, limit: int = 250) -> list[dict] | None:
    """Raw Finnhub /news?category=X feed, filtered to hours_back. Returns
    None (not []) when FINNHUB_API_KEY is unset or the request fails —
    mirrors marketaux_client.get_news_articles()'s None-means-no-signal
    convention exactly. Each record: {"headline", "published_at" (iso),
    "source", "sentiment": None} — Finnhub's free /news response has no
    sentiment field at all; sentiment stays None always, never fabricated."""
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        return None

    try:
        resp = requests.get(BASE_URL, params={"category": category, "token": api_key}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning(f"Finnhub news request failed for category={category}: {exc}")
        return None

    if not isinstance(data, list):
        logger.warning(f"Finnhub news error for category={category}: {data}")
        return None

    cutoff = time.time() - hours_back * 3600
    out: list[dict] = []
    for article in data[:limit]:
        ts = article.get("datetime")
        if not isinstance(ts, (int, float)) or ts < cutoff:
            continue
        out.append({
            "headline": article.get("headline", ""),
            "published_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "source": article.get("source", ""),
            "sentiment": None,
        })
    return out


def get_symbol_news(symbol: str, hours_back: int = 48, limit: int = 250) -> list[dict] | None:
    """Best-effort per-symbol proxy over Finnhub's category-wide /news
    feed: fetches the symbol's mapped category, then keeps only articles
    whose headline mentions one of the symbol's own keyword tokens.
    Returns None when the symbol has no category mapping (metals/indices —
    Finnhub's free /news has no dedicated category for them) or the
    underlying category fetch itself returns None."""
    mapping = FINNHUB_NEWS_SYMBOL_MAP.get(symbol)
    if not mapping:
        return None
    category, tokens = mapping
    articles = get_category_news(category, hours_back=hours_back, limit=limit)
    if articles is None:
        return None
    tokens_lower = tuple(t.lower() for t in tokens)
    return [a for a in articles if any(t in a["headline"].lower() for t in tokens_lower)]
