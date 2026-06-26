"""
confluence/mtf_confirmation.py
--------------------------------
A2: Multi-Timeframe Confirmation

Problem solved:
  H1 BEARISH score=75 but D1 is BULLISH
  = counter-trend trade, high risk of failure

Rule:
  D1 trend must agree with H1 signal direction.
  If D1 contradicts H1 → reduce confluence score by penalty.
  If D1 confirms H1 → add bonus to confluence score.

D1 trend detection (simple, reliable):
  EMA20 > EMA50 on D1 = BULLISH
  EMA20 < EMA50 on D1 = BEARISH
  ADX < 20 on D1 = no clear trend (no penalty)
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Score adjustments
MTF_CONFIRM_BONUS = 8.0    # D1 agrees with H1 → +8 points
MTF_COUNTER_PENALTY = 15.0  # D1 contradicts H1 → -15 points
MTF_MIN_ADX = 20.0          # D1 trend must be strong enough to matter


@dataclass
class MTFResult:
    d1_bias: str          # BULLISH / BEARISH / NEUTRAL
    d1_adx: float
    d1_ema20: float
    d1_ema50: float
    score_adjustment: float
    reason: str
    confirming: bool      # True if D1 agrees with signal


def check_mtf_confirmation(
    h1_bias: str,          # winning bias from confluence vote
    mtf_data: dict[str, pd.DataFrame],
) -> MTFResult:
    """Compare H1 signal direction against D1 trend.

    Args:
        h1_bias: "BULLISH" or "BEARISH" (from confluence vote)
        mtf_data: dict with "D1" key containing daily OHLCV

    Returns:
        MTFResult with score_adjustment to apply
    """
    df_d1 = mtf_data.get("D1")

    if df_d1 is None or len(df_d1) < 50:
        return MTFResult(
            d1_bias="NEUTRAL",
            d1_adx=0.0,
            d1_ema20=0.0,
            d1_ema50=0.0,
            score_adjustment=0.0,
            reason="D1 data unavailable — no MTF adjustment",
            confirming=False,
        )

    close = df_d1["close"]
    ema20 = float(close.ewm(span=20).mean().iloc[-1])
    ema50 = float(close.ewm(span=50).mean().iloc[-1])

    # ADX calculation
    high = df_d1["high"]
    low = df_d1["low"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()

    plus_dm = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr14.replace(0, float("nan")))
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr14.replace(0, float("nan")))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    adx = float(dx.rolling(14).mean().iloc[-1]) if not dx.rolling(14).mean().empty else 0.0

    # D1 bias
    if ema20 > ema50 * 1.001:
        d1_bias = "BULLISH"
    elif ema20 < ema50 * 0.999:
        d1_bias = "BEARISH"
    else:
        d1_bias = "NEUTRAL"

    # No strong D1 trend → no adjustment
    if adx < MTF_MIN_ADX or d1_bias == "NEUTRAL":
        return MTFResult(
            d1_bias=d1_bias,
            d1_adx=round(adx, 1),
            d1_ema20=round(ema20, 5),
            d1_ema50=round(ema50, 5),
            score_adjustment=0.0,
            reason=f"D1 trend weak (ADX={adx:.1f} < {MTF_MIN_ADX}) — no MTF adjustment",
            confirming=False,
        )

    # Compare with H1 bias — NEUTRAL H1 = no conflict
    if h1_bias == "NEUTRAL":
        return MTFResult(
            d1_bias=d1_bias,
            d1_adx=round(adx, 1),
            d1_ema20=round(ema20, 5),
            d1_ema50=round(ema50, 5),
            score_adjustment=0.0,
            reason=f"H1 signal NEUTRAL — no MTF adjustment applied",
            confirming=False,
        )

    confirming = (h1_bias == d1_bias)

    if confirming:
        adj = MTF_CONFIRM_BONUS
        reason = (
            f"D1 {d1_bias} confirms H1 {h1_bias} "
            f"(EMA20={ema20:.5f} vs EMA50={ema50:.5f}, ADX={adx:.1f}) "
            f"→ +{MTF_CONFIRM_BONUS} bonus"
        )
    else:
        adj = -MTF_COUNTER_PENALTY
        reason = (
            f"D1 {d1_bias} contradicts H1 {h1_bias} — counter-trend risk "
            f"(EMA20={ema20:.5f} vs EMA50={ema50:.5f}, ADX={adx:.1f}) "
            f"→ -{MTF_COUNTER_PENALTY} penalty"
        )

    logger.info(f"MTF check: {reason}")

    return MTFResult(
        d1_bias=d1_bias,
        d1_adx=round(adx, 1),
        d1_ema20=round(ema20, 5),
        d1_ema50=round(ema50, 5),
        score_adjustment=adj,
        reason=reason,
        confirming=confirming,
    )
