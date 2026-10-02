"""tests/test_shadow_decision_snapshot.py -- tests for backtest/
shadow_decision_snapshot.py (Hypothesis Discovery Engine, Phase 15D --
SHADOW Decision Snapshot Capture): idempotent capture, the
None-means-nothing-to-capture contract, storage-failure semantics kept
distinct from "no snapshot", and structural independence from every
sibling-phase/execution/scheduler module."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import shadow_decision_snapshot as sds
from storage import hypothesis_live_request as storage_live_request
from storage import shadow_decision_snapshot as storage_snapshot
from storage.d1_client import D1Error


def _live_identity_request_result(
    hypothesis_id: str = "CONFLUENCE-HYPOTHESIS-snap", decision_snapshot: dict | None = None,
) -> dict:
    record = storage_live_request.record_live_identity_request(
        hypothesis_id=hypothesis_id, decision_type="SINGLE_ENGINE", symbol="EURUSD", engine="wyckoff",
        engine_version="v2", timeframe="H4", risk_preset="balanced", preset_definition_hash="hash",
        risk_parameters_used_json="{}", decision="NO_TRADE", decision_reason="seeded for test",
        live_verdict="NO_TRADE",
    )
    if decision_snapshot is None:
        decision_snapshot = {
            "bar_time": "2026-09-01T00:00:00+00:00", "side": "BUY",
            "entry_price": 1.2345, "stop_loss": 1.2300, "take_profit": 1.2450,
        }
    return dict(record, identity={}, gate_result=None, decision_snapshot=decision_snapshot)


def _live_identity_request_result_without_snapshot(**overrides) -> dict:
    result = _live_identity_request_result(**overrides)
    result["decision_snapshot"] = None
    return result


# --- capture_decision_snapshot: the happy path -----------------------------


def test_capture_persists_a_real_snapshot():
    result = _live_identity_request_result()
    row = sds.capture_decision_snapshot(result)
    assert row is not None
    assert row["request_id"] == result["request_id"]
    assert row["hypothesis_id"] == result["hypothesis_id"]
    assert row["side"] == "BUY"
    assert row["entry_price"] == 1.2345


def test_capture_returns_none_without_any_write_when_decision_snapshot_is_none():
    result = _live_identity_request_result_without_snapshot()
    assert sds.capture_decision_snapshot(result) is None
    assert storage_snapshot.get_snapshot_by_request_id(result["request_id"]) is None


# --- idempotency ------------------------------------------------------------


def test_capture_is_idempotent():
    result = _live_identity_request_result()
    first = sds.capture_decision_snapshot(result)
    second = sds.capture_decision_snapshot(result)
    assert first == second
    assert first["snapshot_id"] == second["snapshot_id"]


def test_different_request_ids_for_the_same_hypothesis_get_different_snapshots():
    hyp = "CONFLUENCE-HYPOTHESIS-multi-snap"
    r1 = sds.capture_decision_snapshot(_live_identity_request_result(hypothesis_id=hyp))
    r2 = sds.capture_decision_snapshot(_live_identity_request_result(hypothesis_id=hyp))
    assert r1["snapshot_id"] != r2["snapshot_id"]
    assert r1["hypothesis_id"] == r2["hypothesis_id"] == hyp


# --- storage failure != "no snapshot" --------------------------------------


def test_storage_failure_raises_never_returns_none(monkeypatch):
    result = _live_identity_request_result()

    def _boom(**kwargs):
        raise D1Error("simulated real database failure")

    monkeypatch.setattr(storage_snapshot, "try_insert", _boom)

    with pytest.raises(sds.ShadowDecisionSnapshotError, match="storage failure"):
        sds.capture_decision_snapshot(result)


# --- structural: no coupling with sibling phases/execution/scheduler ------


def _source_without_docstrings() -> str:
    source = inspect.getsource(sds)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_promotion_gate_policy_health_attribution_or_execution_import():
    body = _source_without_docstrings()
    forbidden = (
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from backtest.execution_attribution", "import backtest.execution_attribution",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "from storage.engine_tracker", "import storage.engine_tracker",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_decision_snapshot unexpectedly references {pattern!r}"


def test_never_calls_run_pipeline():
    body = _source_without_docstrings()
    assert "run_pipeline(" not in body


def test_is_deterministic_same_result_same_output():
    result = _live_identity_request_result()
    first = sds.capture_decision_snapshot(result)
    second = sds.capture_decision_snapshot(result)
    assert first == second
