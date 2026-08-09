"""
engines/market_structure_engine.py
-------------------------------------
Market Structure Engine: detects structural shifts (MSS/CHoCH).

More sophisticated than the current SMC engine which uses simple
swing-pair voting. This engine identifies specific structural events:

  BOS  (Break of Structure):  confirms trend continuation
  CHoCH (Change of Character): first sign of reversal
  MSS  (Market Structure Shift): confirmed reversal

Timeframe hierarchy:
  H4: macro structure (major BOS/CHoCH)
  H1: intermediate (current trading structure)
  Bias = H4 and H1 agree? → strong signal

Scoring:
  CHoCH on H1 confirmed by H4: 75
  BOS continuation on H1+H4:   65
  CHoCH on H1 only:             50
  BOS on H1 only:               45
  Conflicting timeframes:       NEUTRAL

vs current SMC engine:
  SMC: counts swing pairs, votes (simple)
  MSS: identifies specific structural EVENTS (more nuanced)

Research status: RESEARCH

Engine Refinement V1 (#369, MARKET-STRUCTURE-REFINE, OBSERVABILITY):
_classify_structure() computes last_event/last_event_bias identically
for BOTH the H1 and H4 windows, but only H1's event ever reached `raw`
(h1_event/h1_event_direction) -- H4's own event was silently discarded
even though decide() itself never reads it either way (only h4_bias/
h4_trend feed scoring, confirmed by direct code read). Fixed,
observability-only: raw now also exposes h4_event/h4_event_direction so
an operator/researcher can see what H4 independently showed, purely
informational (never consulted by decide()). Disabled by default
(engines.enabled.market_structure: false) -- zero live-trading impact.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from engines.base_engine import BaseEngine, Bias, EngineOutput


def _swing_points(df: pd.DataFrame, window: int = 3) -> tuple[list, list]:
    """Return (highs, lows) as lists of (idx, price) tuples.

    Unified onto utils.indicators.find_swings (the same rolling-window
    detector smc_engine.find_swing_points already used) — Confluence
    Engine Overhaul Phase 1. Verified bit-identical to this function's
    prior standalone loop implementation on a real synthetic corpus
    before the swap; positional (not label) indices, matching the old
    loop's `i` semantics exactly."""
    from utils.indicators import find_swings

    swing_high, swing_low = find_swings(df, window=window)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    highs = [(int(pos), float(high.iloc[pos])) for pos in np.where(swing_high.to_numpy())[0]]
    lows = [(int(pos), float(low.iloc[pos])) for pos in np.where(swing_low.to_numpy())[0]]

    return highs, lows


