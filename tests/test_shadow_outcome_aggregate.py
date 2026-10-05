"""tests/test_shadow_outcome_aggregate.py -- tests for backtest/
shadow_outcome_aggregate.py (Observed Evidence Aggregate Composition
Design Gate): the orchestration/pure split, _select_previous()'s locked
exclusion rule, and the fail-fast/no-isolation guarantee adopted
verbatim from backtest.shadow_observation.run_shadow_cycle()'s own
precedent."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backtest import shadow_outcome_aggregate as soa
from backtest.shadow_outcome_resolver import SL_HIT, TP_HIT
from backtest.shadow_outcome_terminality import (
    CONTRADICTED,
    NOT_YET_ASSESSABLE,
    PROVISIONAL,
    TERMINAL_CONFIRMED,
)

HID = "CONFLUENCE-HYPOTHESIS-x"


def _obs(request_id="R1", evaluated_at="2026-09-01T00:00:00+00:00", outcome=TP_HIT, **overrides) -> dict:
    base = {"request_id": request_id, "hypothesis_id": HID, "evaluated_at": evaluated_at, "outcome": outcome}
    base.update(overrides)
    return base


# ---------- _select_previous() ----------------------------------------------


def test_select_previous_returns_none_when_stored_is_empty():
    latest = _obs(evaluated_at="2026-09-05T00:00:00+00:00")
    assert soa._select_previous([], latest) is None


def test_select_previous_excludes_only_the_matching_evaluated_at():
    latest = _obs(evaluated_at="2026-09-05T00:00:00+00:00")
    older = _obs(evaluated_at="2026-09-03T00:00:00+00:00")
    same_as_latest = _obs(evaluated_at="2026-09-05T00:00:00+00:00")
    stored = [same_as_latest, older]  # already newest-first, as the storage API returns
    assert soa._select_previous(stored, latest) == older


def test_select_previous_preserves_storage_api_ordering_no_resort():
    """Locked: candidates[0] after exclusion, never re-sorted by any
    other field (seq, request_id, outcome)."""
    latest = _obs(evaluated_at="2026-09-05T00:00:00+00:00")
    first = _obs(evaluated_at="2026-09-04T00:00:00+00:00")
    second = _obs(evaluated_at="2026-09-02T00:00:00+00:00")
    stored = [first, second]  # already newest-first
    assert soa._select_previous(stored, latest) == first


def test_select_previous_returns_none_when_only_candidate_matches_latest():
    latest = _obs(evaluated_at="2026-09-05T00:00:00+00:00")
    stored = [_obs(evaluated_at="2026-09-05T00:00:00+00:00")]
    assert soa._select_previous(stored, latest) is None


# ---------- count_terminal_confirmed() (PURE) -------------------------------


def test_count_terminal_confirmed_empty_list():
    assert soa.count_terminal_confirmed([]) == 0


def test_count_terminal_confirmed_counts_only_that_state():
    results = [
        {"terminality_state": TERMINAL_CONFIRMED},
        {"terminality_state": PROVISIONAL},
        {"terminality_state": TERMINAL_CONFIRMED},
        {"terminality_state": CONTRADICTED},
        {"terminality_state": NOT_YET_ASSESSABLE},
        {"terminality_state": TERMINAL_CONFIRMED},
    ]
    assert soa.count_terminal_confirmed(results) == 3


# ---------- compute_observed_win_rate() (PURE) -- Observed Win/Loss
# Statistic Design (locked 2026-10) ------------------------------------------


def test_compute_observed_win_rate_none_when_no_terminal_confirmed():
    results = [
        {"terminality_state": PROVISIONAL, "outcome": TP_HIT},
        {"terminality_state": NOT_YET_ASSESSABLE, "outcome": "TIMEOUT"},
    ]
    assert soa.compute_observed_win_rate(results) is None


def test_compute_observed_win_rate_none_on_empty_list():
    assert soa.compute_observed_win_rate([]) is None


def test_compute_observed_win_rate_basic_ratio():
    results = [
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT},
        {"terminality_state": PROVISIONAL, "outcome": TP_HIT},  # excluded -- not TERMINAL_CONFIRMED
    ]
    assert soa.compute_observed_win_rate(results) == pytest.approx(2 / 3)


def test_compute_observed_win_rate_denominator_equals_count_terminal_confirmed():
    """Structural invariant, locked: TP_HIT_count + SL_HIT_count ==
    count_terminal_confirmed(results) exactly."""
    results = [
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT},
        {"terminality_state": CONTRADICTED, "outcome": SL_HIT},
        {"terminality_state": NOT_YET_ASSESSABLE, "outcome": "TIMEOUT"},
    ]
    win_rate = soa.compute_observed_win_rate(results)
    assert win_rate == pytest.approx(1 / 3)
    assert soa.count_terminal_confirmed(results) == 3  # == TP_HIT_count(1) + SL_HIT_count(2)


def test_compute_observed_win_rate_all_wins_is_one():
    results = [{"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT} for _ in range(5)]
    assert soa.compute_observed_win_rate(results) == pytest.approx(1.0)


def test_compute_observed_win_rate_all_losses_is_zero():
    results = [{"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT} for _ in range(5)]
    assert soa.compute_observed_win_rate(results) == pytest.approx(0.0)


def test_compute_observed_win_rate_contract_drift_raises_fail_closed():
    """Locked fail-closed guard: a TERMINAL_CONFIRMED result with an
    outcome other than TP_HIT/SL_HIT must never be silently excluded --
    this is structurally impossible per assess_terminality()'s own
    rules, so if it happens, raise rather than produce a statistic over
    a drifted population."""
    results = [
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": "TIMEOUT", "request_id": "R-drift"},
    ]
    with pytest.raises(soa.ShadowOutcomeAggregateError, match="R-drift"):
        soa.compute_observed_win_rate(results)


def test_compute_observed_win_rate_never_compares_to_a_threshold():
    """Statistic definition vs. verdict threshold stays separate -- this
    function returns only the ratio, never True/False against 0.65 or
    anything else."""
    results = [{"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT} for _ in range(100)]
    result = soa.compute_observed_win_rate(results)
    assert isinstance(result, float)


# ---------- count_terminal_confirmed_by_outcome() (PURE) -- k/n Raw-Count
# Extraction Design Gate (locked 2026-10) ------------------------------------


def test_count_terminal_confirmed_by_outcome_empty_list_is_zero_counts_not_none():
    assert soa.count_terminal_confirmed_by_outcome([]) == {"tp_count": 0, "sl_count": 0}


def test_count_terminal_confirmed_by_outcome_none_terminal_confirmed_is_zero_counts():
    results = [
        {"terminality_state": PROVISIONAL, "outcome": TP_HIT},
        {"terminality_state": NOT_YET_ASSESSABLE, "outcome": "TIMEOUT"},
    ]
    assert soa.count_terminal_confirmed_by_outcome(results) == {"tp_count": 0, "sl_count": 0}


def test_count_terminal_confirmed_by_outcome_mixed_tp_sl():
    results = [
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT},
        {"terminality_state": PROVISIONAL, "outcome": TP_HIT},  # excluded -- not TERMINAL_CONFIRMED
    ]
    assert soa.count_terminal_confirmed_by_outcome(results) == {"tp_count": 2, "sl_count": 1}


def test_count_terminal_confirmed_by_outcome_sum_equals_count_terminal_confirmed():
    """The central locked invariant: tp_count + sl_count ==
    count_terminal_confirmed(results) exactly, for any mix of states."""
    results = [
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT},
        {"terminality_state": CONTRADICTED, "outcome": SL_HIT},
        {"terminality_state": NOT_YET_ASSESSABLE, "outcome": "TIMEOUT"},
        {"terminality_state": PROVISIONAL, "outcome": TP_HIT},
    ]
    counts = soa.count_terminal_confirmed_by_outcome(results)
    assert counts["tp_count"] + counts["sl_count"] == soa.count_terminal_confirmed(results) == 3


def test_count_terminal_confirmed_by_outcome_contract_drift_raises_fail_closed():
    results = [
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": "TIMEOUT", "request_id": "R-drift"},
    ]
    with pytest.raises(soa.ShadowOutcomeAggregateError, match="R-drift"):
        soa.count_terminal_confirmed_by_outcome(results)


def test_count_terminal_confirmed_by_outcome_independent_of_compute_observed_win_rate():
    """Locked scope: neither function calls the other -- same inputs
    must be independently consistent, not wired together."""
    results = [
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT},
        {"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT},
    ]
    counts = soa.count_terminal_confirmed_by_outcome(results)
    win_rate = soa.compute_observed_win_rate(results)
    assert win_rate == pytest.approx(counts["tp_count"] / (counts["tp_count"] + counts["sl_count"]))

    import inspect
    import re
    body = re.sub(r'""".*?"""', "", inspect.getsource(soa.count_terminal_confirmed_by_outcome), flags=re.DOTALL)
    assert "compute_observed_win_rate(" not in body


# ---------- compute_catastrophic_divergence_p_value() (PURE) -- p-value
# Composition Design Gate (locked 2026-10) -----------------------------------


def test_compute_catastrophic_divergence_p_value_none_when_p_is_none():
    """Locked: p=None (no valid baseline) short-circuits before any call
    to binomial_lower_tail_p_value() -- no fallback, no recomputation."""
    results = [{"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT} for _ in range(50)]
    assert soa.compute_catastrophic_divergence_p_value(results, None) is None


def test_compute_catastrophic_divergence_p_value_none_when_no_terminal_confirmed():
    """n=0 flows through binomial_lower_tail_p_value()'s own existing
    n<1 guard -- no second check added here."""
    results = [{"terminality_state": PROVISIONAL, "outcome": TP_HIT}]
    assert soa.compute_catastrophic_divergence_p_value(results, 0.65) is None


def test_compute_catastrophic_divergence_p_value_matches_direct_composition():
    results = (
        [{"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT} for _ in range(7)]
        + [{"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT} for _ in range(13)]
    )
    p = 0.65
    k, n = 7, 20
    expected = soa.binomial_lower_tail_p_value(k, n, p)
    assert soa.compute_catastrophic_divergence_p_value(results, p) == pytest.approx(expected)


def test_compute_catastrophic_divergence_p_value_small_when_observed_far_below_baseline():
    results = (
        [{"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT} for _ in range(7)]
        + [{"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT} for _ in range(43)]
    )
    result = soa.compute_catastrophic_divergence_p_value(results, 0.65)
    assert result is not None
    assert result < 0.001


def test_compute_catastrophic_divergence_p_value_large_when_observed_meets_baseline():
    results = (
        [{"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT} for _ in range(35)]
        + [{"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT} for _ in range(15)]
    )
    result = soa.compute_catastrophic_divergence_p_value(results, 0.65)
    assert result is not None
    assert result > 0.5


def test_compute_catastrophic_divergence_p_value_never_classifies_significance():
    """Scope lock: this function returns only the raw p-value -- never
    a significance label, never a boolean verdict."""
    results = [{"terminality_state": TERMINAL_CONFIRMED, "outcome": SL_HIT} for _ in range(50)]
    result = soa.compute_catastrophic_divergence_p_value(results, 0.65)
    assert isinstance(result, float)


def test_compute_catastrophic_divergence_p_value_has_no_scope_creep_in_source():
    import inspect
    import re
    body = re.sub(
        r'""".*?"""', "",
        inspect.getsource(soa.compute_catastrophic_divergence_p_value), flags=re.DOTALL,
    )
    forbidden = ("classify_significance", "bonferroni_alpha", "diverged_catastrophically",
                 "DIVERGENCE_VERDICT_COMPUTED", "build_shadow_record")
    for pattern in forbidden:
        assert pattern not in body, f"compute_catastrophic_divergence_p_value unexpectedly references {pattern!r}"


# ---------- evaluate_all_requests_for_hypothesis() (ORCHESTRATION) ---------


@patch("backtest.shadow_outcome_aggregate.assess_terminality")
@patch("backtest.shadow_outcome_aggregate.verify_historical_stability")
@patch("backtest.shadow_outcome_aggregate.list_observations_for_request")
@patch("backtest.shadow_outcome_aggregate.resolve_decision_outcome")
@patch("backtest.shadow_outcome_aggregate.get_snapshot_by_request_id")
@patch("backtest.shadow_outcome_aggregate.list_request_ids_for_hypothesis")
def test_empty_enumeration_returns_empty_list(
    mock_list_ids, mock_get_snapshot, mock_resolve, mock_list_obs, mock_verify, mock_assess,
):
    mock_list_ids.return_value = []
    result = soa.evaluate_all_requests_for_hypothesis(HID, base_config={})
    assert result == []
    mock_get_snapshot.assert_not_called()
    mock_resolve.assert_not_called()


@patch("backtest.shadow_outcome_aggregate.assess_terminality")
@patch("backtest.shadow_outcome_aggregate.verify_historical_stability")
@patch("backtest.shadow_outcome_aggregate.list_observations_for_request")
@patch("backtest.shadow_outcome_aggregate.resolve_decision_outcome")
@patch("backtest.shadow_outcome_aggregate.get_snapshot_by_request_id")
@patch("backtest.shadow_outcome_aggregate.list_request_ids_for_hypothesis")
def test_single_request_wires_latest_previous_verification_into_assess_terminality(
    mock_list_ids, mock_get_snapshot, mock_resolve, mock_list_obs, mock_verify, mock_assess,
):
    mock_list_ids.return_value = ["R1"]
    snapshot = {"request_id": "R1", "hypothesis_id": HID, "symbol": "XAUUSD"}
    latest = _obs(request_id="R1", evaluated_at="2026-09-05T00:00:00+00:00")
    mock_get_snapshot.return_value = snapshot
    mock_resolve.return_value = latest
    mock_list_obs.return_value = []  # no production writer today -> always empty
    verification = {"request_id": "R1"}
    mock_verify.return_value = verification
    mock_assess.return_value = {"terminality_state": TERMINAL_CONFIRMED}

    result = soa.evaluate_all_requests_for_hypothesis("H", base_config={"x": 1}, api_key="k")

    mock_get_snapshot.assert_called_once_with("R1")
    mock_resolve.assert_called_once_with(snapshot, base_config={"x": 1})
    mock_list_obs.assert_called_once_with("R1")
    mock_verify.assert_called_once_with(snapshot, latest, base_config={"x": 1}, api_key="k")
    mock_assess.assert_called_once_with(latest, None, verification)
    assert result == [{"terminality_state": TERMINAL_CONFIRMED, "outcome": TP_HIT}]


@patch("backtest.shadow_outcome_aggregate.assess_terminality")
@patch("backtest.shadow_outcome_aggregate.verify_historical_stability")
@patch("backtest.shadow_outcome_aggregate.list_observations_for_request")
@patch("backtest.shadow_outcome_aggregate.resolve_decision_outcome")
@patch("backtest.shadow_outcome_aggregate.get_snapshot_by_request_id")
@patch("backtest.shadow_outcome_aggregate.list_request_ids_for_hypothesis")
def test_multiple_requests_each_produce_one_result_in_order(
    mock_list_ids, mock_get_snapshot, mock_resolve, mock_list_obs, mock_verify, mock_assess,
):
    mock_list_ids.return_value = ["R1", "R2", "R3"]
    mock_get_snapshot.side_effect = lambda rid: {"request_id": rid, "hypothesis_id": HID}
    mock_resolve.side_effect = lambda snap, base_config: _obs(request_id=snap["request_id"])
    mock_list_obs.return_value = []
    mock_verify.return_value = {}
    mock_assess.side_effect = [
        {"terminality_state": TERMINAL_CONFIRMED},
        {"terminality_state": PROVISIONAL},
        {"terminality_state": TERMINAL_CONFIRMED},
    ]

    result = soa.evaluate_all_requests_for_hypothesis(HID, base_config={})
    assert [r["terminality_state"] for r in result] == [
        TERMINAL_CONFIRMED, PROVISIONAL, TERMINAL_CONFIRMED,
    ]
    assert mock_get_snapshot.call_count == 3


# ---------- fail-fast / no isolation (locked) -------------------------------


@patch("backtest.shadow_outcome_aggregate.assess_terminality")
@patch("backtest.shadow_outcome_aggregate.verify_historical_stability")
@patch("backtest.shadow_outcome_aggregate.list_observations_for_request")
@patch("backtest.shadow_outcome_aggregate.resolve_decision_outcome")
@patch("backtest.shadow_outcome_aggregate.get_snapshot_by_request_id")
@patch("backtest.shadow_outcome_aggregate.list_request_ids_for_hypothesis")
def test_one_request_failure_propagates_and_aborts_remaining_requests(
    mock_list_ids, mock_get_snapshot, mock_resolve, mock_list_obs, mock_verify, mock_assess,
):
    """Locked precedent (backtest.shadow_observation.run_shadow_cycle()):
    no isolation. A provider/structural failure on request 2 of 3 must
    propagate unchanged, and request 3 must never be reached."""
    mock_list_ids.return_value = ["R1", "R2", "R3"]
    mock_get_snapshot.side_effect = lambda rid: {"request_id": rid, "hypothesis_id": HID}

    class _FakeProviderFailure(Exception):
        pass

    def _resolve_side_effect(snap, base_config):
        if snap["request_id"] == "R2":
            raise _FakeProviderFailure("provider down")
        return _obs(request_id=snap["request_id"])

    mock_resolve.side_effect = _resolve_side_effect
    mock_list_obs.return_value = []
    mock_verify.return_value = {}
    mock_assess.return_value = {"terminality_state": TERMINAL_CONFIRMED}

    with pytest.raises(_FakeProviderFailure, match="provider down"):
        soa.evaluate_all_requests_for_hypothesis(HID, base_config={})

    # R1 was evaluated (resolve called for it), R2 raised, R3 never reached.
    assert mock_resolve.call_count == 2
    called_request_ids = [call.args[0] for call in mock_get_snapshot.call_args_list]
    assert called_request_ids == ["R1", "R2"]  # R3's snapshot never even fetched


@patch("backtest.shadow_outcome_aggregate.assess_terminality")
@patch("backtest.shadow_outcome_aggregate.verify_historical_stability")
@patch("backtest.shadow_outcome_aggregate.list_observations_for_request")
@patch("backtest.shadow_outcome_aggregate.resolve_decision_outcome")
@patch("backtest.shadow_outcome_aggregate.get_snapshot_by_request_id")
@patch("backtest.shadow_outcome_aggregate.list_request_ids_for_hypothesis")
def test_verification_failure_also_propagates_uncaught(
    mock_list_ids, mock_get_snapshot, mock_resolve, mock_list_obs, mock_verify, mock_assess,
):
    mock_list_ids.return_value = ["R1"]
    mock_get_snapshot.return_value = {"request_id": "R1", "hypothesis_id": HID}
    mock_resolve.return_value = _obs(request_id="R1")
    mock_list_obs.return_value = []
    mock_verify.side_effect = RuntimeError("twelve data unreachable")

    with pytest.raises(RuntimeError, match="twelve data unreachable"):
        soa.evaluate_all_requests_for_hypothesis(HID, base_config={})
    mock_assess.assert_not_called()


# ---------- structural: no storage write, no try/except --------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(soa)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_never_writes_to_storage():
    body = _source_without_docstrings()
    forbidden = ("try_insert", "record_", "update_cell", "INSERT INTO", "UPDATE ")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_aggregate unexpectedly references {pattern!r}"


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body
