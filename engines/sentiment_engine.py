"""
engines/sentiment_engine.py
-----------------------------
Sentiment Engine: market sentiment from COT data + retail positioning.

Data sources (free):
  1. CFTC COT (Commitments of Traders) — released weekly, Fridays
     URL: https://www.cftc.gov/dea/newcot/
     Shows: Large Speculator net positioning (most reliable)

  2. Retail positioning proxy via price action:
     When price is near recent highs, retail tends to be long (contrarian)
     When price is near recent lows, retail tends to be short (contrarian)

Philosophy:
  Follow Large Speculators (smart money), fade Retail (dumb money)
  COT net longs increasing → bullish sentiment
  COT net longs decreasing rapidly → bearish sentiment

Status: RESEARCH
Note: Full COT integration requires weekly data download.
      Current implementation uses price-based retail sentiment proxy
      until COT data is wired up.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


# COT symbol mapping (CFTC contract names)
COT_SYMBOLS = {
    "EURUSD": "EURO FX",
    "GBPUSD": "BRITISH POUND",
    "USDJPY": "JAPANESE YEN",
    "AUDUSD": "AUSTRALIAN DOLLAR",
    "USDCAD": "CANADIAN DOLLAR",
    "NZDUSD": "NEW ZEALAND DOLLAR",
    "USDCHF": "SWISS FRANC",
    "XAUUSD": "GOLD",
    "XAGUSD": "SILVER",
    "USOIL":  "CRUDE OIL, LIGHT SWEET",
    "BTCUSD": "BITCOIN",
}


def _load_cot_data(symbol: str) -> dict | None:
    """Load most recent COT data for symbol from local cache.

    Cache location: data/cot/{SYMBOL}.json
    Updated weekly by: scripts/download_cot.py (to be built)
    """
    cache_path = Path("data/cot") / f"{symbol}.json"
    if not cache_path.exists():
        return None
    try:
        import json
        data = json.loads(cache_path.read_text())
        # Check freshness — COT is weekly
        ts = data.get("timestamp", 0)
        age_days = (time.time() - ts) / 86400
        if age_days > 14:
            logger.warning(f"COT data for {symbol} is {age_days:.0f} days old")
        return data
    except Exception as exc:
        logger.debug(f"COT cache load failed for {symbol}: {exc}")
        return None


def _retail_sentiment_proxy(
    df: pd.DataFrame,
    lookback: int = 200,
) -> dict:
    """Estimate retail positioning from price position in range.

    Logic: Retail traders chase price.
    - Near highs: retail is long → contrarian = bearish
    - Near lows: retail is short → contrarian = bullish
    - Middle: unclear

    This is a rough proxy — real COT data is more reliable.
    """
    if len(df) < lookback:
        lookback = len(df)

    close = float(df["close"].iloc[-1])
    period_high = float(df["high"].tail(lookback).max())
    period_low = float(df["low"].tail(lookback).min())

    if period_high == period_low:
        return {"retail_bias": "neutral", "pct_from_low": 50, "strength": 0}

    pct_from_low = (close - period_low) / (period_high - period_low) * 100

    if pct_from_low >= 75:
        # Near highs — retail is long → contrarian bearish
        retail_bias = "long"
        contrarian = "bearish"
        strength = int((pct_from_low - 75) / 25 * 40)  # 0-40
    elif pct_from_low <= 25:
        # Near lows — retail is short → contrarian bullish
        retail_bias = "short"
        contrarian = "bullish"
        strength = int((25 - pct_from_low) / 25 * 40)  # 0-40
    else:
        retail_bias = "neutral"
        contrarian = "neutral"
        strength = 0

    return {
        "retail_bias": retail_bias,
        "contrarian_signal": contrarian,
        "pct_from_low": round(pct_from_low, 1),
        "period_high": period_high,
        "period_low": period_low,
        "strength": strength,
    }


class SentimentEngine(BaseEngine):
    name = "Sentiment"
    """Market sentiment analysis using COT data and retail positioning proxy.

    Primary: COT Large Speculator net position trend (when available)
    Fallback: Retail sentiment proxy from price position in range

    Research status: RESEARCH (H012)
    Lower weight until COT integration complete.
    """
    name = "Sentiment"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        df = mtf_data.get("H1", mtf_data.get("D1", next(iter(mtf_data.values()))))
        symbol = self._symbol if hasattr(self, "_symbol") else "UNKNOWN"

        if len(df) < 50:
            return EngineOutput(
                engine_name="Sentiment",
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data for sentiment analysis"],
            )

        reasons = []
        bias = Bias.NEUTRAL
        score = 0.0
        cot_available = False

        # --- Primary: COT Data ---
        cot = _load_cot_data(symbol)
        if cot:
            cot_available = True
            net_pos = cot.get("large_spec_net", 0)
            net_change = cot.get("net_change_4w", 0)

            # Large speculators increasing net longs → bullish
            if net_change > 0 and net_pos > 0:
                bias = Bias.BULLISH
                score = min(40 + abs(net_change) // 1000, 75)
                reasons.append(
                    f"COT: Large specs net long {net_pos:,}, "
                    f"4-week change +{net_change:,} (accumulating)"
                )
            elif net_change < 0 and net_pos < 0:
                bias = Bias.BEARISH
                score = min(40 + abs(net_change) // 1000, 75)
                reasons.append(
                    f"COT: Large specs net short {net_pos:,}, "
                    f"4-week change {net_change:,} (distributing)"
                )
            elif net_change < -5000:
                bias = Bias.BEARISH
                score = 35
                reasons.append(
                    f"COT: Large specs reducing longs {net_change:,} (distribution)"
                )
            else:
                reasons.append(f"COT: Mixed positioning (net={net_pos:,})")

        # --- Fallback / Supplement: Retail Proxy ---
        retail = _retail_sentiment_proxy(df, lookback=min(200, len(df)))

        if not cot_available:
            # Use retail proxy as primary signal
            if retail["contrarian_signal"] == "bearish" and retail["strength"] > 10:
                bias = Bias.BEARISH
                score = retail["strength"]
                reasons.append(
                    f"Retail proxy: price at {retail['pct_from_low']:.0f}% of {len(df)}-bar range "
                    f"— retail likely long, contrarian bearish"
                )
            elif retail["contrarian_signal"] == "bullish" and retail["strength"] > 10:
                bias = Bias.BULLISH
                score = retail["strength"]
                reasons.append(
                    f"Retail proxy: price at {retail['pct_from_low']:.0f}% of {len(df)}-bar range "
                    f"— retail likely short, contrarian bullish"
                )
            else:
                reasons.append(
                    f"Retail proxy: price at {retail['pct_from_low']:.0f}% of range — neutral zone"
                )
        else:
            # COT available — use retail as confirmation only
            if retail["contrarian_signal"] == ("bearish" if bias == Bias.BEARISH else "bullish"):
                score = min(score + 10, 80)
                reasons.append(f"Retail proxy confirms: {retail['contrarian_signal']}")

        if bias == Bias.NEUTRAL:
            reasons.append("No clear sentiment signal")

        return EngineOutput(
            engine_name="Sentiment",
            bias=bias,
            score=round(score, 1),
            reasons=reasons,
            raw={
                "timeframe_used": "H1",
                "cot_available": cot_available,
                "retail_pct_from_low": retail["pct_from_low"],
                "retail_contrarian": retail["contrarian_signal"],
                "retail_strength": retail["strength"],
            },
        )
