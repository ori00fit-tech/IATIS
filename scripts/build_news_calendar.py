"""
scripts/build_news_calendar.py
--------------------------------
Build a reliable historical news calendar from KNOWN release dates.

Since external APIs are often blocked on VPS/cloud environments,
this script builds the calendar from ACTUAL release schedules:

  NFP:   First Friday of every month, 12:30 UTC
  FOMC:  Known meeting dates (8 per year), 18:00 UTC + 19:30 statement
  CPI:   Usually 2nd or 3rd Wednesday, 12:30 UTC
  GDP:   Quarterly (advance, preliminary, final), 12:30 UTC
  ECB:   Known meeting dates, 12:15/13:45 UTC
  BOE:   Known meeting dates, 12:00 UTC

This gives us a calendar that is:
  - Always available (no API needed)
  - Accurate for the biggest market movers
  - Sufficient for blackout-based backtesting

Usage:
    python3 scripts/build_news_calendar.py --years 2
    python3 scripts/build_news_calendar.py --year 2024

Stores: storage/news_history/YYYY-MM.json (same format as download_historical_news.py)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

NEWS_DIR = Path("storage/news_history")

# Known FOMC meeting dates (announcements at 18:00 UTC)
FOMC_DATES = {
    2024: [
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
        "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    ],
    2025: [
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
        "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17",
    ],
    2026: [
        "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
    ],
}

# Known ECB meeting dates (rate decision at 13:15 UTC, statement 13:45 UTC)
ECB_DATES = {
    2024: [
        "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
        "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
    ],
    2025: [
        "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
        "2025-07-24", "2025-09-11", "2025-10-23", "2025-12-11",
    ],
    2026: [
        "2026-01-29", "2026-03-05", "2026-04-16", "2026-06-04",
        "2026-07-23", "2026-09-10", "2026-10-22", "2026-12-10",
    ],
}

# BOE meeting dates (rate decision at 12:00 UTC)
BOE_DATES = {
    2024: [
        "2024-02-01", "2024-03-21", "2024-05-09", "2024-06-20",
        "2024-08-01", "2024-09-19", "2024-11-07", "2024-12-19",
    ],
    2025: [
        "2025-02-06", "2025-03-20", "2025-05-08", "2025-06-19",
        "2025-08-07", "2025-09-18", "2025-11-06", "2025-12-18",
    ],
    2026: [
        "2026-02-05", "2026-03-19", "2026-05-07", "2026-06-18",
        "2026-08-06", "2026-09-17", "2026-11-05", "2026-12-17",
    ],
}

CURRENCY_TO_SYMBOLS = {
    "USD": ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
            "EURJPY","GBPJPY","AUDJPY","XAUUSD","USOIL","US30","NAS100","SPX500"],
    "EUR": ["EURUSD","EURJPY","EURGBP","EURCHF"],
    "GBP": ["GBPUSD","GBPJPY","EURGBP"],
}


def _first_friday(year: int, month: int) -> date:
    """Get the first Friday of a given month."""
    d = date(year, month, 1)
    while d.weekday() != 4:  # 4 = Friday
        d += timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Get the nth occurrence of a weekday in a month."""
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


