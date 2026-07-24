"""
confluence/crypto_positioning_modulator.py
----------------------------------------------
H019 (research/results/registry.json — "Crypto positioning/sentiment as an
internal confluence modulator", feasibility resolved 2026-07-24) — a causal
score modulator for BTCUSD/ETHUSD only, using:

  - Funding rate as a crowding/squeeze proxy: extreme positive funding
    means the market is crowded long (everyone paying to stay long) —
    that's squeeze risk AGAINST a bullish trade, not for it. Extreme
    negative funding is the mirror case for a bearish trade.
  - Fear & Greed strictly as a REGIME-SCALING context input, never a
    directional signal on its own (H019's own registered scope) — it only
    amplifies the funding-rate penalty when it confirms the same extreme
    (e.g. extreme greed alongside crowded-long).

Behind engines.crypto_positioning_modulator (default FALSE per H019's
architecture notes, following the H017 precedent) — inert unless
explicitly enabled for a backtest A/B, and even then only ever applied to
BTCUSD/ETHUSD (never a standalone entry signal; never fires for any other
symbol).

Deliberately ONE-DIRECTIONAL: this can only ever REDUCE a score (a squeeze-
risk penalty), never boost one — consistent with the "never a standalone
entry signal" constraint. Every function here is pure and causal: callers
are responsible for passing only funding-rate/Fear-Greed values with a
timestamp strictly BEFORE the decision bar's close (H019's registered
look-ahead guard) — this module has no notion of "now" and cannot enforce
that guard itself, it only computes on whatever history it's given.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

Z_SCORE_THRESHOLD = 1.5   # |z| beyond this counts as "extreme" funding
MAX_PENALTY = 10.0        # same scale as confluence/mtf_confirmation.py's +/-8
MIN_HISTORY_FOR_ZSCORE = 10  # too little history to size a distribution meaningfully

# alternative.me's own classification boundaries.
FEAR_GREED_EXTREME_LOW = 25   # <=24 is "Extreme Fear"
FEAR_GREED_EXTREME_HIGH = 75  # >=76 is "Extreme Greed"
FEAR_GREED_AMPLIFY_SCALE = 1.5


@dataclass
class PositioningModulatorResult:
    score_adjustment: float  # always <= 0.0
    reason: str
    funding_z_score: float | None
    fear_greed_value: int | None


def compute_funding_zscore(prior_funding_rates: list[float], current_rate: float) -> float | None:
    """z-score of `current_rate` against `prior_funding_rates` — the
    caller must ensure every value in `prior_funding_rates` has a
    settlement timestamp strictly before `current_rate`'s (this function
    has no timestamps to check that itself). None if there isn't enough
    history to size a meaningful distribution, or the history is constant
    (zero stdev — nothing to compare against)."""
    if len(prior_funding_rates) < MIN_HISTORY_FOR_ZSCORE:
        return None
    mean = statistics.mean(prior_funding_rates)
    stdev = statistics.pstdev(prior_funding_rates)
    if stdev == 0:
        return None
    return (current_rate - mean) / stdev


def crypto_positioning_penalty(
    funding_z: float | None,
    fear_greed_value: int | None,
    winning_bias: str,
) -> PositioningModulatorResult:
    """The actual modulation logic — pure, no I/O, fully unit-testable
    without any market data. Returns a score_adjustment that is always
    <= 0.0: this is a squeeze-risk penalty on an already-decided
    direction, never a reason to trade, never a boost."""
    if funding_z is None or winning_bias not in ("BULLISH", "BEARISH"):
        return PositioningModulatorResult(0.0, "insufficient data", funding_z, fear_greed_value)

    crowded_long = funding_z > Z_SCORE_THRESHOLD
    crowded_short = funding_z < -Z_SCORE_THRESHOLD

    if not (crowded_long or crowded_short):
        return PositioningModulatorResult(
            0.0, f"funding z={funding_z:.2f} not extreme", funding_z, fear_greed_value,
        )
    if crowded_long and winning_bias != "BULLISH":
        return PositioningModulatorResult(
            0.0, "crowded-long but trade is not BULLISH — no squeeze risk to this trade",
            funding_z, fear_greed_value,
        )
    if crowded_short and winning_bias != "BEARISH":
        return PositioningModulatorResult(
            0.0, "crowded-short but trade is not BEARISH — no squeeze risk to this trade",
            funding_z, fear_greed_value,
        )

    # Base penalty scales with how far past the threshold the z-score is,
    # capped at MAX_PENALTY at 2x the threshold or beyond.
    base = min(abs(funding_z) / (Z_SCORE_THRESHOLD * 2), 1.0) * MAX_PENALTY

    scale = 1.0
    fg_reason = ""
    if fear_greed_value is not None:
        if crowded_long and fear_greed_value >= FEAR_GREED_EXTREME_HIGH:
            scale = FEAR_GREED_AMPLIFY_SCALE
            fg_reason = f", amplified by extreme greed (F&G={fear_greed_value})"
        elif crowded_short and fear_greed_value <= FEAR_GREED_EXTREME_LOW:
            scale = FEAR_GREED_AMPLIFY_SCALE
            fg_reason = f", amplified by extreme fear (F&G={fear_greed_value})"

    penalty = -round(min(base * scale, MAX_PENALTY), 1)
    direction = "long" if crowded_long else "short"
    reason = f"crowded-{direction} squeeze risk (funding z={funding_z:.2f}){fg_reason}"
    return PositioningModulatorResult(penalty, reason, funding_z, fear_greed_value)
