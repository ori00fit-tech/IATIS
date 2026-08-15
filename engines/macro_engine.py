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

Engine Refinement V1 (#366, MACRO-REFINE, OBSERVABILITY, operator-pre-
approved "aggregation semantics, observability only"): confirmed
conflict — the 6 risk-on/off votes (SPY-vs-MA20, VIX bucket, Gold-vs-
SPY, yield-curve inversion, credit-spread direction, Fed-balance-sheet
direction) were summed as if independent and simultaneous, with no
per-observation timestamp/cadence distinction anywhere in this engine,
even though they update on genuinely different real-world cadences
(daily vs. weekly). Fixed, observability-only: extract_features()'s
new `risk_vote_detail` reports each cast vote's own as_of date and
cadence; decide() states the independence-assumption caveat explicitly
in its reasons rather than silently implying `risk_conf` is a
calibrated probability. The vote-counting arithmetic itself is
UNCHANGED — no score/bias/threshold values were altered.
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


def _series_as_of(df: pd.DataFrame | None) -> str | None:
    """The real, most-recent observation DATE a fetched macro series'
    last bar carries — the closest thing to a per-observation timestamp
    this engine can honestly report. Engine Refinement V1 (#366,
    MACRO-REFINE, OBSERVABILITY): confirmed conflict — no per-observation
    observation_timestamp/publication_timestamp/available_at distinction
    existed anywhere in this engine (reports/engine_refinement/
    ENGINE_INVENTORY.md's Macro entry). This is deliberately NOT a
    publication-vintage guarantee — it's the dated VALUE of the series'
    own last row, not proof of when that row was actually published or
    revised (no free source provides revision vintages for any of these
    series — Provider Benchmark Phase 3's own reconfirmed finding).
    Callers must not treat `as_of` as an "available_at" timestamp."""
    if df is None or len(df) == 0:
        return None
    try:
        return str(df.index[-1].date())
    except Exception:
        return str(df.index[-1])


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
    # Engine Refinement V1 (#366, MACRO-REFINE, OBSERVABILITY): each vote
    # also records its own as_of date and cadence via risk_vote_detail
    # below — the confirmed gap (ENGINE_INVENTORY.md's Macro entry) was
    # that no per-observation timing distinction existed anywhere in this
    # engine, even though these 6 series update on genuinely different
    # real-world cadences (daily SPY/VIX/GLD/yields/credit-spread vs.
    # weekly Fed balance sheet) and are summed as if independent and
    # simultaneous. This fix is observability-only: the vote-counting
    # arithmetic itself (below) is UNCHANGED — see decide()'s new
    # independence-assumption reason for the pre-approved framing.
    risk_on = 0
    risk_off = 0
    risk_reasons: list[str] = []
    risk_vote_detail: list[dict] = []

    if spy_df is not None and len(spy_df) >= t.get("spy_ma_period", 20):
        spy = spy_df["close"]
        spy_ma = float(spy.rolling(t.get("spy_ma_period", 20)).mean().iloc[-1])
        spy_current = float(spy.iloc[-1])
        if spy_current > spy_ma:
            risk_on += 1
            direction = "on"
            risk_reasons.append(f"SPY above MA{t.get('spy_ma_period', 20)} ({spy_current:.0f} > {spy_ma:.0f}) — Risk-On")
        else:
            risk_off += 1
            direction = "off"
            risk_reasons.append(f"SPY below MA{t.get('spy_ma_period', 20)} ({spy_current:.0f} < {spy_ma:.0f}) — Risk-Off")
        risk_vote_detail.append({"vote": "spy_vs_ma", "direction": direction, "cadence": "daily", "as_of": _series_as_of(spy_df)})

    if vix_df is not None and len(vix_df) >= 5:
        vix_level = float(vix_df["close"].iloc[-1])
        vix_low = t.get("vix_low", 15)
        vix_high = t.get("vix_high", 25)
        if vix_level < vix_low:
            risk_on += 1
            risk_reasons.append(f"VIX={vix_level:.1f} (low fear) — Risk-On")
            risk_vote_detail.append({"vote": "vix", "direction": "on", "cadence": "daily", "as_of": _series_as_of(vix_df)})
        elif vix_level > vix_high:
            risk_off += 1
            risk_reasons.append(f"VIX={vix_level:.1f} (elevated fear) — Risk-Off")
            risk_vote_detail.append({"vote": "vix", "direction": "off", "cadence": "daily", "as_of": _series_as_of(vix_df)})
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
            risk_vote_detail.append({"vote": "gold_vs_spy", "direction": "off", "cadence": "daily", "as_of": _series_as_of(gld_df)})
        elif spy_chg > gold_threshold and gld_chg < 0:
            risk_on += 1
            risk_reasons.append(f"SPY up {spy_chg:+.1%} while Gold down — Risk-On rotation")
            risk_vote_detail.append({"vote": "gold_vs_spy", "direction": "on", "cadence": "daily", "as_of": _series_as_of(gld_df)})

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
            risk_vote_detail.append({"vote": "yield_curve", "direction": "off", "cadence": "daily", "as_of": _series_as_of(us10y_df)})
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
            risk_vote_detail.append({"vote": "credit_spread", "direction": "off", "cadence": "daily", "as_of": _series_as_of(credit_df)})
        elif credit_change < -credit_widen_threshold:
            risk_on += 1
            risk_reasons.append(f"Credit spread narrowing ({credit_before:.2f}->{credit_now:.2f}) — Risk-On")
            risk_vote_detail.append({"vote": "credit_spread", "direction": "on", "cadence": "daily", "as_of": _series_as_of(credit_df)})

    # --- Fed balance sheet direction (expansion=QE=risk-on, contraction=QT=risk-off) ---
    bs_lookback = t.get("fed_balance_sheet_lookback_weeks", 8)
    bs_threshold = t.get("fed_balance_sheet_threshold_pct", 0.01)
    if balance_sheet_df is not None and len(balance_sheet_df) > bs_lookback:
        bs_chg = float(balance_sheet_df["close"].pct_change(bs_lookback).iloc[-1])
        if bs_chg > bs_threshold:
            risk_on += 1
            risk_reasons.append(f"Fed balance sheet expanding ({bs_chg:+.1%} over {bs_lookback}w) — liquidity Risk-On")
            risk_vote_detail.append({"vote": "fed_balance_sheet", "direction": "on", "cadence": "weekly", "as_of": _series_as_of(balance_sheet_df)})
        elif bs_chg < -bs_threshold:
            risk_off += 1
            risk_reasons.append(f"Fed balance sheet contracting ({bs_chg:+.1%} over {bs_lookback}w) — QT Risk-Off")
            risk_vote_detail.append({"vote": "fed_balance_sheet", "direction": "off", "cadence": "weekly", "as_of": _series_as_of(balance_sheet_df)})

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
        "risk_vote_detail": risk_vote_detail,
        "yield_spread": yield_spread,
        "yield_inverted": yield_inverted,
        "oil_trend": oil_trend,
        "copper_trend": copper_trend,
        "natgas_trend": natgas_trend,
        "data_loaded": data_loaded,
        "load_errors": load_errors,
    }


