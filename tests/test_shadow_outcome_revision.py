"""tests/test_shadow_outcome_revision.py -- tests for backtest/
shadow_outcome_revision.py (Design Gate #3, Cross-Invocation Outcome
Relationship): the locked OUTCOME_REPRODUCED / LEGITIMATE_PROGRESSION /
OUTCOME_REVISED classification, TIMEOUT's deliberate grouping with the
non-causal bucket, ordering/identity structural checks, and
independence from storage/resolver/provider logic."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import shadow_outcome_revision as sor_rev


def _obs(**overrides) -> dict:
    base = {
        "request_id": "LIVE-IDENTITY-REQUEST-abc123", "hypothesis_id": "CONFLUENCE-HYPOTHESIS-x",
        "outcome": "NOT_YET_ASSESSABLE", "resolved_bar_time": None,
        "evaluated_at": "2026-01-01T05:00:00+00:00",
    }
    base.update(overrides)
    return base


# --- OUTCOME_REPRODUCED: causal, exact match -------------------------------


def test_causal_outcome_with_identical_resolved_bar_time_is_reproduced():
    earlier = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                   evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.OUTCOME_REPRODUCED


def test_sl_hit_reproduced_too():
    earlier = _obs(outcome="SL_HIT", resolved_bar_time="2026-01-01T08:00:00+00:00",
                   evaluated_at="2026-01-01T09:00:00+00:00")
    later = _obs(outcome="SL_HIT", resolved_bar_time="2026-01-01T08:00:00+00:00",
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.OUTCOME_REPRODUCED


# --- OUTCOME_REVISED: causal, same label but different resolved_bar_time ---


def test_same_outcome_label_different_resolved_bar_time_is_revised():
    """The operator's own worked example: TP_HIT@10:00 -> TP_HIT@14:00 --
    same label, different concrete causal claim -- must be REVISED, not
    REPRODUCED."""
    earlier = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                   evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T14:00:00+00:00",
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.OUTCOME_REVISED


# --- OUTCOME_REVISED: causal, different outcome label -----------------------


def test_tp_hit_then_sl_hit_is_revised():
    earlier = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                   evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(outcome="SL_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.OUTCOME_REVISED


@pytest.mark.parametrize("later_outcome,later_bar_time", [
    ("TIMEOUT", None), ("DATA_GAP", None), ("NOT_YET_ASSESSABLE", None),
])
def test_causal_falling_back_to_non_causal_is_revised(later_outcome, later_bar_time):
    earlier = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                   evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(outcome=later_outcome, resolved_bar_time=later_bar_time,
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.OUTCOME_REVISED


# --- LEGITIMATE_PROGRESSION: non-causal earlier, any differing later -------


@pytest.mark.parametrize("earlier_outcome", ["NOT_YET_ASSESSABLE", "DATA_GAP", "TIMEOUT"])
@pytest.mark.parametrize("later_outcome,later_bar_time", [
    ("TP_HIT", "2026-01-01T10:00:00+00:00"), ("SL_HIT", "2026-01-01T10:00:00+00:00"),
    ("TIMEOUT", None), ("DATA_GAP", None),
])
def test_non_causal_earlier_progressing_to_a_different_outcome_is_legitimate(
    earlier_outcome, later_outcome, later_bar_time,
):
    if earlier_outcome == later_outcome:
        pytest.skip("same-outcome case is covered by the REPRODUCED tests, not this one")
    earlier = _obs(outcome=earlier_outcome, resolved_bar_time=None,
                   evaluated_at="2026-01-01T05:00:00+00:00")
    later = _obs(outcome=later_outcome, resolved_bar_time=later_bar_time,
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.LEGITIMATE_PROGRESSION


def test_timeout_is_grouped_as_non_causal_not_given_stricter_treatment():
    """The operator's own explicit lock: TIMEOUT never carries a
    resolved_bar_time or walk_ohlc evidence, so it gets exactly the same
    progression-tolerant treatment as DATA_GAP/NOT_YET_ASSESSABLE --
    never the stricter treatment reserved for TP_HIT/SL_HIT."""
    earlier = _obs(outcome="TIMEOUT", resolved_bar_time=None,
                   evaluated_at="2026-01-08T01:00:00+00:00")
    later = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                 evaluated_at="2026-01-08T02:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.LEGITIMATE_PROGRESSION


# --- OUTCOME_REPRODUCED: non-causal, identical outcome ----------------------


@pytest.mark.parametrize("outcome", ["NOT_YET_ASSESSABLE", "DATA_GAP", "TIMEOUT"])
def test_non_causal_identical_outcome_is_reproduced_not_progression(outcome):
    earlier = _obs(outcome=outcome, resolved_bar_time=None, evaluated_at="2026-01-01T05:00:00+00:00")
    later = _obs(outcome=outcome, resolved_bar_time=None, evaluated_at="2026-01-01T09:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.OUTCOME_REPRODUCED


# --- structural misuse -------------------------------------------------------


def test_request_id_mismatch_raises():
    earlier = _obs(request_id="LIVE-IDENTITY-REQUEST-aaa")
    later = _obs(request_id="LIVE-IDENTITY-REQUEST-bbb", evaluated_at="2026-01-02T00:00:00+00:00")

    with pytest.raises(sor_rev.ShadowOutcomeRevisionError, match="request_id mismatch"):
        sor_rev.classify_cross_invocation_relationship(earlier, later)


def test_later_not_strictly_later_raises():
    earlier = _obs(evaluated_at="2026-01-02T00:00:00+00:00")
    later = _obs(evaluated_at="2026-01-02T00:00:00+00:00")  # identical, not strictly later

    with pytest.raises(sor_rev.ShadowOutcomeRevisionError, match="not strictly later"):
        sor_rev.classify_cross_invocation_relationship(earlier, later)


def test_later_before_earlier_raises():
    earlier = _obs(evaluated_at="2026-01-02T00:00:00+00:00")
    later = _obs(evaluated_at="2026-01-01T00:00:00+00:00")  # actually earlier

    with pytest.raises(sor_rev.ShadowOutcomeRevisionError, match="not strictly later"):
        sor_rev.classify_cross_invocation_relationship(earlier, later)


# --- descriptive output shape -----------------------------------------------


def test_result_shape_and_hypothesis_id_passthrough():
    earlier = _obs(hypothesis_id="CONFLUENCE-HYPOTHESIS-x", outcome="TP_HIT",
                   resolved_bar_time="2026-01-01T10:00:00+00:00",
                   evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(hypothesis_id="CONFLUENCE-HYPOTHESIS-x", outcome="TP_HIT",
                 resolved_bar_time="2026-01-01T10:00:00+00:00",
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert set(result.keys()) == {
        "request_id", "hypothesis_id", "relationship", "earlier_outcome", "later_outcome",
        "earlier_resolved_bar_time", "later_resolved_bar_time",
        "earlier_evaluated_at", "later_evaluated_at",
    }
    assert result["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-x"
    assert result["earlier_outcome"] == "TP_HIT"
    assert result["later_outcome"] == "TP_HIT"


def test_outcome_revised_carries_no_automatic_consequence():
    """Purely a descriptive fact -- the result dict never contains a
    governance verdict, a threshold comparison, or any judgment field
    beyond the relationship label itself."""
    earlier = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                   evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(outcome="SL_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                 evaluated_at="2026-01-02T00:00:00+00:00")

    result = sor_rev.classify_cross_invocation_relationship(earlier, later)
    assert result["relationship"] == sor_rev.OUTCOME_REVISED
    forbidden_keys = {"catastrophic_divergence", "threshold", "p_value", "n", "verdict",
                       "promotion", "terminality", "valid", "trustworthy"}
    assert not (forbidden_keys & result.keys())


# --- structural: no coupling with storage/execution/scheduler/provider -----


def _source_without_docstrings() -> str:
    source = inspect.getsource(sor_rev)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_storage_execution_scheduler_or_provider_coupling():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "from storage",
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "import scheduler", "from scheduler", "import main", "from main",
        "fetch_with_failover", "TwelveDataClient", "provider_at_observation",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_revision unexpectedly references {pattern!r}"


def test_never_calls_resolve_decision_outcome_or_verify_historical_stability():
    body = _source_without_docstrings()
    assert "resolve_decision_outcome(" not in body
    assert "verify_historical_stability(" not in body
