"""tests/test_policy_health.py -- tests for backtest/policy_health.py
(Hypothesis Discovery Engine, Phase 14 — Policy Health Classification),
covering the locked comparison contract and escalation rule exactly."""
from __future__ import annotations

import inspect

import pytest

from backtest import policy_health as ph


def _expected(**overrides) -> dict:
    base = {
        "trade_count": 20, "profit_factor": 1.5, "max_drawdown": 0.10,
        "win_rate": 0.55, "execution_slippage": 0.5,
    }
    base.update(overrides)
    return base


# --- exact-match baseline is HEALTHY ------------------------------------


def test_identical_profiles_are_healthy():
    result = ph.assess_policy_health(expected_profile=_expected(), observed_profile=_expected())
    assert result["status"] == ph.HEALTHY
    assert result["reasons"] == []
    assert result["hard_fields"] == []
    assert result["soft_fields"] == []


# --- per-field soft/hard thresholds (decrease-concerning) --------------


@pytest.mark.parametrize("field,expected_value,soft_value,hard_value", [
    ("profit_factor", 1.5, 1.2, 0.9),
    ("win_rate", 0.55, 0.45, 0.30),
])
def test_decrease_concerning_fields_soft_and_hard(field, expected_value, soft_value, hard_value):
    expected = _expected(**{field: expected_value})
    soft = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(**{field: soft_value}))
    hard = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(**{field: hard_value}))
    assert soft["soft_fields"] == [field] and soft["hard_fields"] == []
    assert hard["hard_fields"] == [field]


# --- per-field soft/hard thresholds (increase-concerning) --------------


@pytest.mark.parametrize("field,expected_value,soft_value,hard_value", [
    ("max_drawdown", 0.10, 0.12, 0.20),
    ("execution_slippage", 0.5, 0.6, 0.9),
])
def test_increase_concerning_fields_soft_and_hard(field, expected_value, soft_value, hard_value):
    expected = _expected(**{field: expected_value})
    soft = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(**{field: soft_value}))
    hard = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(**{field: hard_value}))
    assert soft["soft_fields"] == [field] and soft["hard_fields"] == []
    assert hard["hard_fields"] == [field]


# --- symmetric field (trade_count) ---------------------------------------


def test_trade_count_soft_both_directions():
    low = ph.compare_profiles(expected_profile=_expected(), observed_profile=_expected(trade_count=12))
    high = ph.compare_profiles(expected_profile=_expected(), observed_profile=_expected(trade_count=28))
    assert low["soft_fields"] == ["trade_count"]
    assert high["soft_fields"] == ["trade_count"]


def test_trade_count_hard_both_directions():
    """The roadmap's own dramatic example: 8->40 trades/month is a ~5x
    frequency explosion -- a far larger deviation than the hard band.
    Complete collapse to zero trades (observed=0) is always HARD against
    any positive expected value (the symmetric HARD fraction is
    deliberately 80%, not 100%, so this boundary case is unambiguous)."""
    collapsed = ph.compare_profiles(expected_profile=_expected(), observed_profile=_expected(trade_count=0))
    exploded = ph.compare_profiles(expected_profile=_expected(), observed_profile=_expected(trade_count=45))
    assert collapsed["hard_fields"] == ["trade_count"]
    assert exploded["hard_fields"] == ["trade_count"]


def test_trade_count_within_band_is_clean():
    result = ph.compare_profiles(expected_profile=_expected(), observed_profile=_expected(trade_count=22))
    assert result["soft_fields"] == [] and result["hard_fields"] == []


# --- escalation rule (breadth within one snapshot, never time) -------------


def test_zero_hard_zero_soft_is_healthy():
    result = ph.assess_policy_health(expected_profile=_expected(), observed_profile=_expected())
    assert result["status"] == ph.HEALTHY


def test_zero_hard_one_or_more_soft_is_watch():
    result = ph.assess_policy_health(expected_profile=_expected(), observed_profile=_expected(profit_factor=1.2))
    assert result["status"] == ph.WATCH


