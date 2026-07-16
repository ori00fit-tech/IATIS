"""
scripts/download_historical_news.py
--------------------------------------
Download historical economic calendar data for backtesting.

Source: Forex Factory JSON weekly archives (keyless). JBlanked was removed
2026-07-16; the economic calendar is served entirely by Forex Factory now.

Stores: storage/news_history/YYYY-MM.json per month

Each event:
  {
    "name": "Non-Farm Payrolls",
    "currency": "USD",
    "datetime": "2024-01-05T13:30:00+00:00",
    "impact": "High",
    "actual": "216K",
    "forecast": "170K",
    "previous": "199K",
    "surprise": "positive"   # actual > forecast
  }

Usage:
    python3 scripts/download_historical_news.py --years 2
    python3 scripts/download_historical_news.py --year 2024
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

NEWS_DIR = Path("storage/news_history")

HIGH_IMPACT_NAMES = {
    "Non-Farm Payrolls", "NFP", "Federal Funds Rate", "FOMC",
    "CPI", "Core CPI", "GDP", "Core PCE", "PPI",
    "ISM Manufacturing", "Retail Sales",
    "ECB Interest Rate Decision", "BOE Interest Rate Decision",
    "BOJ Interest Rate Decision", "RBA Rate Decision",
    "RBNZ Rate Decision", "BOC Rate Decision",
    "German CPI", "UK CPI", "Tokyo CPI",
}

CURRENCY_TO_SYMBOLS = {
    "USD": ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
            "EURJPY","GBPJPY","AUDJPY","XAUUSD","USOIL","US30","NAS100","SPX500"],
    "EUR": ["EURUSD","EURJPY","EURGBP","EURCHF"],
    "GBP": ["GBPUSD","GBPJPY","EURGBP"],
    "JPY": ["USDJPY","EURJPY","GBPJPY","AUDJPY"],
    "CHF": ["USDCHF","EURCHF"],
    "AUD": ["AUDUSD","AUDJPY"],
    "CAD": ["USDCAD"],
    "NZD": ["NZDUSD"],
}


def _parse_actual(actual_str: str) -> float | None:
    """Parse actual value string to float."""
    if not actual_str or actual_str.strip() in ("", "—", "-"):
        return None
    cleaned = actual_str.replace("K", "000").replace("M", "000000").replace("%", "")
    cleaned = cleaned.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _classify_surprise(actual: str, forecast: str) -> str:
    """Classify if actual was better/worse than forecast."""
    a = _parse_actual(actual)
    f = _parse_actual(forecast)
    if a is None or f is None:
        return "unknown"
    if abs(a - f) < 0.01 * max(abs(f), 1):
        return "inline"
    return "positive" if a > f else "negative"


def fetch_forex_factory_archive(year: int, month: int) -> list[dict]:
    """Fetch Forex Factory weekly archives for a month."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; IATIS/1.0)"}
    events = []

    # FF provides weekly files — find weeks in this month
    from datetime import date
    start = date(year, month, 1)
    # Go to the Monday of the week containing start
    day = start - timedelta(days=start.weekday())

    while day.month <= month or (day.year == year and day.month < month + 1):
        if day.year < year or (day.year == year and day.month < month):
            day += timedelta(weeks=1)
            continue

        date_str = day.strftime("%b%d.%Y").lower()
        url = f"https://nfs.faireconomy.media/ff_calendar_{date_str}.json"
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                for e in resp.json():
                    events.append({
                        "name": e.get("title", ""),
                        "currency": e.get("country", "").upper()[:3],
                        "datetime": e.get("date", ""),
                        "impact": e.get("impact", "Low"),
                        "actual": e.get("actual", ""),
                        "forecast": e.get("forecast", ""),
                        "previous": e.get("previous", ""),
                    })
        except Exception:
            pass

        day += timedelta(weeks=1)
        if day.month > month and day.year >= year:
            break
        time.sleep(0.3)

    return events


