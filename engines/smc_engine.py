"""
engines/smc_engine.py
------------------------
Smart Money Concepts engine.

Phase 1 implements real swing-point detection and a basic structural bias
(are we making higher-highs/higher-lows or lower-highs/lower-lows). This is
the foundation SMC builds on.

Full-spec components (order blocks, fair value gaps, BOS/CHoCH) are now
implemented as CAUSAL, INTERNAL confluence inputs behind the
`engines.smc_full_spec` config flag (default false — live behavior is
unchanged until an A/B backtest justifies flipping it; see registry H017
and scripts/smc_fullspec_ab.py).

Evidence guardrails baked into the design:
  - H001/H002/H008c measured that these concepts have NO standalone entry
    edge (pooled OOS WR 0.489, p=0.83). They are therefore wired only as
    score modulators inside SMC's structural bias — never as entries.
  - All detectors are causal: a pattern at bar i is only usable once every
    bar it references has closed; detections on a prefix of the data never
    change when later bars arrive (locked by tests/test_smc_fullspec.py).
  - Liquidity-sweep detection stays unimplemented on purpose — it FAILED
    three hypotheses (H001/H002/H002b); building it would code a measured
    negative.
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.logger import get_logger

logger = get_logger(__name__)


def find_swing_points(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """Identify swing highs/lows: a bar whose high/low is the max/min
    within +/- `window` bars on either side.

    Returns a DataFrame with boolean columns 'swing_high' and 'swing_low'
    aligned to df's index. Thin wrapper over utils.indicators.find_swings
    (the canonical implementation, unified here Confluence Engine Overhaul
    Phase 1 — this function's name/return shape is kept for backward
    compatibility since ict_engine/wyckoff_engine import it directly).
    """
    from utils.indicators import find_swings

    swing_high, swing_low = find_swings(df, window=window)
    return pd.DataFrame({"swing_high": swing_high, "swing_low": swing_low})


def structural_bias(
    df: pd.DataFrame,
    window: int = 3,
    lookback: int = 6,
    base_score_max: float = 65.0,
    mixed_score: float = 20.0,
) -> tuple[Bias, float, list[str]]:
    """Determine directional bias from the sequence of recent swing highs/lows.

    Uses majority vote over the last `lookback` swing points rather than
    comparing only the last two. This makes the bias more robust to
    short-term noise — a single counter-swing doesn't flip the bias.

    Scoring:
        score = (agreeing_pairs / total_pairs) * 65
        e.g. 5/5 pairs agreeing → score=65 (strong)
             3/5 pairs agreeing → score=39 (weak, may not pass threshold)

    HH + HL majority → BULLISH
    LH + LL majority → BEARISH
    Mixed / insufficient → NEUTRAL
    """
    swings = find_swing_points(df, window=window)
    swing_highs = df["high"][swings["swing_high"]].tail(lookback)
    swing_lows = df["low"][swings["swing_low"]].tail(lookback)

    reasons = []

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return Bias.NEUTRAL, 0.0, ["Not enough swing points to determine structure"]

    # count consecutive pairs that are rising vs falling
    def _count_direction(series):
        rising = falling = 0
        vals = list(series)
        for i in range(1, len(vals)):
            if vals[i] > vals[i - 1]:
                rising += 1
            elif vals[i] < vals[i - 1]:
                falling += 1
        return rising, falling

    highs_rising, highs_falling = _count_direction(swing_highs)
    lows_rising, lows_falling = _count_direction(swing_lows)

    total_pairs = len(swing_highs) - 1 + len(swing_lows) - 1
    bullish_pairs = highs_rising + lows_rising
    bearish_pairs = highs_falling + lows_falling

    if total_pairs == 0:
        return Bias.NEUTRAL, 0.0, ["Not enough swing pairs to vote"]

    bull_ratio = bullish_pairs / total_pairs
    bear_ratio = bearish_pairs / total_pairs

    if bull_ratio > 0.5:
        score = round(bull_ratio * base_score_max, 1)
        reasons.append(
            f"Bullish structure: {bullish_pairs}/{total_pairs} swing pairs rising "
            f"(HH+HL majority)"
        )
        return Bias.BULLISH, score, reasons

    if bear_ratio > 0.5:
        score = round(bear_ratio * base_score_max, 1)
        reasons.append(
            f"Bearish structure: {bearish_pairs}/{total_pairs} swing pairs falling "
            f"(LH+LL majority)"
        )
        return Bias.BEARISH, score, reasons

    reasons.append(
        f"Mixed structure: {bullish_pairs} bullish vs {bearish_pairs} bearish pairs "
        f"out of {total_pairs} — no clear majority"
    )
    return Bias.NEUTRAL, mixed_score, reasons


# ---------------------------------------------------------------------------
# Full-spec components (flag-gated). All causal: bar i only references bars
# <= i, and swing-based logic uses only swings already confirmed by `window`
# subsequent closed bars (the centered rolling in find_swing_points cannot
# mark a swing until its confirmation bars exist).
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    # range_atr, NOT true ATR — deliberate simplified variant, see
    # utils/indicators.py. The floor fallback is SMC-specific.
    from utils.indicators import range_atr
    val = range_atr(df, period)
    return val if val > 0 else float(df["close"].iloc[-1]) * 0.001


def detect_fair_value_gaps(df: pd.DataFrame, lookback: int = 30) -> dict:
    """Most recent UNFILLED fair value gap within `lookback` closed bars.

    Bullish FVG at bar i: low[i] > high[i-2] (3-candle imbalance).
    Bearish FVG at bar i: high[i] < low[i-2].
    A gap is 'filled' once any later bar trades back through its far edge.
    Direction context: an unfilled FVG marks displacement — continuation
    pressure in its direction.
    """
    n = len(df)
    if n < 5:
        return {"direction": "none"}
    highs, lows = df["high"].values, df["low"].values
    start = max(2, n - lookback)
    latest: dict = {"direction": "none"}
    for i in range(start, n):
        if lows[i] > highs[i - 2]:      # bullish gap [high[i-2], low[i]]
            gap_lo, gap_hi = highs[i - 2], lows[i]
            filled = any(lows[j] <= gap_lo for j in range(i + 1, n))
            if not filled:
                latest = {"direction": "bullish", "top": float(gap_hi),
                          "bottom": float(gap_lo), "bar_index": i}
        elif highs[i] < lows[i - 2]:    # bearish gap [high[i], low[i-2]]
            gap_lo, gap_hi = highs[i], lows[i - 2]
            filled = any(highs[j] >= gap_hi for j in range(i + 1, n))
            if not filled:
                latest = {"direction": "bearish", "top": float(gap_hi),
                          "bottom": float(gap_lo), "bar_index": i}
    return latest


def detect_order_blocks(df: pd.DataFrame, lookback: int = 30,
                        displacement_atr: float = 1.0) -> dict:
    """Most recent order block whose zone still holds.

    Bullish OB: the last down-close candle immediately before an upward
    displacement (next-2-bar close gain > displacement_atr × ATR14); zone =
    that candle's [low, high]. Context is bullish while the latest close
    holds at/above the zone bottom. Mirror for bearish.
    """
    n = len(df)
    if n < 6:
        return {"direction": "none"}
    o, c = df["open"].values, df["close"].values
    h, l = df["high"].values, df["low"].values
    atr = _atr(df)
    close_now = float(c[-1])
    latest: dict = {"direction": "none"}
    for i in range(max(1, n - lookback), n - 2):
        move = c[i + 2] - c[i]
        if c[i] < o[i] and move > displacement_atr * atr:      # bullish OB
            if close_now >= l[i]:                              # zone respected
                latest = {"direction": "bullish", "top": float(h[i]),
                          "bottom": float(l[i]), "bar_index": i}
        elif c[i] > o[i] and -move > displacement_atr * atr:   # bearish OB
            if close_now <= h[i]:
                latest = {"direction": "bearish", "top": float(h[i]),
                          "bottom": float(l[i]), "bar_index": i}
    return latest


def detect_bos_choch(df: pd.DataFrame, window: int = 3) -> dict:
    """Break of Structure / Change of Character from CONFIRMED swings.

    Latest close above the last confirmed swing high → bullish break;
    below the last confirmed swing low → bearish break. It is a BOS when
    it continues the prevailing structural bias, a CHoCH when it flips it.
    """
    swings = find_swing_points(df, window=window)
    sh = df["high"][swings["swing_high"]]
    sl = df["low"][swings["swing_low"]]
    if len(sh) < 1 or len(sl) < 1:
        return {"event": "none", "direction": "none"}
    prior_bias, _, _ = structural_bias(df.iloc[:-1], window=window)
    close_now = float(df["close"].iloc[-1])
    last_high, last_low = float(sh.iloc[-1]), float(sl.iloc[-1])
    if close_now > last_high:
        event = "BOS" if prior_bias == Bias.BULLISH else "CHoCH"
        return {"event": event, "direction": "bullish",
                "level": last_high}
    if close_now < last_low:
        event = "BOS" if prior_bias == Bias.BEARISH else "CHoCH"
        return {"event": event, "direction": "bearish",
                "level": last_low}
    return {"event": "none", "direction": "none"}


class SMCEngine(BaseEngine):
    name = "SMC"

    # Set from config (engines.smc_full_spec) by main.build_active_engines.
    # Default False: live behavior identical to the Phase-1 engine until an
    # A/B backtest justifies the flip (registry H017).
    full_spec: bool = False

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        swing_window = t.get("swing_window", 3)
        lookback = t.get("lookback", 6)

        # Use the highest available timeframe for structural bias (more reliable
        # than the lowest timeframe, consistent with SMC's "HTF bias first" principle)
        tf = self._pick_timeframe(mtf_data)
        df = mtf_data[tf]

        bias, score, reasons = structural_bias(
            df, window=swing_window, lookback=lookback,
        )

        if not self.full_spec:
            raw = {
                "timeframe_used": tf,
                # Implemented behind engines.smc_full_spec (default off) —
                # markers kept so downstream consumers see the flag state.
                "order_blocks": "DISABLED_BY_FLAG_smc_full_spec",
                "fvg": "DISABLED_BY_FLAG_smc_full_spec",
                "bos_choch": "DISABLED_BY_FLAG_smc_full_spec",
                "liquidity_zones": "NOT_IMPLEMENTED (H001/H002/H002b FAILED — deliberate)",
            }
            return EngineOutput(engine_name=self.name, bias=bias, score=score,
                                reasons=reasons, raw=raw)

        # --- Full-spec confluence (score modulation, never an entry) ---
        fvg = detect_fair_value_gaps(df, lookback=t.get("fvg_lookback", 30))
        ob = detect_order_blocks(
            df,
            lookback=t.get("order_block_lookback", 30),
            displacement_atr=t.get("order_block_displacement_atr", 1.0),
        )
        bos = detect_bos_choch(df, window=t.get("bos_choch_window", 3))
        components = {"fvg": fvg.get("direction", "none"),
                      "order_block": ob.get("direction", "none"),
                      "bos_choch": bos.get("direction", "none")}
        component_weights = {
            "bos_choch": t.get("component_weight_bos_choch", 12.0),
            "fvg": t.get("component_weight_fvg", 8.0),
            "order_block": t.get("component_weight_order_block", 8.0),
        }
        full_spec_score_cap = t.get("full_spec_score_cap", 85.0)
        full_spec_neutral_floor = t.get("full_spec_neutral_floor", 20.0)
        full_spec_mixed_lean_score = t.get("full_spec_mixed_lean_score", 28.0)

        if bias != Bias.NEUTRAL:
            side = "bullish" if bias == Bias.BULLISH else "bearish"
            other = "bearish" if side == "bullish" else "bullish"
            for name, direction in components.items():
                w = component_weights[name]
                if direction == side:
                    score = min(score + w, full_spec_score_cap)
                    reasons.append(f"{name} aligns {side} (+{w:.0f})")
                elif direction == other:
                    score = max(score - w, 0.0)
                    reasons.append(f"{name} opposes structure (-{w:.0f})")
            if score < full_spec_neutral_floor:
                # Modulation drove conviction below the vote threshold —
                # an abstain, consistent with voting_system's cliff.
                bias = Bias.NEUTRAL
                reasons.append("Full-spec opposition reduced conviction below threshold — NEUTRAL")
        else:
            # No structural majority: 2+ agreeing components give a weak,
            # honest lean (score 28 — above the conviction threshold, below
            # any solo-carry level).
            for side, b in (("bullish", Bias.BULLISH), ("bearish", Bias.BEARISH)):
                agreeing = [n for n, d in components.items() if d == side]
                if len(agreeing) >= 2:
                    bias, score = b, full_spec_mixed_lean_score
                    reasons.append(
                        f"Structure mixed but {'+'.join(agreeing)} agree {side} — weak {side} lean"
                    )
                    break

        raw = {
            "timeframe_used": tf,
            "full_spec": True,
            "order_blocks": ob,
            "fvg": fvg,
            "bos_choch": bos,
            "liquidity_zones": "NOT_IMPLEMENTED (H001/H002/H002b FAILED — deliberate)",
        }
        return EngineOutput(engine_name=self.name, bias=bias, score=round(score, 1),
                            reasons=reasons, raw=raw)

    def _pick_timeframe(self, mtf_data: dict[str, pd.DataFrame]) -> str:
        """Pick the highest timeframe that has enough bars for reliable
        swing-point detection (minimum 100 bars after the rolling window
        consumes its lookback period).

        Why not always use H4: on Twelve Data Free plan, H4 is resampled
        from 500 H1 bars → only ~125 H4 bars. With the 7-bar rolling
        window in find_swing_points(), 125 bars is borderline and often
        produces zero detected swings ('Not enough swing points').
        H1 with 500 bars is far more reliable in this case.
        """
        MIN_BARS = self.thresholds.get("pick_tf_min_bars", 100)
        preference = ["H4", "D1", "H1", "M15"]
        if self.decision_tf == "D1":
            # Decision-on-D1 mode: D1 is fetched natively (500 bars, not a
            # thin resample), and SMC's own "HTF bias first" principle puts
            # it ahead of H4. The MIN_BARS guard below still applies.
            preference = ["D1", "H4", "H1", "M15"]
        # first pass: prefer higher TFs that have enough bars
        for tf in preference:
            if tf in mtf_data and len(mtf_data[tf]) >= MIN_BARS:
                return tf
        # fallback: whatever has the most bars
        return max(mtf_data.keys(), key=lambda tf: len(mtf_data.get(tf, [])),
                   default=next(iter(mtf_data)))
