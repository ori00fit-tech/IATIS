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

Status: RESEARCH (H012)
Note: Real COT is wired: scripts/download_cot.py (weekly cron) writes
      data/cot/{SYMBOL}.json and this engine consumes it as the primary
      signal; the price-based retail proxy remains the explicit fallback
      when no fresh COT cache exists. Engine stays disabled pending H012
      evidence — real data enables EVALUATION, not activation.

MarketAux news sentiment (H021 — PLANNED, research/results/registry.json):
      fundamentals/marketaux_client.py's per-symbol mean sentiment is
      folded in as an additional signal — primary when COT is unavailable,
      confirmation-only when COT is available (same role the retail proxy
      already plays). This is infrastructure for H021's controlled A/B
      test, not a live-behavior change: the Sentiment engine itself stays
      disabled (config/engines.yaml) until H021's pre-registered decision
      rule is applied to that test's result.
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
    # CFTC's contract is listed as "NZ DOLLAR" (not "NEW ZEALAND DOLLAR")
    # as of the 2025 archive — confirmed 2026-07-24 via a real yearly
    # archive probe (H012, research/results/registry.json) after the old
    # name matched zero rows all year.
    "NZDUSD": "NZ DOLLAR",
    "USDCHF": "SWISS FRANC",
    "XAUUSD": "GOLD",
    "XAGUSD": "SILVER",
    # CFTC's NYMEX WTI contract is now listed as "WTI FINANCIAL CRUDE
    # OIL" (not "CRUDE OIL, LIGHT SWEET") — confirmed 2026-07-24. The old
    # name only bare-prefix-matched a DIFFERENT, unintended contract
    # ("CRUDE OIL, LIGHT SWEET-WTI - ICE FUTURES EUROPE", a European
    # venue) — meaning any COT data USOIL ever picked up since this
    # feature's 2026-07-09 wiring was tracking the wrong exchange's
    # positioning, not the intended NYMEX/US benchmark contract.
    "USOIL":  "WTI FINANCIAL CRUDE OIL",
    "BTCUSD": "BITCOIN",
}


def _load_cot_data(symbol: str) -> dict | None:
    """Load most recent COT data for symbol from local cache.

    Cache location: data/cot/{SYMBOL}.json (override dir: IATIS_COT_DIR)
    Updated weekly by scripts/download_cot.py (CFTC legacy futures-only
    report — free, no key; run it from cron/systemd every Saturday).
    """
    cache_path = Path(os.environ.get("IATIS_COT_DIR", "data/cot")) / f"{symbol}.json"
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


def _marketaux_sentiment_signal(symbol: str) -> dict | None:
    """MarketAux news sentiment as a signal input (H021).

    Returns None when unavailable — no MARKETAUX_API_KEY, unmapped symbol,
    or no recent articles — never a synthetic neutral reading, so callers
    can tell "no signal" apart from a genuinely neutral one.
    """
    from fundamentals.marketaux_client import get_news_sentiment
    result = get_news_sentiment(symbol)
    if not result or result["article_count"] == 0:
        return None
    mean = result["mean_sentiment"]
    if mean > 0.1:
        direction = "bullish"
    elif mean < -0.1:
        direction = "bearish"
    else:
        direction = "neutral"
    return {"direction": direction, "mean_sentiment": mean, "article_count": result["article_count"]}


class SentimentEngine(BaseEngine):
    """Market sentiment analysis using COT data and retail positioning proxy.

    Primary: COT Large Speculator net position trend (when available)
    Fallback: Retail sentiment proxy from price position in range

    Research status: RESEARCH (H012)
    Lower weight until COT integration complete.
    """

    name = "Sentiment"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        tf, df = self.decision_frame(mtf_data)
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

        # --- H021 (PLANNED): MarketAux news sentiment ---
        marketaux = _marketaux_sentiment_signal(symbol)

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

            # MarketAux as a secondary primary signal when both COT and the
            # retail proxy are silent (bias still NEUTRAL at this point).
            if bias == Bias.NEUTRAL and marketaux and marketaux["direction"] != "neutral":
                bias = Bias.BULLISH if marketaux["direction"] == "bullish" else Bias.BEARISH
                score = min(30 + abs(marketaux["mean_sentiment"]) * 40, 70)
                reasons.append(
                    f"MarketAux: mean sentiment {marketaux['mean_sentiment']:+.2f} "
                    f"over {marketaux['article_count']} articles"
                )
        else:
            # COT available — use retail as confirmation only
            if retail["contrarian_signal"] == ("bearish" if bias == Bias.BEARISH else "bullish"):
                score = min(score + 10, 80)
                reasons.append(f"Retail proxy confirms: {retail['contrarian_signal']}")

            if marketaux and marketaux["direction"] == ("bullish" if bias == Bias.BULLISH else "bearish"):
                score = min(score + 8, 85)
                reasons.append(
                    f"MarketAux confirms: {marketaux['direction']} ({marketaux['mean_sentiment']:+.2f})"
                )

        if bias == Bias.NEUTRAL:
            reasons.append("No clear sentiment signal")

        return EngineOutput(
            engine_name="Sentiment",
            bias=bias,
            score=round(score, 1),
            reasons=reasons,
            raw={
                "timeframe_used": tf,
                "cot_available": cot_available,
                "retail_pct_from_low": retail["pct_from_low"],
                "retail_contrarian": retail["contrarian_signal"],
                "retail_strength": retail["strength"],
                "marketaux_available": marketaux is not None,
                "marketaux_mean_sentiment": marketaux["mean_sentiment"] if marketaux else None,
                "marketaux_article_count": marketaux["article_count"] if marketaux else None,
            },
        )
