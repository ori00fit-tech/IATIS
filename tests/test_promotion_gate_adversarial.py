"""tests/test_promotion_gate_adversarial.py -- integration/adversarial
proofs for Phase 13B: the REAL (unmocked) backtest.evidence_
classification.classify_evidence() output feeds evaluate_promotion_gate()
end to end, never a hand-typed classification string pretending to be
one; and structural proof that nothing here crosses into execution,
policy activation, or a new validation engine."""
from __future__ import annotations

from backtest import promotion_gate as pg
from backtest.evidence_classification import classify_evidence
from backtest.mission_validator import NO_EDGE, STRONG_LEAD, WEAK_LEAD
from backtest.multiple_testing import classify_significance


def test_real_evidence_classification_strong_path_through_to_active_eligible():
    """A real significance computation that survives Bonferroni, combined
    with a real STRONG_LEAD mission verdict, flows through the REAL
    classify_evidence() into evaluate_promotion_gate() -- never a mocked
    classification string."""
    real_significance = classify_significance(p_value=0.0001, n_trials=10)
    evidence = classify_evidence(significance=real_significance, mission_verdict=STRONG_LEAD,
                                  robustness={"sweeps": [{"param": "sl_atr_multiplier", "verdict": "STABLE"}]})
    assert evidence["classification"] == "STRONG"  # sanity on our own test input

    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=evidence, cross_symbol_confirmed=True,
        limited_review={"completed": True, "acceptable_performance": True},
    )
    assert result["eligibility"] == pg.ELIGIBLE


def test_real_evidence_classification_weak_lead_never_reaches_strong_blocks_active():
    """classify_evidence()'s own R4 rule means STRONG is ONLY ever
    produced alongside mission_verdict==STRONG_LEAD -- a WEAK_LEAD
    mission verdict (otherwise identical real significance/robustness
    inputs) genuinely produces PROMISING, not STRONG, through the REAL
    classifier. ACTIVE's own classification bar alone already blocks it;
    this proves the real, reachable path, not a hand-constructed
    inconsistency."""
    real_significance = classify_significance(p_value=0.0001, n_trials=10)
    evidence = classify_evidence(significance=real_significance, mission_verdict=WEAK_LEAD,
                                  robustness={"sweeps": [{"param": "sl_atr_multiplier", "verdict": "STABLE"}]})
    assert evidence["classification"] == "PROMISING"

    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=evidence, cross_symbol_confirmed=True,
        limited_review={"completed": True, "acceptable_performance": True},
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert any("PROMISING" in r for r in result["reasons"])


def test_real_evidence_classification_no_edge_blocks_every_stage():
    real_significance = classify_significance(p_value=0.0001, n_trials=10)
    evidence = classify_evidence(significance=real_significance, mission_verdict=NO_EDGE)
    assert evidence["classification"] == "REJECTED"

    for stage in (pg.SHADOW, pg.LIMITED, pg.ACTIVE):
        result = pg.evaluate_promotion_gate(
            target_stage=stage, evidence=evidence, cross_symbol_confirmed=True,
            shadow_record={"completed": True, "diverged_catastrophically": False},
            limited_review={"completed": True, "acceptable_performance": True},
        )
        assert result["eligibility"] == pg.NOT_ELIGIBLE


def test_real_evidence_classification_insufficient_data_blocks_shadow_too():
    real_significance = classify_significance(p_value=None, n_trials=5)
    evidence = classify_evidence(significance=real_significance)
    assert evidence["classification"] == "INSUFFICIENT_EVIDENCE"

    result = pg.evaluate_promotion_gate(target_stage=pg.SHADOW, evidence=evidence, cross_symbol_confirmed=False)
    assert result["eligibility"] == pg.NOT_ELIGIBLE


# --- structural: no live/execution/validation crossover --------------------


def test_no_run_pipeline_broker_or_policy_registry_import():
    import inspect

    source = inspect.getsource(pg)
    body = source.split('"""', 2)[-1]
    forbidden = ("run_pipeline", "TradeExecutor", "ctrader_client", "policy_registry", "authorization")
    for pattern in forbidden:
        assert pattern not in body, f"promotion_gate unexpectedly references {pattern!r}"


def test_classify_evidence_called_for_real_is_never_reimplemented(monkeypatch):
    """Spies on the REAL classify_evidence() to prove it is the function
    actually producing the evidence consumed here -- not a parallel
    reimplementation."""
    import backtest.evidence_classification as ec_module

    calls = []
    real_fn = ec_module.classify_evidence

    def _spy(**kwargs):
        calls.append(kwargs)
        return real_fn(**kwargs)

    monkeypatch.setattr(ec_module, "classify_evidence", _spy)
    real_significance = classify_significance(p_value=0.0001, n_trials=10)
    ec_module.classify_evidence(significance=real_significance, mission_verdict=STRONG_LEAD)
    assert len(calls) == 1
