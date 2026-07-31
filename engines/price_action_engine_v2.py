"""
engines/price_action_engine_v2.py
------------------------------------
Confluence Engine Overhaul Track C (Phase 4, 2026-08-01) — a genuinely
PURE price-action engine: no RSI, no Bollinger Bands (v1's
engines/price_action_engine.py still scores on both — that import's
ABSENCE here is itself part of this engine's contract, pinned by
tests/test_engine_variants.py's source-scan). Every signal below comes
from bar-shape/structure alone.

AD-HOC ONLY: this engine is never wired into the live pipeline
(main.py's _ALL_ENGINES) and is never enabled by config/engines.yaml's
`enabled` block. It is reachable exclusively through
backtesting.backtest_engine.build_engine_config_override's
`engine_variants` parameter (Mission Center) — see that function's
docstring. Promoting this to a live default is a human decision made by
hand-editing config/engines.yaml after real walk-forward/Monte-Carlo
evidence, never something this code does itself.

Patterns implemented: Inside Bar, Outside Bar, NR4/NR7 (narrow range),
Fakey, Three Bar Play, Micro Trend (swing-structure), Compression/
Volatility Contraction (VCP), Failed Breakout, Opening Drive, Closing
Strength. Every detector reads only historical bars + the current
(decision) bar of `df` — no lookahead, matching every other engine in
this codebase.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


def _bar_range(bar: pd.Series) -> float:
    return float(bar["high"]) - float(bar["low"])


def _is_inside_bar(df: pd.DataFrame) -> bool:
    """Current bar's range is fully contained within the previous bar's."""
    if len(df) < 2:
        return False
    cur, prev = df.iloc[-1], df.iloc[-2]
    return bool(float(cur["high"]) < float(prev["high"]) and float(cur["low"]) > float(prev["low"]))


def _is_outside_bar(df: pd.DataFrame) -> bool:
    """Current bar's range fully engulfs the previous bar's."""
    if len(df) < 2:
        return False
    cur, prev = df.iloc[-1], df.iloc[-2]
    return bool(float(cur["high"]) > float(prev["high"]) and float(cur["low"]) < float(prev["low"]))


def _is_nr_n(df: pd.DataFrame, n: int) -> bool:
    """True if the current (last) bar's range is the smallest of the
    last n bars — generalizes NR4 (n=4) / NR7 (n=7)."""
    if len(df) < n:
        return False
    ranges = (df["high"] - df["low"]).tail(n)
    return bool(ranges.iloc[-1] <= ranges.min())


def _detect_fakey(df: pd.DataFrame, t: dict) -> tuple[str, float]:
    """Mother bar -> inside bar -> false break beyond the inside bar's
    own range, closing back inside it. Classic 3-bar reversal pattern.
    Returns (direction, strength 0..1)."""
    if len(df) < 3:
        return "none", 0.0
    mother, inside, current = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if not (float(inside["high"]) < float(mother["high"]) and float(inside["low"]) > float(mother["low"])):
        return "none", 0.0  # bar -2 must actually be an inside bar of the mother

    tol = t.get("fakey_break_tolerance", 0.001)
    inside_high, inside_low = float(inside["high"]), float(inside["low"])
    cur_high, cur_low, cur_close = float(current["high"]), float(current["low"]), float(current["close"])
    inside_range = (inside_high - inside_low) or 1e-10

    if cur_low < inside_low * (1 - tol) and cur_close > inside_low:
        depth = (inside_low - cur_low) / inside_range
        return "bullish", min(1.0, 0.5 + depth)
    if cur_high > inside_high * (1 + tol) and cur_close < inside_high:
        depth = (cur_high - inside_high) / inside_range
        return "bearish", min(1.0, 0.5 + depth)
    return "none", 0.0


def _detect_three_bar_play(df: pd.DataFrame, t: dict) -> tuple[str, float]:
    """Strong directional bar -> small-range pause bar -> break of the
    pause bar's extreme in the SAME direction as the strong bar
    (continuation pattern)."""
    if len(df) < 3:
        return "none", 0.0
    strong, pause, current = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    strong_range = _bar_range(strong)
    if strong_range <= 0:
        return "none", 0.0
    max_pause_pct = t.get("three_bar_play_pause_max_range_pct", 0.5)
    if _bar_range(pause) > strong_range * max_pause_pct:
        return "none", 0.0  # the "pause" bar wasn't actually a pause

    strong_bullish = float(strong["close"]) > float(strong["open"])
    strong_bearish = float(strong["close"]) < float(strong["open"])
    cur_close = float(current["close"])

    if strong_bullish and cur_close > float(pause["high"]):
        strength = min(1.0, (cur_close - float(pause["high"])) / strong_range + 0.5)
        return "bullish", strength
    if strong_bearish and cur_close < float(pause["low"]):
        strength = min(1.0, (float(pause["low"]) - cur_close) / strong_range + 0.5)
        return "bearish", strength
    return "none", 0.0


def _micro_trend(df: pd.DataFrame, lookback: int, swing_window: int) -> tuple[str, float]:
    """Higher-highs/higher-lows (or the bearish mirror) swing structure
    over the last `lookback` bars, via utils.indicators.find_swings."""
    needed = lookback + 2 * swing_window + 1
    if len(df) < needed:
        return "none", 0.0
    from utils.indicators import find_swings

    recent = df.tail(needed)
    swing_high, swing_low = find_swings(recent, window=swing_window)
    highs = recent.loc[swing_high, "high"]
    lows = recent.loc[swing_low, "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "none", 0.0

    higher_highs = highs.iloc[-1] > highs.iloc[-2]
    higher_lows = lows.iloc[-1] > lows.iloc[-2]
    lower_highs = highs.iloc[-1] < highs.iloc[-2]
    lower_lows = lows.iloc[-1] < lows.iloc[-2]

    if higher_highs and higher_lows:
        return "bullish", 1.0
    if lower_highs and lower_lows:
        return "bearish", 1.0
    return "none", 0.0


def _compression_ratio(df: pd.DataFrame, short_lookback: int, long_lookback: int) -> float:
    """short-window mean range / long-window mean range. <1 = the market
    is currently trading a tighter range than its own recent norm."""
    if len(df) < long_lookback:
        return 1.0
    ranges = df["high"] - df["low"]
    long_mean = ranges.tail(long_lookback).mean()
    if long_mean <= 0:
        return 1.0
    short_mean = ranges.tail(short_lookback).mean()
    return float(short_mean / long_mean)


def _volatility_contraction_score(df: pd.DataFrame, lookback: int) -> float:
    """0..1: fraction of consecutive bar-to-bar range shrinks over the
    last `lookback` bars — a real VCP has ranges monotonically
    narrowing, a widening/random sequence scores low."""
    if len(df) < lookback + 1:
        return 0.0
    ranges = (df["high"] - df["low"]).tail(lookback + 1).to_numpy()
    shrinks = sum(1 for i in range(1, len(ranges)) if ranges[i] <= ranges[i - 1])
    return shrinks / (len(ranges) - 1)


def _detect_failed_breakout(df: pd.DataFrame, lookback: int, atr_period: int) -> tuple[str, float]:
    """Price wicks beyond an N-bar high/low then closes back inside it —
    a trapped-breakout reversal signal."""
    if len(df) < lookback + 2:
        return "none", 0.0
    from utils.indicators import atr as compute_atr

    prior = df.iloc[-(lookback + 1):-1]
    current = df.iloc[-1]
    prior_high, prior_low = float(prior["high"].max()), float(prior["low"].min())
    cur_high, cur_low, cur_close = float(current["high"]), float(current["low"]), float(current["close"])
    atr_series = compute_atr(df, atr_period)
    atr_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else (prior_high - prior_low) / lookback

    if cur_high > prior_high and cur_close < prior_high:
        depth = (cur_high - prior_high) / atr_val if atr_val > 0 else 0.5
        return "bearish", min(1.0, 0.5 + depth)
    if cur_low < prior_low and cur_close > prior_low:
        depth = (prior_low - cur_low) / atr_val if atr_val > 0 else 0.5
        return "bullish", min(1.0, 0.5 + depth)
    return "none", 0.0


def _detect_opening_drive(df: pd.DataFrame, t: dict) -> tuple[str, float]:
    """Open near one extreme, close near the opposite extreme, with a
    small opposing wick on both ends — a one-directional conviction bar."""
    if len(df) < 1:
        return "none", 0.0
    bar = df.iloc[-1]
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
    rng = h - l
    if rng <= 0:
        return "none", 0.0
    max_wick_pct = t.get("opening_drive_wick_max_pct", 0.25)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if lower_wick > rng * max_wick_pct or upper_wick > rng * max_wick_pct:
        return "none", 0.0

    if c > o:
        return "bullish", min(1.0, (c - o) / rng)
    if c < o:
        return "bearish", min(1.0, (o - c) / rng)
    return "none", 0.0


def _closing_strength(df: pd.DataFrame) -> float:
    """Signed close-position-in-range: +1 = closed at the bar's high,
    -1 = at the low, 0 = exact midpoint."""
    if len(df) < 1:
        return 0.0
    bar = df.iloc[-1]
    h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
    rng = h - l
    if rng <= 0:
        return 0.0
    return ((c - l) / rng) * 2 - 1


def extract_features(df: pd.DataFrame, t: dict) -> dict:
    """Feature Extraction layer — every pattern fact decide() needs, with
    zero bias/score logic. Pure function of (df, thresholds)."""
    from utils.indicators import atr as compute_atr

    atr_series = compute_atr(df, t.get("atr_period", 14))
    atr_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0

    return {
        "inside_bar": _is_inside_bar(df),
        "outside_bar": _is_outside_bar(df),
        "nr4": _is_nr_n(df, 4),
        "nr7": _is_nr_n(df, 7),
        "fakey": _detect_fakey(df, t),
        "three_bar_play": _detect_three_bar_play(df, t),
        "micro_trend": _micro_trend(df, t.get("micro_trend_lookback", 5), t.get("micro_trend_swing_window", 2)),
        "compression_ratio": _compression_ratio(
            df, t.get("compression_short_lookback", 5), t.get("compression_long_lookback", 20)
        ),
        "vcp_score": _volatility_contraction_score(df, t.get("vcp_lookback", 6)),
        "failed_breakout": _detect_failed_breakout(
            df, t.get("failed_breakout_lookback", 20), t.get("atr_period", 14)
        ),
        "opening_drive": _detect_opening_drive(df, t),
        "closing_strength": _closing_strength(df),
        "atr": atr_val,
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer — pure function of (features, thresholds).

    Two-layer design: bar-shape/compression facts (inside/outside bar,
    NR4/NR7, compression_ratio, vcp_score) are a volatility SETUP only,
    never directional evidence by themselves. Only the 6 directional
    detectors below accumulate bull/bear score; a compression bonus is
    then layered on top of whichever side is already nonzero when the
    market shows a genuine coil — "a breakout out of a coil is higher
    quality than one with no setup" is real price-action trading logic,
    not an arbitrary rule.
    """
    reasons: list[str] = []
    bull_score = 0.0
    bear_score = 0.0

    fakey_dir, fakey_strength = features["fakey"]
    fakey_score = t.get("fakey_score", 30.0)
    if fakey_dir == "bullish":
        bull_score += fakey_score * fakey_strength
        reasons.append(f"Bullish Fakey (false break, close back inside; strength={fakey_strength:.0%})")
    elif fakey_dir == "bearish":
        bear_score += fakey_score * fakey_strength
        reasons.append(f"Bearish Fakey (false break, close back inside; strength={fakey_strength:.0%})")

    tbp_dir, tbp_strength = features["three_bar_play"]
    tbp_score = t.get("three_bar_play_score", 20.0)
    if tbp_dir == "bullish":
        bull_score += tbp_score * tbp_strength
        reasons.append(f"Bullish Three Bar Play continuation (strength={tbp_strength:.0%})")
    elif tbp_dir == "bearish":
        bear_score += tbp_score * tbp_strength
        reasons.append(f"Bearish Three Bar Play continuation (strength={tbp_strength:.0%})")

    micro_dir, micro_strength = features["micro_trend"]
    micro_score = t.get("micro_trend_score", 15.0)
    if micro_dir == "bullish":
        bull_score += micro_score * micro_strength
        reasons.append("Micro trend: higher highs + higher lows")
    elif micro_dir == "bearish":
        bear_score += micro_score * micro_strength
        reasons.append("Micro trend: lower highs + lower lows")

    fb_dir, fb_strength = features["failed_breakout"]
    fb_score = t.get("failed_breakout_score", 25.0)
    if fb_dir == "bullish":
        bull_score += fb_score * fb_strength
        reasons.append(f"Failed breakdown — trapped sellers, reversal up (strength={fb_strength:.0%})")
    elif fb_dir == "bearish":
        bear_score += fb_score * fb_strength
        reasons.append(f"Failed breakout — trapped buyers, reversal down (strength={fb_strength:.0%})")

    od_dir, od_strength = features["opening_drive"]
    od_score = t.get("opening_drive_score", 15.0)
    if od_dir == "bullish":
        bull_score += od_score * od_strength
        reasons.append(f"Opening Drive bullish — one-directional conviction bar (strength={od_strength:.0%})")
    elif od_dir == "bearish":
        bear_score += od_score * od_strength
        reasons.append(f"Opening Drive bearish — one-directional conviction bar (strength={od_strength:.0%})")

    closing_strength = features["closing_strength"]
    cs_score = t.get("closing_strength_score", 15.0)
    if closing_strength > 0.3:
        bull_score += cs_score * closing_strength
        reasons.append(f"Strong close near bar high (closing_strength={closing_strength:+.2f})")
    elif closing_strength < -0.3:
        bear_score += cs_score * abs(closing_strength)
        reasons.append(f"Strong close near bar low (closing_strength={closing_strength:+.2f})")

    vcp_score = features["vcp_score"]
    vcp_bonus_threshold = t.get("vcp_bonus_threshold", 0.6)
    compression_bonus_score = t.get("compression_bonus_score", 15.0)
    if (vcp_score > vcp_bonus_threshold or features["nr7"]) and (bull_score > 0 or bear_score > 0):
        if bull_score >= bear_score:
            bull_score += compression_bonus_score
        else:
            bear_score += compression_bonus_score
        reasons.append(f"Volatility coil detected (vcp_score={vcp_score:.2f}) — breakout-quality bonus applied")

    if features["inside_bar"]:
        reasons.append("Inside bar — range contraction, awaiting a break")
    if features["outside_bar"]:
        reasons.append("Outside bar — range expansion")

    bull_score_min = t.get("bull_score_min", 30.0)
    score_cap = t.get("score_cap", 80.0)
    if bull_score > bear_score and bull_score >= bull_score_min:
        bias = Bias.BULLISH
        score = min(round(bull_score, 1), score_cap)
    elif bear_score > bull_score and bear_score >= bull_score_min:
        bias = Bias.BEARISH
        score = min(round(bear_score, 1), score_cap)
    else:
        bias = Bias.NEUTRAL
        score = 0.0
        reasons.append("No clear pure price-action signal")

    return bias, score, reasons


class PriceActionEngineV2(BaseEngine):
    name = "PriceActionV2"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        tf, df = self.decision_frame(mtf_data)

        min_bars = t.get("min_bars", 30)
        if len(df) < min_bars:
            return EngineOutput(
                engine_name=self.name, bias=Bias.NEUTRAL, score=0.0,
                reasons=[f"Insufficient data (need {min_bars}+ bars)"],
            )

        features = extract_features(df, t)
        bias, score, reasons = decide(features, t)

        raw = {
            "timeframe_used": tf,
            "inside_bar": features["inside_bar"],
            "outside_bar": features["outside_bar"],
            "nr4": features["nr4"],
            "nr7": features["nr7"],
            "fakey": features["fakey"][0],
            "three_bar_play": features["three_bar_play"][0],
            "micro_trend": features["micro_trend"][0],
            "failed_breakout": features["failed_breakout"][0],
            "opening_drive": features["opening_drive"][0],
            "closing_strength": round(features["closing_strength"], 2),
            "vcp_score": round(features["vcp_score"], 2),
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
