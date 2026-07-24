"""tests/test_crypto_positioning_modulator.py — H019's pure modulation
logic. Fully unit-testable without market data (compute_funding_zscore
and crypto_positioning_penalty are pure functions); the causal
look-ahead guard itself is the A/B harness's responsibility, not tested
here (this module has no timestamps to check)."""
from __future__ import annotations

from confluence.crypto_positioning_modulator import (
    FEAR_GREED_EXTREME_HIGH,
    FEAR_GREED_EXTREME_LOW,
    MAX_PENALTY,
    Z_SCORE_THRESHOLD,
    compute_funding_zscore,
    crypto_positioning_penalty,
)


# ── compute_funding_zscore ───────────────────────────────────────────────

def test_zscore_none_with_insufficient_history():
    assert compute_funding_zscore([0.0001] * 5, 0.001) is None  # < MIN_HISTORY_FOR_ZSCORE


def test_zscore_none_when_history_is_constant():
    assert compute_funding_zscore([0.0001] * 20, 0.0005) is None  # stdev == 0


def test_zscore_zero_for_the_mean():
    history = [-0.001, 0.0, 0.001] * 5
    z = compute_funding_zscore(history, sum(history) / len(history))
    assert abs(z) < 1e-9


def test_zscore_positive_for_above_mean_rate():
    history = [0.0] * 20
    # nonzero stdev needed — mix in a little variance
    history = [x * 0.0001 for x in range(-10, 10)]
    z = compute_funding_zscore(history, 0.01)  # way above this history's range
    assert z > 0


def test_zscore_negative_for_below_mean_rate():
    history = [x * 0.0001 for x in range(-10, 10)]
    z = compute_funding_zscore(history, -0.01)
    assert z < 0


# ── crypto_positioning_penalty ───────────────────────────────────────────

def test_no_penalty_with_no_zscore():
    r = crypto_positioning_penalty(None, 50, "BULLISH")
    assert r.score_adjustment == 0.0
    assert "insufficient" in r.reason


def test_no_penalty_with_invalid_bias():
    r = crypto_positioning_penalty(2.0, 50, "NEUTRAL")
    assert r.score_adjustment == 0.0


def test_no_penalty_when_funding_not_extreme():
    r = crypto_positioning_penalty(0.5, 50, "BULLISH")
    assert r.score_adjustment == 0.0
    assert "not extreme" in r.reason


def test_crowded_long_penalizes_bullish_trade():
    r = crypto_positioning_penalty(2.0, None, "BULLISH")
    assert r.score_adjustment < 0.0
    assert "crowded-long" in r.reason


def test_crowded_long_does_not_penalize_bearish_trade():
    """The squeeze risk from crowded longs is against a BULLISH trade —
    a BEARISH trade isn't exposed to it."""
    r = crypto_positioning_penalty(2.0, None, "BEARISH")
    assert r.score_adjustment == 0.0
    assert "no squeeze risk" in r.reason


def test_crowded_short_penalizes_bearish_trade():
    r = crypto_positioning_penalty(-2.0, None, "BEARISH")
    assert r.score_adjustment < 0.0
    assert "crowded-short" in r.reason


def test_crowded_short_does_not_penalize_bullish_trade():
    r = crypto_positioning_penalty(-2.0, None, "BULLISH")
    assert r.score_adjustment == 0.0


def test_penalty_never_exceeds_max():
    r = crypto_positioning_penalty(100.0, FEAR_GREED_EXTREME_HIGH, "BULLISH")
    assert abs(r.score_adjustment) <= MAX_PENALTY


def test_penalty_never_positive():
    """This modulator can only ever cost a trade, never help one — pin
    the one-directional constraint directly."""
    for z in [-50, -5, -Z_SCORE_THRESHOLD - 0.01, Z_SCORE_THRESHOLD + 0.01, 5, 50]:
        for bias in ("BULLISH", "BEARISH"):
            for fg in (None, 0, 25, 50, 75, 100):
                r = crypto_positioning_penalty(z, fg, bias)
                assert r.score_adjustment <= 0.0


def test_fear_greed_amplifies_matching_extreme():
    baseline = crypto_positioning_penalty(2.0, None, "BULLISH")
    amplified = crypto_positioning_penalty(2.0, FEAR_GREED_EXTREME_HIGH, "BULLISH")
    assert amplified.score_adjustment < baseline.score_adjustment  # more negative
    assert "extreme greed" in amplified.reason


def test_fear_greed_does_not_amplify_opposite_extreme():
    """Extreme FEAR alongside a crowded-LONG squeeze isn't the confirming
    case (H019: F&G only scales when it confirms the SAME extreme)."""
    baseline = crypto_positioning_penalty(2.0, None, "BULLISH")
    opposite = crypto_positioning_penalty(2.0, FEAR_GREED_EXTREME_LOW, "BULLISH")
    assert opposite.score_adjustment == baseline.score_adjustment


def test_fear_greed_amplifies_crowded_short_with_extreme_fear():
    baseline = crypto_positioning_penalty(-2.0, None, "BEARISH")
    amplified = crypto_positioning_penalty(-2.0, FEAR_GREED_EXTREME_LOW, "BEARISH")
    assert amplified.score_adjustment < baseline.score_adjustment
    assert "extreme fear" in amplified.reason


def test_penalty_magnitude_scales_with_zscore_extremity():
    small = crypto_positioning_penalty(Z_SCORE_THRESHOLD + 0.1, None, "BULLISH")
    large = crypto_positioning_penalty(Z_SCORE_THRESHOLD * 3, None, "BULLISH")
    assert abs(large.score_adjustment) > abs(small.score_adjustment)


def test_neutral_fear_greed_value_does_not_amplify():
    baseline = crypto_positioning_penalty(2.0, None, "BULLISH")
    neutral_fg = crypto_positioning_penalty(2.0, 50, "BULLISH")
    assert neutral_fg.score_adjustment == baseline.score_adjustment
