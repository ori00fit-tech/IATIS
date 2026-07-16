#!/usr/bin/env python3
"""
scripts/cache_calendar.py
---------------------------
Download economic calendar and cache it locally.
Run this script once per day (via cron or external trigger).

The VPS may be blocked by some calendar providers.
This script can be run from any machine that has access,
then the cached file is read by news_calendar.py.

Cron example (run at 00:05 UTC daily):
  5 0 * * * /root/IATIS/venv/bin/python3 /root/IATIS/scripts/cache_calendar.py

Or add to .env: CALENDAR_CACHE_ENABLED=true
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CACHE_PATH = Path("storage/calendar_cache.json")


def fetch_from_forex_factory() -> list[dict]:
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            raw = r.json()
            events = [{
                "name": e.get("title",""), "currency": e.get("country","").upper()[:3],
                "date": e.get("date",""), "impact": e.get("impact","Low"),
                "actual": e.get("actual",""), "forecast": e.get("forecast",""),
                "previous": e.get("previous",""), "source": "forex_factory",
            } for e in raw]
            print(f"  Forex Factory: {len(events)} events")
            return events
    except Exception as e:
        print(f"  Forex Factory failed: {e}")
    return []


def main():
    print(f"Calendar cache update: {datetime.now(timezone.utc).isoformat()}")

    events = fetch_from_forex_factory()

    if events:
        cache_data = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "events": events,
            "count": len(events),
        }
        CACHE_PATH.write_text(json.dumps(cache_data, indent=2))
        print(f"Cached {len(events)} events → {CACHE_PATH}")
    else:
        print("WARNING: No events fetched — calendar cache not updated")


if __name__ == "__main__":
    main()
