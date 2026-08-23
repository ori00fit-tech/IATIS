"""tests/test_hypothesis_promotion.py -- pure decision-table / structural
tests for backtest/hypothesis_promotion.py (Hypothesis Discovery Engine,
Phase 5 — Symbol-Scoped Promotion Gate)."""
from __future__ import annotations

import inspect

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_promotion as hp


def _cell(status: str, **overrides) -> dict:
    base = {
        "status": status, "stage_a_p_value": 0.001, "stage_b_verdict": None,
        "rejection_reason": None, "family_id": "fam1", "fingerprint": "fp1",
    }
    base.update(overrides)
    return base


# --- pure decision table -------------------------------------------------


def test_validated_and_confirmed_is_promoted():
    decision, reason = hp._decide(_cell("VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED"))
    assert decision == hp.PROMOTED
    assert "confirmed" in reason.lower()


def test_validated_with_a_non_confirming_verdict_is_blocked_not_rejected():
    """Structurally should never happen (the orchestrator only sets
    VALIDATED for a confirmed verdict) -- but if it ever did, this is an
    evidence-integrity blocker, never a silent promotion AND never a
    silent NOT_PROMOTED (which would misrepresent a real evaluated
    rejection that never actually happened)."""
    decision, reason = hp._decide(_cell("VALIDATED", stage_b_verdict="SOME_OTHER_VERDICT"))
    assert decision == hp.BLOCKED
    assert "evidence-integrity" in reason.lower() or "invariant" in reason.lower()


def test_rejected_is_not_promoted():
    decision, reason = hp._decide(_cell("REJECTED", rejection_reason="did not survive correction"))
    assert decision == hp.NOT_PROMOTED
    assert "REJECTED" in reason


@pytest.mark.parametrize("status", ["INSUFFICIENT_DATA", "FAILED"])
def test_no_evidence_statuses_are_blocked(status):
    decision, reason = hp._decide(_cell(status, rejection_reason="zero variance"))
    assert decision == hp.BLOCKED


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "SCREENED", "CANDIDATE", "VALIDATING"])
def test_incomplete_statuses_are_blocked_never_promoted_or_rejected(status):
    decision, reason = hp._decide(_cell(status))
    assert decision == hp.BLOCKED


def test_unrecognized_status_fails_closed_to_blocked():
    decision, reason = hp._decide(_cell("SOME_FUTURE_STATUS_NOT_YET_INVENTED"))
    assert decision == hp.BLOCKED
    assert "fail closed" in reason.lower()


def test_missing_evidence_never_defaults_to_promoted():
    """Every status EXCEPT the one real confirmed-VALIDATED case must
    never resolve to PROMOTED -- the core fail-closed guarantee."""
    for status in ("QUEUED", "RUNNING", "SCREENED", "CANDIDATE", "VALIDATING", "INSUFFICIENT_DATA", "FAILED", "REJECTED"):
        decision, _ = hp._decide(_cell(status, stage_b_verdict="SAME_SYMBOL_CONFIRMED"))
        assert decision != hp.PROMOTED, f"status={status} must never resolve to PROMOTED"


# --- compute_promotion_id -------------------------------------------------


def test_compute_promotion_id_is_deterministic():
    snap = {"cell_status": "VALIDATED", "stage_a_p_value": 0.001}
    a = hp.compute_promotion_id("h1", "m1", "c1", snap)
    b = hp.compute_promotion_id("h1", "m1", "c1", snap)
    assert a == b
    assert a.startswith("PROMOTION-")


def test_compute_promotion_id_changes_with_governance_snapshot():
    """A genuinely different governance state (e.g. the cell moved from
    CANDIDATE to VALIDATED since the last evaluation) must produce a
    different, coexisting promotion_id -- never silently reuse the old
    one, since that would misrepresent WHEN the decision was actually
    made from."""
    a = hp.compute_promotion_id("h1", "m1", "c1", {"cell_status": "CANDIDATE"})
    b = hp.compute_promotion_id("h1", "m1", "c1", {"cell_status": "VALIDATED"})
    assert a != b


def test_compute_promotion_id_changes_with_identity_triple():
    snap = {"cell_status": "VALIDATED"}
    a = hp.compute_promotion_id("h1", "m1", "c1", snap)
    b = hp.compute_promotion_id("h2", "m1", "c1", snap)
    assert a != b


# --- evaluate_promotion: structural "no orphan/no free-form scope" -------


def test_evaluate_promotion_signature_accepts_only_identity_chain_parameters():
    """Mirrors Phase 4's own guardrail: the promotion gate's entry points
    accept only the identity chain (hypothesis_id, mission_id, cell_id)
    -- never a symbol/engine/timeframe/risk_preset/direction passed as a
    free-form decision input (operator's own explicit Phase 5 point 9)."""
    for fn in (hp.evaluate_promotion, hp.record_promotion):
        params = set(inspect.signature(fn).parameters)
        forbidden = ("symbol", "engine", "timeframe", "risk_preset", "direction", "engines", "timeframes")
        for name in params:
            assert name.lower() not in forbidden, f"{fn.__name__}() unexpectedly accepts a raw parameter: {name!r}"
        assert {"hypothesis_id", "mission_id", "cell_id"} <= params


# --- reuse, never rebuild / no recomputation ------------------------------


def test_never_recomputes_bonferroni_or_p_values():
    source = inspect.getsource(hp)
    for forbidden in ("bonferroni_alpha(", "apply_matrix_wide_correction(", "classify_significance(", "trial_p_value("):
        assert forbidden not in source, f"backtest.hypothesis_promotion unexpectedly references {forbidden!r}"


def test_reuses_the_existing_hypothesis_execution_error_never_a_new_exception_type():
    import re

    source = inspect.getsource(hp)
    assert "HypothesisExecutionError" in source
    # no new exception/dataclass DEFINITION in this module (a "class X:" at
    # the start of a line) -- prose mentioning "exception class" in a
    # docstring is fine, an actual class statement is not.
    assert re.search(r"^class \w", source, re.MULTILINE) is None


def test_storage_hypothesis_promotion_never_imports_backtest():
    from storage import hypothesis_promotion as storage_module

    source = inspect.getsource(storage_module)
    assert "import backtest" not in source
    assert "from backtest" not in source


def test_no_update_or_delete_path_exists_for_promotions():
    from storage import hypothesis_promotion as storage_module

    public_names = [n for n in dir(storage_module) if not n.startswith("_")]
    forbidden_substrings = ("update_promotion", "delete_promotion", "edit_promotion")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"storage.hypothesis_promotion unexpectedly exposes {name!r}"


def test_evaluate_promotion_rejects_unknown_identity_with_the_shared_error_type():
    with pytest.raises(he.HypothesisExecutionError, match="unknown hypothesis_id"):
        hp.evaluate_promotion("DISCOVERY-HYPOTHESIS-ghost", "HYPOTHESIS-MISSION-ghost", "MATRIX-CELL-ghost")
