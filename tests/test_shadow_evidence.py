"""tests/test_shadow_evidence.py -- tests for backtest/shadow_evidence.py
(Hypothesis Discovery Engine, Phase 15B — SHADOW Evidence Record): the
locked 6-field contract, the permanent divergence_assessable=False
structural fact (never a measurement outcome), and structural independence
from promotion_gate/policy_health/live_roster/execution internals and from
any storage write."""
from __future__ import annotations

import inspect

import pytest

from backtest import shadow_evidence as se
from backtest.shadow_observation import compute_shadow_observed_profile
from storage import hypothesis_live_request as storage_live_request


def _seed_request(hypothesis_id: str, *, live_verdict: str | None, decision: str) -> None:
    storage_live_request.record_live_identity_request(
        hypothesis_id=hypothesis_id, decision_type="SINGLE_ENGINE", symbol="EURUSD", engine="wyckoff",
        engine_version="v2", timeframe="H4", risk_preset="balanced", preset_definition_hash="hash",
        risk_parameters_used_json="{}", decision=decision, decision_reason="seeded for test",
        live_verdict=live_verdict,
    )


# --- 1: a normal record matches compute_shadow_observed_profile() verbatim -


def test_observed_profile_matches_compute_shadow_observed_profile_verbatim():
    hyp = "H-se-1"
    _seed_request(hyp, live_verdict="EXECUTE", decision="PROCEED")
    _seed_request(hyp, live_verdict="NO_TRADE", decision="NO_TRADE")

    record = se.build_shadow_evidence_record(hyp, window=10)
    assert record["observed_profile"] == compute_shadow_observed_profile(hyp, window=10)


# --- 2: records_found == observed_profile["request_count"] invariant ------


@pytest.mark.parametrize("seed_count,window", [(0, 5), (1, 5), (3, 2), (7, 100)])
def test_records_found_equals_request_count_invariant(seed_count, window):
    hyp = f"H-se-invariant-{seed_count}-{window}"
    for _ in range(seed_count):
        _seed_request(hyp, live_verdict="EXECUTE", decision="PROCEED")
    record = se.build_shadow_evidence_record(hyp, window=window)
    assert record["records_found"] == record["observed_profile"]["request_count"]


# --- 3: divergence_assessable is always False -- a structural fact --------


@pytest.mark.parametrize("seed_count,window,verdict,decision", [
    (0, 5, "EXECUTE", "PROCEED"),
    (10, 5, "EXECUTE", "PROCEED"),
    (3, 50, "NO_TRADE", "NO_TRADE"),
])
def test_divergence_assessable_is_always_false_regardless_of_data_shape(seed_count, window, verdict, decision):
    hyp = f"H-se-divergence-{seed_count}-{window}-{verdict}"
    for _ in range(seed_count):
        _seed_request(hyp, live_verdict=verdict, decision=decision)
    record = se.build_shadow_evidence_record(hyp, window=window)
    assert record["divergence_assessable"] is False
    assert record["divergence_assessable"] is se.DIVERGENCE_ASSESSABLE


# --- 4: divergence_reason is non-empty and names the real gap -------------


def test_divergence_reason_names_the_missing_outcome_data():
    record = se.build_shadow_evidence_record("H-se-reason", window=10)
    assert record["divergence_reason"] == se.DIVERGENCE_NOT_ASSESSABLE_REASON
    assert "P&L" in record["divergence_reason"] or "outcome" in record["divergence_reason"]


# --- 5: no history -> every count field is zero, never an error -----------


def test_no_history_returns_all_zero_counts_not_an_error():
    record = se.build_shadow_evidence_record("H-se-never-requested", window=10)
    assert record["records_found"] == 0
    assert record["observed_profile"] == {
        "request_count": 0, "execute_verdict_count": 0, "proceed_count": 0, "no_trade_count": 0,
    }
    assert record["divergence_assessable"] is False


# --- 6: window is a count (reused 15A semantics), never a time range ------


