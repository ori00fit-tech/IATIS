"""tests/test_matrix_evidence.py -- pure-function tests for backtest/matrix_evidence.py."""
from __future__ import annotations

import json

from backtest import matrix_evidence as evidence
from backtest import research_matrix as rm
from backtest.multiple_testing import bonferroni_alpha


def _cell(**overrides) -> dict:
    base = {
        "cell_id": "MATRIX-CELL-abc123",
        "family_id": "fam1",
        "fingerprint": "abc123",
        "symbol": "EURUSD",
        "bundle_json": json.dumps({"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"]}),
        "risk_preset": "balanced",
        "confluence_overrides_json": None,
        "engine_variants_json": None,
        "data_provider": None,
        "status": rm.QUEUED,
        "rejection_reason": None,
        "stage_a_mission_id": None,
        "stage_a_trial_number": None,
        "stage_a_metrics_json": None,
        "stage_a_p_value": None,
        "lead_id": None,
        "stage_b_validation_id": None,
        "stage_b_verdict": None,
        "requeue_count": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


# --- family_status_breakdown -------------------------------------------


def test_family_status_breakdown_counts_every_known_status_including_zero():
    cells = [_cell(status=rm.SCREENED), _cell(status=rm.SCREENED), _cell(status=rm.REJECTED)]
    breakdown = evidence.family_status_breakdown(cells)
    assert breakdown[rm.SCREENED] == 2
    assert breakdown[rm.REJECTED] == 1
    assert breakdown[rm.QUEUED] == 0  # present, zero -- never a missing key
    assert breakdown[rm.VALIDATED] == 0


def test_family_status_breakdown_empty_cells_all_zero():
    breakdown = evidence.family_status_breakdown([])
    assert all(v == 0 for v in breakdown.values())
    assert set(breakdown.keys()) == set(rm.CELL_STATUSES)


# --- family_evidence_summary --------------------------------------------


def test_family_evidence_summary_reports_fixed_planned_n_and_corrected_alpha():
    family = {"family_id": "fam1", "planned_n": 40, "family_alpha": 0.05, "symbols_json": '["EURUSD","GBPUSD"]', "created_at": "2026-01-01T00:00:00+00:00"}
    cells = [_cell(status=rm.SCREENED), _cell(status=rm.CANDIDATE)]
    summary = evidence.family_evidence_summary(family, cells)
    assert summary["planned_n"] == 40
    assert summary["family_alpha"] == 0.05
    assert summary["bonferroni_alpha"] == bonferroni_alpha(40, 0.05)
    assert summary["symbols"] == ["EURUSD", "GBPUSD"]
    assert summary["cells_generated"] == 2
    assert summary["status_breakdown"][rm.SCREENED] == 1
    assert summary["status_breakdown"][rm.CANDIDATE] == 1


def test_family_evidence_summary_sums_requeue_counts_across_cells():
    family = {"family_id": "fam1", "planned_n": 2, "family_alpha": 0.05, "symbols_json": None, "created_at": "x"}
    cells = [_cell(requeue_count=2), _cell(requeue_count=3), _cell(requeue_count=0)]
    summary = evidence.family_evidence_summary(family, cells)
    assert summary["total_requeues"] == 5


def test_family_evidence_summary_tolerates_malformed_symbols_json():
    family = {"family_id": "fam1", "planned_n": 1, "family_alpha": 0.05, "symbols_json": "not json", "created_at": "x"}
    summary = evidence.family_evidence_summary(family, [])
    assert summary["symbols"] == []


# --- group stats (per-symbol/per-bundle/per-risk-preset) ------------------


def test_per_symbol_stats_groups_correctly():
    cells = [
        _cell(symbol="EURUSD", status=rm.SCREENED),
        _cell(symbol="EURUSD", status=rm.REJECTED),
        _cell(symbol="GBPUSD", status=rm.VALIDATED),
    ]
    stats = evidence.per_symbol_stats(cells)
    assert stats["EURUSD"]["total"] == 2
    assert stats["EURUSD"][rm.SCREENED] == 1
    assert stats["EURUSD"][rm.REJECTED] == 1
    assert stats["GBPUSD"]["total"] == 1
    assert stats["GBPUSD"][rm.VALIDATED] == 1


def test_per_bundle_stats_groups_by_bundle_name_not_raw_json():
    bundle_a = json.dumps({"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"]})
    bundle_b = json.dumps({"name": "NNFX trend", "timeframes": ["H4"], "engines": ["nnfx"]})
    cells = [
        _cell(bundle_json=bundle_a, status=rm.CANDIDATE),
        _cell(bundle_json=bundle_a, status=rm.REJECTED),
        _cell(bundle_json=bundle_b, status=rm.VALIDATED),
    ]
    stats = evidence.per_bundle_stats(cells)
    assert stats["SMC only"]["total"] == 2
    assert stats["NNFX trend"]["total"] == 1
    assert stats["NNFX trend"][rm.VALIDATED] == 1


