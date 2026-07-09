"""
tests/test_axis8_and_downgrade.py
----------------------------------
Regression tests for two philosophy-audit fixes:

Axis 8 — information-aware quorum: confluence requires a SPEAKING panel.
  When most of the enabled engine weight is mute (NEUTRAL / below the
  conviction threshold), the 2-of-4 quorum must not silently degenerate
  into "the only two fed engines co-signed".

Axis 1.3 — auditable downgrades: when the regime filter or the Meta layer
  downgrades EXECUTE → NO_TRADE, the decision record must carry a
  fail_reason (previously NULL, and the summary claimed "risk gate
  rejected — All risk checks passed").
"""

from __future__ import annotations

import pandas as pd
import pytest

import main as main_mod
from engines.base_engine import Bias, EngineOutput
from confluence.voting_system import informative_weight_share
from main import _ConfluenceEval, _evaluate_confluence, _final_verdict
from storage.decision_db import log_decision_db

# Production weights (config.yaml confluence.weights)
WEIGHTS = {
    "smc": 0.202, "price_action": 0.1869, "nnfx": 0.2273,
    "wyckoff": 0.0707, "ict": 0.0657, "quant": 0.0707,
    "divergence": 0.0606, "market_structure": 0.0859,
    "sentiment": 0.0303, "macro": 0.0,
}

STARVED_PANEL = [  # the observed live failure mode
    EngineOutput("SMC", Bias.BULLISH, 60),
    EngineOutput("PriceAction", Bias.BULLISH, 70),
    EngineOutput("NNFX", Bias.NEUTRAL, 0),      # data-starved
    EngineOutput("Wyckoff", Bias.NEUTRAL, 0),   # no range event
]

HEALTHY_PANEL = [
    EngineOutput("SMC", Bias.BULLISH, 60),
    EngineOutput("PriceAction", Bias.BULLISH, 70),
    EngineOutput("NNFX", Bias.BULLISH, 80),     # trend baseline speaks
    EngineOutput("Wyckoff", Bias.NEUTRAL, 0),
]


def _pipeline_config(**confluence_overrides):
    conf = {
        "min_engines_agreeing": 2,
        "min_score_to_trade": 58,
        "min_informative_weight_share": 0.6,
        "weights": dict(WEIGHTS),
    }
    conf.update(confluence_overrides)
    return {
        "confluence": conf,
        "data": {"symbol": "EURUSD", "timeframes": ["H4", "D1", "H1"],
                 "twelve_data_symbols": []},
        "risk": {"min_risk_reward": 2.0},
    }


# ─────────────────────────── Axis 8 ───────────────────────────

def test_informative_share_math_matches_the_live_pathology():
    # SMC+PA speaking = (0.202+0.1869) / enabled 0.6869 ≈ 56.6% — the
    # exact degeneration seen live; it must sit BELOW the 0.6 gate.
    share = informative_weight_share(STARVED_PANEL, WEIGHTS)
    assert share == pytest.approx(0.3889 / 0.6869, abs=1e-3)
    assert share < 0.6

    # NNFX joining lifts the share above the gate.
    assert informative_weight_share(HEALTHY_PANEL, WEIGHTS) > 0.6


def test_mute_panel_fails_confluence_with_explicit_reason():
    conf = _evaluate_confluence(
        _pipeline_config(), STARVED_PANEL, mtf_data={},
        regime_state="TRENDING", regime_volatility="normal",
    )
    assert not conf.passed
    assert any("panel mostly mute" in r for r in conf.fail_reasons)
    assert conf.informative_weight_share < 0.6


def test_speaking_panel_passes_the_information_gate():
    conf = _evaluate_confluence(
        _pipeline_config(), HEALTHY_PANEL, mtf_data={},
        regime_state="TRENDING", regime_volatility="normal",
    )
    assert not any("panel mostly mute" in r for r in conf.fail_reasons)
    assert conf.informative_weight_share > 0.6


def test_gate_disabled_at_zero_threshold():
    conf = _evaluate_confluence(
        _pipeline_config(min_informative_weight_share=0.0), STARVED_PANEL,
        mtf_data={}, regime_state="TRENDING", regime_volatility="normal",
    )
    assert not any("panel mostly mute" in r for r in conf.fail_reasons)


