"""
tests/test_promotion_criteria.py
---------------------------------
Codified promotion bar (philosophy-audit follow-up): a PASSED hypothesis
is only trusted when its evidence block clears min_trades / min_oos_pf /
walk_forward / monte_carlo. Legacy PASSED rows (H009) must be flagged,
not silently trusted — and boot must never break over it.
"""

from research.edge_gate import (
    PROMOTION_CRITERIA,
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
