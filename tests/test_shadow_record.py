"""tests/test_shadow_record.py -- tests for backtest/shadow_record.py
(the Full Observed Evidence Producer, refactored under the Membership
Integration Design Gate, 2026-10, decision C): compose_shadow_record()'s
three-conjunct `completed` formula (with DIVERGENCE_VERDICT_COMPUTED
hardcoded False) as the SOLE definition of that formula, the minimal
two-key record shape, build_shadow_record()'s delegation to
evaluate_shadow_evidence() + compose_shadow_record(), and fail-fast
propagation. build_shadow_record()'s own PUBLIC signature and output are
asserted unchanged by the refactor."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backtest import shadow_record as sr


def _evidence(**overrides) -> dict:
    base = dict(
        hypothesis_id="H", n_t=0, tp_count=0, sl_count=0, p_value=None,
        divergence_statistic_valid=False, baseline_p=None, baseline_failure_reason="NO_CANONICAL_CELL",
        request_ids=[], canonical_identity_state="NO_CANONICAL_CELL",
        hypothesis_fingerprint=None, research_code_commit=None,
    )
    base.update(overrides)
    return base


def test_divergence_verdict_computed_is_hardcoded_false():
    """Locked: this constant must never silently become True -- doing
    so honestly requires a separate, future Design Gate."""
    assert sr.DIVERGENCE_VERDICT_COMPUTED is False


def test_n_min_terminal_confirmed_is_forty():
    assert sr.N_MIN_TERMINAL_CONFIRMED == 40


# ---------- compose_shadow_record() -- the formula's SOLE definition ------


def test_compose_completed_is_always_false_even_with_abundant_n_t_and_valid_baseline():
    """The central guard this Design Gate exists for: n_T(H)>=40 AND a
    valid baseline statistic are NECESSARY but NOT SUFFICIENT. completed
    must stay False because DIVERGENCE_VERDICT_COMPUTED is False,
    regardless of how strong the other two conjuncts look."""
    evidence = _evidence(n_t=100, divergence_statistic_valid=True, baseline_p=0.7)
    assert sr.compose_shadow_record(evidence) == {"completed": False, "diverged_catastrophically": False}


def test_compose_completed_false_when_n_t_below_forty_even_if_baseline_valid():
    evidence = _evidence(n_t=39, divergence_statistic_valid=True)
    assert sr.compose_shadow_record(evidence)["completed"] is False


def test_compose_completed_false_when_baseline_invalid_even_if_n_t_abundant():
    evidence = _evidence(n_t=1000, divergence_statistic_valid=False)
    assert sr.compose_shadow_record(evidence)["completed"] is False


def test_compose_diverged_catastrophically_is_always_false():
    evidence = _evidence(n_t=0, divergence_statistic_valid=False)
    assert sr.compose_shadow_record(evidence)["diverged_catastrophically"] is False


def test_compose_record_shape_has_exactly_two_keys():
    """Locked minimal shape -- no diagnostic fields (n_t, baseline,
    identity, etc.) leak into the returned record."""
    evidence = _evidence()
    assert set(sr.compose_shadow_record(evidence).keys()) == {"completed", "diverged_catastrophically"}


def test_compose_ignores_canonical_identity_fields():
    """Operator's own locked rule: completion never depends on canonical
    identity. An IDENTITY_MISMATCH evidence dict with otherwise-passing
    n_t/divergence_statistic_valid must compose identically to one with
    CANONICAL_IDENTITY_RESOLVED."""
    mismatched = _evidence(n_t=100, divergence_statistic_valid=True, canonical_identity_state="IDENTITY_MISMATCH")
    resolved = _evidence(
        n_t=100, divergence_statistic_valid=True, canonical_identity_state="CANONICAL_IDENTITY_RESOLVED",
        hypothesis_fingerprint="FP", research_code_commit="abc",
    )
    assert sr.compose_shadow_record(mismatched) == sr.compose_shadow_record(resolved)


# ---------- build_shadow_record() -- delegates, unchanged public contract -


@patch("backtest.shadow_record.evaluate_shadow_evidence")
def test_build_shadow_record_delegates_to_evaluate_shadow_evidence_and_compose(mock_evaluate):
    mock_evaluate.return_value = _evidence(n_t=100, divergence_statistic_valid=True, baseline_p=0.7)
    promotions = [{"promotion_id": "P1"}]
    cell = {"cell_id": "C1"}
    validation_results = [{"symbol": "XAUUSD"}]

    result = sr.build_shadow_record(
        "H", base_config={"k": "v"}, promotions=promotions, cell=cell,
        validation_results=validation_results, api_key="secret",
    )

    mock_evaluate.assert_called_once_with(
        "H", base_config={"k": "v"}, promotions=promotions, cell=cell,
        validation_results=validation_results, api_key="secret",
    )
    assert result == {"completed": False, "diverged_catastrophically": False}


@patch("backtest.shadow_record.evaluate_shadow_evidence")
def test_build_shadow_record_record_shape_has_exactly_two_keys(mock_evaluate):
    mock_evaluate.return_value = _evidence()
    result = sr.build_shadow_record(
        "H", base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert set(result.keys()) == {"completed", "diverged_catastrophically"}


@patch("backtest.shadow_record.evaluate_shadow_evidence")
def test_observed_side_failure_propagates_uncaught(mock_evaluate):
    """Fail-fast: an exception raised while evaluating shared evidence
    must propagate unchanged -- no partial/fabricated record."""
    mock_evaluate.side_effect = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        sr.build_shadow_record("H", base_config={}, promotions=[], cell=None, validation_results=[])


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(sr)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_never_writes_to_storage():
    body = _source_without_docstrings()
    forbidden = ("try_insert", "record_", "update_cell", "INSERT INTO", "UPDATE ")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_record unexpectedly references {pattern!r}"
