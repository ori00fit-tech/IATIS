"""
engines/wyckoff_engine_v2.py
--------------------------------
Confluence Engine Overhaul Track C (Phase 4, 2026-08-01) — additive
Wyckoff extension: a real Phase A->E schematic reconstruction (Selling/
Buying Climax, Automatic Rally/Reaction, Secondary Test, Sign of
Strength/Weakness + Last Point of Support/Supply-Rally, Phase E markup/
markdown confirmation) layered ON TOP of v1's existing, proven
spring/upthrust + range-position + volume logic — imported and reused
directly, never rewritten. Also finally wires in v1's `_effort_vs_result`
(computed there since day one but never consulted by its own decide())
as a Composite-Operator-footprint heuristic.

AD-HOC ONLY: never wired into the live pipeline (main.py's _ALL_ENGINES)
and never enabled by config/engines.yaml's `enabled` block. Reachable
exclusively through backtesting.backtest_engine.
build_engine_config_override's `engine_variants` parameter (Mission
Center) — see that function's docstring.

Simplification, stated honestly rather than silently: the phase-C
determination below is pragmatic ("has a Sign of Strength/Weakness
appeared, or does the current bar show v1's own spring/upthrust event
in the schematic's direction, after a confirmed Secondary Test") rather
than a literal textbook Wyckoff Phase C (Spring/Test-in-range, strictly
before any breakout). A SOW/LPSY distribution-side mirror of SOS/LPS is
also an engineering addition beyond Wyckoff's 7 named canonical events,
needed for a working symmetric machine.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from engines.wyckoff_engine import (
    _effort_vs_result,
    decide as v1_decide,
    extract_features as v1_extract_features,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def _find_climax(df: pd.DataFrame, t: dict) -> dict:
    """Searches the last `climax_lookback` bars, chronologically, for the
    FIRST unusually wide bar (range >= climax_atr_multiple x its own
    local ATR) that closes decisively in one direction: a Selling Climax
    (SC — a wide DOWN bar closing in the lower 30% of its own range) or a
    Buying Climax (BC — a wide UP bar closing in the upper 30%). The
    earliest such bar is treated as the schematic's origin, since
    everything else (AR/ST/SOS/LPS) is defined relative to when it
    happened, not just how extreme it was.

    Deliberately bar-shape-only (no range_low/range_high dependency): a
    climax IS what establishes the range in the first place, so
    detecting it against a range computed from a window that may already
    include the climax (or a later breakout) would be self-referential.
    _phase_range() below computes the actual pre-climax range once this
    function has located where the climax happened."""
    from utils.indicators import atr as compute_atr

    lookback = t.get("climax_lookback", 60)
    atr_multiple = t.get("climax_atr_multiple", 2.5)
    atr_series = compute_atr(df, 14)
    window = df.tail(lookback)
    atr_window = atr_series.tail(lookback)
    n = len(window)

    for i in range(n):
        bar = window.iloc[i]
        a = atr_window.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        bar_range = h - l
        if bar_range < a * atr_multiple:
            continue
        bars_ago = n - 1 - i
        close_pos = (c - l) / bar_range if bar_range > 0 else 0.5
        if c < o and close_pos <= 0.3:
            return {"type": "sc", "bars_ago": bars_ago, "price": l, "strength": bar_range / a}
        if c > o and close_pos >= 0.7:
            return {"type": "bc", "bars_ago": bars_ago, "price": h, "strength": bar_range / a}
    return {"type": "none", "bars_ago": -1, "price": 0.0, "strength": 0.0}


def _phase_range(df: pd.DataFrame, climax: dict, t: dict) -> tuple[float | None, float | None]:
    """The trading range as it stood strictly BEFORE the climax —
    computed from data prior to the climax bar via v1's own
    _identify_trading_range, so the later AR/ST/SOS/LPS checks can never
    be contaminated by the very breakout bars they're trying to detect
    (which a range computed from a window ending 'now' would be)."""
    if climax["type"] == "none":
        return None, None
    from engines.wyckoff_engine import _identify_trading_range

    n = len(df)
    climax_pos = n - 1 - climax["bars_ago"]
    pre_climax = df.iloc[:climax_pos]
    if len(pre_climax) < 10:
        return None, None
    lookback = min(t.get("range_lookback", 40), len(pre_climax))
    recent_lookback = min(t.get("range_recent_lookback", 10), len(pre_climax))
    range_low, range_high, _ = _identify_trading_range(
        pre_climax, lookback=lookback,
        range_atr_max=t.get("range_atr_max", 8.0), recent_lookback=recent_lookback,
    )
    return range_low, range_high


def _find_automatic_reaction(df: pd.DataFrame, climax: dict, t: dict) -> dict:
    """After a climax, the market reflexively swings the opposite way —
    Automatic Rally (after an SC) or Automatic Reaction (after a BC).
    Searches forward from the climax bar for the first opposing swing
    point via utils.indicators.find_swings."""
    if climax["type"] == "none":
        return {"found": False, "bars_ago": -1, "price": 0.0}
    from utils.indicators import find_swings

    ar_lookback = t.get("ar_lookback", 15)
    swing_window = t.get("ar_swing_window", 2)
    n = len(df)
    climax_pos = n - 1 - climax["bars_ago"]
    search_end = min(n, climax_pos + 1 + ar_lookback + swing_window)
    if search_end - climax_pos < 2 * swing_window + 1:
        return {"found": False, "bars_ago": -1, "price": 0.0}
    segment = df.iloc[climax_pos:search_end]
    swing_high, swing_low = find_swings(segment, window=swing_window)

    mask = swing_high if climax["type"] == "sc" else swing_low
    candidates = segment.loc[mask]
    if candidates.empty:
        return {"found": False, "bars_ago": -1, "price": 0.0}
    first_ts = candidates.index[0]
    pos_in_segment = segment.index.get_loc(first_ts)
    bars_ago = n - 1 - (climax_pos + pos_in_segment)
    price = float(candidates.iloc[0]["high"] if climax["type"] == "sc" else candidates.iloc[0]["low"])
    return {"found": True, "bars_ago": bars_ago, "price": price}


def _find_secondary_test(df: pd.DataFrame, climax: dict, ar: dict, t: dict) -> dict:
    """A subsequent bar revisiting the climax's own price area WITHOUT
    closing meaningfully beyond it — confirms the demand (after an SC) or
    supply (after a BC) that formed the climax is still holding."""
    if climax["type"] == "none" or not ar["found"]:
        return {"found": False, "bars_ago": -1}
    st_lookback = t.get("st_lookback", 25)
    tolerance = t.get("st_tolerance", 0.003)
    n = len(df)
    ar_pos = n - 1 - ar["bars_ago"]
    search_end = min(n, ar_pos + 1 + st_lookback)
    if search_end <= ar_pos + 1:
        return {"found": False, "bars_ago": -1}
    segment = df.iloc[ar_pos + 1:search_end]

    for i in range(len(segment)):
        bar = segment.iloc[i]
        if climax["type"] == "sc":
            revisits = float(bar["low"]) <= climax["price"] * (1 + tolerance)
            holds = float(bar["close"]) > climax["price"] * (1 - tolerance)
        else:
            revisits = float(bar["high"]) >= climax["price"] * (1 - tolerance)
            holds = float(bar["close"]) < climax["price"] * (1 + tolerance)
        if revisits and holds:
            bars_ago = n - 1 - (ar_pos + 1 + i)
            return {"found": True, "bars_ago": bars_ago}
    return {"found": False, "bars_ago": -1}


def _find_sos_lps(df: pd.DataFrame, range_high: float, ar: dict, t: dict) -> dict:
    """Accumulation Phase D: Sign of Strength (a real breakout above
    range_high, a bar with genuine momentum, not a single wick) followed
    by a Last Point of Support (a later pullback whose low dips back
    toward the breakout but whose close holds above the old range —
    confirming new support at the former resistance)."""
    from utils.indicators import atr as compute_atr

    sos_lookback = t.get("sos_lookback", 20)
    atr_multiple = t.get("sos_breakout_atr_multiple", 1.2)
    n = len(df)
    start_pos = n - 1 - ar["bars_ago"] if ar["found"] else 0
    search_start = max(start_pos, n - sos_lookback)
    if search_start >= n:
        return {"sos_found": False, "lps_found": False}
    segment = df.iloc[search_start:]
    atr_series = compute_atr(df, 14).iloc[search_start:]

    sos_idx, sos_close = None, None
    for i in range(len(segment)):
        bar = segment.iloc[i]
        a = atr_series.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        bar_range = float(bar["high"]) - float(bar["low"])
        if float(bar["close"]) > range_high and bar_range >= a * atr_multiple:
            sos_idx, sos_close = i, float(bar["close"])
            break
    if sos_idx is None:
        return {"sos_found": False, "lps_found": False}

    for j in range(sos_idx + 1, len(segment)):
        bar = segment.iloc[j]
        if float(bar["close"]) > range_high and float(bar["low"]) < sos_close:
            return {"sos_found": True, "lps_found": True}
    return {"sos_found": True, "lps_found": False}


def _find_sow_lpsy(df: pd.DataFrame, range_low: float, ar: dict, t: dict) -> dict:
    """Distribution Phase D mirror: Sign of Weakness (a real breakdown
    below range_low) followed by a Last Point of Supply/Rally (a rally
    that fails to reclaim the broken range)."""
    from utils.indicators import atr as compute_atr

    sos_lookback = t.get("sos_lookback", 20)
    atr_multiple = t.get("sos_breakout_atr_multiple", 1.2)
    n = len(df)
    start_pos = n - 1 - ar["bars_ago"] if ar["found"] else 0
    search_start = max(start_pos, n - sos_lookback)
    if search_start >= n:
        return {"sow_found": False, "lpsy_found": False}
    segment = df.iloc[search_start:]
    atr_series = compute_atr(df, 14).iloc[search_start:]

    sow_idx, sow_close = None, None
    for i in range(len(segment)):
        bar = segment.iloc[i]
        a = atr_series.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        bar_range = float(bar["high"]) - float(bar["low"])
        if float(bar["close"]) < range_low and bar_range >= a * atr_multiple:
            sow_idx, sow_close = i, float(bar["close"])
            break
    if sow_idx is None:
        return {"sow_found": False, "lpsy_found": False}

    for j in range(sow_idx + 1, len(segment)):
        bar = segment.iloc[j]
        if float(bar["close"]) < range_low and float(bar["high"]) > sow_close:
            return {"sow_found": True, "lpsy_found": True}
    return {"sow_found": True, "lpsy_found": False}


def _composite_operator_footprint(df: pd.DataFrame, range_low: float, range_high: float, current: float, t: dict) -> tuple[float, str]:
    """Wires in v1's previously-dead _effort_vs_result(): a WIDE bar
    (high effort) that produces a WEAK net move (weak result) is
    absorption — where it happens determines whose absorption it is.
    Near the range low, that reads as demand quietly buying (bullish
    footprint); near the range high, as supply quietly selling (bearish
    footprint). Returns (score 0..1, bias)."""
    effort, result = _effort_vs_result(df, t.get("effort_lookback", 10))
    if effort != "high" or result != "weak":
        return 0.0, "neutral"

    range_span = (range_high - range_low) or 1e-10
    position_pct = (current - range_low) / range_span
    low_zone = t.get("range_position_low_pct", 0.25)
    high_zone = t.get("range_position_high_pct", 0.75)

    if position_pct <= low_zone:
        score = 1.0 if low_zone <= 0 else min(1.0, max(0.3, 1.0 - position_pct / low_zone))
        return score, "bullish"
    if position_pct >= high_zone:
        span = 1.0 - high_zone
        score = 1.0 if span <= 0 else min(1.0, max(0.3, (position_pct - high_zone) / span))
        return score, "bearish"
    return 0.0, "neutral"


def _detect_phase(df: pd.DataFrame, t: dict, v1_event: str) -> dict:
    """Reconstructs Wyckoff's A->E phase sequence from the events found
    by the helpers above. 'schematic' is only set once a climax has been
    identified — everything before that is phase NONE. Uses the STABLE
    pre-climax range from _phase_range() throughout (never the caller's
    live/current range), so a later SOS/LPS bar can never inflate the
    very threshold it's being tested against."""
    climax = _find_climax(df, t)
    events = {"sc": False, "bc": False, "ar": False, "st": False,
              "sos": False, "lps": False, "sow": False, "lpsy": False}

    if climax["type"] == "none":
        return {"phase": "NONE", "schematic": None, "events": events}

    range_low, range_high = _phase_range(df, climax, t)
    if range_low is None:
        return {"phase": "NONE", "schematic": None, "events": events}

    schematic = "accumulation" if climax["type"] == "sc" else "distribution"
    events["sc"] = climax["type"] == "sc"
    events["bc"] = climax["type"] == "bc"

    ar = _find_automatic_reaction(df, climax, t)
    events["ar"] = ar["found"]
    if not ar["found"]:
        return {"phase": "A", "schematic": schematic, "events": events}

    st = _find_secondary_test(df, climax, ar, t)
    events["st"] = st["found"]
    if not st["found"]:
        return {"phase": "B", "schematic": schematic, "events": events}

    from utils.indicators import atr as compute_atr
    atr_val = compute_atr(df, 14).iloc[-1]
    atr_val = float(atr_val) if not pd.isna(atr_val) else 0.0
    current = float(df["close"].iloc[-1])
    phase_e_mult = t.get("phase_e_atr_multiple", 1.5)

    if schematic == "accumulation":
        d = _find_sos_lps(df, range_high, ar, t)
        events["sos"], events["lps"] = d["sos_found"], d["lps_found"]
        if events["lps"]:
            phase = "E" if atr_val > 0 and (current - range_high) >= atr_val * phase_e_mult else "D"
            return {"phase": phase, "schematic": schematic, "events": events}
        if events["sos"] or v1_event == "spring":
            return {"phase": "C", "schematic": schematic, "events": events}
        return {"phase": "B", "schematic": schematic, "events": events}
    else:
        d = _find_sow_lpsy(df, range_low, ar, t)
        events["sow"], events["lpsy"] = d["sow_found"], d["lpsy_found"]
        if events["lpsy"]:
            phase = "E" if atr_val > 0 and (range_low - current) >= atr_val * phase_e_mult else "D"
            return {"phase": phase, "schematic": schematic, "events": events}
        if events["sow"] or v1_event == "upthrust":
            return {"phase": "C", "schematic": schematic, "events": events}
        return {"phase": "B", "schematic": schematic, "events": events}


