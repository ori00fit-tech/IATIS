"""
engines/ict_engine.py
------------------------
ICT (Inner Circle Trader) concepts engine — Phase 3.

Implements the core ICT framework:
1. Killzone bias: directional expectation at London/NY opens
2. Premium/Discount: price position within the current dealing range
3. Judas swing: false breakout detection at session open
4. HTF bias alignment: confluence with higher timeframe structure

ICT theory: price seeks liquidity at extremes (swing highs/lows),
then reverses from premium (sell) or discount (buy) zones. The
killzones (London 07:00-09:00, NY 12:00-14:00 UTC) are when
institutional order flow is highest.

Phase 3 implementation: real logic with real data.
Phase 4 will add: OB (order blocks), FVG (fair value gaps), MSS
(market structure shift) — these require tick-level data or very
high-resolution M1 data for reliable detection.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from engines.smc_engine import find_swing_points
from regimes.session_context import SessionContext, detect_session_from_df
from utils.logger import get_logger

logger = get_logger(__name__)


def _dealing_range(df: pd.DataFrame, lookback: int = 20) -> tuple[float, float]:
    """Current dealing range: high and low of the last `lookback` bars."""
    window = df.tail(lookback)
    return float(window["low"].min()), float(window["high"].max())


def _premium_discount_zone(
    current_price: float,
    range_low: float,
    range_high: float,
    premium_pct: float = 0.60,
    discount_pct: float = 0.40,
) -> tuple[str, float]:
    """Position of price within the dealing range.

    Returns (zone, equilibrium_pct) where:
        zone = 'PREMIUM' (above 50%) | 'DISCOUNT' (below 50%) | 'EQUILIBRIUM'
        equilibrium_pct = 0.0 (range_low) to 1.0 (range_high)
    """
    if range_high == range_low:
        return "EQUILIBRIUM", 0.5

    pct = (current_price - range_low) / (range_high - range_low)
    if pct >= premium_pct:
        zone = "PREMIUM"
    elif pct <= discount_pct:
        zone = "DISCOUNT"
    else:
        zone = "EQUILIBRIUM"

    return zone, round(pct, 3)


def _detect_judas_swing(
    df: pd.DataFrame,
    session: SessionContext,
    lookback_pre_session: int = 5,
) -> tuple[bool, str]:
    """Detect a Judas swing: false breakout at session open.

    A Judas swing occurs when price briefly breaks above/below the
    pre-session range and then reverses sharply — a stop hunt before
    the real move.

    Returns (is_judas, direction) where direction is 'up' or 'down'.
    """
    if not session.is_session_open or len(df) < lookback_pre_session + 3:
        return False, "none"

    pre_session = df.iloc[-(lookback_pre_session + 3): -3]
    session_bars = df.tail(3)

    pre_high = float(pre_session["high"].max())
    pre_low = float(pre_session["low"].min())
    current_close = float(df["close"].iloc[-1])
    current_low = float(df["low"].iloc[-1])
    current_high = float(df["high"].iloc[-1])

    # Judas up: price broke above pre-session high but closed back inside
    if current_high > pre_high and current_close < pre_high:
        return True, "up"

    # Judas down: price broke below pre-session low but closed back inside
    if current_low < pre_low and current_close > pre_low:
        return True, "down"

    return False, "none"


def extract_features(mtf_data: dict[str, pd.DataFrame], t: dict) -> dict:
    """Feature Extraction layer (Confluence Engine Overhaul Phase 2) —
    session/dealing-range/zone/judas-swing/trend facts decide() needs.
    Pure function of (mtf_data, thresholds), no bias/score logic."""
    min_bars = t.get("min_bars", 30)
    tf_session = "H1" if "H1" in mtf_data else next(iter(mtf_data))
    tf_range = "H4" if "H4" in mtf_data and len(mtf_data["H4"]) >= min_bars else tf_session
    df_session = mtf_data[tf_session]
    df_range = mtf_data[tf_range]

    session = detect_session_from_df(df_session)
    current_price = float(df_session["close"].iloc[-1])

    # Dealing range on H4 (wider, more structural) — 20 H4 bars = ~3 days
    range_low, range_high = _dealing_range(df_range, lookback=t.get("dealing_range_lookback", 20))
    zone, pct = _premium_discount_zone(
        current_price, range_low, range_high,
        premium_pct=t.get("premium_pct", 0.60),
        discount_pct=t.get("discount_pct", 0.40),
    )
    is_judas, judas_dir = _detect_judas_swing(df_session, session)

    h1_df = mtf_data.get("H1", df_session)
    in_uptrend = in_downtrend = False
    trend_buffer_pct = t.get("trend_buffer_pct", 0.001)
    if len(h1_df) >= 50:
        ema20 = float(h1_df["close"].ewm(span=t.get("trend_ema_fast", 20)).mean().iloc[-1])
        ema50 = float(h1_df["close"].ewm(span=t.get("trend_ema_slow", 50)).mean().iloc[-1])
        in_uptrend = ema20 > ema50 * (1 + trend_buffer_pct)
        in_downtrend = ema20 < ema50 * (1 - trend_buffer_pct)

    return {
        "tf_session": tf_session, "tf_range": tf_range, "session": session,
        "range_low": range_low, "range_high": range_high,
        "zone": zone, "pct": pct, "is_judas": is_judas, "judas_dir": judas_dir,
        "in_uptrend": in_uptrend, "in_downtrend": in_downtrend,
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer (Confluence Engine Overhaul Phase 2) — turns
    an extract_features() snapshot into a bias/score opinion.

    ICT-SEMANTICS-FIX (Engine Refinement V1, #362 — pre-approved by the
    operator, "fix must happen now"): premium/discount zone position and
    killzone session timing are CONTEXT, not trading-event evidence — a
    price simply being in the "expensive" half of a 20-bar range, or a
    clock simply reading London-open hours, is not itself proof anything
    happened. The pre-fix version auto-set bias/score directly off zone
    position (DISCOUNT->BULLISH, PREMIUM->BEARISH) and off killzone
    timing alone, conflating context with signal — exactly the "feature
    != proven evidence weight" pattern flagged for Divergence/SMC
    full-spec elsewhere in this refinement pass.

    Fixed shape: the only REAL trading-event evidence this engine
    detects is a Judas swing (a false breakout that reverses — a genuine
    price-action event, the same class of thing as SMC's spring/upthrust
    or BOS/CHoCH). Judas swing alone now sets bias/score; zone/killzone/
    trend become CONFIRMATION-ONLY modifiers on an already-real event —
    they can strengthen or weaken confidence in a detected Judas swing,
    but can never manufacture a signal on their own. With no Judas swing,
    bias is NEUTRAL/score is 0.0 regardless of zone or killzone state.
    zone/killzone/trend remain fully observable in extract_features()'s
    output and in EngineOutput.raw either way (unchanged) — this is a
    scoring-logic fix, not an observability reduction.

    ICT is disabled by default (config/engines.yaml engines.enabled.ict:
    false) — this changes zero live-decision behavior."""
    session = features["session"]
    zone, pct = features["zone"], features["pct"]
    is_judas, judas_dir = features["is_judas"], features["judas_dir"]
    in_uptrend, in_downtrend = features["in_uptrend"], features["in_downtrend"]
    in_killzone = session.is_session_open and session.primary_session in ("London", "NewYork", "Overlap")

    reasons: list[str] = []
    score = 0.0
    bias = Bias.NEUTRAL

    # --- Context, always reported, never sets bias/score on its own ---
    if zone == "PREMIUM":
        reasons.append(f"Context: price in PREMIUM zone ({pct:.0%} of range) — not itself a signal")
    elif zone == "DISCOUNT":
        reasons.append(f"Context: price in DISCOUNT zone ({pct:.0%} of range) — not itself a signal")
    else:
        reasons.append(f"Context: price at EQUILIBRIUM ({pct:.0%})")

    if in_killzone:
        reasons.append(
            f"Context: in {session.primary_session} killzone "
            f"(session hour {session.session_hour} UTC) — not itself a signal"
        )
    else:
        reasons.append("Context: outside a recognized killzone")

    # --- Real trading-event evidence: Judas swing (false breakout + reversal) ---
    judas_base_score = t.get("judas_base_score", 45.0)
    if is_judas:
        if judas_dir == "up":
            bias = Bias.BEARISH
            score += judas_base_score
            reasons.append(
                "Judas swing UP detected — false breakout above the pre-session "
                "range, closed back inside: stop-hunt reversal, BEARISH"
            )
        elif judas_dir == "down":
            bias = Bias.BULLISH
            score += judas_base_score
            reasons.append(
                "Judas swing DOWN detected — false breakout below the pre-session "
                "range, closed back inside: stop-hunt reversal, BULLISH"
            )

        # --- Zone confirmation: does the statistical context agree? ---
        zone_confirm_score = t.get("zone_confirm_score", 20.0)
        zone_conflict_penalty = t.get("zone_conflict_penalty", 10.0)
        if bias == Bias.BEARISH and zone == "PREMIUM":
            score += zone_confirm_score
            reasons.append("Zone confirms: PREMIUM aligns with the BEARISH Judas reversal")
        elif bias == Bias.BULLISH and zone == "DISCOUNT":
            score += zone_confirm_score
            reasons.append("Zone confirms: DISCOUNT aligns with the BULLISH Judas reversal")
        elif bias == Bias.BEARISH and zone == "DISCOUNT":
            score = max(0, score - zone_conflict_penalty)
            reasons.append("Zone conflicts: DISCOUNT disagrees with the BEARISH Judas reversal — reducing confidence")
        elif bias == Bias.BULLISH and zone == "PREMIUM":
            score = max(0, score - zone_conflict_penalty)
            reasons.append("Zone conflicts: PREMIUM disagrees with the BULLISH Judas reversal — reducing confidence")

        # --- Killzone confirmation: institutional-timing reliability ---
        killzone_confirm_score = t.get("killzone_confirm_score", 15.0)
        if in_killzone:
            score += killzone_confirm_score
            reasons.append(
                f"Killzone confirms: reversal detected during {session.primary_session} "
                f"— higher institutional-flow reliability"
            )

        # --- Trend filter: penalize a reversal call fighting the H1 trend ---
        trend_conflict_penalty = t.get("trend_conflict_penalty", 10.0)
        if bias == Bias.BEARISH and in_uptrend:
            score = max(0, score - trend_conflict_penalty)
            reasons.append("H1 uptrend active — countertrend reversal call, reducing confidence")
        elif bias == Bias.BULLISH and in_downtrend:
            score = max(0, score - trend_conflict_penalty)
            reasons.append("H1 downtrend active — countertrend reversal call, reducing confidence")
    else:
        reasons.append("No Judas swing detected — no ICT trading-event evidence this bar")

    # cap score
    score = min(round(score, 1), t.get("score_cap", 80.0))

    if score < t.get("score_neutral_floor", 20.0):
        bias = Bias.NEUTRAL

    return bias, score, reasons


