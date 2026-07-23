"""
tests/test_research_layer.py
---------------------------------
Tests for the research layer additions: the edge gate (blocks unproven
engines from being enabled) and the decision log (records every
EXECUTE/NO_TRADE with reasons).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.edge_gate import EdgeNotProvenError, check_edge_gate
from storage.decision_log import filter_decisions, log_decision, read_decisions, summarize_decisions


# ---------- edge gate ----------

def test_edge_gate_allows_smc_and_price_action_via_research_hypotheses():
    # smc (H101) and price_action (H102) are RESEARCH-status hypotheses as
    # of 2026-07-23 (governance closure, docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md
    # P1-1) — no longer a bare EXEMPT_ENGINES bypass, but still allowed.
    check_edge_gate({"smc": True, "price_action": True})  # should not raise


def test_edge_gate_allows_all_disabled():
    check_edge_gate({"smc": False, "ict": False, "nnfx": False, "quant": False})  # should not raise


def test_edge_gate_blocks_unproven_engine(monkeypatch):
    # any engine with None mapping — use a fake engine name
    import research.edge_gate as eg
    monkeypatch.setattr(eg, "ENGINE_HYPOTHESIS_MAP", {"fake_engine": None})
    with pytest.raises(EdgeNotProvenError):
        check_edge_gate({"fake_engine": True})


def test_edge_gate_allows_research_status_engine():
    # ICT/NNFX/Quant now have RESEARCH status — allowed for paper trading
    # This should NOT raise — RESEARCH is in ALLOWED_STATUSES
    check_edge_gate({"ict": True, "nnfx": True, "quant": True})


def test_edge_gate_blocks_pending_hypothesis(monkeypatch):
    # PENDING is not in ALLOWED_STATUSES — must be blocked
    import research.edge_gate as edge_gate_module
    monkeypatch.setattr(edge_gate_module, "ENGINE_HYPOTHESIS_MAP", {"nnfx": "H002"})
    with pytest.raises(EdgeNotProvenError):
        check_edge_gate({"nnfx": True})


def test_edge_gate_blocks_failed_hypothesis_just_as_strictly_as_pending(monkeypatch):
    """Regression-style check for the real H001 result: a hypothesis
    that has been tested and FAILED must be blocked exactly as strictly
    as one that's still PENDING. FAILED is not "good enough" — only
    PASSED unlocks an engine. This is checked against a temporary
    mapping (not the real ENGINE_HYPOTHESIS_MAP) so the test doesn't
    silently stop testing anything if engine wiring changes later.
    """
    import research.edge_gate as edge_gate_module

    monkeypatch.setattr(edge_gate_module, "ENGINE_HYPOTHESIS_MAP", {"nnfx": "H001"})

    registry = edge_gate_module._load_registry()
    h001_status = registry.get("hypotheses", {}).get("H001", {}).get("status")
    assert h001_status == "FAILED", (
        "This test assumes H001's real status is FAILED (per the actual "
        "2026-06-21 experiment run on real EURUSD data). If H001's status "
        "changes, update this assertion intentionally — don't let it pass "
        "silently against a different status."
    )

    with pytest.raises(EdgeNotProvenError, match="FAILED"):
        check_edge_gate({"nnfx": True})


# ---------- decision log ----------

@pytest.fixture
def tmp_log_path(tmp_path):
    return tmp_path / "decisions_test.jsonl"


def test_log_decision_writes_entry(tmp_log_path):
    report = {"final_verdict": "NO_TRADE", "symbol": "EURUSD"}
    log_decision(report, path=tmp_log_path)

    assert tmp_log_path.exists()
    decisions = read_decisions(tmp_log_path)
    assert len(decisions) == 1
    assert decisions[0]["final_verdict"] == "NO_TRADE"


def test_log_decision_appends(tmp_log_path):
    log_decision({"final_verdict": "NO_TRADE", "symbol": "EURUSD"}, path=tmp_log_path)
    log_decision({"final_verdict": "EXECUTE", "symbol": "EURUSD"}, path=tmp_log_path)

    decisions = read_decisions(tmp_log_path)
    assert len(decisions) == 2
    assert decisions[0]["final_verdict"] == "NO_TRADE"
    assert decisions[1]["final_verdict"] == "EXECUTE"


def test_read_decisions_empty_when_no_file(tmp_path):
    missing_path = tmp_path / "does_not_exist.jsonl"
    assert read_decisions(missing_path) == []


def test_summarize_decisions_counts_correctly(tmp_log_path):
    log_decision({"final_verdict": "EXECUTE", "symbol": "EURUSD"}, path=tmp_log_path)
    log_decision(
        {
            "final_verdict": "NO_TRADE",
            "symbol": "EURUSD",
            "confluence": {"fail_reasons": ["Only 1 engine(s) agree, minimum required is 3"]},
        },
        path=tmp_log_path,
    )
    log_decision(
        {
            "final_verdict": "NO_TRADE",
            "symbol": "EURUSD",
            "confluence": {"fail_reasons": ["Only 1 engine(s) agree, minimum required is 3"]},
        },
        path=tmp_log_path,
    )

    summary = summarize_decisions(tmp_log_path)
    assert summary["total"] == 3
    assert summary["execute"] == 1
    assert summary["no_trade"] == 2
    assert summary["no_trade_reasons"]["Only 1 engine(s) agree, minimum required is 3"] == 2


def test_summarize_decisions_handles_validation_failure_reason(tmp_log_path):
    log_decision({"final_verdict": "NO_TRADE", "reason": "Data validation failed: bad bars"}, path=tmp_log_path)
    summary = summarize_decisions(tmp_log_path)
    assert summary["no_trade_reasons"]["Data validation failed: bad bars"] == 1


# ---------- filter_decisions (Decision Explorer, module 7) ----------

def _entry(**overrides):
    base = {
        "timestamp": "2026-07-10T12:00:00+00:00",
        "final_verdict": "NO_TRADE",
        "symbol": "EURUSD",
        "report": {
            "confluence": {"score": 40.0, "fail_reasons": ["Only 1 engine(s) agree"]},
            "engine_outputs": [{"engine": "smc", "bias": "BEARISH", "score": 40}],
            "risk": {"passed": False, "reasons": ["RR below minimum"]},
        },
    }
    base.update(overrides)
    return base


def test_filter_decisions_by_symbol():
    decisions = [_entry(symbol="EURUSD"), _entry(symbol="XAUUSD")]
    assert [d["symbol"] for d in filter_decisions(decisions, symbol="xauusd")] == ["XAUUSD"]


def test_filter_decisions_by_date_range():
    decisions = [
        _entry(timestamp="2026-07-01T00:00:00+00:00"),
        _entry(timestamp="2026-07-10T00:00:00+00:00"),
        _entry(timestamp="2026-07-20T00:00:00+00:00"),
    ]
    out = filter_decisions(decisions, date_from="2026-07-05", date_to="2026-07-15")
    assert [d["timestamp"] for d in out] == ["2026-07-10T00:00:00+00:00"]


def test_filter_decisions_by_engine_case_insensitive():
    decisions = [
        _entry(report={"engine_outputs": [{"engine": "SMC"}]}),
        _entry(report={"engine_outputs": [{"engine": "nnfx"}]}),
    ]
    out = filter_decisions(decisions, engine="smc")
    assert len(out) == 1
    assert out[0]["report"]["engine_outputs"][0]["engine"] == "SMC"


def test_filter_decisions_by_min_score():
    decisions = [
        _entry(report={"confluence": {"score": 40.0}}),
        _entry(report={"confluence": {"score": 70.0}}),
    ]
    out = filter_decisions(decisions, min_score=60)
    assert len(out) == 1 and out[0]["report"]["confluence"]["score"] == 70.0


def test_filter_decisions_risk_rejected_only():
    decisions = [
        _entry(report={"risk": {"passed": False, "reasons": ["RR below minimum"]}}),
        _entry(report={"risk": {"passed": True, "reasons": []}}),
    ]
    out = filter_decisions(decisions, risk_rejected=True)
    assert len(out) == 1 and out[0]["report"]["risk"]["passed"] is False


def test_filter_decisions_by_reason_substring():
    decisions = [
        _entry(report={"confluence": {"fail_reasons": ["Only 1 engine(s) agree"]}}),
        _entry(report={"confluence": {"fail_reasons": ["News blackout window"]}}),
    ]
    out = filter_decisions(decisions, reason="blackout")
    assert len(out) == 1
    assert "News blackout window" in out[0]["report"]["confluence"]["fail_reasons"]


def test_filter_decisions_combines_filters_with_and():
    decisions = [
        _entry(symbol="EURUSD", report={"confluence": {"score": 70.0}}),
        _entry(symbol="EURUSD", report={"confluence": {"score": 20.0}}),
        _entry(symbol="XAUUSD", report={"confluence": {"score": 80.0}}),
    ]
    out = filter_decisions(decisions, symbol="EURUSD", min_score=50)
    assert len(out) == 1
    assert out[0]["symbol"] == "EURUSD" and out[0]["report"]["confluence"]["score"] == 70.0


def test_filter_decisions_no_filters_returns_all():
    decisions = [_entry(), _entry()]
    assert filter_decisions(decisions) == decisions
