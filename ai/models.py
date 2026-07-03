"""
ai/models.py
-------------
Typed result shapes returned by AIAnalyzer, independent of which
provider produced them. Every AI call in this system returns one of
these — plain dicts, so they serialize straight into API responses and
Telegram/dashboard rendering, but constructed through a single place so
every provider is forced to fill in the same contract.

These are explanation/reporting outputs only. Nothing here carries a
BUY/SELL decision — the confluence + risk engines remain the sole
authority for final_verdict (see main.py). AIAnalyzer only explains or
contextualizes a decision that was already made.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TradeExplanation:
    """Natural-language explanation of an already-decided trade signal."""

    summary: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    risk_level: str = "UNKNOWN"          # LOW | MEDIUM | HIGH | UNKNOWN
    confidence: float = 0.0              # 0-100, the AI's own confidence in its explanation
    recommendation: str = ""
    market_sentiment: str = "NEUTRAL"    # Bullish | Bearish | Neutral
    news_risk: str = "UNKNOWN"
    explanation: str = ""
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider: str = ""
    status: str = "ok"                   # ok | error | disabled | cached
    error: str = ""
    generated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "pros": self.pros,
            "cons": self.cons,
            "risk_level": self.risk_level,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "market_sentiment": self.market_sentiment,
            "news_risk": self.news_risk,
            "explanation": self.explanation,
            "sources": self.sources,
            "warnings": self.warnings,
            "provider": self.provider,
            "status": self.status,
            "error": self.error,
            "generated_at": self.generated_at,
        }


@dataclass
class NewsAnalysis:
    """AI read on current economic news, for dashboard/report display only
    — the actual trading blackout logic remains fundamentals/news_risk.py."""

    sentiment: str = "NEUTRAL"
    impact: str = "LOW"                  # LOW | MEDIUM | HIGH
    affected_symbols: list[str] = field(default_factory=list)
    duration: str = ""
    confidence: float = 0.0
    summary: str = ""
    sources: list[str] = field(default_factory=list)
    provider: str = ""
    status: str = "ok"
    error: str = ""
    generated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "sentiment": self.sentiment,
            "impact": self.impact,
            "affected_symbols": self.affected_symbols,
            "duration": self.duration,
            "confidence": self.confidence,
            "summary": self.summary,
            "sources": self.sources,
            "provider": self.provider,
            "status": self.status,
            "error": self.error,
            "generated_at": self.generated_at,
        }


@dataclass
class MacroAnalysis:
    """AI read on macro/cross-asset context, for dashboard/report display."""

    summary: str = ""
    risk_on_off: str = "NEUTRAL"         # RISK_ON | RISK_OFF | NEUTRAL
    dxy_bias: str = "NEUTRAL"
    key_drivers: list[str] = field(default_factory=list)
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)
    provider: str = ""
    status: str = "ok"
    error: str = ""
    generated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "risk_on_off": self.risk_on_off,
            "dxy_bias": self.dxy_bias,
            "key_drivers": self.key_drivers,
            "confidence": self.confidence,
            "sources": self.sources,
            "provider": self.provider,
            "status": self.status,
            "error": self.error,
            "generated_at": self.generated_at,
        }