def extract_features(df: pd.DataFrame, t: dict) -> dict:
    """Feature Extraction layer. `base` is v1's own extract_features()
    output — byte-identical range/spring/volume facts, REUSED not
    recomputed — with the new phase/footprint facts layered on top."""
    base = v1_extract_features(df, t)
    phase = _detect_phase(df, t, base["event"])
    footprint_score, footprint_bias = _composite_operator_footprint(
        df, base["range_low"], base["range_high"], base["current"], t,
    )
    return {**base, "phase": phase, "co_footprint_score": footprint_score, "co_footprint_bias": footprint_bias}


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer. Calls v1's decide() directly for the base
    layer — genuinely additive, not a rewrite — then layers phase-event
    and Composite-Operator-footprint bonuses on top. This can UPGRADE a
    v1-NEUTRAL result (no spring/upthrust in v1's exact tolerance window)
    when SOS+LPS / SOW+LPSY fire — this is v2's real added value over v1."""
    bias, score, reasons = v1_decide(features, t)

    phase = features["phase"]
    schematic = phase["schematic"]
    events = phase["events"]

    if schematic == "accumulation":
        if events["ar"]:
            reasons.append("Automatic Rally detected — reflexive bounce after a Selling Climax")
        if events["st"]:
            reasons.append("Secondary Test confirmed — demand held at the Selling Climax's level")
        if events["sos"]:
            score += t.get("sos_score", 30.0)
            reasons.append("Sign of Strength — real breakout above the trading range")
            if bias != Bias.BULLISH:
                bias = Bias.BULLISH
        if events["lps"]:
            score += t.get("lps_score", 20.0)
            reasons.append("Last Point of Support — pullback held above the old range, new support confirmed")
            bias = Bias.BULLISH
    elif schematic == "distribution":
        if events["ar"]:
            reasons.append("Automatic Reaction detected — reflexive drop after a Buying Climax")
        if events["st"]:
            reasons.append("Secondary Test confirmed — supply held at the Buying Climax's level")
        if events["sow"]:
            score += t.get("sow_score", 30.0)
            reasons.append("Sign of Weakness — real breakdown below the trading range")
            if bias != Bias.BEARISH:
                bias = Bias.BEARISH
        if events["lpsy"]:
            score += t.get("lpsy_score", 20.0)
            reasons.append("Last Point of Supply/Rally — rally failed to reclaim the old range, new resistance confirmed")
            bias = Bias.BEARISH

    footprint_score = features["co_footprint_score"]
    footprint_bias = features["co_footprint_bias"]
    if footprint_score > 0 and (
        (footprint_bias == "bullish" and schematic in (None, "accumulation"))
        or (footprint_bias == "bearish" and schematic in (None, "distribution"))
    ):
        contribution = t.get("co_footprint_score", 15.0) * footprint_score
        score += contribution
        if bias == Bias.NEUTRAL:
            bias = Bias.BULLISH if footprint_bias == "bullish" else Bias.BEARISH
        side = "low" if footprint_bias == "bullish" else "high"
        reasons.append(f"Composite Operator footprint: high effort/weak result near the range {side} — {footprint_bias} absorption")

    score_cap = t.get("score_cap", 85.0)
    score = min(round(score, 1), score_cap)
    return bias, score, reasons


class WyckoffEngineV2(BaseEngine):
    name = "WyckoffV2"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        min_bars = t.get("min_bars", 60)

        tf = next(
            (tfname for tfname in ["H4", "D1", "H1"] if tfname in mtf_data and len(mtf_data[tfname]) >= min_bars),
            next(iter(mtf_data)),
        )
        df = mtf_data[tf]

        if len(df) < min_bars:
            return EngineOutput(
                engine_name=self.name, bias=Bias.NEUTRAL, score=0.0,
                reasons=[f"Insufficient data for Wyckoff v2 analysis (need {min_bars}+ bars)"],
            )

        features = extract_features(df, t)
        bias, score, reasons = decide(features, t)

        raw = {
            "timeframe_used": tf,
            "trading_range": {
                "low": features["range_low"], "high": features["range_high"],
                "in_range": features["in_range"],
            },
            "event": features["event"],
            "event_strength_pct": features["strength"],
            "volume_analysis": features["vol"],
            "phase": features["phase"]["phase"],
            "schematic": features["phase"]["schematic"],
            "phase_events": features["phase"]["events"],
            "co_footprint_score": round(features["co_footprint_score"], 2),
            "co_footprint_bias": features["co_footprint_bias"],
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
