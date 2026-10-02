"""tests/test_shadow_outcome_observation.py -- tests for backtest/
shadow_outcome_observation.py (Hypothesis Discovery Engine, Phase 15E --
SHADOW Outcome Observation persistence + pure aggregation): the
append-only event-log model, the EXACT idempotency/collision distinction
the operator locked (same result+evaluated_at -> idempotent; different
observation colliding on the same pair -> explicit error, never
overwritten), the latest-per-request_id reduction, and structural
independence from every sibling-phase/execution/scheduler module and
from backtest.shadow_outcome_resolver itself (one-way dependency)."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import shadow_outcome_observation as soo
from backtest import shadow_outcome_resolver as sor
from storage import hypothesis_live_request as storage_live_request
from storage import shadow_decision_snapshot as storage_snapshot


def _real_snapshot(hypothesis_id: str = "CONFLUENCE-HYPOTHESIS-outcome") -> str:
    record = storage_live_request.record_live_identity_request(
        hypothesis_id=hypothesis_id, decision_type="SINGLE_ENGINE", symbol="EURUSD", engine="wyckoff",
        engine_version="v2", timeframe="H4", risk_preset="balanced", preset_definition_hash="hash",
        risk_parameters_used_json="{}", decision="NO_TRADE", decision_reason="seeded for test",
        live_verdict="NO_TRADE",
    )
    request_id = record["request_id"]
    storage_snapshot.try_insert(
        snapshot_id=f"SHADOW-SNAPSHOT-{request_id[-12:]}", request_id=request_id, hypothesis_id=hypothesis_id,
        symbol="EURUSD", timeframe="H4", bar_time="2026-01-01T00:00:00+00:00", side="BUY",
        entry_price=1.2345, stop_loss=1.2300, take_profit=1.2450,
    )
    return request_id


def _resolver_result(
    request_id: str, hypothesis_id: str, outcome: str, evaluated_at: str, resolved_bar_time: str | None = None,
) -> dict:
    return {
        "request_id": request_id, "hypothesis_id": hypothesis_id, "outcome": outcome,
        "resolved_bar_time": resolved_bar_time, "evaluated_at": evaluated_at,
    }


# --- record_shadow_outcome_observation: the happy path --------------------


def test_records_a_real_observation():
    hyp = "CONFLUENCE-HYPOTHESIS-happy"
    request_id = _real_snapshot(hyp)
    result = _resolver_result(request_id, hyp, sor.NOT_YET_ASSESSABLE, "2026-01-01T05:00:00+00:00")

    row = soo.record_shadow_outcome_observation(result)
    assert row["request_id"] == request_id
    assert row["hypothesis_id"] == hyp
    assert row["outcome"] == sor.NOT_YET_ASSESSABLE


def test_hypothesis_id_is_derived_from_the_snapshot_never_trusted_from_resolver_result():
    """The self-caught fix: a resolver_result claiming a WRONG
    hypothesis_id (mismatched from the real, persisted snapshot) must
    never cause that wrong value to be persisted -- the canonical
    snapshot's own hypothesis_id always wins."""
    real_hyp = "CONFLUENCE-HYPOTHESIS-real-owner"
    request_id = _real_snapshot(real_hyp)
    spoofed_result = _resolver_result(request_id, "CONFLUENCE-HYPOTHESIS-spoofed",
                                       sor.NOT_YET_ASSESSABLE, "2026-01-01T05:00:00+00:00")

    row = soo.record_shadow_outcome_observation(spoofed_result)
    assert row["hypothesis_id"] == real_hyp
    assert row["hypothesis_id"] != "CONFLUENCE-HYPOTHESIS-spoofed"


def test_raises_for_unknown_request_id():
    result = _resolver_result("LIVE-IDENTITY-REQUEST-ghost", "CONFLUENCE-HYPOTHESIS-x",
                               sor.NOT_YET_ASSESSABLE, "2026-01-01T05:00:00+00:00")
    with pytest.raises(soo.ShadowOutcomeObservationError, match="unknown request_id"):
        soo.record_shadow_outcome_observation(result)


