"""
engines/nnfx_engine.py
-------------------------
NNFX (No Nonsense Forex) methodology engine — Phase 3.

The NNFX system uses a layered confirmation approach:
1. Baseline indicator (200 EMA): overall trend direction
2. Confirmation indicator (ADX): trend strength
3. Volume/momentum filter: avoid low-momentum entries
4. ATR-based exit sizing: consistent risk per trade

Simplified implementation using indicators available from OHLCV only
(no volume-based indicators since Twelve Data Free plan returns 0
for FX volume). Uses EMA stack for baseline and ADX for strength.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.indicators import atr as _atr_series
from utils.indicators import rsi as _rsi_series
from utils.logger import get_logger

logger = get_logger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — trend strength (not direction).
    Deliberately the "simplified" ADX this module's docstring describes —
    a plain rolling mean, not Wilder's exponential smoothing of DI+/DI-/
    ADX. Not touched here (strategy design, out of scope for this pass)."""
    high, low = df["high"], df["low"]

    dm_plus = (high - high.shift()).clip(lower=0)
    dm_minus = (low.shift() - low).clip(lower=0)
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

    # utils.indicators.atr()'s own rolling(window=period, min_periods=period)
    # mean is formula-identical to the plain tr.rolling(period).mean() this
    # used to compute locally — reused, not duplicated (Confluence Engine
    # Overhaul Phase 1's indicator-unification charter).
    atr_val = _atr_series(df, period)
    di_plus = 100 * dm_plus.rolling(period).mean() / atr_val.replace(0, np.nan)
    di_minus = 100 * dm_minus.rolling(period).mean() / atr_val.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return dx.rolling(period).mean()


