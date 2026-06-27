"""
engines/divergence_engine.py
------------------------------
Divergence Engine: detects RSI and MACD divergence patterns.

Divergence = price makes new high/low but momentum indicator doesn't.
This is one of the most reliable reversal signals across all asset classes.

Types:
  Regular Bearish: price HH, RSI LH → bearish reversal
  Regular Bullish: price LL, RSI HL → bullish reversal
  Hidden Bearish:  price LH, RSI HH → continuation down (less weight)
  Hidden Bullish:  price HL, RSI LL → continuation up (less weight)

Scoring:
  Regular divergence: 60-80 (stronger, counter-trend)
  Hidden divergence:  35-50 (continuation confirmation)
  Both MACD + RSI agree: +15 bonus
  Killzone timing (London/NY): +10 bonus

Research status: RESEARCH (H010 pending — needs statistical validation)
Compatible with: SMC (BOS confirms divergence), NNFX (trend filter)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast=12, slow=26, signal=9) -> tuple[pd.Series, pd.Series]:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _find_swing_highs(series: pd.Series, window: int = 5) -> pd.Series:
    """Return boolean mask of local highs."""
    return series == series.rolling(window * 2 + 1, center=True).max()


def _find_swing_lows(series: pd.Series, window: int = 5) -> pd.Series:
    """Return boolean mask of local lows."""
    return series == series.rolling(window * 2 + 1, center=True).min()


def _detect_divergence(
    price: pd.Series,
    indicator: pd.Series,
    lookback: int = 50,
    swing_window: int = 5,
) -> dict:
    """Detect divergence between price and indicator in last N bars."""
    if len(price) < lookback + swing_window * 2:
        return {"type": "none", "strength": 0}

    p = price.iloc[-lookback:]
    ind = indicator.iloc[-lookback:]

    # Find recent swing highs and lows
    sh_mask = _find_swing_highs(p, swing_window)
    sl_mask = _find_swing_lows(p, swing_window)

    sh_idx = p[sh_mask].index.tolist()
    sl_idx = p[sl_mask].index.tolist()

    result = {"type": "none", "strength": 0, "details": ""}

    # Regular Bearish: price HH, indicator LH
    if len(sh_idx) >= 2:
        last_sh = sh_idx[-1]
        prev_sh = sh_idx[-2]
        price_hh = p[last_sh] > p[prev_sh]
        ind_lh = ind[last_sh] < ind[prev_sh]
        if price_hh and ind_lh:
            strength = min(80, 55 + int(abs(p[last_sh] - p[prev_sh]) / p[prev_sh] * 1000))
            result = {
                "type": "regular_bearish",
                "strength": strength,
                "details": f"Price HH ({p[last_sh]:.5f}>{p[prev_sh]:.5f}), Indicator LH",
            }

    # Regular Bullish: price LL, indicator HL
    if len(sl_idx) >= 2:
        last_sl = sl_idx[-1]
        prev_sl = sl_idx[-2]
        price_ll = p[last_sl] < p[prev_sl]
        ind_hl = ind[last_sl] > ind[prev_sl]
        if price_ll and ind_hl:
            strength = min(80, 55 + int(abs(p[prev_sl] - p[last_sl]) / p[prev_sl] * 1000))
            candidate = {
                "type": "regular_bullish",
                "strength": strength,
                "details": f"Price LL ({p[last_sl]:.5f}<{p[prev_sl]:.5f}), Indicator HL",
            }
            if candidate["strength"] > result["strength"]:
                result = candidate

    # Hidden Bearish: price LH, indicator HH (continuation)
    if result["type"] == "none" and len(sh_idx) >= 2:
        last_sh = sh_idx[-1]
        prev_sh = sh_idx[-2]
        price_lh = p[last_sh] < p[prev_sh]
        ind_hh = ind[last_sh] > ind[prev_sh]
        if price_lh and ind_hh:
            result = {
                "type": "hidden_bearish",
                "strength": 40,
                "details": "Price LH, Indicator HH — bearish continuation",
            }

    # Hidden Bullish: price HL, indicator LL (continuation)
    if result["type"] == "none" and len(sl_idx) >= 2:
        last_sl = sl_idx[-1]
        prev_sl = sl_idx[-2]
        price_hl = p[last_sl] > p[prev_sl]
        ind_ll = ind[last_sl] < ind[prev_sl]
        if price_hl and ind_ll:
            result = {
                "type": "hidden_bullish",
                "strength": 40,
                "details": "Price HL, Indicator LL — bullish continuation",
            }

    return result


class DivergenceEngine(BaseEngine):
    name = "Divergence"
    """Detects RSI and MACD divergence on H1 timeframe.

    Research status: RESEARCH
    Hypothesis H010: divergence on H1 + trend confirmation
    improves win rate over random entry (to be tested).
    """

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        df = mtf_data.get("H1", mtf_data.get("M15", next(iter(mtf_data.values()))))

        if len(df) < 60:
            return EngineOutput(
                engine_name="Divergence",
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data (need 60+ bars)"],
            )

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        rsi = _rsi(close)
        macd_line, signal_line = _macd(close)
        macd_hist = macd_line - signal_line

        # Detect divergence on RSI
        rsi_div = _detect_divergence(close, rsi, lookback=60)
        # Detect divergence on MACD histogram
        macd_div = _detect_divergence(close, macd_hist, lookback=60)

        reasons = []
        score = 0.0
        bias = Bias.NEUTRAL

        # RSI current level context
        current_rsi = float(rsi.iloc[-1])
        rsi_context = ""
        if current_rsi > 70:
            rsi_context = f"RSI overbought ({current_rsi:.1f})"
        elif current_rsi < 30:
            rsi_context = f"RSI oversold ({current_rsi:.1f})"
        else:
            rsi_context = f"RSI neutral ({current_rsi:.1f})"

        # Primary: RSI divergence
        if rsi_div["type"] != "none":
            score = rsi_div["strength"]
            if "bearish" in rsi_div["type"]:
                bias = Bias.BEARISH
                reasons.append(f"RSI {rsi_div['type'].replace('_',' ')}: {rsi_div['details']}")
            else:
                bias = Bias.BULLISH
                reasons.append(f"RSI {rsi_div['type'].replace('_',' ')}: {rsi_div['details']}")

            # MACD confirmation bonus
            if macd_div["type"] != "none" and macd_div["type"] == rsi_div["type"]:
                score = min(score + 15, 90)
                reasons.append(f"MACD confirms: {macd_div['details']}")

        # Secondary: MACD only (if no RSI divergence)
        elif macd_div["type"] != "none":
            score = macd_div["strength"] * 0.75  # lower confidence without RSI
            if "bearish" in macd_div["type"]:
                bias = Bias.BEARISH
            else:
                bias = Bias.BULLISH
            reasons.append(f"MACD {macd_div['type'].replace('_',' ')} only: {macd_div['details']}")

        if bias == Bias.NEUTRAL:
            reasons.append(f"No divergence detected. {rsi_context}")

        reasons.append(rsi_context)

        return EngineOutput(
            engine_name="Divergence",
            bias=bias,
            score=round(score, 1),
            reasons=reasons,
            raw={
                "timeframe_used": "H1",
                "rsi": round(current_rsi, 1),
                "rsi_divergence": rsi_div["type"],
                "macd_divergence": macd_div["type"],
                "rsi_div_strength": rsi_div["strength"],
                "macd_div_strength": macd_div["strength"],
            },
        )