# --- idempotency vs. collision: the exact locked distinction --------------


def test_identical_retry_is_idempotent_returns_existing_row():
    hyp = "CONFLUENCE-HYPOTHESIS-retry"
    request_id = _real_snapshot(hyp)
    result = _resolver_result(request_id, hyp, sor.TP_HIT, "2026-01-01T05:00:00+00:00",
                               resolved_bar_time="2026-01-01T04:00:00+00:00")

    first = soo.record_shadow_outcome_observation(result)
    second = soo.record_shadow_outcome_observation(result)  # byte-identical retry
    assert first == second
    assert first["observation_id"] == second["observation_id"]


def test_different_observation_colliding_on_same_pair_raises_never_overwrites():
    hyp = "CONFLUENCE-HYPOTHESIS-collision"
    request_id = _real_snapshot(hyp)
    same_evaluated_at = "2026-01-01T05:00:00+00:00"
    first_result = _resolver_result(request_id, hyp, sor.NOT_YET_ASSESSABLE, same_evaluated_at)
    colliding_result = _resolver_result(request_id, hyp, sor.SL_HIT, same_evaluated_at,
                                         resolved_bar_time="2026-01-01T04:00:00+00:00")

    first = soo.record_shadow_outcome_observation(first_result)
    with pytest.raises(soo.ShadowOutcomeObservationError, match="DIFFERENT observation already exists"):
        soo.record_shadow_outcome_observation(colliding_result)

    # the original row is untouched -- never overwritten
    from storage import shadow_outcome_observation as storage_observation
    unchanged = storage_observation.get_observation_by_request_id_and_evaluated_at(request_id, same_evaluated_at)
    assert unchanged == first
    assert unchanged["outcome"] == sor.NOT_YET_ASSESSABLE


def test_a_genuinely_new_evaluation_at_a_different_time_creates_a_new_observation():
    """The append-only model's own core proof: re-evaluating the SAME
    request_id later, with a different evaluated_at, never updates the
    earlier row -- it creates a second, independent observation."""
    hyp = "CONFLUENCE-HYPOTHESIS-reeval"
    request_id = _real_snapshot(hyp)
    first_result = _resolver_result(request_id, hyp, sor.NOT_YET_ASSESSABLE, "2026-01-01T05:00:00+00:00")
    later_result = _resolver_result(request_id, hyp, sor.TP_HIT, "2026-01-05T05:00:00+00:00",
                                      resolved_bar_time="2026-01-03T00:00:00+00:00")

    first = soo.record_shadow_outcome_observation(first_result)
    second = soo.record_shadow_outcome_observation(later_result)

    assert first["observation_id"] != second["observation_id"]
    from storage import shadow_outcome_observation as storage_observation
    assert len(storage_observation.list_observations_for_request(request_id)) == 2


# --- reduce_to_latest_observations -----------------------------------------


def test_reduce_to_latest_observations_picks_the_greatest_evaluated_at_per_request_id():
    observations = [
        {"request_id": "R1", "evaluated_at": "2026-01-01T01:00:00+00:00", "outcome": "NOT_YET_ASSESSABLE"},
        {"request_id": "R1", "evaluated_at": "2026-01-02T01:00:00+00:00", "outcome": "TP_HIT"},
        {"request_id": "R2", "evaluated_at": "2026-01-01T01:00:00+00:00", "outcome": "DATA_GAP"},
    ]
    reduced = soo.reduce_to_latest_observations(observations)
    by_request = {o["request_id"]: o for o in reduced}
    assert len(reduced) == 2
    assert by_request["R1"]["outcome"] == "TP_HIT"  # the later evaluation, not the earlier one
    assert by_request["R2"]["outcome"] == "DATA_GAP"


def test_reduce_to_latest_observations_is_deterministic():
    observations = [
        {"request_id": "R1", "evaluated_at": "2026-01-01T01:00:00+00:00", "outcome": "SL_HIT"},
        {"request_id": "R1", "evaluated_at": "2026-01-01T02:00:00+00:00", "outcome": "SL_HIT"},
    ]
    first = soo.reduce_to_latest_observations(observations)
    second = soo.reduce_to_latest_observations(observations)
    assert first == second


