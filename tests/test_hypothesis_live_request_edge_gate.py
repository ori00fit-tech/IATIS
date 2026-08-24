"""tests/test_hypothesis_live_request_edge_gate.py -- the operator's own
required REAL (non-mocked) proof for Phase 8C's Point 6: the Edge Gate
(research.edge_gate.check_edge_gate(), called as the first statement
inside main.build_active_engines(), itself called inside the REAL, UNMOCKED
main.run_pipeline()) is never bypassed, caught, or silently absorbed by
backtest.hypothesis_live_request.evaluate_live_identity_request().

Design (traced directly from main.py, research/edge_gate.py, and
tests/test_replay.py's own proven injected-data pattern -- nothing here is
guessed):

  - Every engine in research/edge_gate.py's ENGINE_HYPOTHESIS_MAP currently
    has registry status "RESEARCH", never "PASSED" (module comments, and
    directly re-verified below by reading the real, unmodified
    research/results/registry.json). check_edge_gate()'s allow_live_trading
    branch raises EdgeNotProvenError for ANY status other than "PASSED" --
    so config["execution"]["allow_live_trading"]=True plus any real bundle
    engine (wyckoff here) triggers a genuine, registry-unmodified
    EdgeNotProvenError. registry.json is never written to produce this
    failure -- it is READ-ONLY throughout this test.
  - main.run_pipeline() is called for REAL (no monkeypatch on
    "main.run_pipeline" anywhere in this file, unlike every other Phase 8C
    integration test) -- data.source="injected" + a real core.data_loader.
    load_synthetic() frame (the exact mechanism tests/test_replay.py
    already proves works end-to-end through the real pipeline) satisfies
    _load_market_data()/validate_ohlcv() so the run reaches build_active_
    engines() at all, and features.market_quality_gate=False skips the MQS
    gate's should_trade branch without needing perfectly market-realistic
    data.
  - check_edge_gate() runs BEFORE any engine's own safe_analyze() -- so
    this test never depends on wyckoff's own internal logic succeeding on
    synthetic data, only on the gate itself firing first.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest import hypothesis_factory as hf
from backtest import hypothesis_live_request as hlr
from research.edge_gate import ENGINE_HYPOTHESIS_MAP, EdgeNotProvenError
from storage import hypothesis_factory as hf_storage
from storage import hypothesis_live_request as hlr_storage

REGISTRY_PATH = Path("research/results/registry.json")


def _real_registry_confirms_wyckoff_is_not_passed() -> None:
    """Re-verifies, against the REAL repo file (never mutated by this
    test), the precondition the whole test design depends on: wyckoff's
    backing hypothesis is not PASSED, so allow_live_trading=True must
    reject it. If this ever stops being true (wyckoff genuinely earns
    PASSED status), this test's own precondition check fails loudly here
    rather than the test silently passing for the wrong reason."""
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)
    hyp_id = ENGINE_HYPOTHESIS_MAP["wyckoff"]
    status = registry.get("hypotheses", {}).get(hyp_id, {}).get("status")
    assert status != "PASSED", (
        f"precondition violated: {hyp_id} (wyckoff) is now PASSED in the real registry -- "
        f"this test's design (an unavoidable EdgeNotProvenError under allow_live_trading=True) "
        f"no longer holds and must be redesigned against a different engine."
    )


def _persist_single_engine_hypothesis() -> hf.Hypothesis:
    h = hf.generate_hypotheses(
        symbols=["EURUSD"], engines=["wyckoff"], timeframes=["H4"],
        risk_presets=["balanced"], engine_versions={"wyckoff": ("v2",)},
    )[0]
    hf_storage.record_hypotheses([h])
    return h


def _edge_gate_config() -> dict:
    from core.data_loader import load_synthetic

    return {
        "engines": {"enabled": {}, "versions": {}},
        "data": {
            "symbol": "EURUSD", "timeframes": ["H4"],
            "source": "injected",
            "_injected_df": load_synthetic(bars=400, timeframe="H4", seed=42),
            "twelve_data_symbols": [],
        },
        "risk": {
            "starting_balance": 10000.0, "max_drawdown_reduce": 0.1, "max_drawdown_stop": 0.15,
            "max_exposure": 0.05, "min_risk_reward": 3.0, "risk_per_trade_max": 0.01,
            "risk_per_trade_min": 0.0025, "sl_atr_multiplier": 2.5,
            "pretrade_limits": {"enabled": True},
        },
        "confluence": {"min_score_to_trade": 0, "min_engines_agreeing": 1},
        "execution": {"allow_live_trading": True},
        "features": {"market_quality_gate": False},
        "system": {"replay_mode": True, "_replay_now": "2025-01-07T14:00:00+00:00"},
    }


def test_edge_gate_precondition_holds_against_the_real_registry():
    _real_registry_confirms_wyckoff_is_not_passed()


def test_real_unmocked_run_pipeline_propagates_edge_not_proven_error():
    """The core proof: NOT mocked (no monkeypatch on main.run_pipeline
    anywhere in this file) -- the real pipeline runs for real, reaches
    the real build_active_engines(), and the real check_edge_gate()
    raises. Never converted to PROCEED, never absorbed into a NO_TRADE-
    shaped result -- the exception itself reaches this test."""
    _real_registry_confirms_wyckoff_is_not_passed()
    h = _persist_single_engine_hypothesis()

    with pytest.raises(EdgeNotProvenError, match="wyckoff"):
        hlr.evaluate_live_identity_request(h.hypothesis_id, _edge_gate_config())


def test_edge_not_proven_error_never_recorded_as_a_fake_decision():
    """The exception is raised before evaluate_live_identity_request()
    ever reaches its own storage.hypothesis_live_request.record_live_
    identity_request() call -- proven here by checking that NOTHING was
    persisted for this hypothesis, i.e. the failure was never laundered
    into a stored PROCEED/NO_TRADE-looking audit record."""
    h = _persist_single_engine_hypothesis()
    with pytest.raises(EdgeNotProvenError):
        hlr.evaluate_live_identity_request(h.hypothesis_id, _edge_gate_config())
    assert hlr_storage.list_live_identity_requests_for_hypothesis(h.hypothesis_id) == []


def test_registry_json_is_never_modified_by_this_test():
    before = REGISTRY_PATH.read_bytes()
    h = _persist_single_engine_hypothesis()
    with pytest.raises(EdgeNotProvenError):
        hlr.evaluate_live_identity_request(h.hypothesis_id, _edge_gate_config())
    after = REGISTRY_PATH.read_bytes()
    assert before == after, "research/results/registry.json was modified -- forbidden by the operator's own hard constraint"
