"""tests/test_execution_attribution.py -- tests for backtest/
execution_attribution.py (Hypothesis Discovery Engine, Phase 15C --
Execution Attribution Foundation): the single-source-of-identity
guarantee (hypothesis_id always derived, never caller-supplied),
idempotent creation, the strict lifecycle-sequence invariant in
classify_attribution_state(), and structural independence from every
sibling-phase/execution/time-window-inference mechanism."""
from __future__ import annotations

import inspect

import pytest

from backtest import execution_attribution as ea
from storage import hypothesis_live_request as storage_live_request


def _real_request_id(hypothesis_id: str = "CONFLUENCE-HYPOTHESIS-attrib") -> str:
    record = storage_live_request.record_live_identity_request(
        hypothesis_id=hypothesis_id, decision_type="SINGLE_ENGINE", symbol="EURUSD", engine="wyckoff",
        engine_version="v2", timeframe="H4", risk_preset="balanced", preset_definition_hash="hash",
        risk_parameters_used_json="{}", decision="PROCEED", decision_reason="seeded for test",
        live_verdict="EXECUTE",
    )
    return record["request_id"]


# --- create_attribution_record: single source of identity -----------------


def test_create_attribution_record_signature_accepts_only_request_id():
    params = list(inspect.signature(ea.create_attribution_record).parameters)
    assert params == ["request_id"]


def test_hypothesis_id_is_derived_from_the_real_stored_request_never_fabricated():
    request_id = _real_request_id(hypothesis_id="CONFLUENCE-HYPOTHESIS-derived")
    record = ea.create_attribution_record(request_id)
    assert record["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-derived"
    assert record["request_id"] == request_id


def test_raises_for_unknown_request_id():
    with pytest.raises(ea.ExecutionAttributionError, match="unknown request_id"):
        ea.create_attribution_record("LIVE-IDENTITY-REQUEST-ghost-does-not-exist")


def test_raises_for_empty_request_id():
    with pytest.raises(ea.ExecutionAttributionError, match="request_id"):
        ea.create_attribution_record("")


# --- idempotency ------------------------------------------------------------


def test_create_attribution_record_is_idempotent():
    request_id = _real_request_id()
    first = ea.create_attribution_record(request_id)
    second = ea.create_attribution_record(request_id)
    assert first == second
    assert first["attribution_id"] == second["attribution_id"]


def test_different_request_ids_for_the_same_hypothesis_get_different_attribution_rows():
    hyp = "CONFLUENCE-HYPOTHESIS-multi"
    r1 = ea.create_attribution_record(_real_request_id(hyp))
    r2 = ea.create_attribution_record(_real_request_id(hyp))
    assert r1["attribution_id"] != r2["attribution_id"]
    assert r1["hypothesis_id"] == r2["hypothesis_id"] == hyp


# --- every field 15C can ever produce is NULL except identity -------------


def test_created_record_has_all_downstream_fields_null():
    record = ea.create_attribution_record(_real_request_id())
    assert record["policy_id"] is None
    assert record["authorization_id"] is None
    assert record["execution_attempt_id"] is None
    assert record["trade_id"] is None
    assert record["outcome_signal_id"] is None


# --- get_attribution_record --------------------------------------------------


def test_get_attribution_record_returns_none_for_unknown_request_id():
    assert ea.get_attribution_record("LIVE-IDENTITY-REQUEST-never-attributed") is None


def test_get_attribution_record_returns_the_created_row():
    request_id = _real_request_id()
    created = ea.create_attribution_record(request_id)
    fetched = ea.get_attribution_record(request_id)
    assert fetched == created


# --- classify_attribution_state: the only reachable state in 15C ----------


def test_classify_attribution_state_is_always_decision_identity_only_in_15c():
    record = ea.create_attribution_record(_real_request_id())
    assert ea.classify_attribution_state(record) == ea.DECISION_IDENTITY_ONLY


# --- classify_attribution_state: strict lifecycle invariant (adversarial) -


def _record(**overrides) -> dict:
    base = {
        "authorization_id": None, "execution_attempt_id": None, "trade_id": None, "outcome_signal_id": None,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("populated,expected", [
    ({}, "DECISION_IDENTITY_ONLY"),
    ({"authorization_id": "A1"}, "AUTHORIZED"),
    ({"authorization_id": "A1", "execution_attempt_id": "E1"}, "ATTEMPTED"),
    ({"authorization_id": "A1", "execution_attempt_id": "E1", "trade_id": "T1"}, "FILLED"),
    ({"authorization_id": "A1", "execution_attempt_id": "E1", "trade_id": "T1", "outcome_signal_id": "O1"},
     "OUTCOME_LINKED"),
])
def test_classify_attribution_state_valid_sequences(populated, expected):
    assert ea.classify_attribution_state(_record(**populated)) == getattr(ea, expected)


@pytest.mark.parametrize("gapped", [
    {"trade_id": "T123"},  # authorization_id and execution_attempt_id both NULL, but trade_id set
    {"execution_attempt_id": "E1", "trade_id": "T1"},  # authorization_id NULL
    {"outcome_signal_id": "O1"},  # everything before it NULL
    {"authorization_id": "A1", "trade_id": "T1"},  # execution_attempt_id NULL, trade_id set
])
def test_classify_attribution_state_raises_on_a_gapped_sequence(gapped):
    """The operator's own locked correction: a populated field downstream
    of a still-NULL required field is a structurally invalid row, never a
    silently-misclassified one."""
    with pytest.raises(ea.ExecutionAttributionError, match="invalid attribution sequence"):
        ea.classify_attribution_state(_record(**gapped))


# --- structural: no coupling with sibling phases/execution/time-window ----


def _source_without_docstrings() -> str:
    import re
    source = inspect.getsource(ea)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_promotion_gate_policy_health_roster_shadow_or_execution_import():
    body = _source_without_docstrings()
    forbidden = (
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from backtest.live_roster", "import backtest.live_roster",
        "from backtest.shadow_observation", "import backtest.shadow_observation",
        "from backtest.shadow_evidence", "import backtest.shadow_evidence",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "from execution import authorization", "from execution import trade_executor",
        "from storage.engine_tracker", "import storage.engine_tracker",
        "from storage import engine_tracker",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"execution_attribution unexpectedly references {pattern!r}"


def test_no_time_window_or_proximity_matching_logic():
    body = _source_without_docstrings()
    forbidden = ("window_seconds", "timedelta", "time_window", "proximity")
    for pattern in forbidden:
        assert pattern not in body, f"execution_attribution unexpectedly references {pattern!r}"


def test_no_link_mutator_functions_exist_anywhere():
    public_names = [n for n in dir(ea) if not n.startswith("_")]
    forbidden_substrings = ("link_authorization", "link_execution_attempt", "link_trade", "link_outcome")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"execution_attribution unexpectedly exposes {name!r}"
    body = _source_without_docstrings()
    for forbidden in forbidden_substrings:
        assert f"def {forbidden}" not in body


def test_is_deterministic_same_request_id_same_output():
    request_id = _real_request_id()
    first = ea.create_attribution_record(request_id)
    second = ea.create_attribution_record(request_id)
    assert first == second
