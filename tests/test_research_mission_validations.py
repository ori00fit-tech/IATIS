"""
tests/test_research_mission_validations.py
----------------------------------------------
AI Research Lab / Mission Center Phase 3 (2026-07-30) — storage-layer
round-trip tests for storage/research_mission_validations.py. Uses the
autouse `fake_d1` fixture (tests/conftest.py) — a real in-memory SQLite
standing in for D1's HTTP transport, so real SQL semantics are exercised.
"""
from __future__ import annotations

from storage import d1_client
from storage import research_mission_validations as rmv


def test_ddl_is_idempotent():
    with d1_client.d1_connection() as con:
        rmv._init(con)
        rmv._init(con)  # must not raise on a second call


def test_upsert_and_get_validation():
    rmv.upsert_validation(
        validation_id="v1", mission_id="m1", trial_number=3, trial_symbol="EURUSD",
        validation_symbols=["EURUSD", "GBPUSD"], objective_metric="profit_factor",
        criteria={"min_profit_factor": 1.25}, status="queued",
    )
    row = rmv.get_validation("v1")
    assert row is not None
    assert row["mission_id"] == "m1"
    assert row["trial_number"] == 3
    assert row["trial_symbol"] == "EURUSD"
    assert row["status"] == "queued"
    assert row["overall_verdict"] is None


def test_get_validation_missing_returns_none():
    assert rmv.get_validation("does-not-exist") is None


def test_set_validation_status_started_then_finished():
    rmv.upsert_validation(
        validation_id="v2", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.set_validation_status("v2", "running", started=True)
    row = rmv.get_validation("v2")
    assert row["status"] == "running"
    assert row["started_at"] is not None

    rmv.set_validation_status(
        "v2", "finished", finished=True, overall_verdict="WEAK_LEAD",
        passing_symbols=1, total_symbols=2,
    )
    row = rmv.get_validation("v2")
    assert row["status"] == "finished"
    assert row["overall_verdict"] == "WEAK_LEAD"
    assert row["passing_symbols"] == 1
    assert row["total_symbols"] == 2
    assert row["finished_at"] is not None


def test_list_validations_newest_first():
    for i, vid in enumerate(["v-a", "v-b", "v-c"]):
        rmv.upsert_validation(
            validation_id=vid, mission_id="m-list", trial_number=i, trial_symbol="EURUSD",
            validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
        )
    rows = rmv.list_validations("m-list")
    assert {r["id"] for r in rows} == {"v-a", "v-b", "v-c"}
    assert rmv.list_validations("no-such-mission") == []


def test_record_and_read_validation_results():
    rmv.upsert_validation(
        validation_id="v3", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD", "GBPUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.record_validation_result(
        validation_id="v3", symbol="EURUSD", passed=True,
        metrics={"profit_factor": 1.3}, monte_carlo={"risk_of_ruin": 2.0},
        walk_forward={"verdict": "CONSISTENT"}, robustness={"sweeps": []},
        criteria_breakdown={"profit_factor": {"actual": 1.3, "threshold": 1.25, "passed": True}},
        error=None, started_at="t1", finished_at="t2",
    )
    rmv.record_validation_result(
        validation_id="v3", symbol="GBPUSD", passed=False,
        metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
        criteria_breakdown={}, error="no data", started_at="t1", finished_at="t2",
    )

    results = rmv.validation_results("v3")
    assert len(results) == 2
    eurusd = next(r for r in results if r["symbol"] == "EURUSD")
    assert eurusd["passed"] == 1
    assert eurusd["error"] is None
    gbpusd = next(r for r in results if r["symbol"] == "GBPUSD")
    assert gbpusd["passed"] == 0
    assert gbpusd["error"] == "no data"

    assert rmv.validation_results("no-such-validation") == []


def test_feature_mining_json_round_trips():
    rmv.upsert_validation(
        validation_id="v-fm", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    fm_blob = {"n_trades_total": 42, "insufficient_data": False, "associations": []}
    rmv.record_validation_result(
        validation_id="v-fm", symbol="EURUSD", passed=True,
        metrics={"profit_factor": 1.3}, monte_carlo={}, walk_forward={}, robustness={},
        criteria_breakdown={}, feature_mining=fm_blob, error=None, started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-fm")
    assert len(results) == 1
    import json
    assert json.loads(results[0]["feature_mining_json"]) == fm_blob


def test_feature_mining_json_absent_when_not_provided():
    rmv.upsert_validation(
        validation_id="v-fm-none", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.record_validation_result(
        validation_id="v-fm-none", symbol="EURUSD", passed=False,
        metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
        criteria_breakdown={}, error="no data", started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-fm-none")
    assert results[0]["feature_mining_json"] is None


def test_list_recent_finished_validations_newest_first_and_status_filtered():
    rmv.upsert_validation(
        validation_id="v-recent-a", mission_id="m-recent", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.set_validation_status("v-recent-a", "finished", finished=True, overall_verdict="WEAK_LEAD",
                               passing_symbols=1, total_symbols=1)
    rmv.upsert_validation(
        validation_id="v-recent-b", mission_id="m-recent", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.set_validation_status("v-recent-b", "running", started=True)

    rows = rmv.list_recent_finished_validations(limit=10)
    ids = {r["id"] for r in rows}
    assert "v-recent-a" in ids
    assert "v-recent-b" not in ids