def _classify_structure(
    highs: list,
    lows: list,
    close_now: float | None = None,
    bos_strength: float = 65,
    choch_strength: float = 75,
    ranging_strength: float = 20,
    weak_structure_strength: float = 45,
) -> dict:
    """Classify market structure from recent swings.

    Returns dict with:
      trend: 'bullish' | 'bearish' | 'ranging'
      last_event: 'BOS' | 'CHoCH' | 'MSS' | 'none'
      last_event_bias: 'bullish' | 'bearish'
      strength: 0-100
      broke_level / break_direction / break_price: was the most recent
        confirmed swing high/low actually broken by the current close?

    Engine Refinement V1 (#363, MARKET-STRUCTURE-REFINE, SEMANTIC_FIX,
    operator-approved via "audit + fix if needed"): last_event (BOS/
    CHoCH/MSS) now REQUIRES a real level break — the current close must
    have actually closed beyond the most recent confirmed swing high
    (bullish) or swing low (bearish), mirroring smc_engine.
    detect_bos_choch's already-established close-beyond-level
    convention. Before this fix, last_event was assigned purely from a
    geometric comparison of the last 2-3 swing-pivot VALUES (was the
    newest swing high numerically higher than the previous one?) with
    no requirement that price ever actually broke a level — exactly the
    "swing pair voting" this module's own header docstring claims this
    engine improves on over the plain SMC engine. `trend` (the HH/HL/
    LH/LL swing-pattern classification) is UNCHANGED by this fix:
    describing an existing swing sequence as bullish/bearish/ranging is
    a legitimate geometric fact that doesn't need a break to be true —
    only the event label (and the strength it drives) needed the break
    requirement. broke_level/break_direction/break_price are always
    returned (even when False/none/None) for observability.
    """
    if len(highs) < 2 or len(lows) < 2:
        return {
            "trend": "ranging", "last_event": "none", "last_event_bias": "none", "strength": 0,
            "broke_level": False, "break_direction": "none", "break_price": None,
        }

    # Get last 4 highs and lows
    recent_h = highs[-4:]
    recent_l = lows[-4:]

    # Determine current trend from swing structure
    hh = recent_h[-1][1] > recent_h[-2][1] if len(recent_h) >= 2 else False
    hl = recent_l[-1][1] > recent_l[-2][1] if len(recent_l) >= 2 else False
    lh = recent_h[-1][1] < recent_h[-2][1] if len(recent_h) >= 2 else False
    ll = recent_l[-1][1] < recent_l[-2][1] if len(recent_l) >= 2 else False

    bullish_structure = hh and hl
    bearish_structure = lh and ll

    # Break-of-level check (mirrors smc_engine.detect_bos_choch): does the
    # CURRENT close actually close beyond the most recent confirmed swing
    # high/low? This is a fact about price action, independent of the
    # swing-pivot-value comparison above.
    last_high_price = highs[-1][1]
    last_low_price = lows[-1][1]
    broke_level = False
    break_direction = "none"
    break_price = None
    if close_now is not None:
        if close_now > last_high_price:
            broke_level, break_direction, break_price = True, "bullish", last_high_price
        elif close_now < last_low_price:
            broke_level, break_direction, break_price = True, "bearish", last_low_price

    # Detect structural events — each requires a real break in the
    # matching direction, not just a swing-value pattern.
    last_event = "none"
    last_event_bias = "none"

    if len(recent_h) >= 3 and len(recent_l) >= 3:
        # CHoCH Bullish: was making LH+LL, then breaks above last LH
        was_bearish = recent_h[-3][1] > recent_h[-2][1]  # LH
        broke_above = recent_h[-1][1] > recent_h[-2][1]  # now HH
        low_made = recent_l[-1][1] > recent_l[-2][1]      # HL too

        if was_bearish and broke_above and broke_level and break_direction == "bullish":
            last_event = "CHoCH" if not low_made else "MSS"
            last_event_bias = "bullish"

        # CHoCH Bearish: was making HH+HL, then breaks below last HL
        was_bullish = recent_l[-3][1] < recent_l[-2][1]  # HL
        broke_below = recent_l[-1][1] < recent_l[-2][1]  # now LL
        high_made = recent_h[-1][1] < recent_h[-2][1]    # LH too

        if was_bullish and broke_below and broke_level and break_direction == "bearish":
            last_event = "CHoCH" if not high_made else "MSS"
            last_event_bias = "bearish"

        # BOS: trend continuation break
        if bullish_structure and last_event == "none" and broke_level and break_direction == "bullish":
            last_event = "BOS"
            last_event_bias = "bullish"
        elif bearish_structure and last_event == "none" and broke_level and break_direction == "bearish":
            last_event = "BOS"
            last_event_bias = "bearish"

    # Calculate strength based on consistency
    if bullish_structure:
        trend = "bullish"
        strength = bos_strength if last_event == "BOS" else (
            choch_strength if last_event in ("CHoCH", "MSS") else weak_structure_strength)
    elif bearish_structure:
        trend = "bearish"
        strength = bos_strength if last_event == "BOS" else (
            choch_strength if last_event in ("CHoCH", "MSS") else weak_structure_strength)
    else:
        trend = "ranging"
        strength = ranging_strength

    return {
        "trend": trend,
        "last_event": last_event,
        "last_event_bias": last_event_bias,
        "structure_hh": hh, "structure_hl": hl,
        "structure_lh": lh, "structure_ll": ll,
        "strength": strength,
        "broke_level": broke_level,
        "break_direction": break_direction,
        "break_price": break_price,
    }