class ICTEngine(BaseEngine):
    """ICT methodology engine — killzones, premium/discount, judas swing."""

    name = "ICT"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        min_bars = t.get("min_bars", 30)

        # Use H1 for session/killzone (intraday timing)
        tf_session = "H1" if "H1" in mtf_data else next(iter(mtf_data))
        if len(mtf_data[tf_session]) < min_bars:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data for ICT analysis"],
            )

        features = extract_features(mtf_data, t)
        bias, score, reasons = decide(features, t)

        session = features["session"]
        # JSON-safe copy for EngineOutput.features — decide() above needs
        # the real SessionContext object's attributes, but a Feature
        # Extraction snapshot meant for storage/analysis should be plain
        # primitives, matching this codebase's Feature Mining convention.
        output_features = {
            **features,
            "session": {
                "primary_session": session.primary_session,
                "active_sessions": session.active_sessions,
                "is_overlap": session.is_overlap,
                "is_session_open": session.is_session_open,
                "session_hour": session.session_hour,
                "volatility_expectation": session.volatility_expectation,
            },
        }

        # BUG_FIX (#362, same pass as the zone/killzone semantics fix
        # below): is_killzone previously reported session.is_session_open
        # alone, which is True for ANY major session's first two hours
        # (including Asia) — not the London/NewYork/Overlap definition
        # this engine's own module docstring and decide()'s real killzone
        # check both use. Corrected to the same definition decide() now
        # uses, so raw["is_killzone"] can no longer misreport True during
        # an Asia-session open.
        in_killzone = session.is_session_open and session.primary_session in ("London", "NewYork", "Overlap")

        raw = {
            "timeframe_session": features["tf_session"],
            "timeframe_range": features["tf_range"],
            "session": session.primary_session,
            "active_sessions": session.active_sessions,
            "is_killzone": in_killzone,
            "zone": features["zone"],
            "zone_pct": features["pct"],
            "dealing_range": {"low": features["range_low"], "high": features["range_high"]},
            "judas_swing": features["judas_dir"] if features["is_judas"] else "none",
            # ICT-SEMANTICS-FIX (#362): explicit context/event separation
            # in raw, mirroring decide()'s own fix — premium/discount zone
            # and killzone timing are CONTEXT (never a signal on their
            # own); Judas swing is the only real trading-event evidence
            # this engine detects. Additive grouping — every key above is
            # unchanged/preserved.
            "context": {
                "zone": features["zone"],
                "zone_pct": features["pct"],
                "is_killzone": in_killzone,
                "session": session.primary_session,
                "in_uptrend": features["in_uptrend"],
                "in_downtrend": features["in_downtrend"],
            },
            "event": {
                "judas_swing": features["judas_dir"] if features["is_judas"] else "none",
            },
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=output_features,
        )