def extract_features(df: pd.DataFrame, t: dict) -> dict:
    """Feature Extraction layer (Confluence Engine Overhaul Phase 2) —
    EMA stack + ADX + RSI values decide() needs. Pure function of
    (df, thresholds), no bias/score logic."""
    close = df["close"]
    ema50 = _ema(close, t.get("ema_fast", 50))
    ema100 = _ema(close, t.get("ema_mid", 100))
    ema200 = _ema(close, t.get("ema_slow", 200))
    adx = _adx(df, t.get("adx_period", 14))
    rsi = _rsi_series(close, t.get("rsi_period", 14))

    current = float(close.iloc[-1])
    e200 = float(ema200.iloc[-1])

    # NaN here means the underlying ratio was genuinely undefined at this
    # bar (ADX: DI+ == DI- == 0, no directional movement at all in the
    # window; RSI: zero average loss or zero average gain, RS undefined —
    # true value 100/0, not "neutral") — not "no information." adx_undefined/
    # rsi_undefined make the fallback distinguishable from a genuine
    # near-0/near-50 reading (OBSERVABILITY only; the fallback values
    # themselves are unchanged — correcting them would shift decide()'s
    # threshold branching, a strategy-semantics change out of scope here).
    adx_undefined = bool(pd.isna(adx.iloc[-1]))
    rsi_undefined = bool(pd.isna(rsi.iloc[-1]))

    return {
        "current": current,
        "e50": float(ema50.iloc[-1]),
        "e100": float(ema100.iloc[-1]),
        "e200": e200,
        "adx_val": float(adx.iloc[-1]) if not adx_undefined else 0.0,
        "adx_undefined": adx_undefined,
        "rsi_val": float(rsi.iloc[-1]) if not rsi_undefined else 50.0,
        "rsi_undefined": rsi_undefined,
        "price_vs_ema200_pct": round((current - e200) / e200 * 100, 3),
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer (Confluence Engine Overhaul Phase 2) — turns
    an extract_features() snapshot into a bias/score opinion via NNFX's
    layered baseline -> stack -> ADX -> RSI confirmation sequence. Pure
    function of (features, thresholds)."""
    current, e50, e100, e200 = (
        features["current"], features["e50"], features["e100"], features["e200"],
    )
    adx_val = features["adx_val"]
    rsi_val = features["rsi_val"]

    reasons: list[str] = []
    score = 0.0
    bias = Bias.NEUTRAL

    baseline_score = t.get("baseline_score", 25.0)
    # --- Baseline: EMA200 ---
    if current > e200:
        bias = Bias.BULLISH
        score += baseline_score
        reasons.append(f"Price above EMA200 ({e200:.5f}) — bullish baseline")
    elif current < e200:
        bias = Bias.BEARISH
        score += baseline_score
        reasons.append(f"Price below EMA200 ({e200:.5f}) — bearish baseline")

    stack_aligned_score = t.get("stack_aligned_score", 20.0)
    stack_weak_score = t.get("stack_weak_score", 5.0)
    # --- EMA stack confirmation ---
    if bias == Bias.BULLISH and e50 > e100 > e200:
        score += stack_aligned_score
        reasons.append("EMA stack aligned bullish (50>100>200)")
    elif bias == Bias.BEARISH and e50 < e100 < e200:
        score += stack_aligned_score
        reasons.append("EMA stack aligned bearish (50<100<200)")
    elif bias != Bias.NEUTRAL:
        reasons.append("EMA stack not fully aligned — weak confirmation")
        score += stack_weak_score

    adx_strong = t.get("adx_strong", 25)
    adx_strong_score = t.get("adx_strong_score", 20.0)
    adx_moderate = t.get("adx_moderate", 15)
    adx_moderate_score = t.get("adx_moderate_score", 8.0)
    adx_weak_penalty = t.get("adx_weak_penalty", 15.0)
    adx_weak_neutral_floor = t.get("adx_weak_neutral_floor", 15.0)
    # --- ADX strength filter ---
    if adx_val >= adx_strong:
        score += adx_strong_score
        reasons.append(f"ADX={adx_val:.1f} ≥ {adx_strong} — trending market, strong confirmation")
    elif adx_val >= adx_moderate:
        score += adx_moderate_score
        reasons.append(f"ADX={adx_val:.1f} — moderate trend strength")
    else:
        reasons.append(f"ADX={adx_val:.1f} < {adx_moderate} — weak trend, NNFX cautions against entry")
        score = max(0, score - adx_weak_penalty)
        if score < adx_weak_neutral_floor:
            bias = Bias.NEUTRAL

    rsi_confirm_score = t.get("rsi_confirm_score", 15.0)
    rsi_conflict_penalty = t.get("rsi_conflict_penalty", 10.0)
    # --- RSI second confirmation (NNFX methodology) ---
    if bias == Bias.BULLISH and rsi_val > 50:
        score += rsi_confirm_score
        reasons.append(f"RSI={rsi_val:.1f} confirms bullish (>50)")
    elif bias == Bias.BEARISH and rsi_val < 50:
        score += rsi_confirm_score
        reasons.append(f"RSI={rsi_val:.1f} confirms bearish (<50)")
    elif bias != Bias.NEUTRAL:
        reasons.append(f"RSI={rsi_val:.1f} does not confirm direction — reduced confidence")
        score = max(0, score - rsi_conflict_penalty)

    score = min(round(score, 1), t.get("score_cap", 80.0))
    return bias, score, reasons


class NNFXEngine(BaseEngine):
    name = "NNFX"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        tf, df = self.decision_frame(mtf_data)

        min_bars = t.get("min_bars", 210)
        if len(df) < min_bars:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=[f"Insufficient data for NNFX analysis (need {min_bars}+ bars for EMA200)"],
            )

        features = extract_features(df, t)
        bias, score, reasons = decide(features, t)

        raw = {
            "timeframe_used": tf,
            "ema50": round(features["e50"], 5),
            "ema100": round(features["e100"], 5),
            "ema200": round(features["e200"], 5),
            "adx": round(features["adx_val"], 1),
            "adx_undefined": features["adx_undefined"],
            "rsi": round(features["rsi_val"], 1),
            "rsi_undefined": features["rsi_undefined"],
            "price_vs_ema200_pct": features["price_vs_ema200_pct"],
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
