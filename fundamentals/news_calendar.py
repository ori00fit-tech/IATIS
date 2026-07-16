"""
fundamentals/news_calendar.py
-------------------------------
Economic calendar client for the pre-news blackout gate.

Source of truth: the **Forex Factory** public calendar JSON — the trusted,
key-free economic-calendar provider already wired into this system. It is the
industry-standard retail event calendar (scheduled events with times, needed to
blackout N minutes *before* release), unlike news/sentiment providers such as
MarketAux which only report articles after the fact.

JBlanked was removed (2026-07-16): its API returned persistent 401s and it was
never load-bearing — the system already ran on Forex Factory + the local cache.

Resolution order (most reliable first, since the VPS may be rate-limited by
live calendar hosts):
  1. Local cache  — storage/calendar_cache.json, refreshed daily by
                    scripts/cache_calendar.py (which itself pulls Forex Factory).
  2. Forex Factory live JSON  — nfs/cdn faireconomy.media feeds.
  3. Minimal hardcoded US schedule  — last-resort so the blackout gate never
                    fails open during a known high-impact window.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

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


def _forex_factory_fallback() -> list[dict]:
    """Fallback: Forex Factory public calendar JSON (no API key)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; IATIS-Trading-Bot/1.0)",
        "Accept": "application/json",
    }
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
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
            logger.warning(f"Forex Factory fallback failed ({url}): {exc}")
            continue

    # Last resort: return known high-impact events for today
    # This ensures blackout system never fails completely
    logger.warning("All calendar sources failed — using minimal hardcoded schedule")
    return _minimal_us_schedule()


def _minimal_us_schedule() -> list[dict]:
    """Emergency fallback: known US high-impact recurring events.

    When all APIs fail, assume standard US economic schedule.
    Conservative approach: better to miss a trade than trade during NFP.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Mon, 4=Fri
    hour = now.hour

    events = []
    # NFP: First Friday of month, 12:30 UTC
    if weekday == 4 and hour in (11, 12, 13):
        events.append({
            "name": "Non-Farm Payrolls (possible — verify manually)",
            "currency": "USD",
            "date": now.strftime("%Y-%m-%d") + "T12:30:00",
            "impact": "High",
            "source": "hardcoded_fallback",
        })

    return events


CACHE_PATH = Path(__file__).resolve().parent.parent / "storage" / "calendar_cache.json"


def _read_cache() -> list[dict]:
    """Read locally cached calendar (updated by scripts/cache_calendar.py)."""
    if not CACHE_PATH.exists():
        return []
    try:
        data = json.loads(CACHE_PATH.read_text())
        fetched_at = data.get("fetched_at", "")
        # Only use cache if less than 25 hours old
        if fetched_at:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)
            if age.total_seconds() > 25 * 3600:
                logger.warning("Calendar cache is stale (>25h) — ignoring")
                return []
        events = data.get("events", [])
        logger.info(f"Calendar cache: {len(events)} events (fetched {fetched_at[:10]})")
        return events
    except Exception as exc:
        logger.warning(f"Calendar cache read failed: {exc}")
        return []


def get_calendar_today() -> list[dict]:
    """Get today's economic events (local cache → Forex Factory → minimal)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Prefer the daily-refreshed local cache: the VPS can be rate-limited by
    # live calendar hosts, so the cron-populated cache is the reliable path.
    cached = _read_cache()
    if cached:
        today_events = [e for e in cached if e.get("date", "").startswith(today)]
        if today_events:
            logger.info(f"Calendar cache (today): {len(today_events)} events")
            return today_events

    # Live Forex Factory public JSON (ends in a minimal hardcoded schedule if
    # even that is unreachable, so the blackout gate never fails open).
    all_week = _forex_factory_fallback()
    today_events = [e for e in all_week if e.get("date", "").startswith(today)]
    logger.info(f"Forex Factory today: {len(today_events)} events")
    return today_events


def get_calendar_week() -> list[dict]:
    """Get this week's economic events (local cache → Forex Factory)."""
    cached = _read_cache()
    if cached:
        return cached

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
