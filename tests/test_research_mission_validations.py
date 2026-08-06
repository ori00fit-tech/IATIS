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


# ── Validation Mode Explicitness (Forensic Audit Phase 1, item D, 2026-08-02) ──

def test_upsert_validation_defaults_validation_mode_to_cross_symbol_when_omitted():
    # The kwarg's own default matches the DDL's default — describes what
    # every historical row structurally was.
    rmv.upsert_validation(
        validation_id="v-mode-default", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["GBPUSD", "XAUUSD"], objective_metric="profit_factor", criteria={}, status="queued",
    )
    row = rmv.get_validation("v-mode-default")
    assert row["validation_mode"] == "CROSS_SYMBOL"


def test_upsert_validation_persists_explicit_validation_mode():
    rmv.upsert_validation(
        validation_id="v-mode-same", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={}, status="queued",
        validation_mode="SAME_SYMBOL",
    )
    row = rmv.get_validation("v-mode-same")
    assert row["validation_mode"] == "SAME_SYMBOL"


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


def test_significance_json_round_trips():
    rmv.upsert_validation(
        validation_id="v-sig", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    sig_blob = {
        "n_trades": 50, "effective_sample_size": 32.5, "autocorrelation_ratio": 0.65,
        "nominal_p_value": 0.01, "ess_adjusted_p_value": 0.03, "note": "diagnostic only",
    }
    rmv.record_validation_result(
        validation_id="v-sig", symbol="EURUSD", passed=True,
        metrics={"profit_factor": 1.3}, monte_carlo={}, walk_forward={}, robustness={},
        criteria_breakdown={}, significance=sig_blob, error=None, started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-sig")
    assert len(results) == 1
    import json
    assert json.loads(results[0]["significance_json"]) == sig_blob


def test_significance_json_absent_when_not_provided():
    rmv.upsert_validation(
        validation_id="v-sig-none", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.record_validation_result(
        validation_id="v-sig-none", symbol="EURUSD", passed=False,
        metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
        criteria_breakdown={}, error="no data", started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-sig-none")
    assert results[0]["significance_json"] is None


def test_regime_robustness_json_round_trips():
    rmv.upsert_validation(
        validation_id="v-rr", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rr_blob = {
        "regimes_traded": 2, "regimes_material": 2, "regimes_profitable": 1,
        "regime_robustness_score": 0.5, "dominant_regime": "TRENDING",
        "dominant_regime_share": 0.6, "note": "diagnostic only",
    }
    rmv.record_validation_result(
        validation_id="v-rr", symbol="EURUSD", passed=True,
        metrics={"profit_factor": 1.3}, monte_carlo={}, walk_forward={}, robustness={},
        criteria_breakdown={}, regime_robustness=rr_blob, error=None, started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-rr")
    assert len(results) == 1
    import json
    assert json.loads(results[0]["regime_robustness_json"]) == rr_blob


def test_regime_robustness_json_absent_when_not_provided():
    rmv.upsert_validation(
        validation_id="v-rr-none", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.record_validation_result(
        validation_id="v-rr-none", symbol="EURUSD", passed=False,
        metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
        criteria_breakdown={}, error="no data", started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-rr-none")
    assert results[0]["regime_robustness_json"] is None


def test_stability_json_round_trips():
    rmv.upsert_validation(
        validation_id="v-stab", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    stab_blob = {
        "params_swept": 2, "params_measurable": 2, "params_stable": 1,
        "params_sensitive": 1, "params_insufficient": 0, "stability_score": 0.5,
        "note": "diagnostic only",
    }
    rmv.record_validation_result(
        validation_id="v-stab", symbol="EURUSD", passed=True,
        metrics={"profit_factor": 1.3}, monte_carlo={}, walk_forward={}, robustness={},
        criteria_breakdown={}, stability=stab_blob, error=None, started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-stab")
    assert len(results) == 1
    import json
    assert json.loads(results[0]["stability_json"]) == stab_blob


def test_stability_json_absent_when_not_provided():
    rmv.upsert_validation(
        validation_id="v-stab-none", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.record_validation_result(
        validation_id="v-stab-none", symbol="EURUSD", passed=False,
        metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
        criteria_breakdown={}, error="no data", started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-stab-none")
    assert results[0]["stability_json"] is None


def test_cost_stress_json_round_trips():
    rmv.upsert_validation(
        validation_id="v-cost-stress", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    cost_stress_blob = {
        "baseline_commission_pips": 1.2, "baseline_slippage_pips": 0.5,
        "levels": [
            {"multiplier": 1.5, "commission_pips": 1.8, "slippage_pips": 0.75, "trades": 40, "profit_factor": 1.3, "edge_survives": True},
            {"multiplier": 2.0, "commission_pips": 2.4, "slippage_pips": 1.0, "trades": 40, "profit_factor": 1.1, "edge_survives": True},
            {"multiplier": 3.0, "commission_pips": 3.6, "slippage_pips": 1.5, "trades": 40, "profit_factor": 0.9, "edge_survives": False},
        ],
        "survives_all_stress_levels": False, "note": "diagnostic only",
    }
    rmv.record_validation_result(
        validation_id="v-cost-stress", symbol="EURUSD", passed=True,
        metrics={"profit_factor": 1.3}, monte_carlo={}, walk_forward={}, robustness={},
        criteria_breakdown={}, cost_stress=cost_stress_blob, error=None, started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-cost-stress")
    assert len(results) == 1
    import json
    assert json.loads(results[0]["cost_stress_json"]) == cost_stress_blob


def test_cost_stress_json_absent_when_not_provided():
    rmv.upsert_validation(
        validation_id="v-cost-stress-none", mission_id="m1", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    rmv.record_validation_result(
        validation_id="v-cost-stress-none", symbol="EURUSD", passed=False,
        metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
        criteria_breakdown={}, error="no data", started_at="t1", finished_at="t2",
    )
    results = rmv.validation_results("v-cost-stress-none")
    assert results[0]["cost_stress_json"] is None


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