# --- compute_shadow_outcome_profile: the adversarial repeated-re-eval proof


def test_profile_counts_each_request_id_exactly_once_despite_many_re_evaluations():
    hyp = "CONFLUENCE-HYPOTHESIS-adversarial"
    request_id = _real_snapshot(hyp)
    # Re-evaluate the SAME decision 5 times at 5 different moments, mostly
    # NOT_YET_ASSESSABLE, finally resolving to TP_HIT.
    for i in range(4):
        soo.record_shadow_outcome_observation(
            _resolver_result(request_id, hyp, sor.NOT_YET_ASSESSABLE, f"2026-01-0{i + 1}T05:00:00+00:00")
        )
    soo.record_shadow_outcome_observation(
        _resolver_result(request_id, hyp, sor.TP_HIT, "2026-01-05T05:00:00+00:00",
                          resolved_bar_time="2026-01-04T00:00:00+00:00")
    )

    profile = soo.compute_shadow_outcome_profile(hyp, window=50)
    assert profile["observation_count"] == 1  # one decision, not five rows
    assert profile["tp_hit_count"] == 1
    assert profile["not_yet_assessable_count"] == 0  # the LATEST state is TP_HIT, not the earlier ones


def test_profile_counts_multiple_distinct_decisions_correctly():
    hyp = "CONFLUENCE-HYPOTHESIS-multi-decision"
    r1 = _real_snapshot(hyp)
    r2 = _real_snapshot(hyp)
    soo.record_shadow_outcome_observation(_resolver_result(r1, hyp, sor.TP_HIT, "2026-01-01T05:00:00+00:00",
                                                             resolved_bar_time="2026-01-01T04:00:00+00:00"))
    soo.record_shadow_outcome_observation(_resolver_result(r2, hyp, sor.SL_HIT, "2026-01-01T05:00:00+00:00",
                                                             resolved_bar_time="2026-01-01T04:00:00+00:00"))

    profile = soo.compute_shadow_outcome_profile(hyp, window=50)
    assert profile["observation_count"] == 2
    assert profile["tp_hit_count"] == 1
    assert profile["sl_hit_count"] == 1


def test_profile_returns_exactly_the_five_frequency_fields_no_performance_metrics():
    hyp = "CONFLUENCE-HYPOTHESIS-shape"
    profile = soo.compute_shadow_outcome_profile(hyp, window=10)
    assert set(profile) == {
        "observation_count", "tp_hit_count", "sl_hit_count", "timeout_count",
        "data_gap_count", "not_yet_assessable_count",
    }
    forbidden = ("win_rate", "profit_factor", "pnl", "diverged_catastrophically", "trade_count")
    for field in forbidden:
        assert field not in profile


# --- structural: no coupling with sibling phases/execution/scheduler ------


def _source_without_docstrings(module) -> str:
    source = inspect.getsource(module)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_promotion_gate_policy_health_attribution_or_execution_import():
    body = _source_without_docstrings(soo)
    forbidden = (
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from backtest.execution_attribution", "import backtest.execution_attribution",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "from storage.outcome_tracker", "import storage.outcome_tracker",
        "from storage.shadow_book", "import storage.shadow_book",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_observation unexpectedly references {pattern!r}"


def test_resolver_module_has_no_reverse_dependency_on_this_module():
    """One-way dependency direction: backtest.shadow_outcome_resolver
    must remain completely unaware this persistence/aggregation module
    exists -- it stays pure, exactly as locked in Phase 15D."""
    resolver_body = _source_without_docstrings(sor)
    assert "shadow_outcome_observation" not in resolver_body


def test_no_divergence_or_performance_logic_in_source():
    body = _source_without_docstrings(soo)
    forbidden = ("diverged_catastrophically", "win_rate", "profit_factor", "0.65")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_observation unexpectedly references {pattern!r}"
