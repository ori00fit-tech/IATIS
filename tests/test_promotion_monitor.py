"""tests/test_promotion_monitor.py -- tests for backtest/promotion_
monitor.py (Hypothesis Discovery Engine, Phase 13C — Promotion Monitor)."""
from __future__ import annotations

import inspect

import pytest

from backtest import promotion_monitor as pm


def _evidence(classification: str) -> dict:
    return {"classification": classification, "reason": "test", "inputs": {}}


# --- CURRENT -----------------------------------------------------------


def test_unchanged_classification_is_current():
    result = pm.assess_evidence_staleness(
        current_evidence=_evidence("STRONG"), evidence_at_promotion=_evidence("STRONG"),
    )
    assert result["status"] == pm.CURRENT


def test_improved_classification_is_current():
    result = pm.assess_evidence_staleness(
        current_evidence=_evidence("STRONG"), evidence_at_promotion=_evidence("PROMISING"),
    )
    assert result["status"] == pm.CURRENT


# --- DEGRADED ------------------------------------------------------------


@pytest.mark.parametrize("at_promotion,current", [
    ("STRONG", "PROMISING"), ("STRONG", "MIXED"), ("STRONG", "WEAK"),
    ("PROMISING", "MIXED"), ("PROMISING", "WEAK"), ("MIXED", "WEAK"),
])
def test_dropping_to_a_lower_positive_tier_is_degraded(at_promotion, current):
    result = pm.assess_evidence_staleness(
        current_evidence=_evidence(current), evidence_at_promotion=_evidence(at_promotion),
    )
    assert result["status"] == pm.DEGRADED
    assert result["current_classification"] == current
    assert result["classification_at_promotion"] == at_promotion


# --- INVALIDATED -----------------------------------------------------------


@pytest.mark.parametrize("current", ["REJECTED", "INSUFFICIENT_EVIDENCE"])
@pytest.mark.parametrize("at_promotion", ["STRONG", "PROMISING", "MIXED", "WEAK"])
def test_current_rejected_or_insufficient_is_always_invalidated_regardless_of_promotion_tier(current, at_promotion):
    result = pm.assess_evidence_staleness(
        current_evidence=_evidence(current), evidence_at_promotion=_evidence(at_promotion),
    )
    assert result["status"] == pm.INVALIDATED


# --- structural: descriptive only, no action, no new lifecycle -------------


def _source_without_module_docstring() -> str:
    source = inspect.getsource(pm)
    return source.split('"""', 2)[-1]


def test_never_calls_revoke_or_activate_policy():
    body = _source_without_module_docstring()
    forbidden = ("revoke_policy(", "activate_policy(", "validate_policy(")
    for pattern in forbidden:
        assert pattern not in body, f"promotion_monitor unexpectedly calls {pattern!r}"


def test_no_storage_or_policy_registry_import():
    body = _source_without_module_docstring()
    forbidden = ("from storage", "import storage", "policy_registry", "d1_client")
    for pattern in forbidden:
        assert pattern not in body, f"promotion_monitor unexpectedly references {pattern!r}"


def test_no_new_lifecycle_state_invented():
    public_names = [n for n in dir(pm) if not n.startswith("_")]
    forbidden_substrings = ("paused", "downgraded")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"promotion_monitor unexpectedly exposes {name!r}"


def test_never_recomputes_significance_mission_or_robustness():
    body = _source_without_module_docstring()
    forbidden = ("classify_significance(", "classify_evidence(", "run_validation(", "run_robustness(")
    for pattern in forbidden:
        assert pattern not in body, f"promotion_monitor unexpectedly calls {pattern!r}"


def test_nothing_outside_tests_imports_promotion_monitor_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "promotion_monitor" not in text, f"{path} unexpectedly references promotion_monitor"


def test_is_deterministic_same_inputs_same_output():
    kwargs = dict(current_evidence=_evidence("WEAK"), evidence_at_promotion=_evidence("STRONG"))
    assert pm.assess_evidence_staleness(**kwargs) == pm.assess_evidence_staleness(**kwargs)
