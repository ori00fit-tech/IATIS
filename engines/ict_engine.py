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
    current_price: float, range_low: float, range_high: float
) -> tuple[str, float]:
    """Position of price within the dealing range.

    Returns (zone, equilibrium_pct) where:
        zone = 'PREMIUM' (above 50%) | 'DISCOUNT' (below 50%) | 'EQUILIBRIUM'
        equilibrium_pct = 0.0 (range_low) to 1.0 (range_high)
    """
    if range_high == range_low:
        return "EQUILIBRIUM", 0.5

    pct = (current_price - range_low) / (range_high - range_low)
    if pct >= 0.60:
        zone = "PREMIUM"
    elif pct <= 0.40:
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


class ICTEngine(BaseEngine):
    """ICT methodology engine — killzones, premium/discount, judas swing."""

    name = "ICT"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        # Use H1 for session/killzone (intraday timing)
        # Use H4 for dealing range (wider structural context)
        tf_session = "H1" if "H1" in mtf_data else next(iter(mtf_data))
        tf_range = "H4" if "H4" in mtf_data and len(mtf_data["H4"]) >= 30 else tf_session
        df_session = mtf_data[tf_session]
        df_range = mtf_data[tf_range]

        if len(df_session) < 30:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=["Insufficient data for ICT analysis"],
            )

        session = detect_session_from_df(df_session)
        current_price = float(df_session["close"].iloc[-1])

        # Dealing range on H4 (wider, more structural) — 20 H4 bars = ~3 days
        range_low, range_high = _dealing_range(df_range, lookback=20)
        zone, pct = _premium_discount_zone(current_price, range_low, range_high)
        is_judas, judas_dir = _detect_judas_swing(df_session, session)

        reasons = []
        score = 0.0
        bias = Bias.NEUTRAL

        # --- Premium/Discount zone bias (with trend filter) ---
        # ICT: sell from premium, buy from discount — BUT only in non-trending markets
        h1_df = mtf_data.get("H1", df_session)
        in_uptrend = in_downtrend = False
        if len(h1_df) >= 50:
            ema20 = float(h1_df["close"].ewm(span=20).mean().iloc[-1])
            ema50 = float(h1_df["close"].ewm(span=50).mean().iloc[-1])
            in_uptrend = ema20 > ema50 * 1.001    # 0.1% buffer
            in_downtrend = ema20 < ema50 * 0.999

        if zone == "DISCOUNT":
            # Buy from discount only if not in a strong downtrend
            if not in_downtrend:
                bias = Bias.BULLISH
                score += 35.0
                reasons.append(
                    f"Price in DISCOUNT zone ({pct:.0%} of range) — "
                    f"ICT expects bullish move toward equilibrium"
                )
            else:
                # Downtrend: discount is not a reversal signal, stay neutral
                reasons.append(
                    f"Price in DISCOUNT zone ({pct:.0%}) but H1 downtrend active — "
                    f"no reversal bias (trend filter)"
                )
        elif zone == "PREMIUM":
            # Sell from premium only if not in a strong uptrend
            if not in_uptrend:
                bias = Bias.BEARISH
                score += 35.0
                reasons.append(
                    f"Price in PREMIUM zone ({pct:.0%} of range) — "
                    f"ICT expects bearish move toward equilibrium"
                )
            else:
                reasons.append(
                    f"Price in PREMIUM zone ({pct:.0%}) but H1 uptrend active — "
                    f"no reversal bias (trend filter)"
                )
        else:
            reasons.append(f"Price at EQUILIBRIUM ({pct:.0%}) — no zone bias")

        # --- Killzone bonus ---
        if session.is_session_open and session.primary_session in ("London", "NewYork", "Overlap"):
            score += 20.0
            reasons.append(
                f"In {session.primary_session} killzone "
                f"(session hour {session.session_hour} UTC)"
            )

        # --- Judas swing confirmation ---
        if is_judas:
            if judas_dir == "up" and bias == Bias.BEARISH:
                score += 20.0
                reasons.append(
                    "Judas swing UP detected — false breakout above range, "
                    "confirms BEARISH reversal"
                )
            elif judas_dir == "down" and bias == Bias.BULLISH:
                score += 20.0
                reasons.append(
                    "Judas swing DOWN detected — false breakout below range, "
                    "confirms BULLISH reversal"
                )
            else:
                reasons.append(
                    f"Judas swing {judas_dir.upper()} detected but "
                    f"conflicts with zone bias — reducing confidence"
                )
                score = max(0, score - 10)

        # cap score
        score = min(round(score, 1), 80.0)

        if score < 20:
            bias = Bias.NEUTRAL

        raw = {
            "timeframe_session": tf_session,
            "timeframe_range": tf_range,
            "session": session.primary_session,
            "active_sessions": session.active_sessions,
            "is_killzone": session.is_session_open,
            "zone": zone,
            "zone_pct": pct,
            "dealing_range": {"low": range_low, "high": range_high},
            "judas_swing": judas_dir if is_judas else "none",
        }

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
        )