def decide(features: dict, t: dict, usd_is_base: bool = False) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer — turns an extract_features() snapshot into
    a bias/score opinion. Pure function of (features, thresholds,
    usd_is_base), never touches a DataFrame or fetches anything.

    usd_is_base (Forensic Audit, 2026-08-04 — BUG-008): whether the
    symbol currently being analyzed has USD as its BASE currency (e.g.
    USDJPY, USDCHF, USDCAD) rather than its quote currency (EURUSD,
    GBPUSD, ...) or a USD-denominated non-FX asset (XAUUSD, BTCUSD,
    indices). A stronger dollar makes USDJPY/USDCHF/USDCAD RISE, the
    OPPOSITE of every other symbol this engine was originally written
    for — the DXY->bias mapping below must flip for exactly this case.
    Defaults to False (today's original, unchanged behavior) so every
    existing caller that doesn't pass this is unaffected."""
    reasons: list[str] = []
    score = 0.0
    bias = Bias.NEUTRAL

    if features["load_errors"]:
        reasons.append(f"Partial data (failed: {', '.join(features['load_errors'])})")

    dxy_direction = features["dxy_direction"]
    if dxy_direction is not None:
        spread_pct = features["dxy_spread_pct"] or 0.0
        dxy_score = min(abs(spread_pct) * t.get("dxy_score_scale", 15.0), t.get("dxy_score_cap", 40.0))
        usd_up_bias = Bias.BULLISH if usd_is_base else Bias.BEARISH
        usd_down_bias = Bias.BEARISH if usd_is_base else Bias.BULLISH
        base_note = " — USD is the BASE currency for this pair, so USD strength is bullish for it" if usd_is_base else ""
        if dxy_direction == "up":
            bias = usd_up_bias
            reasons.append(f"DXY rising (spread={spread_pct:+.2f}%) — USD bullish{base_note}")
        else:
            bias = usd_down_bias
            reasons.append(f"DXY falling (spread={spread_pct:+.2f}%) — USD bearish{base_note}")
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

    # Engine Refinement V1 (#366, MACRO-REFINE, OBSERVABILITY, operator-
    # pre-approved "aggregation semantics, observability only"): the
    # confirmed conflict was that these votes are summed as if
    # independent (equal weight, no correlation adjustment) with no
    # stated caveat. Fixed by stating it explicitly here rather than
    # silently implying risk_conf is a calibrated probability — the
    # vote-counting arithmetic above is UNCHANGED, this is text only.
    if total_votes >= 2:
        reasons.append(
            f"Risk vote aggregation treats {total_votes} signal(s) as independent "
            f"(a known simplification — some may correlate, e.g. yield-curve "
            f"inversion and credit-spread widening both reflect tightening "
            f"conditions); risk_conf is a signal-count ratio, not a calibrated "
            f"probability. See raw['risk_vote_detail'] for each vote's own as_of date."
        )

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


def is_usd_base_symbol(symbol: str) -> bool:
    """True when USD is the BASE currency of `symbol` (USDJPY, USDCHF,
    USDCAD, ...) rather than its quote currency (EURUSD, GBPUSD, ...) or
    a USD-denominated non-FX asset (XAUUSD, BTCUSD, US30, ...). Matches
    this codebase's own FX naming convention (base currency listed
    first, e.g. `config/symbols.yaml`'s `internal: USDJPY`)."""
    return bool(symbol) and symbol.upper().startswith("USD")


class MacroEngine(BaseEngine):
    name = "Macro"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        """mtf_data is accepted only to satisfy BaseEngine's abstract
        signature — it is never read. Macro's facts come entirely from
        an external daily/weekly/monthly snapshot (DXY/SPY/VIX/GLD/
        yields/oil/copper/natgas/credit-spread/Fed-balance-sheet) via
        core/alt_data_loader.py's load_macro_snapshot.

        `self._symbol` (set by main.py's live engine-construction loop,
        BUG-005's precedent) IS consulted here, for exactly one purpose:
        BUG-008's fix — inverting the DXY->bias mapping for USD-base
        pairs (USDJPY/USDCHF/USDCAD). Absent (e.g. every existing
        zero-arg test construction) defaults to `usd_is_base=False`,
        i.e. today's original, unchanged mapping. Note: MacroEngine is
        never constructed by backtesting/backtest_engine.py's engine
        loop at all (deliberately excluded from ENGINE_KEYS — no
        runnable path there), so this only ever fires on the live
        path — the TE-3 fix (2026-08-15) to that loop's `._symbol`
        wiring reaches SentimentEngine, not this class."""
        t = self.thresholds
        usd_is_base = is_usd_base_symbol(getattr(self, "_symbol", ""))
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
        bias, score, reasons = decide(features, t, usd_is_base=usd_is_base)
        raw = {**features, "timeframe_used": "D1", "usd_is_base": usd_is_base}

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