def test_per_bundle_stats_tolerates_malformed_bundle_json():
    cells = [_cell(bundle_json="{not valid json")]
    stats = evidence.per_bundle_stats(cells)
    assert "?" in stats
    assert stats["?"]["total"] == 1


def test_per_risk_preset_stats_groups_correctly():
    cells = [_cell(risk_preset="balanced"), _cell(risk_preset="balanced"), _cell(risk_preset="aggressive")]
    stats = evidence.per_risk_preset_stats(cells)
    assert stats["balanced"]["total"] == 2
    assert stats["aggressive"]["total"] == 1


# --- cell_evidence -------------------------------------------------------


def test_cell_evidence_decodes_json_columns():
    cell = _cell(
        bundle_json=json.dumps({"name": "SMC only"}),
        confluence_overrides_json=json.dumps({"min_engines_agreeing": 1}),
        engine_variants_json=json.dumps({"price_action": "v2"}),
        stage_a_metrics_json=json.dumps({"profit_factor": 1.8}),
    )
    result = evidence.cell_evidence(cell)
    assert result["bundle"] == {"name": "SMC only"}
    assert result["confluence_overrides"] == {"min_engines_agreeing": 1}
    assert result["engine_variants"] == {"price_action": "v2"}
    assert result["stage_a"]["metrics"] == {"profit_factor": 1.8}


def test_cell_evidence_handles_absent_stage_a_and_stage_b():
    cell = _cell()
    result = evidence.cell_evidence(cell)
    assert result["stage_a"]["trial_detail"] is None
    assert result["stage_b"]["validation_detail"] is None
    assert result["stage_a"]["mission_id"] is None
    assert result["stage_b"]["validation_id"] is None


def test_cell_evidence_includes_provided_trial_and_validation_detail():
    cell = _cell(stage_a_mission_id="m1", stage_a_trial_number=0, stage_b_validation_id="v1")
    trial = {"state": "COMPLETE", "trades": 50}
    validation = {"overall_verdict": "SAME_SYMBOL_CONFIRMED"}
    result = evidence.cell_evidence(cell, trial=trial, validation=validation)
    assert result["stage_a"]["trial_detail"] == trial
    assert result["stage_b"]["validation_detail"] == validation


def test_cell_evidence_carries_requeue_count_and_identity_fields():
    cell = _cell(requeue_count=3, family_id="famX", fingerprint="deadbeef")
    result = evidence.cell_evidence(cell)
    assert result["requeue_count"] == 3
    assert result["family_id"] == "famX"
    assert result["fingerprint"] == "deadbeef"
    assert result["cell_id"] == cell["cell_id"]


def test_cell_evidence_carries_research_code_commit():
    cell = _cell(research_code_commit="abc1234")
    result = evidence.cell_evidence(cell)
    assert result["research_code_commit"] == "abc1234"


def test_cell_evidence_handles_absent_validation_result():
    cell = _cell()
    result = evidence.cell_evidence(cell)
    assert result["stage_b"]["validation_result"] is None


def test_cell_evidence_decodes_validation_result_diagnostics():
    """Phase 2C: the Stage B per-symbol validation_results row carries
    Monte Carlo/Walk-Forward/robustness/regime-robustness/stability/
    cost-stress — all diagnostic-only, never gating `passed`."""
    cell = _cell(stage_b_validation_id="v1")
    validation_result_row = {
        "symbol": "EURUSD",
        "passed": 1,
        "metrics_json": json.dumps({"profit_factor": 1.9}),
        "monte_carlo_json": json.dumps({"p5_pf": 1.1}),
        "walk_forward_json": json.dumps({"oos_pf": 1.4}),
        "robustness_json": json.dumps({"stable": True}),
        "criteria_breakdown_json": json.dumps({"min_trades": True}),
        "significance_json": json.dumps({"ess": 42}),
        "regime_robustness_json": json.dumps({"trending_pf": 1.5}),
        "stability_json": json.dumps({"rolling_pf_std": 0.2}),
        "cost_stress_json": json.dumps({"pf_at_2x_costs": 1.1}),
        "discovery_score_json": json.dumps({"score": 0.7}),
        "error": None,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T01:00:00+00:00",
    }
    result = evidence.cell_evidence(cell, validation_result=validation_result_row)
    vr = result["stage_b"]["validation_result"]
    assert vr["symbol"] == "EURUSD"
    assert vr["passed"] is True
    assert vr["metrics"] == {"profit_factor": 1.9}
    assert vr["monte_carlo"] == {"p5_pf": 1.1}
    assert vr["walk_forward"] == {"oos_pf": 1.4}
    assert vr["robustness"] == {"stable": True}
    assert vr["criteria_breakdown"] == {"min_trades": True}
    assert vr["significance"] == {"ess": 42}
    assert vr["regime_robustness"] == {"trending_pf": 1.5}
    assert vr["stability"] == {"rolling_pf_std": 0.2}
    assert vr["cost_stress"] == {"pf_at_2x_costs": 1.1}
    assert vr["discovery_score"] == {"score": 0.7}


