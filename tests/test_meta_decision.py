"""tests/test_meta_decision.py"""
from __future__ import annotations
import pytest
from engines.base_engine import Bias, EngineOutput
from confluence.meta_decision import (
    evaluate_meta_decision, _engine_contribution,
    _stability_score, _confidence_score, MetaDecision
)


def _make_output(name: str, bias: str, score: float) -> EngineOutput:
    b = {"BULLISH": Bias.BULLISH, "BEARISH": Bias.BEARISH, "NEUTRAL": Bias.NEUTRAL}[bias]
    return EngineOutput(engine_name=name, bias=b, score=score)


def _mock_vote(bias="BEARISH", agree=5):
    class V:
        winning_bias = Bias.BEARISH if bias == "BEARISH" else Bias.BULLISH
        agree_count = agree
    return V()


WEIGHTS = {
    "smc": 0.20, "price_action": 0.185, "nnfx": 0.225,
    "ict": 0.065, "quant": 0.07, "wyckoff": 0.04,
    "divergence": 0.1, "market_structure": 0.085, "sentiment": 0.03,
}


def test_high_confidence_execute():
    outputs = [
        _make_output("SMC", "BEARISH", 80),
        _make_output("PriceAction", "BEARISH", 75),
        _make_output("NNFX", "BEARISH", 70),
        _make_output("Quant", "BEARISH", 65),
        _make_output("ICT", "NEUTRAL", 0),
        _make_output("Wyckoff", "NEUTRAL", 0),
    ]
    result = evaluate_meta_decision(outputs, WEIGHTS, 78.0, _mock_vote("BEARISH", 4))
    assert result.verdict == "EXECUTE"
    assert result.confidence >= 50
    assert result.position_multiplier > 0


def test_low_confidence_block():
    outputs = [
        _make_output("SMC", "BEARISH", 30),
        _make_output("PriceAction", "BULLISH", 60),
        _make_output("NNFX", "BULLISH", 55),
        _make_output("Quant", "BEARISH", 25),
        _make_output("Divergence", "BULLISH", 50),
    ]
    result = evaluate_meta_decision(outputs, WEIGHTS, 56.0, _mock_vote("BEARISH", 2))
    # Low agreement, high contradiction → should reduce or block
    assert result.position_multiplier <= 0.75
    assert result.confidence < 70


def test_engine_contributions_positive_for_agreeing():
    outputs = [
        _make_output("NNFX", "BEARISH", 70),
        _make_output("SMC", "BULLISH", 60),  # disagrees
    ]
    contribs = _engine_contribution(outputs, WEIGHTS, 65.0, Bias.BEARISH)
    assert contribs["nnfx"] > 0
    assert contribs["smc"] < 0


def test_engine_contributions_zero_for_neutral():
    outputs = [_make_output("ICT", "NEUTRAL", 0)]
    contribs = _engine_contribution(outputs, WEIGHTS, 65.0, Bias.BEARISH)
    assert contribs["ict"] == 0.0


def test_stability_high_when_all_agree():
    outputs = [
        _make_output("SMC", "BEARISH", 80),
        _make_output("NNFX", "BEARISH", 70),
        _make_output("PriceAction", "BEARISH", 75),
        _make_output("Quant", "BEARISH", 60),
    ]
    stab = _stability_score(outputs, Bias.BEARISH)
    assert stab >= 80


def test_stability_low_when_mixed():
    outputs = [
        _make_output("SMC", "BEARISH", 80),
        _make_output("NNFX", "BULLISH", 70),
        _make_output("Divergence", "BULLISH", 60),
    ]
    stab = _stability_score(outputs, Bias.BEARISH)
    assert stab < 60


def test_dominant_engine_identified():
    outputs = [
        _make_output("SMC", "BEARISH", 30),
        _make_output("NNFX", "BEARISH", 90),  # highest agreeing score
    ]
    result = evaluate_meta_decision(outputs, WEIGHTS, 70.0, _mock_vote("BEARISH", 2))
    assert result.dominant_engine == "nnfx"


def test_to_dict_has_required_keys():
    outputs = [_make_output("SMC", "BEARISH", 60)]
    result = evaluate_meta_decision(outputs, WEIGHTS, 65.0, _mock_vote("BEARISH", 1))
    d = result.to_dict()
    for key in ["confidence", "stability", "verdict", "position_multiplier",
                "engine_contributions", "uncertainty_flags", "reason"]:
        assert key in d


def test_uncertainty_flags_with_few_agreeing():
    outputs = [
        _make_output("SMC", "BEARISH", 45),
        _make_output("NNFX", "BULLISH", 60),
        _make_output("PriceAction", "NEUTRAL", 0),
    ]
    result = evaluate_meta_decision(outputs, WEIGHTS, 56.0, _mock_vote("BEARISH", 1))
    assert any("FEW_AGREEING" in f or "LOW" in f for f in result.uncertainty_flags)


def test_position_multiplier_range():
    outputs = [_make_output("SMC", "BEARISH", 50)]
    result = evaluate_meta_decision(outputs, WEIGHTS, 60.0, _mock_vote("BEARISH", 1))
    assert 0.0 <= result.position_multiplier <= 1.0
