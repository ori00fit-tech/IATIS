"""
tests/test_axis6_consistency.py
--------------------------------
Regression tests for the Axis-6 unification (philosophy audit addendum):
one majority definition (weighted conviction, tally_votes), one conviction
threshold applied identically in the vote and the score, and no
tie-break — a dead heat is no information.

These lock in the fix for the observed live discontinuity where two
near-identical engine configurations produced cf_score 39 vs 80 (BTC vs
ETH class of jump).
"""

import pytest

from engines.base_engine import Bias, EngineOutput
from confluence.voting_system import MIN_CONVICTION_SCORE, tally_votes
from confluence.score_calculator import calculate_score

WEIGHTS = {
    "smc": 0.202, "price_action": 0.1869, "nnfx": 0.2273,
    "wyckoff": 0.0707, "ict": 0.0657, "quant": 0.0707,
    "divergence": 0.0606, "market_structure": 0.0859,
    "sentiment": 0.0303, "macro": 0.0,
}


def _score_for(outputs):
    vote = tally_votes(outputs, WEIGHTS)
    return vote, calculate_score(outputs, WEIGHTS, vote.winning_bias)


def test_score_always_describes_the_vote_winner():
    # Old bug: with a 1-1 count tie, score_calculator reported whichever
    # side had the higher RAW average — even when weighted conviction (the
    # verdict direction) chose the other side. Wyckoff bearish 85 has the
    # higher raw score, but PriceAction bullish 80 carries more conviction
    # (0.1869*80 = 14.95 > 0.0707*85 = 6.01).
    outputs = [
        EngineOutput("PriceAction", Bias.BULLISH, 80),
        EngineOutput("Wyckoff", Bias.BEARISH, 85),
    ]
    vote, score = _score_for(outputs)
    assert vote.winning_bias == Bias.BULLISH
    assert score.final_score == pytest.approx(80.0)      # bull side, not 85
    assert score.directional_score == pytest.approx(80.0)  # sign matches verdict


def test_one_point_nudge_cannot_flip_the_reported_side():
    # The BTC-39 vs ETH-80 case: SMC bearish at 45 vs 39 must not change
    # which side the score describes — conviction picks PriceAction's side
    # in both configurations, so both must report the same score.
    def run(smc_score):
        outputs = [
            EngineOutput("SMC", Bias.BEARISH, smc_score),
            EngineOutput("PriceAction", Bias.BULLISH, 80),
        ]
        return _score_for(outputs)

    vote_a, score_a = run(45)
    vote_b, score_b = run(39)
    assert vote_a.winning_bias == vote_b.winning_bias == Bias.BULLISH
    assert score_a.final_score == score_b.final_score == pytest.approx(80.0)


def test_conviction_threshold_applied_identically_in_vote_and_score():
    # An engine below MIN_CONVICTION_SCORE is NEUTRAL for the quorum AND
    # excluded from the score/contributions (previously it still steered
    # the score).
    weak = MIN_CONVICTION_SCORE - 1
    outputs = [
        EngineOutput("PriceAction", Bias.BULLISH, 80),
        EngineOutput("SMC", Bias.BULLISH, weak),
    ]
    vote, score = _score_for(outputs)
    assert vote.agree_count == 1                          # weak vote is mute
    assert score.engines_participating == 1
    assert score.final_score == pytest.approx(80.0)       # not avg(80, weak)
    assert score.contributions["SMC"] == 0.0              # no silent steering
    assert score.participating_weight_share == pytest.approx(
        WEIGHTS["price_action"] / sum(WEIGHTS.values()), abs=1e-3
    )


def test_exact_conviction_tie_is_no_information():
    # Equal weight, equal score, opposite sides: the old fallback declared
    # BULLISH; a dead heat must be NEUTRAL with score 0.
    outputs = [
        EngineOutput("SMC", Bias.BULLISH, 60),
        EngineOutput("SMC", Bias.BEARISH, 60),
    ]
    vote, score = _score_for(outputs)
    assert vote.winning_bias == Bias.NEUTRAL
    assert vote.agree_count == 0
    assert score.final_score == 0.0
    assert score.directional_score == 0.0


def test_omitted_winning_bias_derives_the_same_majority():
    # Backward-compatible call sites (no winning_bias argument) must get
    # the same single majority definition, not a second one.
    outputs = [
        EngineOutput("PriceAction", Bias.BULLISH, 80),
        EngineOutput("Wyckoff", Bias.BEARISH, 85),
    ]
    vote = tally_votes(outputs, WEIGHTS)
    explicit = calculate_score(outputs, WEIGHTS, vote.winning_bias)
    derived = calculate_score(outputs, WEIGHTS)
    assert derived.final_score == explicit.final_score
    assert derived.directional_score == explicit.directional_score
    assert derived.engines_participating == explicit.engines_participating