def test_exactly_one_hard_is_degraded():
    result = ph.assess_policy_health(expected_profile=_expected(), observed_profile=_expected(profit_factor=0.9))
    assert result["status"] == ph.DEGRADED


def test_two_or_more_hard_is_paused():
    result = ph.assess_policy_health(
        expected_profile=_expected(),
        observed_profile=_expected(profit_factor=0.9, win_rate=0.30),
    )
    assert result["status"] == ph.PAUSED


def test_many_soft_fields_never_escalates_past_watch_without_a_hard():
    result = ph.assess_policy_health(
        expected_profile=_expected(),
        observed_profile=_expected(profit_factor=1.2, win_rate=0.45, max_drawdown=0.12,
                                    execution_slippage=0.6, trade_count=12),
    )
    assert result["status"] == ph.WATCH  # all five soft, zero hard -- never DEGRADED/PAUSED


def test_reasons_accumulate_for_every_failing_field_not_just_the_first():
    result = ph.assess_policy_health(
        expected_profile=_expected(),
        observed_profile=_expected(profit_factor=0.9, win_rate=0.30, max_drawdown=0.20),
    )
    assert len(result["reasons"]) == 3
    assert len(result["hard_fields"]) == 3
    assert result["status"] == ph.PAUSED


# --- missing fields: structural error, never a silent default ------------


def test_missing_field_in_expected_raises():
    incomplete = _expected()
    del incomplete["win_rate"]
    with pytest.raises(ph.PolicyHealthError, match="win_rate"):
        ph.assess_policy_health(expected_profile=incomplete, observed_profile=_expected())


def test_missing_field_in_observed_raises():
    incomplete = _expected()
    del incomplete["max_drawdown"]
    with pytest.raises(ph.PolicyHealthError, match="max_drawdown"):
        ph.assess_policy_health(expected_profile=_expected(), observed_profile=incomplete)


# --- zero-baseline edge case: no crash, conservative flag -------------------


def test_zero_expected_value_never_crashes():
    expected = _expected(execution_slippage=0.0)
    result = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(execution_slippage=0.3))
    assert "execution_slippage" in result["hard_fields"]  # any nonzero observed against a 0 baseline is HARD


def test_zero_expected_trade_count_never_crashes():
    expected = _expected(trade_count=0)
    result = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(trade_count=5))
    assert "trade_count" in result["hard_fields"]


# --- structural: no DB/live/execution coupling, no lifecycle mutation ------


def _source_without_module_docstring() -> str:
    source = inspect.getsource(ph)
    return source.split('"""', 2)[-1]


def test_no_storage_execution_scheduler_or_main_import():
    body = _source_without_module_docstring()
    forbidden = ("from storage", "import storage", "from execution", "import execution",
                 "import scheduler", "from scheduler", "import main", "from main")
    for pattern in forbidden:
        assert pattern not in body, f"policy_health unexpectedly references {pattern!r}"


def test_no_policy_registry_lifecycle_mutation():
    body = _source_without_module_docstring()
    forbidden = ("revoke_policy(", "activate_policy(", "validate_policy(", "policy_registry")
    for pattern in forbidden:
        assert pattern not in body, f"policy_health unexpectedly references {pattern!r}"


def test_no_previous_status_or_history_parameter():
    """The operator's own locked rejection of statefulness: no function
    here accepts a previous-state/history parameter of any kind."""
    for name, fn in vars(ph).items():
        if not inspect.isfunction(fn) or name.startswith("_") or inspect.getmodule(fn) is not ph:
            continue
        params = set(inspect.signature(fn).parameters)
        forbidden = {"previous_status", "history", "prior_status", "last_status"}
        assert not (params & forbidden), f"{name}() unexpectedly accepts {params & forbidden}"


def test_nothing_outside_tests_imports_policy_health_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "policy_health" not in text, f"{path} unexpectedly references policy_health"


def test_is_deterministic_same_inputs_same_output():
    kwargs = dict(expected_profile=_expected(), observed_profile=_expected(profit_factor=0.9))
    assert ph.assess_policy_health(**kwargs) == ph.assess_policy_health(**kwargs)
