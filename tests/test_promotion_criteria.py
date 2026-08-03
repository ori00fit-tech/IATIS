"""
tests/test_promotion_criteria.py
---------------------------------
Codified promotion bar (philosophy-audit follow-up): a PASSED hypothesis
is only trusted when its evidence block clears min_trades / min_oos_pf /
walk_forward / monte_carlo. Legacy PASSED rows (H009) must be flagged,
not silently trusted — and boot must never break over it.
"""

import pytest

from research.edge_gate import (
    PROMOTION_CRITERIA,
    EdgeNotProvenError,
    audit_passed_hypotheses,
    check_edge_gate,
)


def test_passed_without_evidence_is_flagged():
    warnings = audit_passed_hypotheses({
        "H009": {"status": "PASSED", "notes": "legacy walk-forward"},
    })
    assert len(warnings) == 1
    assert "H009" in warnings[0]
    assert "treat as RESEARCH" in warnings[0]


def test_passed_with_qualifying_evidence_is_trusted():
    warnings = audit_passed_hypotheses({
        "H099": {"status": "PASSED", "evidence": {
            "oos_trades": 350, "oos_pf": 1.31,
            "walk_forward": True, "monte_carlo": True,
        }},
    })
    assert warnings == []


def test_each_missing_bar_is_named():
    warnings = audit_passed_hypotheses({
        "H098": {"status": "PASSED", "evidence": {
            "oos_trades": 120, "oos_pf": 1.05,
            "walk_forward": True, "monte_carlo": False,
        }},
    })
    assert len(warnings) == 1
    w = warnings[0]
    assert f"< {PROMOTION_CRITERIA['min_trades']}" in w
    assert f"< {PROMOTION_CRITERIA['min_oos_pf']}" in w
    assert "monte_carlo" in w
    assert "walk_forward" not in w


def test_research_status_is_not_audited():
    assert audit_passed_hypotheses({
        "H010": {"status": "RESEARCH"},
        "H001": {"status": "FAILED"},
    }) == []


def test_real_registry_flags_h009_and_boot_survives():
    # The live registry's only PASSED entry (H009) predates the codified
    # bar — the audit must flag it, and check_edge_gate must still pass
    # for the production engine set (non-fatal by design).
    from research.edge_gate import _load_registry
    warnings = audit_passed_hypotheses(_load_registry().get("hypotheses", {}))
    assert any("H009" in w for w in warnings)
    check_edge_gate({"smc": True, "price_action": True,
                     "nnfx": True, "wyckoff": True})  # must not raise


# ── Live-capital governance hardening (Forensic Audit, 2026-08-04) ─────────
# check_edge_gate() previously treated PASSED and RESEARCH identically no
# matter what — the only thing standing between a RESEARCH-status engine
# and real capital was the completely independent allow_live_trading flag
# in execution/trade_executor.py, with zero cross-check. These tests pin
# the fix: allow_live_trading=True now demands a genuinely PASSED,
# evidence-qualifying hypothesis per enabled engine.

def test_allow_live_trading_false_is_unaffected_by_research_status():
    """Regression pin: the default (current production) config keeps
    working exactly as before — RESEARCH-status prod4 engines are fine
    when allow_live_trading is False (or omitted)."""
    check_edge_gate({"smc": True, "price_action": True,
                     "nnfx": True, "wyckoff": True})
    check_edge_gate({"smc": True, "price_action": True,
                     "nnfx": True, "wyckoff": True}, allow_live_trading=False)


def test_allow_live_trading_true_blocks_research_status_engine(monkeypatch):
    """The authoritative proof: flipping allow_live_trading to True must
    refuse to let a RESEARCH-status engine (H101, SMC's real hypothesis)
    pass the gate — this is the exact scenario that was previously
    silently allowed."""
    import research.edge_gate as eg

    monkeypatch.setattr(eg, "_load_registry", lambda: {
        "hypotheses": {"H101": {"status": "RESEARCH"}}
    })
    with pytest.raises(EdgeNotProvenError, match="paper/demo-only"):
        check_edge_gate({"smc": True}, allow_live_trading=True)


def test_allow_live_trading_true_blocks_passed_without_qualifying_evidence(monkeypatch):
    """A PASSED status alone isn't enough for real capital either — it
    must clear the same codified promotion bar audit_passed_hypotheses()
    already checks (min_trades/min_oos_pf/walk_forward/monte_carlo)."""
    import research.edge_gate as eg

    monkeypatch.setattr(eg, "_load_registry", lambda: {
        "hypotheses": {"H101": {"status": "PASSED", "notes": "legacy, no evidence block"}}
    })
    with pytest.raises(EdgeNotProvenError, match="without qualifying evidence"):
        check_edge_gate({"smc": True}, allow_live_trading=True)


def test_allow_live_trading_true_permits_genuinely_qualifying_passed_engine(monkeypatch):
    """The positive case: a PASSED hypothesis WITH a qualifying evidence
    block must clear the gate under allow_live_trading=True — this isn't
    a blanket ban on live trading, only on unproven engines."""
    import research.edge_gate as eg

    monkeypatch.setattr(eg, "_load_registry", lambda: {
        "hypotheses": {"H101": {"status": "PASSED", "evidence": {
            "oos_trades": 350, "oos_pf": 1.31,
            "walk_forward": True, "monte_carlo": True,
        }}}
    })
    check_edge_gate({"smc": True}, allow_live_trading=True)  # must not raise
