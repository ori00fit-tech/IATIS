"""
engines/macro_engine.py
---------------------------
Macro Layer engine — top-level market context.

This engine doesn't analyze price patterns. It analyzes:
1. Dollar Strength (DXY): inversely correlated with most risk assets
2. Risk-On / Risk-Off: determines the broad market appetite
3. Yield Curve: US10Y vs US02Y spread — recession indicator
4. VIX: fear gauge — high = defensive, low = risk-seeking

The Macro Engine uses trusted official sources for daily data on these
indices (CBOE for VIX, FRED for DXY/SPY/GLD via core/alt_data_loader.py's
load_macro_snapshot). It runs once per day on D1 data, not on every H1
bar — macro context doesn't change intraday.

Phase 3 implementation: real logic with real data.
Requires: network access to CBOE / FRED (FRED_API_KEY optional — the
keyless fredgraph.csv export is used when it's absent).
Gracefully degrades to NEUTRAL if data unavailable.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)

# Risk-On assets: when these rise, risk appetite is high
_RISK_ON_INDICATORS = {"SPY", "NAS100", "BTCUSD"}
# Risk-Off assets: when these rise, risk appetite is low
_RISK_OFF_INDICATORS = {"GLD", "DXY", "VIX"}


def _compute_dxy_bias(dxy_df: pd.DataFrame) -> tuple[Bias, float, str]:
    """DXY trend determines broad USD direction.

    DXY up = USD strong = bearish for EURUSD, XAUUSD, BTCUSD
    DXY down = USD weak = bullish for risk assets
    """
    if dxy_df is None or len(dxy_df) < 20:
        return Bias.NEUTRAL, 0.0, "DXY data unavailable"

    close = dxy_df["close"]
    ema10 = close.ewm(span=10).mean()
    ema20 = close.ewm(span=20).mean()
    current = float(close.iloc[-1])
    e10 = float(ema10.iloc[-1])
    e20 = float(ema20.iloc[-1])
    spread_pct = (e10 - e20) / e20 * 100

    if e10 > e20:
        return (Bias.BEARISH, min(abs(spread_pct) * 15, 40),
                f"DXY rising (EMA10={e10:.2f} > EMA20={e20:.2f}, spread={spread_pct:+.2f}%) — USD bullish")
    else:
        return (Bias.BULLISH, min(abs(spread_pct) * 15, 40),
                f"DXY falling (EMA10={e10:.2f} < EMA20={e20:.2f}, spread={spread_pct:+.2f}%) — USD bearish")


def _compute_risk_appetite(
    spy_df: pd.DataFrame | None,
    vix_df: pd.DataFrame | None,
    gld_df: pd.DataFrame | None,
) -> tuple[str, float, list[str]]:
    """Determine Risk-On / Risk-Off from SPY trend + VIX level + Gold vs SPY.

    Returns (state, confidence, reasons)
    state: 'RISK_ON' | 'RISK_OFF' | 'NEUTRAL'
    """
    risk_on_signals = 0
    risk_off_signals = 0
    reasons = []

    # SPY trend (S&P 500)
    if spy_df is not None and len(spy_df) >= 20:
        spy = spy_df["close"]
        spy_ma20 = float(spy.rolling(20).mean().iloc[-1])
        spy_current = float(spy.iloc[-1])
        if spy_current > spy_ma20:
            risk_on_signals += 1
            reasons.append(f"SPY above MA20 ({spy_current:.0f} > {spy_ma20:.0f}) — Risk-On")
        else:
            risk_off_signals += 1
            reasons.append(f"SPY below MA20 ({spy_current:.0f} < {spy_ma20:.0f}) — Risk-Off")

    # VIX (fear gauge)
    if vix_df is not None and len(vix_df) >= 5:
        vix_level = float(vix_df["close"].iloc[-1])
        if vix_level < 15:
            risk_on_signals += 1
            reasons.append(f"VIX={vix_level:.1f} (low fear) — Risk-On")
        elif vix_level > 25:
            risk_off_signals += 1
            reasons.append(f"VIX={vix_level:.1f} (elevated fear) — Risk-Off")
        else:
            reasons.append(f"VIX={vix_level:.1f} (neutral)")

    # Gold vs SPY divergence
    if gld_df is not None and spy_df is not None and len(gld_df) >= 5 and len(spy_df) >= 5:
        gld_5d = float(gld_df["close"].pct_change(5).iloc[-1])
        spy_5d = float(spy_df["close"].pct_change(5).iloc[-1])
        if gld_5d > 0.01 and spy_5d < -0.01:
            risk_off_signals += 1
            reasons.append(
                f"Gold up {gld_5d:+.1%} while SPY down {spy_5d:+.1%} — flight to safety Risk-Off"
            )
        elif spy_5d > 0.01 and gld_5d < 0:
            risk_on_signals += 1
            reasons.append(f"SPY up {spy_5d:+.1%} while Gold down — Risk-On rotation")

    total = risk_on_signals + risk_off_signals
    if total == 0:
        return "NEUTRAL", 0.0, reasons

    if risk_on_signals > risk_off_signals:
        confidence = risk_on_signals / total
        return "RISK_ON", round(confidence, 2), reasons
    elif risk_off_signals > risk_on_signals:
        confidence = risk_off_signals / total
        return "RISK_OFF", round(confidence, 2), reasons
    else:
        return "NEUTRAL", 0.5, reasons


class MacroEngine(BaseEngine):
    name = "Macro"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        """Analyze macro context from trusted official sources.

        mtf_data is used only to determine the instrument being analyzed
        (via the symbol in the raw dict from a previous engine, if available).
        The macro series (DXY/SPY/VIX/GLD) come from CBOE + FRED via
        load_macro_snapshot — Yahoo was removed as an untrusted feed
        (2026-07-17); every series now has a free official source.
        """
        try:
            from core.alt_data_loader import load_macro_snapshot
        except ImportError:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Macro loader unavailable — Macro engine disabled"],
            )

        snapshot = load_macro_snapshot(["DXY", "SPY", "VIX", "GLD"])
        dxy_df = snapshot.get("DXY")
        spy_df = snapshot.get("SPY")
        vix_df = snapshot.get("VIX")
        gld_df = snapshot.get("GLD")
        load_errors = [s for s in ["DXY", "SPY", "VIX", "GLD"] if s not in snapshot]

        if all(d is None for d in [dxy_df, spy_df, vix_df, gld_df]):
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["All macro data unavailable — CBOE/FRED unreachable"],
                raw={"errors": load_errors},
            )

        reasons = []
        if load_errors:
            reasons.append(f"Partial data (failed: {', '.join(load_errors)})")

        # DXY analysis
        dxy_bias, dxy_score, dxy_reason = _compute_dxy_bias(dxy_df)
        reasons.append(dxy_reason)

        # Risk appetite
        risk_state, risk_conf, risk_reasons = _compute_risk_appetite(spy_df, vix_df, gld_df)
        reasons.extend(risk_reasons)

        # Combine into final bias + score
        total_score = 0.0
        bias = Bias.NEUTRAL

        if dxy_bias != Bias.NEUTRAL:
            bias = dxy_bias
            total_score += dxy_score * 0.6  # DXY is primary macro signal

        # Risk state modifies bias and score
        if risk_state == "RISK_ON" and bias == Bias.BULLISH:
            total_score += risk_conf * 30
            reasons.append(f"Risk-On environment (conf={risk_conf:.0%}) confirms bullish bias")
        elif risk_state == "RISK_OFF" and bias == Bias.BEARISH:
            total_score += risk_conf * 30
            reasons.append(f"Risk-Off environment (conf={risk_conf:.0%}) confirms bearish bias")
        elif risk_state == "RISK_ON" and bias == Bias.BEARISH:
            total_score -= 10  # contradiction
            reasons.append("Risk-On conflicts with bearish DXY bias — reducing confidence")
        elif risk_state == "RISK_OFF" and bias == Bias.BULLISH:
            total_score -= 10
            reasons.append("Risk-Off conflicts with bullish DXY bias — reducing confidence")

        total_score = max(0, min(round(total_score, 1), 70.0))
        if total_score < 15:
            bias = Bias.NEUTRAL

        raw = {
            "dxy_bias": dxy_bias.value,
            "dxy_score": dxy_score,
            "risk_state": risk_state,
            "risk_confidence": risk_conf,
            "data_loaded": [s for s, d in [("DXY", dxy_df), ("SPY", spy_df),
                                            ("VIX", vix_df), ("GLD", gld_df)] if d is not None],
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=total_score,
            reasons=reasons,
            raw=raw,
        )
