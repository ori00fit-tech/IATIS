"""
regimes/session_context.py
-----------------------------
Session context detection — which trading session is active and what
that means for expected volatility and directional bias.

Phase 3: real session detection based on UTC timestamp.

Sessions:
    Asia:    21:00 - 08:00 UTC  (Tokyo/Sydney)
    London:  07:00 - 16:00 UTC
    NewYork: 12:00 - 21:00 UTC
    Overlap: 12:00 - 16:00 UTC  (London + NY open simultaneously — highest volume)

This is NOT a trading signal. It provides context to engines that
behave differently across sessions (e.g. ICT killzones, PA breakouts
that only trigger at session opens).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone

import pandas as pd


@dataclass
class SessionContext:
    active_sessions: list[str]    # e.g. ["London", "NewYork", "Overlap"]
    primary_session: str          # dominant session
    is_overlap: bool              # London+NY overlap — highest volume
    is_session_open: bool         # within first 2 hours of any major session
    session_hour: int             # UTC hour of latest bar
    volatility_expectation: str   # LOW | MEDIUM | HIGH


# Session windows in UTC hours (start inclusive, end exclusive)
_SESSIONS = {
    "Asia":    (21, 8),    # wraps midnight
    "London":  (7, 16),
    "NewYork": (12, 21),
}

_OVERLAP_START = 12
_OVERLAP_END = 16

# Session opens (UTC hour) — first 2 hours have highest directional potential
_SESSION_OPENS = {
    "Asia":    21,
    "London":  7,
    "NewYork": 12,
}


def _hour_in_session(hour: int, start: int, end: int) -> bool:
    if start < end:
        return start <= hour < end
    # wraps midnight (e.g. Asia 21-8)
    return hour >= start or hour < end


def detect_session(dt: pd.Timestamp | None = None) -> SessionContext:
    """Detect active session(s) for a given UTC timestamp.

    Args:
        dt: UTC timestamp. If None, uses current UTC time.

    Returns:
        SessionContext with active sessions and volatility expectation.
    """
    if dt is None:
        dt = pd.Timestamp.now(tz="UTC")
    elif dt.tzinfo is None:
        dt = dt.tz_localize("UTC")

    hour = dt.hour

    active = [
        name for name, (start, end) in _SESSIONS.items()
        if _hour_in_session(hour, start, end)
    ]

    is_overlap = _hour_in_session(hour, _OVERLAP_START, _OVERLAP_END)
    if is_overlap and "Overlap" not in active:
        active.append("Overlap")

    # session open = within 2 hours of any session's start
    is_open = any(
        hour == open_h or hour == (open_h + 1) % 24
        for open_h in _SESSION_OPENS.values()
    )

    # primary session — Overlap > NY > London > Asia
    if is_overlap:
        primary = "Overlap"
    elif "NewYork" in active:
        primary = "NewYork"
    elif "London" in active:
        primary = "London"
    elif "Asia" in active:
        primary = "Asia"
    else:
        primary = "Off-Hours"

    # volatility expectation
    if is_overlap:
        vol = "HIGH"
    elif "London" in active or "NewYork" in active:
        vol = "MEDIUM"
    elif "Asia" in active:
        vol = "LOW"
    else:
        vol = "LOW"

    return SessionContext(
        active_sessions=active,
        primary_session=primary,
        is_overlap=is_overlap,
        is_session_open=is_open,
        session_hour=hour,
        volatility_expectation=vol,
    )


def detect_session_from_df(df: pd.DataFrame) -> SessionContext:
    """Detect session based on the latest bar's timestamp."""
    if df.empty:
        return detect_session()
    latest = df.index[-1]
    if not hasattr(latest, "hour"):
        return detect_session()
    return detect_session(latest)
