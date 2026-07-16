"""
core/market_quality.py
------------------------
Market Quality Score (MQS): 0-100

Evaluates whether market conditions are suitable for trading
BEFORE running any engine. Low MQS → NO_TRADE immediately.

Factors:
  - Session activity (London/NY = high, Asian overlap = low)
  - ATR percentile (too low = dead market, too high = chaotic)
  - Volatility regime (normal = good, extreme = caution)
  - Day of week (Monday open / Friday close = caution)
  - Spread proxy (via ATR/price ratio)

Thresholds:
  MQS >= 60: GOOD  — run full analysis
  MQS 40-60: FAIR  — run analysis, reduce position size
  MQS < 40:  POOR  — NO_TRADE, save API credits

This alone eliminates:
  - Dead Asian session trades
  - Monday 00:00 UTC liquidity gaps
  - Friday 20:00+ UTC thin markets
  - Extreme volatility events (flash crashes)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Session hours UTC
SESSIONS = {
    "Sydney":  (21, 6),   # 21:00 - 06:00 UTC
    "Tokyo":   (23, 8),   # 23:00 - 08:00 UTC
    "London":  (7,  16),  # 07:00 - 16:00 UTC
    "NewYork": (12, 21),  # 12:00 - 21:00 UTC
}

MQS_THRESHOLD_GOOD = 60
MQS_THRESHOLD_FAIR = 40


@dataclass
class MarketQualityResult:
    score: float          # 0-100
    grade: str            # GOOD / FAIR / POOR
    should_trade: bool
    session: str
    active_sessions: list[str]
    atr_percentile: float
    volatility_grade: str
    day_penalty: float
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "mqs_score": round(self.score, 1),
            "grade": self.grade,
            "should_trade": self.should_trade,
            "session": self.session,
            "active_sessions": self.active_sessions,
            "atr_percentile": round(self.atr_percentile, 2),
            "volatility_grade": self.volatility_grade,
            "day_penalty": self.day_penalty,
            "reasons": self.reasons,
        }


def _active_sessions(hour_utc: int) -> list[str]:
    active = []
    for name, (start, end) in SESSIONS.items():
        if start > end:  # crosses midnight
            if hour_utc >= start or hour_utc < end:
                active.append(name)
        else:
            if start <= hour_utc < end:
                active.append(name)
    return active


def _session_score(active: list[str]) -> tuple[float, str]:
    """Score based on active trading sessions."""
    if "London" in active and "NewYork" in active:
        return 35.0, "London+NY overlap (peak liquidity)"
    elif "London" in active:
        return 30.0, "London session (high liquidity)"
    elif "NewYork" in active:
        return 25.0, "New York session (good liquidity)"
    elif "Tokyo" in active:
        return 15.0, "Tokyo session (moderate liquidity)"
    elif "Sydney" in active:
        return 10.0, "Sydney session (low liquidity)"
    else:
        return 5.0, "No major session (very low liquidity)"


def _atr_score(df: pd.DataFrame) -> tuple[float, float, str]:
    """Score based on ATR percentile (healthy = middle range)."""
    if len(df) < 20:
        return 15.0, 0.5, "Insufficient data"

    from utils.indicators import atr as _atr

    atr_full = _atr(df, 14)
    atr14 = float(atr_full.iloc[-1])
    atr_series = atr_full.dropna()

    if len(atr_series) < 20:
        return 15.0, 0.5, "ATR: insufficient history"

    percentile = float((atr_series < atr14).mean())

    if 0.25 <= percentile <= 0.75:
        score, grade = 30.0, "Normal volatility (ideal)"
    elif 0.15 <= percentile < 0.25:
        score, grade = 20.0, "Below-normal volatility (quiet)"
    elif 0.75 < percentile <= 0.85:
        score, grade = 22.0, "Above-normal volatility (active)"
    elif percentile < 0.15:
        score, grade = 10.0, "Very low volatility (dead market)"
    else:  # > 0.85
        score, grade = 12.0, "Very high volatility (chaotic)"

    return score, percentile, grade


def _day_penalty(weekday: int, hour_utc: int) -> tuple[float, str]:
    """Penalty for low-quality trading times."""
    # Monday 00:00-07:00: gaps from weekend
    if weekday == 0 and hour_utc < 7:
        return 15.0, "Monday pre-London (weekend gaps)"
    # Friday 20:00+: thin market, weekend approaching
    if weekday == 4 and hour_utc >= 20:
        return 15.0, "Friday late session (thin market)"
    # Sunday: markets reopening
    if weekday == 6:
        return 20.0, "Sunday (market reopening, wide spreads)"
    return 0.0, ""


def _trend_clarity_score(df: pd.DataFrame) -> float:
    """Bonus if trend is clear (not choppy)."""
    if len(df) < 50:
        return 5.0
    close = df["close"]
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()
    spread_pct = abs(float(ema20.iloc[-1] - ema50.iloc[-1])) / float(ema50.iloc[-1]) * 100
    if spread_pct > 0.3:
        return 10.0  # Clear trend
    elif spread_pct > 0.1:
        return 7.0   # Moderate trend
    return 4.0       # Choppy


def assess_market_quality(
    df: pd.DataFrame,
    symbol: str = "",
    now: datetime | None = None,
    timeframe: str = "H1",
    threshold_good: float = MQS_THRESHOLD_GOOD,
    threshold_fair: float = MQS_THRESHOLD_FAIR,
) -> MarketQualityResult:
    """
    Calculate Market Quality Score for current market conditions.

    Args:
        df: H1 OHLCV DataFrame
        symbol: for logging
        now: current UTC time (default: datetime.now(UTC))
        threshold_good: score at/above which grade is GOOD (config: market_quality.threshold_good)
        threshold_fair: score at/above which grade is FAIR, else POOR/no-trade
            (config: market_quality.threshold_fair)

    Returns:
        MarketQualityResult with score and trading recommendation
    """
    if now is None:
        now = datetime.now(timezone.utc)

    hour_utc = now.hour
    weekday = now.weekday()  # 0=Mon, 6=Sun
    reasons = []

    # Detect asset class for session scoring
    is_crypto = any(c in symbol.upper() for c in ["BTC","ETH","XRP","LTC","SOL"])
    is_short_tf = timeframe in ("M1","M5","M15","5m","15m","30m") if timeframe else False
    # Daily (or slower) decision timeframe: a D1 position lives through
    # every session, so intraday-session scoring is meaningless — score it
    # neutrally instead of punishing whatever hour the scheduler happens
    # to run at.
    is_daily_tf = timeframe in ("D1", "1day", "W1", "1week") if timeframe else False

    # 1. Session score (35 pts max)
    # Crypto trades 24/7 — session matters less
    # Short TF (M15) — session penalty reduced
    active = _active_sessions(hour_utc)
    if is_daily_tf:
        session_pts, session_reason = 25.0, "Daily decision timeframe (session neutral)"
    elif is_crypto:
        session_pts, session_reason = 25.0, "Crypto 24/7 (session neutral)"
    else:
        session_pts, session_reason = _session_score(active)
        if is_short_tf and session_pts < 15:
            session_pts = max(session_pts, 15.0)  # M15 less sensitive to session
    main_session = active[0] if active else "None"
    reasons.append(f"Session: {session_reason}")

    # 2. ATR / Volatility score (30 pts max)
    atr_pts, atr_pct, atr_reason = _atr_score(df)
    reasons.append(f"Volatility: {atr_reason}")

    # 3. Trend clarity bonus (10 pts max)
    trend_pts = _trend_clarity_score(df)

    # 4. Base score (always available = 15 pts)
    base_pts = 15.0

    # 5. Day/time penalty
    penalty, penalty_reason = _day_penalty(weekday, hour_utc)
    if penalty_reason:
        reasons.append(f"Caution: {penalty_reason}")

    # Total
    raw_score = session_pts + atr_pts + trend_pts + base_pts - penalty
    score = max(0.0, min(100.0, raw_score))

    if score >= threshold_good:
        grade = "GOOD"
        should_trade = True
    elif score >= threshold_fair:
        grade = "FAIR"
        should_trade = True  # trade but reduce size
    else:
        grade = "POOR"
        should_trade = False
        reasons.append(f"MQS={score:.0f} < {threshold_fair} → NO_TRADE")

    logger.info(
        f"MQS {symbol}: {score:.0f}/100 ({grade}) "
        f"session={main_session} atr_pct={atr_pct:.2f}"
    )

    return MarketQualityResult(
        score=round(score, 1),
        grade=grade,
        should_trade=should_trade,
        session=main_session,
        active_sessions=active,
        atr_percentile=round(atr_pct, 2),
        volatility_grade=atr_reason,
        day_penalty=penalty,
        reasons=reasons,
    )
