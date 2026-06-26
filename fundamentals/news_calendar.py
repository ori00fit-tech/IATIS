"""
fundamentals/news_calendar.py
-------------------------------
Economic calendar client using jblanked.com News API.

API: https://www.jblanked.com/news/api/docs/calendar/
Free tier: 1 request/second, API key required
Register: https://www.jblanked.com/

Endpoints used:
  GET /news/api/calendar/today/     — today's events (all currencies)
  GET /news/api/calendar/week/      — this week's events

Response fields:
  name, currency, date, actual, forecast, previous, impact, outcome
  strength (Strong/Weak), quality (Good/Bad)

Fallback: Forex Factory public JSON (no key needed, limited data)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path
import json

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Impact levels for each event type
# ---------------------------------------------------------------------------
HIGH_IMPACT_EVENTS = {
    # US
    "Non-Farm Payrolls", "NFP", "FOMC", "Federal Funds Rate",
    "CPI", "Core CPI", "PPI", "Core PPI", "GDP", "Retail Sales",
    "ISM Manufacturing", "ISM Services", "ADP Non-Farm",
    "Initial Jobless Claims", "JOLTS",
    # EUR
    "ECB Interest Rate Decision", "ECB Press Conference",
    "German CPI", "German GDP", "Euro Zone CPI",
    # GBP
    "BOE Interest Rate Decision", "UK CPI", "UK GDP",
    # JPY
    "BOJ Interest Rate Decision", "Tokyo CPI",
    # Other central banks
    "RBA Rate Decision", "RBNZ Rate Decision",
    "BOC Rate Decision", "SNB Rate Decision",
}

MEDIUM_IMPACT_EVENTS = {
    "Trade Balance", "Current Account", "Industrial Production",
    "Consumer Confidence", "Manufacturing PMI", "Services PMI",
    "Unemployment Rate", "Average Earnings",
}

# Currency → affected symbol groups
CURRENCY_SYMBOLS = {
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
            "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY", "XAUUSD", "USOIL",
            "US30", "NAS100", "SPX500", "BTCUSD", "ETHUSD"],
    "EUR": ["EURUSD", "EURJPY", "EURGBP", "EURCHF"],
    "GBP": ["GBPUSD", "GBPJPY", "EURGBP"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY"],
    "CHF": ["USDCHF", "EURCHF"],
    "AUD": ["AUDUSD", "AUDJPY"],
    "CAD": ["USDCAD"],
    "NZD": ["NZDUSD"],
}


@lru_cache(maxsize=1)
def _get_api_key() -> str:
    return os.environ.get("JBLANKED_API_KEY", "")


def _jblanked_request(endpoint: str) -> list[dict] | None:
    """Make authenticated request to jblanked News API."""
    api_key = _get_api_key()
    if not api_key:
        return None

    # New endpoint structure (updated Feb 2026):
    # /news/api/ is deprecated → use /news/api/mql5/ or /news/api/forex-factory/
    url = f"https://www.jblanked.com/news/api/mql5/{endpoint}"
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("results", [])
    except Exception as exc:
        logger.warning(f"JBlanked API error: {type(exc).__name__}: {str(exc)[:100]}")
        return None


def _forex_factory_fallback() -> list[dict]:
    """Fallback: Forex Factory public calendar JSON (no API key)."""
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        events = []
        for e in raw:
            events.append({
                "name": e.get("title", ""),
                "currency": e.get("country", "").upper()[:3],
                "date": e.get("date", ""),
                "impact": e.get("impact", "Low"),
                "actual": e.get("actual", ""),
                "forecast": e.get("forecast", ""),
                "previous": e.get("previous", ""),
                "source": "forex_factory",
            })
        logger.info(f"Forex Factory fallback: {len(events)} events this week")
        return events
    except Exception as exc:
        logger.warning(f"Forex Factory fallback failed: {exc}")
        return []


def get_calendar_today() -> list[dict]:
    """Get today's economic events. Uses jblanked API with FF fallback."""
    # Try jblanked first
    events = _jblanked_request("calendar/today/")
    if events is not None:
        logger.info(f"JBlanked calendar: {len(events)} events today")
        return events

    # Fallback: filter this week's FF calendar to today
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_week = _forex_factory_fallback()
    today_events = [e for e in all_week if e.get("date", "").startswith(today)]
    return today_events


def get_calendar_week() -> list[dict]:
    """Get this week's economic events."""
    events = _jblanked_request("calendar/week/")
    if events is not None:
        logger.info(f"JBlanked calendar: {len(events)} events this week")
        return events
    return _forex_factory_fallback()


def get_upcoming_events(
    symbol: str,
    within_minutes: int = 60,
    events: list[dict] | None = None,
) -> list[dict]:
    """Get events affecting a symbol within the next N minutes.

    Args:
        symbol: e.g. "EURUSD"
        within_minutes: look-ahead window (default: 60 min)
        events: pre-fetched events list (avoids repeated API calls)
    """
    if events is None:
        events = get_calendar_today()

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=within_minutes)

    # Determine which currencies affect this symbol
    affected_currencies = set()
    for currency, symbols in CURRENCY_SYMBOLS.items():
        if symbol in symbols:
            affected_currencies.add(currency)

    upcoming = []
    for event in events:
        event_currency = event.get("currency", "").upper()
        if event_currency not in affected_currencies:
            continue

        # Parse event datetime
        event_time = _parse_event_time(event.get("date", ""))
        if event_time is None:
            continue

        if now <= event_time <= cutoff:
            upcoming.append({
                **event,
                "minutes_until": int((event_time - now).total_seconds() / 60),
                "event_time_utc": event_time.isoformat(),
            })

    return sorted(upcoming, key=lambda x: x["minutes_until"])


def _parse_event_time(date_str: str) -> datetime | None:
    """Parse various date formats from news APIs."""
    if not date_str:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%m-%d-%Y %I:%M%p",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