def extract_features(df_cur: pd.DataFrame, df_macro: pd.DataFrame, t: dict) -> dict:
    """Feature Extraction layer (Confluence Engine Overhaul Phase 2) —
    H1/H4 swing-point classification decide() needs. Pure function of
    (df_cur, df_macro, thresholds), no bias/score logic."""
    h1_lookback_bars = t.get("h1_lookback_bars", 100)
    h4_lookback_bars = t.get("h4_lookback_bars", 60)
    structure_kwargs = {
        "bos_strength": t.get("bos_strength", 65),
        "choch_strength": t.get("choch_strength", 75),
        "ranging_strength": t.get("ranging_strength", 20),
        "weak_structure_strength": t.get("weak_structure_strength", 45),
    }

    h1_window = df_cur.tail(h1_lookback_bars)
    h4_window = df_macro.tail(h4_lookback_bars)
    h1_highs, h1_lows = _swing_points(h1_window, window=t.get("h1_window", 3))
    h4_highs, h4_lows = _swing_points(h4_window, window=t.get("h4_window", 2))

    h1_close_now = float(h1_window["close"].iloc[-1]) if len(h1_window) else None
    h4_close_now = float(h4_window["close"].iloc[-1]) if len(h4_window) else None

    h1_struct = _classify_structure(h1_highs, h1_lows, close_now=h1_close_now, **structure_kwargs)
    h4_struct = _classify_structure(h4_highs, h4_lows, close_now=h4_close_now, **structure_kwargs)

    last_h1_high = h1_highs[-1][1] if h1_highs else 0
    last_h1_low = h1_lows[-1][1] if h1_lows else 0
    # Feature Mining Phase 1 (2026-07-30) — swing bar-ages, needed for a
    # "how many bars since the last swing high/low" feature. idx is
    # positional within h1_window (the tail(N) slice used above for
    # _swing_points), so age is relative to that slice's last bar,
    # matching the decision-time snapshot everywhere else in raw.
    last_high_idx = h1_highs[-1][0] if h1_highs else None
    last_low_idx = h1_lows[-1][0] if h1_lows else None
    last_high_bar_age = (len(h1_window) - 1 - last_high_idx) if last_high_idx is not None else None
    last_low_bar_age = (len(h1_window) - 1 - last_low_idx) if last_low_idx is not None else None

    return {
        "h1_struct": h1_struct,
        "h4_struct": h4_struct,
        "last_h1_high": last_h1_high,
        "last_h1_low": last_h1_low,
        "last_high_bar_age": last_high_bar_age,
        "last_low_bar_age": last_low_bar_age,
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer (Confluence Engine Overhaul Phase 2) — turns
    an extract_features() snapshot into a bias/score opinion via H1/H4
    trend-agreement voting. Pure function of (features, thresholds)."""
    h1_struct, h4_struct = features["h1_struct"], features["h4_struct"]
    h1_bias = h1_struct["trend"]
    h4_bias = h4_struct["trend"]
    h1_event = h1_struct["last_event"]
    h1_event_dir = h1_struct["last_event_bias"]

    aligned_bonus = t.get("aligned_bonus", 10.0)
    aligned_score_cap = t.get("aligned_score_cap", 85.0)
    disagree_score = t.get("disagree_score", 50.0)
    h1_only_score = t.get("h1_only_score", 40.0)

    reasons: list[str] = []
    score = 0.0
    bias = Bias.NEUTRAL

    # Both timeframes agree — strong signal
    if h1_bias == h4_bias and h1_bias != "ranging":
        if h1_bias == "bullish":
            bias = Bias.BULLISH
            score = h1_struct["strength"]
            if h1_event in ("CHoCH", "MSS"):
                score = min(score + aligned_bonus, aligned_score_cap)
                reasons.append(
                    f"H1 {h1_event} bullish (confirmed break of {h1_struct['break_price']:.5f}) "
                    f"confirmed by H4 bullish structure"
                )
            else:
                reasons.append(f"H1+H4 bullish structure (BOS continuation)")
        else:
            bias = Bias.BEARISH
            score = h1_struct["strength"]
            if h1_event in ("CHoCH", "MSS"):
                score = min(score + aligned_bonus, aligned_score_cap)
                reasons.append(
                    f"H1 {h1_event} bearish (confirmed break of {h1_struct['break_price']:.5f}) "
                    f"confirmed by H4 bearish structure"
                )
            else:
                reasons.append(f"H1+H4 bearish structure (BOS continuation)")

    # H1 CHoCH/MSS against H4 trend — high priority reversal signal
    elif h1_event in ("CHoCH", "MSS") and h1_event_dir != "none":
        if h1_event_dir == "bullish":
            bias = Bias.BULLISH
            score = disagree_score  # lower confidence — H4 disagrees
            reasons.append(
                f"H1 {h1_event} bullish (confirmed break of {h1_struct['break_price']:.5f}) "
                f"but H4 still {h4_bias} — early reversal"
            )
        else:
            bias = Bias.BEARISH
            score = disagree_score
            reasons.append(
                f"H1 {h1_event} bearish (confirmed break of {h1_struct['break_price']:.5f}) "
                f"but H4 still {h4_bias} — early reversal"
            )

    # Only H1 structure
    elif h1_bias != "ranging":
        if h1_bias == "bullish":
            bias = Bias.BULLISH
            score = h1_only_score
        else:
            bias = Bias.BEARISH
            score = h1_only_score
        reasons.append(f"H1 {h1_bias} structure (H4 ranging/insufficient)")

    else:
        reasons.append(f"H1 ranging (HH={h1_struct['structure_hh']}, "
                       f"HL={h1_struct['structure_hl']}, "
                       f"LH={h1_struct['structure_lh']}, "
                       f"LL={h1_struct['structure_ll']})")

    reasons.append(
        f"Last H1 swing: high={features['last_h1_high']:.5f}, low={features['last_h1_low']:.5f}"
    )

    return bias, score, reasons


class MarketStructureEngine(BaseEngine):
    """Advanced market structure analysis: BOS, CHoCH, MSS.

    Dual-timeframe: the decision timeframe for current structure and the
    next-higher available timeframe for macro context. Both must agree
    for a strong signal.

    Research status: RESEARCH (H011)
    """

    # Ordered coarsest→finest; used to pick a macro frame one step above
    # the decision timeframe.
    _TF_ORDER = ["D1", "H4", "H1", "M15"]

    name = "MarketStructure"

    def _macro_frame(self, mtf_data: dict[str, pd.DataFrame], decision_tf: str):
        """The next timeframe coarser than the decision TF, else the
        decision frame itself (single-TF fallback)."""
        if decision_tf in self._TF_ORDER:
            for tf in self._TF_ORDER[: self._TF_ORDER.index(decision_tf)]:
                if tf in mtf_data:
                    return mtf_data[tf]
        return None

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        min_bars = t.get("min_bars", 30)

        # Current structure on the decision TF (was hardcoded H1); macro
        # context one timeframe higher (was hardcoded H4).
        _tf, df_cur = self.decision_frame(mtf_data)
        df_macro = self._macro_frame(mtf_data, _tf)
        if df_macro is None:
            df_macro = df_cur

        if len(df_cur) < min_bars:
            return EngineOutput(
                engine_name="MarketStructure",
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data"],
            )

        features = extract_features(df_cur, df_macro, t)
        bias, score, reasons = decide(features, t)

        return EngineOutput(
            engine_name="MarketStructure",
            bias=bias,
            score=round(score, 1),
            reasons=reasons,
            features=features,
            raw={
                "timeframe_h1": "H1",
                "timeframe_h4": "H4",
                "h1_trend": features["h1_struct"]["trend"],
                "h4_trend": features["h4_struct"]["trend"],
                "h1_event": features["h1_struct"]["last_event"],
                "h1_event_direction": features["h1_struct"]["last_event_bias"],
                "h1_strength": features["h1_struct"]["strength"],
                "h4_strength": features["h4_struct"]["strength"],
                # Engine Refinement V1 (#369, OBSERVABILITY): H4's own
                # event, computed identically to h1's above but never
                # exposed before -- informational only, decide() never
                # reads it (only h4_bias/h4_trend feed scoring).
                "h4_event": features["h4_struct"]["last_event"],
                "h4_event_direction": features["h4_struct"]["last_event_bias"],
                "aligned": features["h1_struct"]["trend"] == features["h4_struct"]["trend"],
                # Feature Mining Phase 1 (2026-07-30) — additive only, never
                # consulted by bias/score/control-flow above.
                "h1_structure_hh": features["h1_struct"].get("structure_hh"),
                "h1_structure_hl": features["h1_struct"].get("structure_hl"),
                "h1_structure_lh": features["h1_struct"].get("structure_lh"),
                "h1_structure_ll": features["h1_struct"].get("structure_ll"),
                "last_h1_high": features["last_h1_high"],
                "last_h1_low": features["last_h1_low"],
                "last_high_bar_age": features["last_high_bar_age"],
                "last_low_bar_age": features["last_low_bar_age"],
                # Engine Refinement V1 (#363) — the level-break fact behind
                # h1_event/h4's own trend; see _classify_structure's
                # docstring for why last_event now requires this.
                "h1_broke_level": features["h1_struct"]["broke_level"],
                "h1_break_direction": features["h1_struct"]["break_direction"],
                "h1_break_price": features["h1_struct"]["break_price"],
                "h4_broke_level": features["h4_struct"]["broke_level"],
                "h4_break_direction": features["h4_struct"]["break_direction"],
                "h4_break_price": features["h4_struct"]["break_price"],
            },
        )
