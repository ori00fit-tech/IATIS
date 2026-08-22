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