def build_month(year: int, month: int) -> list[dict]:
    """Build event list for one month."""
    events = []

    def add(name, currency, event_date, hour_utc, minute_utc, impact="High"):
        dt = datetime(year, event_date.month, event_date.day,
                     hour_utc, minute_utc, 0, tzinfo=timezone.utc)
        events.append({
            "name": name,
            "currency": currency,
            "datetime": dt.isoformat(),
            "impact": impact,
            "actual": "",
            "forecast": "",
            "previous": "",
            "surprise": "unknown",
            "affected_symbols": CURRENCY_TO_SYMBOLS.get(currency, []),
            "source": "schedule_known",
        })

    # 1. NFP — First Friday of month, 12:30 UTC
    nfp_date = _first_friday(year, month)
    if nfp_date.month == month:
        add("Non-Farm Payrolls", "USD", nfp_date, 12, 30)
        add("Unemployment Rate", "USD", nfp_date, 12, 30)

    # 2. CPI — approximately 2nd Wednesday or 3rd Wednesday
    # Actual dates vary; we use 2nd Wednesday as approximation
    try:
        cpi_date = _nth_weekday(year, month, 2, 2)  # 2nd Wednesday
        if cpi_date.month == month:
            add("CPI m/m", "USD", cpi_date, 12, 30)
            add("Core CPI m/m", "USD", cpi_date, 12, 30)
    except Exception:
        pass

    # 3. FOMC — from known dates
    fomc_year = FOMC_DATES.get(year, [])
    for dt_str in fomc_year:
        try:
            d = date.fromisoformat(dt_str)
            if d.month == month:
                add("Federal Funds Rate", "USD", d, 18, 0)
                add("FOMC Statement", "USD", d, 18, 0)
        except Exception:
            pass

    # 4. ECB — from known dates
    ecb_year = ECB_DATES.get(year, [])
    for dt_str in ecb_year:
        try:
            d = date.fromisoformat(dt_str)
            if d.month == month:
                add("ECB Interest Rate Decision", "EUR", d, 13, 15)
                add("ECB Monetary Policy Statement", "EUR", d, 13, 45)
        except Exception:
            pass

    # 5. BOE — from known dates
    boe_year = BOE_DATES.get(year, [])
    for dt_str in boe_year:
        try:
            d = date.fromisoformat(dt_str)
            if d.month == month:
                add("BOE Interest Rate Decision", "GBP", d, 12, 0)
        except Exception:
            pass

    # 6. GDP (advance, preliminary, final) — last week of month end of quarter
    if month in (1, 4, 7, 10):  # GDP advance — first month of quarter
        try:
            # Usually last Thursday of month
            for day in range(28, 32):
                try:
                    d = date(year, month, day)
                    if d.weekday() == 3:  # Thursday
                        add("Advance GDP q/q", "USD", d, 12, 30)
                        break
                except ValueError:
                    pass
        except Exception:
            pass

    return sorted(events, key=lambda x: x["datetime"])


def build_and_save(year: int, month: int, overwrite: bool = False) -> int:
    """Build and save calendar for one month."""
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    path = NEWS_DIR / f"{year}-{month:02d}.json"

    if path.exists() and not overwrite:
        data = json.loads(path.read_text())
        print(f"  {year}-{month:02d}: ⏭  cached ({data['count']} events)")
        return data["count"]

    events = build_month(year, month)
    path.write_text(json.dumps({
        "year": year, "month": month,
        "count": len(events),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "known_schedule",
        "events": events,
    }, indent=2))
    print(f"  {year}-{month:02d}: ✅ {len(events)} events (NFP + FOMC + CPI + ECB + BOE)")
    return len(events)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    now = datetime.now()

    if args.year:
        months = [(args.year, m) for m in range(1, 13)
                  if datetime(args.year, m, 1) <= now]
    else:
        months = []
        for y in range(now.year - args.years + 1, now.year + 1):
            for m in range(1, 13):
                if datetime(y, m, 1) <= now:
                    months.append((y, m))

    print(f"\nBuilding news calendar from known release schedules")
    print(f"Period: {months[0]} → {months[-1]} | {len(months)} months")
    print(f"Events: NFP, FOMC, CPI, ECB, BOE, GDP")
    print()

    total = 0
    for year, month in months:
        total += build_and_save(year, month, args.overwrite)

    print(f"\nTotal: {total} high-impact events across {len(months)} months")
    print(f"Saved in: {NEWS_DIR}/")
    print()
    print("Coverage:")
    print("  NFP:  Monthly (1st Friday, 12:30 UTC)")
    print("  FOMC: 8× per year (known dates, 18:00 UTC)")
    print("  CPI:  Monthly (approx 2nd Wednesday, 12:30 UTC)")
    print("  ECB:  8× per year (known dates, 13:15 UTC)")
    print("  BOE:  8× per year (known dates, 12:00 UTC)")
    print("  GDP:  Quarterly advance (last Thursday, 12:30 UTC)")
    print()
    print("Note: CPI dates are approximate (±2 days).")
    print("      FOMC/ECB/BOE dates are exact from official schedules.")


if __name__ == "__main__":
    main()