def normalize_event(raw: dict, source: str) -> dict | None:
    """Normalize event from any source to standard format."""
    name = raw.get("name", raw.get("title", ""))
    currency = raw.get("currency", raw.get("country", "")).upper()[:3]
    impact = raw.get("impact", "Low")
    dt_str = raw.get("datetime", raw.get("date", ""))
    actual = str(raw.get("actual", ""))
    forecast = str(raw.get("forecast", ""))
    previous = str(raw.get("previous", ""))

    if not name or not currency or not dt_str:
        return None

    # Determine affected symbols
    affected = CURRENCY_TO_SYMBOLS.get(currency, [])

    # Is high impact?
    is_high = (
        impact.lower() in ("high", "3") or
        any(h.lower() in name.lower() for h in HIGH_IMPACT_NAMES)
    )

    return {
        "name": name,
        "currency": currency,
        "datetime": dt_str,
        "impact": "High" if is_high else impact,
        "actual": actual,
        "forecast": forecast,
        "previous": previous,
        "surprise": _classify_surprise(actual, forecast),
        "affected_symbols": affected,
        "source": source,
    }


def download_month(year: int, month: int) -> list[dict]:
    """Download events for one month from the Forex Factory weekly archives."""
    events = []

    raw = fetch_forex_factory_archive(year, month)
    if raw:
        for e in raw:
            n = normalize_event(e, "forex_factory")
            if n:
                events.append(n)
        print(f"    Forex Factory: {len(events)} events")

    return events


def save_month(year: int, month: int, events: list[dict]) -> Path:
    """Save events to storage/news_history/YYYY-MM.json"""
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    path = NEWS_DIR / f"{year}-{month:02d}.json"
    path.write_text(json.dumps({
        "year": year, "month": month,
        "count": len(events),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }, indent=2))
    return path


def load_news_for_date(dt: datetime) -> list[dict]:
    """Load events for a specific date from cache."""
    path = NEWS_DIR / f"{dt.year}-{dt.month:02d}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        date_str = dt.strftime("%Y-%m-%d")
        return [
            e for e in data.get("events", [])
            if e.get("datetime", "").startswith(date_str)
        ]
    except Exception:
        return []


def get_events_in_window(
    symbol: str,
    bar_time: datetime,
    look_ahead_min: int = 60,
) -> list[dict]:
    """
    Get high-impact events affecting a symbol within look_ahead_min minutes.
    Used by backtesting engine for historical news simulation.
    """
    events = load_news_for_date(bar_time)
    window_end = bar_time + timedelta(minutes=look_ahead_min)
    window_start = bar_time - timedelta(minutes=15)  # also capture just-released

    relevant = []
    for event in events:
        if symbol not in event.get("affected_symbols", []):
            continue
        if event.get("impact", "Low") not in ("High", "3"):
            continue

        try:
            event_dt = datetime.fromisoformat(
                event["datetime"].replace("Z", "+00:00")
            )
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if window_start <= event_dt <= window_end:
            minutes_until = int((event_dt - bar_time).total_seconds() / 60)
            relevant.append({**event, "minutes_until": minutes_until})

    return sorted(relevant, key=lambda x: abs(x["minutes_until"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()

    now = datetime.now()

    if args.year:
        months = [(args.year, m) for m in range(1, 13) if
                  datetime(args.year, m, 1) <= now]
    else:
        months = []
        for y in range(now.year - args.years + 1, now.year + 1):
            for m in range(1, 13):
                if datetime(y, m, 1) <= now:
                    months.append((y, m))

    print(f"\nDownloading historical news calendar")
    print(f"Period: {months[0]} → {months[-1]}")
    print(f"Months: {len(months)}")
    print(f"Source: Forex Factory weekly archives (keyless)")
    print()

    total_events = 0
    for year, month in months:
        cache_path = NEWS_DIR / f"{year}-{month:02d}.json"
        if cache_path.exists():
            data = json.loads(cache_path.read_text())
            print(f"  {year}-{month:02d}: ⏭  cached ({data['count']} events)")
            total_events += data["count"]
            continue

        print(f"  {year}-{month:02d}: downloading...", end=" ", flush=True)
        events = download_month(year, month)
        if events:
            save_month(year, month, events)
            total_events += len(events)
        else:
            print("⚠️  no events found")
        time.sleep(0.5)

    print(f"\nTotal: {total_events:,} events across {len(months)} months")
    print(f"Saved in: {NEWS_DIR}/")

    # Summary of high-impact
    high_count = 0
    for year, month in months:
        path = NEWS_DIR / f"{year}-{month:02d}.json"
        if path.exists():
            data = json.loads(path.read_text())
            high_count += sum(1 for e in data["events"] if e.get("impact") == "High")

    print(f"High-impact events: {high_count:,}")
    print(f"\nUsage in backtest:")
    print(f"  from scripts.download_historical_news import get_events_in_window")
    print(f"  events = get_events_in_window('EURUSD', bar_datetime, 60)")


if __name__ == "__main__":
    main()