def test_window_is_a_count_only_the_most_recent_are_considered():
    hyp = "H-se-window"
    for _ in range(5):
        _seed_request(hyp, live_verdict="EXECUTE", decision="PROCEED")
    record = se.build_shadow_evidence_record(hyp, window=3)
    assert record["records_found"] == 3
    assert record["window"] == 3


# --- 7: ShadowEvidenceError for structural misuse --------------------------


def test_raises_for_empty_hypothesis_id():
    with pytest.raises(se.ShadowEvidenceError, match="hypothesis_id"):
        se.build_shadow_evidence_record("", window=10)


@pytest.mark.parametrize("bad_window", [0, -1, -10])
def test_raises_for_non_positive_window(bad_window):
    with pytest.raises(se.ShadowEvidenceError, match="window"):
        se.build_shadow_evidence_record("H-se-badwindow", window=bad_window)


@pytest.mark.parametrize("bad_window", [1.5, "10", None, True, False])
def test_raises_for_non_integer_window(bad_window):
    with pytest.raises(se.ShadowEvidenceError, match="window"):
        se.build_shadow_evidence_record("H-se-badwindow-type", window=bad_window)


# --- 8: deterministic -- same inputs, same output --------------------------


def test_is_deterministic_same_inputs_same_output():
    hyp = "H-se-deterministic"
    _seed_request(hyp, live_verdict="EXECUTE", decision="PROCEED")
    first = se.build_shadow_evidence_record(hyp, window=10)
    second = se.build_shadow_evidence_record(hyp, window=10)
    assert first == second


# --- 9-11: structural boundary -- the exact dependency direction ----------


def _source_without_docstrings() -> str:
    """Strips EVERY triple-quoted string (module AND per-function
    docstrings) before scanning -- this module's own docstrings legitimately
    explain, in prose, several calls/shapes it must never make, which would
    otherwise trip a bare-substring check against the prose itself."""
    import re
    source = inspect.getsource(se)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_promotion_gate_policy_health_roster_or_execution_import():
    body = _source_without_docstrings()
    forbidden = (
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from backtest.live_roster", "import backtest.live_roster",
        "from storage.live_roster", "import storage.live_roster",
        "from storage.hypothesis_live_request", "import storage.hypothesis_live_request",
        "from storage import hypothesis_live_request", "from storage import live_roster",
        "from execution", "import execution",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_evidence unexpectedly references {pattern!r}"


def test_never_calls_evaluate_promotion_gate_or_assess_policy_health():
    body = _source_without_docstrings()
    assert "evaluate_promotion_gate(" not in body
    assert "assess_policy_health(" not in body


def test_no_storage_write_of_any_kind():
    body = _source_without_docstrings()
    forbidden = ("record_", "insert_", "try_insert_", "update_", "write_", "d1_connection(")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_evidence unexpectedly references {pattern!r}"


# --- 12: forbidden fields never appear in the output -----------------------


def test_forbidden_fields_never_appear_in_the_output():
    hyp = "H-se-forbidden"
    _seed_request(hyp, live_verdict="EXECUTE", decision="PROCEED")
    record = se.build_shadow_evidence_record(hyp, window=10)

    top_level_forbidden = ("completed", "diverged_catastrophically", "window_fully_populated")
    for field in top_level_forbidden:
        assert field not in record, f"shadow_evidence record unexpectedly includes {field!r}"

    performance_forbidden = ("trade_count", "win_rate", "profit_factor", "max_drawdown", "execution_slippage")
    for field in performance_forbidden:
        assert field not in record
        assert field not in record["observed_profile"]


def test_output_has_exactly_the_locked_six_fields():
    record = se.build_shadow_evidence_record("H-se-shape", window=10)
    assert set(record) == {
        "hypothesis_id", "window", "observed_profile", "records_found",
        "divergence_assessable", "divergence_reason",
    }
    assert set(record["observed_profile"]) == {
        "request_count", "execute_verdict_count", "proceed_count", "no_trade_count",
    }