# --- compare_cells_provenance --------------------------------------------


def test_compare_cells_provenance_same_family_and_commit():
    cells = [
        _cell(family_id="fam1", research_code_commit="abc1234"),
        _cell(family_id="fam1", research_code_commit="abc1234", symbol="GBPUSD"),
    ]
    result = evidence.compare_cells_provenance(cells)
    assert result["cell_count"] == 2
    assert result["same_family"] is True
    assert result["same_commit"] is True
    assert result["family_ids"] == ["fam1"]
    assert result["commits"] == ["abc1234"]


def test_compare_cells_provenance_cross_family_flags_false():
    cells = [
        _cell(family_id="famA"),
        _cell(family_id="famB", symbol="GBPUSD"),
    ]
    result = evidence.compare_cells_provenance(cells)
    assert result["same_family"] is False
    assert result["family_ids"] == ["famA", "famB"]


def test_compare_cells_provenance_cross_commit_flags_false():
    cells = [
        _cell(research_code_commit="commitA"),
        _cell(research_code_commit="commitB", symbol="GBPUSD"),
    ]
    result = evidence.compare_cells_provenance(cells)
    assert result["same_commit"] is False
    assert result["commits"] == ["commitA", "commitB"]


def test_compare_cells_provenance_missing_commit_treated_as_unknown():
    cells = [_cell(research_code_commit=None), _cell(research_code_commit=None, symbol="GBPUSD")]
    result = evidence.compare_cells_provenance(cells)
    assert result["same_commit"] is True
    assert result["commits"] == ["unknown"]


def test_compare_cells_provenance_same_data_provider():
    cells = [_cell(data_provider="ccxt"), _cell(data_provider="ccxt", symbol="GBPUSD")]
    result = evidence.compare_cells_provenance(cells)
    assert result["same_data_provider"] is True
    assert result["data_providers"] == ["ccxt"]


def test_compare_cells_provenance_missing_data_provider_treated_as_unspecified():
    cells = [_cell(data_provider=None)]
    result = evidence.compare_cells_provenance(cells)
    assert result["data_providers"] == ["unspecified"]


def test_compare_cells_provenance_detects_same_hypothesis_lineage():
    """Comparison Type 3: same symbol/bundle/risk_preset across different
    code commits -- detects whether an apparent 'improvement' came only
    from a code change, not the hypothesis itself."""
    bundle = json.dumps({"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"]})
    cells = [
        _cell(symbol="EURUSD", bundle_json=bundle, risk_preset="balanced", research_code_commit="commitA"),
        _cell(symbol="EURUSD", bundle_json=bundle, risk_preset="balanced", research_code_commit="commitB"),
        _cell(symbol="EURUSD", bundle_json=bundle, risk_preset="balanced", research_code_commit="commitC"),
    ]
    result = evidence.compare_cells_provenance(cells)
    assert result["same_hypothesis_lineage"] is True
    assert result["lineage_key"] == {"symbol": "EURUSD", "bundle": "SMC only", "risk_preset": "balanced"}
    assert result["same_commit"] is False


def test_compare_cells_provenance_lineage_false_when_symbol_differs():
    bundle = json.dumps({"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"]})
    cells = [
        _cell(symbol="EURUSD", bundle_json=bundle, risk_preset="balanced"),
        _cell(symbol="GBPUSD", bundle_json=bundle, risk_preset="balanced"),
    ]
    result = evidence.compare_cells_provenance(cells)
    assert result["same_hypothesis_lineage"] is False
    assert result["lineage_key"] is None


def test_compare_cells_provenance_single_cell_all_flags_trivially_true():
    result = evidence.compare_cells_provenance([_cell()])
    assert result["cell_count"] == 1
    assert result["same_family"] is True
    assert result["same_commit"] is True
    assert result["same_data_provider"] is True
    assert result["same_hypothesis_lineage"] is True


def test_compare_cells_provenance_ignores_none_family_id():
    cells = [_cell(family_id=None), _cell(family_id=None, symbol="GBPUSD")]
    result = evidence.compare_cells_provenance(cells)
    assert result["family_ids"] == []
    assert result["same_family"] is True
