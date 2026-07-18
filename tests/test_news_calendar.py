"""tests/test_news_calendar.py — calendar cache-first behavior."""
from __future__ import annotations
from datetime import datetime, timezone

import fundamentals.news_calendar as nc


def test_fresh_cache_empty_today_does_not_hit_live_feed(monkeypatch):
    """A fresh cache with 0 events for today (quiet day / weekend) is
    authoritative — get_calendar_today() must return [] WITHOUT falling
    through to the rate-limited live Forex Factory feed."""
    # Fresh cache holds this-week events, but none dated today.
    monkeypatch.setattr(nc, "_read_cache", lambda: [{"date": "1999-01-01T12:00:00", "impact": "High"}])

    called = {"live": 0}
    monkeypatch.setattr(nc, "_forex_factory_fallback", lambda: called.__setitem__("live", called["live"] + 1) or [])

    out = nc.get_calendar_today()
    assert out == []
    assert called["live"] == 0, "live feed must not be queried when the cache is fresh"


def test_fresh_cache_with_today_events_returns_them(monkeypatch):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ev = {"date": f"{today}T12:30:00", "impact": "High", "title": "CPI"}
    monkeypatch.setattr(nc, "_read_cache", lambda: [ev])
    monkeypatch.setattr(nc, "_forex_factory_fallback", lambda: (_ for _ in ()).throw(AssertionError("live must not be called")))
    assert nc.get_calendar_today() == [ev]


def test_empty_cache_falls_through_to_live(monkeypatch):
    """When the cache is missing/stale, the live feed IS the fallback."""
    monkeypatch.setattr(nc, "_read_cache", lambda: [])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    live_ev = {"date": f"{today}T08:00:00", "impact": "High"}
    monkeypatch.setattr(nc, "_forex_factory_fallback", lambda: [live_ev])
    assert nc.get_calendar_today() == [live_ev]
