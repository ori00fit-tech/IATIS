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
from storage.decision_log import log_decision, read_decisions, summarize_decisions


# ---------- edge gate ----------

def test_edge_gate_allows_exempt_engines():
    # smc and price_action are plain technical reads, always allowed
    check_edge_gate({"smc": True, "price_action": True})  # should not raise


def test_edge_gate_allows_all_disabled():
    check_edge_gate({"smc": False, "ict": False, "nnfx": False, "quant": False})  # should not raise


def test_edge_gate_blocks_unproven_engine():
    with pytest.raises(EdgeNotProvenError):
        check_edge_gate({"ict": True})


def test_edge_gate_blocks_pending_hypothesis():
    # H001 exists in the registry but its status is PENDING, not PASSED.
    # No engine currently maps to it, but this documents the intended
    # behavior once smc_advanced is wired to ENGINE_HYPOTHESIS_MAP.
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
