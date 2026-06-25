"""
fundamentals/news_risk.py
---------------------------
News Risk Score: quantifies upcoming news risk for a symbol.

Risk Score (0-100):
  0-25  = LOW  — safe to trade
  26-50 = MEDIUM — reduce position size
  51-75 = HIGH — wait for news
  76-100 = EXTREME — blackout period (NO_TRADE regardless of technicals)

Blackout periods (automatic NO_TRADE):
  - 30 minutes BEFORE high-impact event
  - 15 minutes AFTER high-impact event (volatility settling)

Integration with IATIS:
  - Called after confluence check, before final decision
  - High NEWS_RISK_SCORE can VETO an EXECUTE signal
  - Added to Telegram Intelligence Report
  - Stored in decision_db for historical analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from utils.logger import get_logger

logger = get_logger(__name__)

# Risk score by event impact level
IMPACT_SCORES = {
    "High":   90,
    "Medium": 50,
    "Low":    15,
    "high":   90,
    "medium": 50,
    "low":    15,
    "3":      90,   # some APIs use 1/2/3
    "2":      50,
    "1":      15,
}

# Well-known event name → override score
KNOWN_HIGH_IMPACT = {
    "Non-Farm Payrolls": 100,
    "NFP": 100,
    "Federal Funds Rate": 100,
    "FOMC": 100,
    "CPI": 90,
    "Core CPI": 90,
    "GDP": 85,
    "ECB Interest Rate Decision": 100,
    "BOE Interest Rate Decision": 100,
    "BOJ Interest Rate Decision": 100,
    "RBA Rate Decision": 95,
    "RBNZ Rate Decision": 95,
    "BOC Rate Decision": 95,
}

# Blackout windows (minutes)
BLACKOUT_BEFORE_HIGH = 30    # 30 min before high-impact
BLACKOUT_AFTER_HIGH = 15     # 15 min after high-impact
BLACKOUT_BEFORE_MEDIUM = 10  # 10 min before medium-impact


@dataclass
class NewsRiskResult:
    symbol: str
    news_risk_score: float           # 0-100
    risk_level: str                  # LOW / MEDIUM / HIGH / EXTREME
    blackout_active: bool            # True = veto trade
    blackout_reason: str             # human-readable reason
    upcoming_events: list[dict]      # events within next 60 min
    next_high_impact: dict | None    # nearest high-impact event
    assessed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def should_block(self) -> bool:
        """True if news risk should veto an EXECUTE signal."""
        return self.blackout_active or self.news_risk_score >= 76

    def to_dict(self) -> dict:
        return {
            "news_risk_score": round(self.news_risk_score, 1),
            "risk_level": self.risk_level,
            "blackout_active": self.blackout_active,
            "blackout_reason": self.blackout_reason,
            "upcoming_events_count": len(self.upcoming_events),
            "next_high_impact": self.next_high_impact,
            "assessed_at": self.assessed_at,
        }


def _event_score(event: dict) -> float:
    """Calculate risk score for a single event."""
    name = event.get("name", event.get("title", ""))
    impact = str(event.get("impact", event.get("strength", "Low")))

    # Check known events first
    for known, score in KNOWN_HIGH_IMPACT.items():
        if known.lower() in name.lower():
            return float(score)

    # Fall back to impact level
    return float(IMPACT_SCORES.get(impact, IMPACT_SCORES.get(impact.capitalize(), 15)))


def _is_blackout(event: dict, minutes_until: float) -> tuple[bool, str]:
    """Check if we're in a blackout window for this event."""
    score = _event_score(event)
    name = event.get("name", event.get("title", "Unknown"))

    if score >= 80:  # High-impact
        if -BLACKOUT_AFTER_HIGH <= minutes_until <= BLACKOUT_BEFORE_HIGH:
            if minutes_until < 0:
                return True, f"Post-news blackout: {name} released {abs(int(minutes_until))} min ago"
            return True, f"Pre-news blackout: {name} in {int(minutes_until)} min"
    elif score >= 40:  # Medium-impact
        if 0 <= minutes_until <= BLACKOUT_BEFORE_MEDIUM:
            return True, f"Pre-news caution: {name} in {int(minutes_until)} min"

    return False, ""


def assess_news_risk(
    symbol: str,
    calendar_events: list[dict] | None = None,
    look_ahead_minutes: int = 120,
) -> NewsRiskResult:
    """
    Calculate news risk score for a symbol at the current moment.

    Args:
        symbol: e.g. "EURUSD"
        calendar_events: pre-fetched events (None = fetch now, slower)
        look_ahead_minutes: how far ahead to check (default: 120 min)

    Returns:
        NewsRiskResult with score, level, blackout status
    """
    # Lazy import to avoid circular dependency
    if calendar_events is None:
        try:
            from fundamentals.news_calendar import get_calendar_today, get_upcoming_events
            events_today = get_calendar_today()
            upcoming = get_upcoming_events(symbol, within_minutes=look_ahead_minutes,
                                           events=events_today)
        except Exception as exc:
            logger.warning(f"Could not fetch calendar for {symbol}: {exc}")
            upcoming = []
    else:
        from fundamentals.news_calendar import get_upcoming_events
        upcoming = get_upcoming_events(symbol, within_minutes=look_ahead_minutes,
                                       events=calendar_events)

    if not upcoming:
        return NewsRiskResult(
            symbol=symbol,
            news_risk_score=0.0,
            risk_level="LOW",
            blackout_active=False,
            blackout_reason="No upcoming events",
            upcoming_events=[],
            next_high_impact=None,
        )

    # Calculate composite risk score
    # Method: highest event score × time_weight
    # Events closer in time have higher weight
    max_score = 0.0
    blackout_active = False
    blackout_reason = ""
    next_high_impact = None

    for event in upcoming:
        minutes_until = float(event.get("minutes_until", 60))
        event_score = _event_score(event)

        # Time decay: closer events are more risky
        if minutes_until <= 30:
            time_weight = 1.0
        elif minutes_until <= 60:
            time_weight = 0.8
        elif minutes_until <= 120:
            time_weight = 0.5
        else:
            time_weight = 0.3

        weighted_score = event_score * time_weight
        if weighted_score > max_score:
            max_score = weighted_score

        # Check blackout
        if not blackout_active:
            is_bo, reason = _is_blackout(event, minutes_until)
            if is_bo:
                blackout_active = True
                blackout_reason = reason

        # Track nearest high-impact
        if event_score >= 80 and next_high_impact is None:
            next_high_impact = {
                "name": event.get("name", event.get("title", "?")),
                "currency": event.get("currency", "?"),
                "minutes_until": int(minutes_until),
                "score": event_score,
            }

    final_score = round(min(max_score, 100.0), 1)

    if final_score >= 76 or blackout_active:
        risk_level = "EXTREME"
    elif final_score >= 51:
        risk_level = "HIGH"
    elif final_score >= 26:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    logger.info(
        f"News risk {symbol}: score={final_score} level={risk_level} "
        f"blackout={blackout_active} events={len(upcoming)}"
    )

    return NewsRiskResult(
        symbol=symbol,
        news_risk_score=final_score,
        risk_level=risk_level,
        blackout_active=blackout_active,
        blackout_reason=blackout_reason if blackout_active else "",
        upcoming_events=upcoming,
        next_high_impact=next_high_impact,
    )


def risk_level_icon(level: str) -> str:
    return {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "EXTREME": "🚨"}.get(level, "⚪")