def test_dissenting_votes_count_as_information():
    # Information = any effective vote, agreeing OR dissenting: a genuine
    # SMC-vs-PA disagreement with NNFX speaking is a speaking panel (it
    # fails on the quorum, not on muteness).
    panel = [
        EngineOutput("SMC", Bias.BULLISH, 60),
        EngineOutput("PriceAction", Bias.BEARISH, 70),
        EngineOutput("NNFX", Bias.BULLISH, 80),
        EngineOutput("Wyckoff", Bias.NEUTRAL, 0),
    ]
    assert informative_weight_share(panel, WEIGHTS) > 0.6


# ─────────────────── Downgrade fail_reason (Axis 1.3) ───────────────────

class _StubMQS:
    def to_dict(self):
        return {"mqs_score": 70}


def _passing_conf(symbol_cfg=None):
    """A _ConfluenceEval that passed every confluence check."""
    from confluence.voting_system import tally_votes
    from confluence.score_calculator import calculate_score
    vote = tally_votes(HEALTHY_PANEL, WEIGHTS)
    score = calculate_score(HEALTHY_PANEL, WEIGHTS, vote.winning_bias)
    return _ConfluenceEval(
        vote_result=vote, score_result=score, contradiction_result=None,
        mtf_result=None, reversal_veto=None, active_weights=WEIGHTS,
        adjusted_score=70.0, fail_reasons=[], passed=True,
        symbol_cfg=symbol_cfg or {}, informative_weight_share=0.9,
    )


def test_regime_filter_downgrade_carries_a_reason():
    verdict, meta, reason = _final_verdict(
        _pipeline_config(), _passing_conf({"regime_filter": "TRENDING"}),
        risk_pass=True, news_blocked=False, regime_state="RANGING",
        mqs_result=_StubMQS(), outputs=HEALTHY_PANEL,
    )
    assert verdict == "NO_TRADE"
    assert reason is not None and "regime filter" in reason


def test_meta_block_downgrade_carries_a_reason(monkeypatch):
    class _BlockingMeta:
        verdict = "BLOCK"
        reason = "Confidence=35% too low"
    monkeypatch.setattr(main_mod, "evaluate_meta_decision",
                        lambda **kw: _BlockingMeta())
    verdict, meta, reason = _final_verdict(
        _pipeline_config(), _passing_conf(),
        risk_pass=True, news_blocked=False, regime_state="TRENDING",
        mqs_result=_StubMQS(), outputs=HEALTHY_PANEL,
    )
    assert verdict == "NO_TRADE"
    assert reason is not None and "Meta Decision blocked" in reason


def test_downgrade_reason_persisted_as_fail_reason(fake_d1):
    report = {
        "symbol": "EURUSD",
        "final_verdict": "NO_TRADE",
        "summary": "NO_TRADE: Meta Decision blocked: Confidence=35% too low",
        "regime": {"state": "TRENDING"},
        "confluence": {"score": 70.0, "engines_participating": 3,
                       "fail_reasons": []},
        "risk": {"passed": True, "reasons": ["All risk checks passed"]},
        "downgrade_reason": "Meta Decision blocked: Confidence=35% too low",
        "engine_outputs": [],
    }
    log_decision_db(report)
    row = fake_d1.execute(
        "SELECT verdict, fail_reason FROM decisions").fetchone()
    assert row["verdict"] == "NO_TRADE"
    assert "Meta Decision blocked" in row["fail_reason"]


def test_news_blackout_persisted_as_fail_reason(fake_d1):
    report = {
        "symbol": "EURUSD",
        "final_verdict": "NO_TRADE",
        "summary": "NO_TRADE: NFP in 42 minutes",
        "regime": {"state": "TRENDING"},
        "confluence": {"score": 70.0, "engines_participating": 3,
                       "fail_reasons": []},
        "risk": {"passed": True, "reasons": ["All risk checks passed"]},
        "news": {"blackout_active": True, "blackout_reason": "NFP in 42 minutes"},
        "engine_outputs": [],
    }
    log_decision_db(report)
    row = fake_d1.execute(
        "SELECT fail_reason FROM decisions").fetchone()
    assert "News blackout: NFP in 42 minutes" in row["fail_reason"]
