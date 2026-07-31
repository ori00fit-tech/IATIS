"""
engines/divergence_engine.py
------------------------------
Divergence Engine: detects RSI and MACD divergence patterns — Confluence
Engine Overhaul Phase 3b (2026-08-01) full rebuild.

Divergence = price makes a new swing high/low but momentum doesn't
confirm it. This engine is DISABLED (config/engines.yaml
engines.enabled.divergence: false) and H010 ("RSI/MACD Divergence
Engine") stays research/results/registry.json status: RESEARCH — this
rebuild changes internal implementation only, never live behavior.

What changed from v1:
  - Swing detection: v1's rolling-window local-extrema finder treated
    every tiny wiggle as a "swing" and only ever compared the LAST 2 of
    them. v2 uses utils.indicators.zigzag_pivots — a magnitude (ATR-
    filtered) + spacing-filtered pivot detector — so only genuine swings
    are compared.
  - Two new divergence-strengthening signals: Triple divergence (a 3rd,
    earlier confirmed swing also agreeing with an existing Regular
    divergence — a confirmation on top of Regular, not a separate
    signal path) and Multi-timeframe confirmation (does the next coarser
    configured timeframe show the same Regular-divergence direction).
  - RSI/MACD math moved to utils/indicators.py as rsi_wilder()/macd() —
    Phase 1 (2026-07-31) explicitly deferred unifying divergence's RSI
    onto the SMA-smoothed rsi() used elsewhere "since divergence's whole
    swing/momentum detection gets rebuilt in Phase 3 anyway." Resolved
    here: Wilder's EWM smoothing is the textbook-original RSI formula
    and is kept, not force-unified — it is a genuinely different,
    equally valid formula, not a duplicate.
  - The old docstring's "Killzone timing (London/NY): +10 bonus" claim
    is REMOVED — it never existed in the actual analyze() body, was
    dead documentation, and nobody asked for a new session-dependent
    feature here.

Types (unchanged from v1's definitions):
  Regular Bearish: price HH, momentum LH → bearish reversal
  Regular Bullish: price LL, momentum HL → bullish reversal
  Hidden Bearish:  price LH, momentum HH → continuation down (less weight)
  Hidden Bullish:  price HL, momentum LL → continuation up (less weight)

Both RSI and MACD comparisons are driven by the SAME price-pivot bars
(matches v1's real behavior: _detect_divergence always found swings on
`price`, only ever reading the indicator's value AT those bars — not a
separately swing-detected MACD/RSI series). RSI is primary; MACD confirms
or, absent any RSI divergence, stands alone at a discount.

Research status: RESEARCH (H010 pending — needs statistical validation)
Compatible with: SMC (BOS confirms divergence), NNFX (trend filter)
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.indicators import atr, macd, rsi_wilder, zigzag_pivots

# Confluence Engine Overhaul Phase 3b — Multi-timeframe confirmation looks
# at the next coarser of the module's own supported timeframes. Local,
# private table (mirrors quant_engine.py's own local _TF_MINUTES_LOCAL
# precedent) rather than importing core.timeframe_sync's underscore-
# private internals. D1 has nothing coarser to check — a correct,
# expected degradation (mtf_regular_div_type stays None), not a bug.
_NEXT_COARSER_TF: dict[str, str | None] = {"M15": "H1", "H1": "H4", "H4": "D1", "D1": None}


def _detect_pattern(price: pd.Series, indicator: pd.Series, highs: pd.DataFrame, lows: pd.DataFrame) -> dict:
    """Regular-then-Hidden divergence check between `price`'s last 2
    confirmed zigzag pivots (of one type) and `indicator`'s value at
    those same bars — mirrors v1's _detect_divergence() priority order
    (Regular Bearish/Bullish checked first — whichever is stronger by
    price-move magnitude — Hidden only if neither found), now fed by
    real magnitude+spacing-filtered pivots instead of naive local
    extrema. `price_move_pct` is the raw fractional price move between
    the 2 compared swings — a pure fact; decide() turns it into a score."""
    result = {"type": "none", "price_move_pct": 0.0}

    high_positions = highs.index.tolist()
    if len(high_positions) >= 2:
        i1, i2 = high_positions[-2], high_positions[-1]
        p1, p2 = price.loc[i1], price.loc[i2]
        ind1, ind2 = indicator.loc[i1], indicator.loc[i2]
        if p2 > p1 and ind2 < ind1:  # Regular Bearish: price HH, indicator LH
            result = {"type": "regular_bearish", "price_move_pct": abs(p2 - p1) / p1}

    low_positions = lows.index.tolist()
    if len(low_positions) >= 2:
        i1, i2 = low_positions[-2], low_positions[-1]
        p1, p2 = price.loc[i1], price.loc[i2]
        ind1, ind2 = indicator.loc[i1], indicator.loc[i2]
        if p2 < p1 and ind2 > ind1:  # Regular Bullish: price LL, indicator HL
            candidate = {"type": "regular_bullish", "price_move_pct": abs(p1 - p2) / p1}
            if result["type"] == "none" or candidate["price_move_pct"] > result["price_move_pct"]:
                result = candidate

    if result["type"] == "none" and len(high_positions) >= 2:
        i1, i2 = high_positions[-2], high_positions[-1]
        p1, p2 = price.loc[i1], price.loc[i2]
        ind1, ind2 = indicator.loc[i1], indicator.loc[i2]
        if p2 < p1 and ind2 > ind1:  # Hidden Bearish: price LH, indicator HH
            result = {"type": "hidden_bearish", "price_move_pct": 0.0}

    if result["type"] == "none" and len(low_positions) >= 2:
        i1, i2 = low_positions[-2], low_positions[-1]
        p1, p2 = price.loc[i1], price.loc[i2]
        ind1, ind2 = indicator.loc[i1], indicator.loc[i2]
        if p2 > p1 and ind2 < ind1:  # Hidden Bullish: price HL, indicator LL
            result = {"type": "hidden_bullish", "price_move_pct": 0.0}

    return result


def _check_triple(price: pd.Series, indicator: pd.Series, highs: pd.DataFrame, lows: pd.DataFrame, pattern: dict) -> bool:
    """A 3rd, earlier confirmed swing that ALSO agrees with `pattern`'s
    direction strengthens an existing Regular divergence into a Triple
    divergence — this extends the Regular pattern, it is never checked
    for (or reported on top of) a Hidden divergence."""
    if pattern["type"] == "regular_bearish":
        positions = highs.index.tolist()
        if len(positions) >= 3:
            i1, i2 = positions[-3], positions[-2]
            p1, p2 = price.loc[i1], price.loc[i2]
            ind1, ind2 = indicator.loc[i1], indicator.loc[i2]
            return bool(p2 > p1 and ind2 < ind1)
    elif pattern["type"] == "regular_bullish":
        positions = lows.index.tolist()
        if len(positions) >= 3:
            i1, i2 = positions[-3], positions[-2]
            p1, p2 = price.loc[i1], price.loc[i2]
            ind1, ind2 = indicator.loc[i1], indicator.loc[i2]
            return bool(p2 < p1 and ind2 > ind1)
    return False


def _mtf_regular_direction(mtf_data: dict[str, pd.DataFrame], t: dict, tf: str) -> tuple[str | None, str | None]:
    """Regular-divergence direction ('bearish'/'bullish') on the next
    coarser configured timeframe, or None if unavailable/no coarser
    pattern found — an honest "not applicable," never fabricated."""
    coarser_tf = _NEXT_COARSER_TF.get(tf)
    if not coarser_tf or coarser_tf not in mtf_data:
        return None, coarser_tf

    c_df = mtf_data[coarser_tf]
    if len(c_df) < t.get("min_bars", 80):
        return None, coarser_tf

    c_close = c_df["close"].astype(float)
    c_atr = atr(c_df, t.get("atr_period", 14))
    c_rsi = rsi_wilder(c_close, t.get("rsi_period", 14))
    c_pivots = zigzag_pivots(
        c_close, c_atr,
        min_move_atr_mult=t.get("swing_min_move_atr_mult", 1.5),
        min_bars_between=t.get("swing_min_bars_between", 3),
    )
    c_highs = c_pivots[c_pivots["pivot_type"] == "high"]
    c_lows = c_pivots[c_pivots["pivot_type"] == "low"]
    c_pattern = _detect_pattern(c_close, c_rsi, c_highs, c_lows)

    if c_pattern["type"] == "regular_bearish":
        return "bearish", coarser_tf
    if c_pattern["type"] == "regular_bullish":
        return "bullish", coarser_tf
    return None, coarser_tf


def extract_features(mtf_data: dict[str, pd.DataFrame], t: dict, tf: str) -> dict:
    """Feature Extraction layer — every raw divergence fact decide()
    needs. Pure function of (mtf_data, thresholds, tf); mtf_data (not
    just the decision-timeframe df) is needed because Multi-timeframe
    confirmation genuinely requires the coarser frame's own data,
    matching ict_engine.py's established "whatever inputs the facts
    need" precedent."""
    df = mtf_data[tf]
    close = df["close"].astype(float)

    atr_series = atr(df, t.get("atr_period", 14))
    rsi_series = rsi_wilder(close, t.get("rsi_period", 14))
    macd_line, signal_line = macd(
        close, t.get("macd_fast", 12), t.get("macd_slow", 26), t.get("macd_signal", 9),
    )
    macd_hist = macd_line - signal_line

    price_pivots = zigzag_pivots(
        close, atr_series,
        min_move_atr_mult=t.get("swing_min_move_atr_mult", 1.5),
        min_bars_between=t.get("swing_min_bars_between", 3),
    )
    highs = price_pivots[price_pivots["pivot_type"] == "high"]
    lows = price_pivots[price_pivots["pivot_type"] == "low"]

    rsi_pattern = _detect_pattern(close, rsi_series, highs, lows)
    macd_pattern = _detect_pattern(close, macd_hist, highs, lows)
    triple_div = _check_triple(close, rsi_series, highs, lows, rsi_pattern)
    mtf_regular_div_type, mtf_tf_checked = _mtf_regular_direction(mtf_data, t, tf)

    current_rsi = float(rsi_series.iloc[-1]) if len(rsi_series) and pd.notna(rsi_series.iloc[-1]) else None
    if current_rsi is None:
        rsi_context = "RSI unavailable"
    elif current_rsi > t.get("rsi_overbought", 70):
        rsi_context = f"RSI overbought ({current_rsi:.1f})"
    elif current_rsi < t.get("rsi_oversold", 30):
        rsi_context = f"RSI oversold ({current_rsi:.1f})"
    else:
        rsi_context = f"RSI neutral ({current_rsi:.1f})"

    last_atr = float(atr_series.iloc[-1]) if len(atr_series) and pd.notna(atr_series.iloc[-1]) else None

    return {
        "n_bars": len(df),
        "rsi_current": current_rsi,
        "rsi_context": rsi_context,
        "rsi_pattern_type": rsi_pattern["type"],
        "rsi_pattern_price_move_pct": round(rsi_pattern["price_move_pct"], 5),
        "macd_pattern_type": macd_pattern["type"],
        "macd_pattern_price_move_pct": round(macd_pattern["price_move_pct"], 5),
        "triple_div": triple_div,
        "mtf_tf_checked": mtf_tf_checked,
        "mtf_regular_div_type": mtf_regular_div_type,
        "atr_value": last_atr,
        "n_confirmed_highs": int(len(highs)),
        "n_confirmed_lows": int(len(lows)),
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer — turns an extract_features() snapshot into
    a bias/score opinion. Priority: Regular (+ Triple/MACD/MTF layered
    on top) > Hidden (+ MACD only) > MACD-only fallback > none. Pure
    function of (features, thresholds), never touches a DataFrame."""
    reasons: list[str] = []
    score = 0.0
    bias = Bias.NEUTRAL

    rsi_type = features["rsi_pattern_type"]
    macd_type = features["macd_pattern_type"]

    if rsi_type in ("regular_bearish", "regular_bullish"):
        is_bearish = rsi_type == "regular_bearish"
        bias = Bias.BEARISH if is_bearish else Bias.BULLISH
        magnitude_bonus = min(
            t.get("regular_magnitude_cap", 25.0),
            features["rsi_pattern_price_move_pct"] * t.get("regular_magnitude_scale", 1000.0),
        )
        score = t.get("regular_base_score", 55.0) + magnitude_bonus
        reasons.append(
            f"RSI {rsi_type.replace('_', ' ')} divergence "
            f"(price move {features['rsi_pattern_price_move_pct'] * 100:.2f}%)"
        )
        if features["triple_div"]:
            score += t.get("triple_bonus", 15.0)
            reasons.append("Triple divergence: a third confirmed swing also agrees — stronger conviction")
        if macd_type == rsi_type:
            score += t.get("macd_confirm_bonus", 15.0)
            reasons.append(f"MACD confirms: {macd_type.replace('_', ' ')}")
        this_direction = "bearish" if is_bearish else "bullish"
        mtf_type = features["mtf_regular_div_type"]
        if mtf_type == this_direction:
            score += t.get("mtf_confirm_bonus", 10.0)
            reasons.append(f"{features['mtf_tf_checked']} confirms same-direction regular divergence")
        elif mtf_type is not None and mtf_type != this_direction:
            score -= t.get("mtf_conflict_penalty", 5.0)
            reasons.append(f"{features['mtf_tf_checked']} shows opposite-direction divergence — conflicting signal")

    elif rsi_type in ("hidden_bearish", "hidden_bullish"):
        bias = Bias.BEARISH if rsi_type == "hidden_bearish" else Bias.BULLISH
        score = t.get("hidden_score", 40.0)
        reasons.append(f"RSI {rsi_type.replace('_', ' ')} divergence — continuation")
        if macd_type == rsi_type:
            score += t.get("macd_confirm_bonus", 15.0)
            reasons.append(f"MACD confirms: {macd_type.replace('_', ' ')}")

    elif macd_type != "none":
        bias = Bias.BEARISH if "bearish" in macd_type else Bias.BULLISH
        if "regular" in macd_type:
            magnitude_bonus = min(
                t.get("regular_magnitude_cap", 25.0),
                features["macd_pattern_price_move_pct"] * t.get("regular_magnitude_scale", 1000.0),
            )
            base_score = t.get("regular_base_score", 55.0) + magnitude_bonus
        else:
            base_score = t.get("hidden_score", 40.0)
        score = base_score * t.get("macd_only_scale", 0.75)
        reasons.append(f"MACD {macd_type.replace('_', ' ')} only (no RSI divergence)")

    else:
        reasons.append(f"No divergence detected. {features['rsi_context']}")

    reasons.append(features["rsi_context"])

    score = max(0.0, min(round(score, 1), t.get("score_cap", 90.0)))
    if score < t.get("score_neutral_floor", 15.0):
        bias = Bias.NEUTRAL

    return bias, score, reasons


class DivergenceEngine(BaseEngine):
    """Detects RSI and MACD divergence on the decision timeframe, with
    Triple-divergence and Multi-timeframe confirmation.

    Research status: RESEARCH (H010) — divergence + trend confirmation
    improves win rate over random entry (to be validated forward).
    """

    name = "Divergence"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        tf, df = self.decision_frame(mtf_data)

        min_bars = t.get("min_bars", 80)
        if len(df) < min_bars:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=[f"Insufficient data for divergence analysis (need {min_bars}+ bars)"],
            )

        features = extract_features(mtf_data, t, tf)
        bias, score, reasons = decide(features, t)
        raw = {**features, "timeframe_used": tf}

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
