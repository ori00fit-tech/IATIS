"""
engines/macro_engine.py
---------------------------
Macro Layer engine — top-level market context. Confluence Engine
Overhaul Phase 3c (2026-08-01) rebuild.

This engine doesn't analyze price patterns. It analyzes:
1. Dollar Strength (DXY): inversely correlated with most risk assets
2. Risk-On / Risk-Off: broad market appetite, from SPY/VIX/Gold-vs-SPY,
   plus 3 new votes this phase: yield-curve inversion, credit-spread
   direction, Fed-balance-sheet direction (expansion/contraction)
3. Yield Curve: US10Y vs US02Y spread — a real feature FINALLY wired up
   this phase. Fetching US10Y/US02Y was already supported by
   core/alt_data_loader.py's load_macro_snapshot, but v1's analyze()
   never requested them — this was a documented-but-never-implemented
   promise, not a working feature, until now.
4. VIX: fear gauge — high = defensive, low = risk-seeking
5. Commodities (oil/copper/natural gas): trend direction, INFORMATIONAL
   ONLY — their risk-on/off interpretation is genuinely ambiguous
   (a commodity rally can mean growth OR stagflation depending on
   context), so they are surfaced in output but never scored, the same
   restraint quant_engine.py's volatility-clustering feature applies.

MOVE index and an Economic Surprise Index were both considered (per the
Confluence Engine Overhaul roadmap's original wishlist) and dropped:
zero free/official source exists for either (confirmed by a repo-wide
and web search — MOVE is an ICE-licensed product, Economic Surprise
indices are proprietary to Citi/Bloomberg). Not silently missing —
there is simply nothing free to fetch.

The Macro Engine uses trusted official sources for daily/weekly/monthly
data (CBOE for VIX, FRED for everything else via
core/alt_data_loader.py's load_macro_snapshot). It runs once per day on
D1 data conceptually, not on every H1 bar — macro context doesn't
change intraday. `mtf_data` is accepted only to satisfy BaseEngine's
abstract analyze() signature; it is never read (v1's own docstring
claimed otherwise but never actually did so — corrected here).

Requires: network access to CBOE / FRED (FRED_API_KEY optional — the
keyless fredgraph.csv export is used when it's absent).
Gracefully degrades to NEUTRAL if data unavailable.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)

_SNAPSHOT_SYMBOLS = [
    "DXY", "SPY", "VIX", "GLD", "US10Y", "US02Y",
    "OIL_WTI", "COPPER", "NATGAS", "CREDIT_SPREAD", "FED_BALANCE_SHEET",
]


def _series_trend(df: pd.DataFrame | None, lookback: int, flat_threshold_pct: float) -> str | None:
    """Simple % change over `lookback` bars -> 'up'/'down'/'flat', or None
    if unavailable. Frequency-agnostic on purpose: works the same way for
    a daily series (lookback in bars) or a monthly one (lookback in
    months) — the caller picks a lookback appropriate to that series'
    own native cadence, not a shared assumption."""
    if df is None or len(df) < lookback + 1:
        return None
    close = df["close"]
    pct = float(close.pct_change(lookback).iloc[-1])
    if pct > flat_threshold_pct:
        return "up"
    if pct < -flat_threshold_pct:
        return "down"
    return "flat"


def extract_features(snapshot: dict[str, pd.DataFrame], t: dict) -> dict:
    """Feature Extraction layer — every raw macro fact decide() needs.
    Pure function of an ALREADY-FETCHED snapshot dict (the fetch call
    itself lives in analyze() only, matching every other engine's
    "receive already-loaded data, don't fetch it yourself" contract).
    No df/mtf_data/tf here at all — Macro's facts come from an external
    daily/weekly/monthly snapshot, not OHLCV bars, the most extreme case
    yet of "whatever inputs the facts need" (already established by
    ict_engine's mtf_data-only and quant_engine's df+tf shapes)."""
    dxy_df = snapshot.get("DXY")
    spy_df = snapshot.get("SPY")
    vix_df = snapshot.get("VIX")
    gld_df = snapshot.get("GLD")
    us10y_df = snapshot.get("US10Y")
    us02y_df = snapshot.get("US02Y")
    credit_df = snapshot.get("CREDIT_SPREAD")
    balance_sheet_df = snapshot.get("FED_BALANCE_SHEET")

    # --- DXY trend ---
    dxy_direction: str | None = None
    dxy_spread_pct: float | None = None
    if dxy_df is not None and len(dxy_df) >= t.get("dxy_ema_slow", 20):
        close = dxy_df["close"]
        ema_fast = close.ewm(span=t.get("dxy_ema_fast", 10)).mean()
        ema_slow = close.ewm(span=t.get("dxy_ema_slow", 20)).mean()
        e_fast = float(ema_fast.iloc[-1])
        e_slow = float(ema_slow.iloc[-1])
        dxy_spread_pct = (e_fast - e_slow) / e_slow * 100
        dxy_direction = "up" if e_fast > e_slow else "down"

    # --- Risk-on/off votes (facts only — decide() classifies) ---
    risk_on = 0
    risk_off = 0
    risk_reasons: list[str] = []

    if spy_df is not None and len(spy_df) >= t.get("spy_ma_period", 20):
        spy = spy_df["close"]
        spy_ma = float(spy.rolling(t.get("spy_ma_period", 20)).mean().iloc[-1])
        spy_current = float(spy.iloc[-1])
        if spy_current > spy_ma:
            risk_on += 1
            risk_reasons.append(f"SPY above MA{t.get('spy_ma_period', 20)} ({spy_current:.0f} > {spy_ma:.0f}) — Risk-On")
        else:
            risk_off += 1
            risk_reasons.append(f"SPY below MA{t.get('spy_ma_period', 20)} ({spy_current:.0f} < {spy_ma:.0f}) — Risk-Off")

    if vix_df is not None and len(vix_df) >= 5:
        vix_level = float(vix_df["close"].iloc[-1])
        vix_low = t.get("vix_low", 15)
        vix_high = t.get("vix_high", 25)
        if vix_level < vix_low:
            risk_on += 1
            risk_reasons.append(f"VIX={vix_level:.1f} (low fear) — Risk-On")
        elif vix_level > vix_high:
            risk_off += 1
            risk_reasons.append(f"VIX={vix_level:.1f} (elevated fear) — Risk-Off")
        else:
            risk_reasons.append(f"VIX={vix_level:.1f} (neutral)")

    gold_lookback = t.get("gold_spy_lookback", 5)
    gold_threshold = t.get("gold_spy_threshold_pct", 0.01)
    if gld_df is not None and spy_df is not None and len(gld_df) > gold_lookback and len(spy_df) > gold_lookback:
        gld_chg = float(gld_df["close"].pct_change(gold_lookback).iloc[-1])
        spy_chg = float(spy_df["close"].pct_change(gold_lookback).iloc[-1])
        if gld_chg > gold_threshold and spy_chg < -gold_threshold:
            risk_off += 1
            risk_reasons.append(f"Gold up {gld_chg:+.1%} while SPY down {spy_chg:+.1%} — flight to safety Risk-Off")
        elif spy_chg > gold_threshold and gld_chg < 0:
            risk_on += 1
            risk_reasons.append(f"SPY up {spy_chg:+.1%} while Gold down — Risk-On rotation")

    # --- Yield curve (real feature, finally wired up this phase) ---
    yield_spread: float | None = None
    yield_inverted: bool | None = None
    if us10y_df is not None and us02y_df is not None and len(us10y_df) and len(us02y_df):
        us10y = float(us10y_df["close"].iloc[-1])
        us02y = float(us02y_df["close"].iloc[-1])
        yield_spread = round(us10y - us02y, 3)
        yield_inverted = yield_spread < 0
        if yield_inverted:
            risk_off += 1
            risk_reasons.append(f"Yield curve INVERTED (US10Y {us10y:.2f} - US02Y {us02y:.2f} = {yield_spread:+.2f}) — recession-warning Risk-Off")
        else:
            risk_reasons.append(f"Yield curve normal (spread={yield_spread:+.2f})")

    # --- Credit spread direction (Baa - 10Y Treasury) ---
    credit_lookback = t.get("credit_spread_lookback_days", 20)
    credit_widen_threshold = t.get("credit_spread_widen_threshold", 0.1)
    if credit_df is not None and len(credit_df) > credit_lookback:
        credit_now = float(credit_df["close"].iloc[-1])
        credit_before = float(credit_df["close"].iloc[-1 - credit_lookback])
        credit_change = credit_now - credit_before
        if credit_change > credit_widen_threshold:
            risk_off += 1
            risk_reasons.append(f"Credit spread widening ({credit_before:.2f}->{credit_now:.2f}) — credit-stress Risk-Off")
        elif credit_change < -credit_widen_threshold:
            risk_on += 1
            risk_reasons.append(f"Credit spread narrowing ({credit_before:.2f}->{credit_now:.2f}) — Risk-On")

    # --- Fed balance sheet direction (expansion=QE=risk-on, contraction=QT=risk-off) ---
    bs_lookback = t.get("fed_balance_sheet_lookback_weeks", 8)
    bs_threshold = t.get("fed_balance_sheet_threshold_pct", 0.01)
    if balance_sheet_df is not None and len(balance_sheet_df) > bs_lookback:
        bs_chg = float(balance_sheet_df["close"].pct_change(bs_lookback).iloc[-1])
        if bs_chg > bs_threshold:
            risk_on += 1
            risk_reasons.append(f"Fed balance sheet expanding ({bs_chg:+.1%} over {bs_lookback}w) — liquidity Risk-On")
        elif bs_chg < -bs_threshold:
            risk_off += 1
            risk_reasons.append(f"Fed balance sheet contracting ({bs_chg:+.1%} over {bs_lookback}w) — QT Risk-Off")

    # --- Commodities: informational only, never scored (see module docstring) ---
    commodity_lookback = t.get("commodity_lookback_periods", 20)
    commodity_flat = t.get("commodity_flat_threshold_pct", 0.02)
    oil_trend = _series_trend(snapshot.get("OIL_WTI"), commodity_lookback, commodity_flat)
    copper_trend = _series_trend(snapshot.get("COPPER"), 6, commodity_flat)  # copper is monthly — 6-month lookback
    natgas_trend = _series_trend(snapshot.get("NATGAS"), commodity_lookback, commodity_flat)

    requested = _SNAPSHOT_SYMBOLS
    data_loaded = [s for s in requested if snapshot.get(s) is not None]
    load_errors = [s for s in requested if snapshot.get(s) is None]

    return {
        "dxy_direction": dxy_direction,
        "dxy_spread_pct": round(dxy_spread_pct, 4) if dxy_spread_pct is not None else None,
        "risk_on_votes": risk_on,
        "risk_off_votes": risk_off,
        "risk_reasons": risk_reasons,
        "yield_spread": yield_spread,
        "yield_inverted": yield_inverted,
        "oil_trend": oil_trend,
        "copper_trend": copper_trend,
        "natgas_trend": natgas_trend,
        "data_loaded": data_loaded,
        "load_errors": load_errors,
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer — turns an extract_features() snapshot into
    a bias/score opinion. Pure function of (features, thresholds), never
    touches a DataFrame or fetches anything."""
    reasons: list[str] = []
    score = 0.0
    bias = Bias.NEUTRAL

    if features["load_errors"]:
        reasons.append(f"Partial data (failed: {', '.join(features['load_errors'])})")

    dxy_direction = features["dxy_direction"]
    if dxy_direction is not None:
        spread_pct = features["dxy_spread_pct"] or 0.0
        dxy_score = min(abs(spread_pct) * t.get("dxy_score_scale", 15.0), t.get("dxy_score_cap", 40.0))
        if dxy_direction == "up":
            bias = Bias.BEARISH
            reasons.append(f"DXY rising (spread={spread_pct:+.2f}%) — USD bullish")
        else:
            bias = Bias.BULLISH
            reasons.append(f"DXY falling (spread={spread_pct:+.2f}%) — USD bearish")
        score += dxy_score * t.get("dxy_weight", 0.6)
    else:
        reasons.append("DXY data unavailable")

    risk_on, risk_off = features["risk_on_votes"], features["risk_off_votes"]
    total_votes = risk_on + risk_off
    if total_votes == 0:
        risk_state, risk_conf = "NEUTRAL", 0.0
    elif risk_on > risk_off:
        risk_state, risk_conf = "RISK_ON", round(risk_on / total_votes, 2)
    elif risk_off > risk_on:
        risk_state, risk_conf = "RISK_OFF", round(risk_off / total_votes, 2)
    else:
        risk_state, risk_conf = "NEUTRAL", 0.5
    reasons.extend(features["risk_reasons"])

    risk_confirm_score = t.get("risk_confirm_score", 30.0)
    risk_conflict_penalty = t.get("risk_conflict_penalty", 10.0)
    if risk_state == "RISK_ON" and bias == Bias.BULLISH:
        score += risk_conf * risk_confirm_score
        reasons.append(f"Risk-On environment (conf={risk_conf:.0%}) confirms bullish bias")
    elif risk_state == "RISK_OFF" and bias == Bias.BEARISH:
        score += risk_conf * risk_confirm_score
        reasons.append(f"Risk-Off environment (conf={risk_conf:.0%}) confirms bearish bias")
    elif risk_state == "RISK_ON" and bias == Bias.BEARISH:
        score -= risk_conflict_penalty
        reasons.append("Risk-On conflicts with bearish DXY bias — reducing confidence")
    elif risk_state == "RISK_OFF" and bias == Bias.BULLISH:
        score -= risk_conflict_penalty
        reasons.append("Risk-Off conflicts with bullish DXY bias — reducing confidence")

    # Commodities: informational only — appended to reasons, never scored.
    commodity_bits = []
    if features["oil_trend"]:
        commodity_bits.append(f"oil {features['oil_trend']}")
    if features["copper_trend"]:
        commodity_bits.append(f"copper {features['copper_trend']}")
    if features["natgas_trend"]:
        commodity_bits.append(f"nat-gas {features['natgas_trend']}")
    if commodity_bits:
        reasons.append(f"Commodities (informational only): {', '.join(commodity_bits)}")

    score = max(0.0, min(round(score, 1), t.get("score_cap", 70.0)))
    if score < t.get("score_neutral_floor", 15.0):
        bias = Bias.NEUTRAL

    return bias, score, reasons


class MacroEngine(BaseEngine):
    name = "Macro"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        """mtf_data is accepted only to satisfy BaseEngine's abstract
        signature — it is never read. Macro's facts come entirely from
        an external daily/weekly/monthly snapshot (DXY/SPY/VIX/GLD/
        yields/oil/copper/natgas/credit-spread/Fed-balance-sheet) via
        core/alt_data_loader.py's load_macro_snapshot."""
        t = self.thresholds
        try:
            from core.alt_data_loader import load_macro_snapshot
        except ImportError:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Macro loader unavailable — Macro engine disabled"],
            )

        snapshot = load_macro_snapshot(_SNAPSHOT_SYMBOLS)
        if not snapshot or all(df is None for df in snapshot.values()):
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["All macro data unavailable — CBOE/FRED unreachable"],
                raw={"errors": _SNAPSHOT_SYMBOLS},
            )

        features = extract_features(snapshot, t)
        bias, score, reasons = decide(features, t)
        raw = {**features, "timeframe_used": "D1"}

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
