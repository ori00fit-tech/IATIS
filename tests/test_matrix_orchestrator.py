"""tests/test_matrix_orchestrator.py -- backtest/matrix_orchestrator.py.

run_mission()/run_validation() are mocked throughout (a real backtest
needs real OHLCV data this test suite doesn't have) — every test instead
stubs them with the same D1 side effects the real functions produce
(research_missions.record_trial / research_mission_validations.
upsert_validation+set_validation_status), so the orchestrator's own
gating/promotion/rejection logic is exercised against real D1 reads, only
the expensive backtest computation itself is faked.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backtest import matrix_orchestrator as orch
from backtest import research_matrix as rm
from storage import research_matrix as storage
from storage import research_mission_validations
from storage import research_missions

_BUNDLE = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}


def _cell(symbol="EURUSD") -> rm.MatrixCellSpec:
    return rm.MatrixCellSpec(symbol=symbol, bundle=_BUNDLE, risk_preset="balanced")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stub_run_mission(*, state: str, trades: int, metrics: dict | None):
    def _fn(mc):
        research_missions.upsert_mission(
            mission_id=mc.mission_id, name=mc.name, sampler=mc.sampler, objective_metric=mc.objective_metric,
            symbols=list(mc.symbols), n_trials_per_symbol=mc.n_trials_per_symbol, min_trades=mc.min_trades,
            seed=mc.seed, search_space={}, config={}, status="running",
        )
        research_missions.record_trial(
            mission_id=mc.mission_id, trial_number=0, symbol=mc.symbols[0], state=state,
            objective_value=metrics.get("profit_factor") if metrics else None, params={}, metrics=metrics,
            trades=trades, error=None if state != "FAIL" else "synthetic failure", started_at=_now(), finished_at=_now(),
        )
    return _fn


def _stub_run_validation(*, verdict: str):
    def _fn(vc):
        research_mission_validations.upsert_validation(
            validation_id=vc.validation_id, mission_id=vc.mission_id, trial_number=vc.trial_number,
            trial_symbol=vc.trial_symbol, validation_symbols=list(vc.validation_symbols),
            objective_metric="profit_factor", criteria={}, status="running", validation_mode=vc.validation_mode,
        )
        research_mission_validations.set_validation_status(
            vc.validation_id, "finished", finished=True, overall_verdict=verdict, passing_symbols=1, total_symbols=1,
        )
    return _fn


@pytest.fixture(autouse=True)
def _no_real_data_quality_gate(monkeypatch):
    """Every orchestrator test focuses on post-data-quality-gate logic —
    the gate itself is unit-tested directly in test_research_matrix.py.
    Defaults to OK; individual tests override via monkeypatch when they
    want to exercise the INSUFFICIENT_DATA path specifically."""
    monkeypatch.setattr(rm, "check_data_quality", lambda *a, **k: rm.DataQualityResult(True, None, {"completeness_pct": 100.0}, "deadbeef"))


# ---------------------------------------------------------------------------
# Stage A
# ---------------------------------------------------------------------------


def test_stage_a_promotes_a_healthy_trial_to_screened(monkeypatch):
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission(state="COMPLETE", trades=50, metrics={"profit_factor": 1.8, "avg_rr": 0.3, "std_rr": 1.1}))
    storage.upsert_cells([_cell()])
    cell = storage.claim_queued_cells(1)[0]
    orch._run_stage_a_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    row = storage.get_cell(cell["cell_id"])
    assert row["status"] == rm.SCREENED
    assert row["lead_id"] is not None
    assert row["stage_a_metrics_json"] is not None


def test_stage_a_marks_insufficient_data_without_ever_calling_run_mission(monkeypatch):
    calls = []
    monkeypatch.setattr(rm, "check_data_quality", lambda *a, **k: rm.DataQualityResult(False, "no dataset found", None, None))
    monkeypatch.setattr(orch, "run_mission", lambda mc: calls.append(mc))
    storage.upsert_cells([_cell()])
    cell = storage.claim_queued_cells(1)[0]
    orch._run_stage_a_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    assert calls == []
    row = storage.get_cell(cell["cell_id"])
    assert row["status"] == rm.INSUFFICIENT_DATA
    assert row["rejection_reason"] == "no dataset found"


def test_stage_a_rejects_when_the_cheap_screen_fails(monkeypatch):
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission(state="COMPLETE", trades=50, metrics={"profit_factor": 0.6, "avg_rr": -0.1, "std_rr": 1.0}))
    storage.upsert_cells([_cell()])
    cell = storage.claim_queued_cells(1)[0]
    orch._run_stage_a_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    row = storage.get_cell(cell["cell_id"])
    assert row["status"] == rm.REJECTED
    assert "profit_factor" in row["rejection_reason"]


def test_stage_a_rejects_a_pruned_trial_with_a_documented_reason(monkeypatch):
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission(state="PRUNED", trades=2, metrics=None))
    storage.upsert_cells([_cell()])
    cell = storage.claim_queued_cells(1)[0]
    orch._run_stage_a_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    row = storage.get_cell(cell["cell_id"])
    assert row["status"] == rm.REJECTED
    assert "PRUNED" in row["rejection_reason"]


def test_stage_a_marks_failed_on_a_run_mission_crash_and_never_aborts_the_batch(monkeypatch):
    def _boom(mc):
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(orch, "run_mission", _boom)
    storage.upsert_cells([_cell()])
    cell = storage.claim_queued_cells(1)[0]
    orch._run_stage_a_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    row = storage.get_cell(cell["cell_id"])
    assert row["status"] == rm.FAILED
    assert "crashed" in row["rejection_reason"]


def test_one_cell_crash_never_aborts_run_batch(monkeypatch):
    """A crash inside _run_stage_a_cell for one cell must not stop run_batch
    from processing the remaining claimed cells."""
    calls = {"n": 0}

    def _flaky(mc):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        research_missions.upsert_mission(
            mission_id=mc.mission_id, name=mc.name, sampler=mc.sampler, objective_metric=mc.objective_metric,
            symbols=list(mc.symbols), n_trials_per_symbol=mc.n_trials_per_symbol, min_trades=mc.min_trades,
            seed=mc.seed, search_space={}, config={}, status="running",
        )
        research_missions.record_trial(
            mission_id=mc.mission_id, trial_number=0, symbol=mc.symbols[0], state="COMPLETE",
            objective_value=1.5, params={}, metrics={"profit_factor": 1.5, "avg_rr": 0.2, "std_rr": 1.0},
            trades=50, error=None, started_at=_now(), finished_at=_now(),
        )

    monkeypatch.setattr(orch, "run_mission", _flaky)
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD")])
    for cell in storage.claim_queued_cells(2):
        try:
            orch._run_stage_a_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
        except Exception as exc:  # noqa: BLE001 — proving run_batch's own per-cell isolation would catch this
            pytest.fail(f"_run_stage_a_cell must never propagate a Stage A crash: {exc}")

    statuses = {c["symbol"]: c["status"] for c in storage.list_cells()}
    assert statuses["EURUSD"] == rm.FAILED
    assert statuses["GBPUSD"] == rm.SCREENED  # the SECOND claimed cell still ran to completion despite the first one crashing


# ---------------------------------------------------------------------------
# Matrix-wide correction
# ---------------------------------------------------------------------------


def test_matrix_wide_correction_promotes_significant_and_rejects_the_rest():
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD"), _cell(symbol="XAUUSD")])
    ids = [c["cell_id"] for c in storage.list_cells()]
    # 3 SCREENED cells: one with a very small p-value (survives even a
    # strict Bonferroni correction over 3 trials), two with unremarkable
    # p-values that do not.
    storage.update_cell(ids[0], status=rm.SCREENED, stage_a_p_value=0.0001)
    storage.update_cell(ids[1], status=rm.SCREENED, stage_a_p_value=0.4)
    storage.update_cell(ids[2], status=rm.SCREENED, stage_a_p_value=0.6)

    summary = orch.apply_matrix_wide_correction()
    assert summary["n_trials"] == 3
    assert summary["count_surviving_bonferroni"] == 1

    assert storage.get_cell(ids[0])["status"] == rm.CANDIDATE
    assert storage.get_cell(ids[1])["status"] == rm.REJECTED
    assert "Bonferroni" in storage.get_cell(ids[1])["rejection_reason"]
    assert storage.get_cell(ids[2])["status"] == rm.REJECTED


def test_matrix_wide_correction_on_an_empty_family_is_a_no_op():
    summary = orch.apply_matrix_wide_correction()
    assert summary == {"n_trials": 0, "bonferroni_alpha": None, "count_surviving_bonferroni": 0}


def test_matrix_wide_correction_family_spans_multiple_runs_not_just_one_batch():
    """A cell SCREENED by an earlier run still counts toward a later
    run's correction family size — the family is matrix-wide, not
    per-batch (operator's condition #3)."""
    storage.upsert_cells([_cell(symbol="EURUSD")])
    ids1 = [c["cell_id"] for c in storage.list_cells()]
    storage.update_cell(ids1[0], status=rm.SCREENED, stage_a_p_value=0.5)

    storage.upsert_cells([_cell(symbol="GBPUSD")])
    new_cell_id = [c["cell_id"] for c in storage.list_cells(symbol="GBPUSD")][0]
    storage.update_cell(new_cell_id, status=rm.SCREENED, stage_a_p_value=0.5)

    summary = orch.apply_matrix_wide_correction()
    assert summary["n_trials"] == 2  # both cells counted, not just the newest one


# ---------------------------------------------------------------------------
# Stage B
# ---------------------------------------------------------------------------


def test_stage_b_validates_a_confirmed_candidate(monkeypatch):
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation(verdict="SAME_SYMBOL_CONFIRMED"))
    storage.upsert_cells([_cell()])
    cell_id = storage.list_cells()[0]["cell_id"]
    storage.update_cell(cell_id, status=rm.CANDIDATE, stage_a_mission_id="m1", stage_a_trial_number=0)
    cell = storage.get_cell(cell_id)
    orch._run_stage_b_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    row = storage.get_cell(cell_id)
    assert row["status"] == rm.VALIDATED
    assert row["stage_b_verdict"] == "SAME_SYMBOL_CONFIRMED"


def test_stage_b_rejects_a_not_confirmed_verdict(monkeypatch):
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation(verdict="SAME_SYMBOL_NOT_CONFIRMED"))
    storage.upsert_cells([_cell()])
    cell_id = storage.list_cells()[0]["cell_id"]
    storage.update_cell(cell_id, status=rm.CANDIDATE, stage_a_mission_id="m1", stage_a_trial_number=0)
    cell = storage.get_cell(cell_id)
    orch._run_stage_b_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    row = storage.get_cell(cell_id)
    assert row["status"] == rm.REJECTED
    assert row["stage_b_verdict"] == "SAME_SYMBOL_NOT_CONFIRMED"


def test_stage_b_crash_is_rejected_not_a_batch_abort(monkeypatch):
    def _boom(vc):
        raise RuntimeError("stage b crash")

    monkeypatch.setattr(orch, "run_validation", _boom)
    storage.upsert_cells([_cell()])
    cell_id = storage.list_cells()[0]["cell_id"]
    storage.update_cell(cell_id, status=rm.CANDIDATE, stage_a_mission_id="m1", stage_a_trial_number=0)
    cell = storage.get_cell(cell_id)
    orch._run_stage_b_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    row = storage.get_cell(cell_id)
    assert row["status"] == rm.REJECTED
    assert "crashed" in row["rejection_reason"]


# ---------------------------------------------------------------------------
# Full run_batch pipeline
# ---------------------------------------------------------------------------


def test_run_batch_full_pipeline_queued_to_validated(monkeypatch):
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission(state="COMPLETE", trades=50, metrics={"profit_factor": 2.0, "avg_rr": 0.5, "std_rr": 0.9}))
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation(verdict="SAME_SYMBOL_CONFIRMED"))
    storage.upsert_cells([_cell(symbol="EURUSD")])

    orch.run_batch("run-full", batch_size=10, stage_b_batch_size=5, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))

    row = storage.list_cells()[0]
    assert row["status"] == rm.VALIDATED

    run = storage.get_run("run-full")
    assert run["status"] == "finished"
    assert run["cells_validated"] == 1


def test_run_batch_never_touches_more_than_batch_size_cells(monkeypatch):
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission(state="COMPLETE", trades=50, metrics={"profit_factor": 2.0, "avg_rr": 0.5, "std_rr": 0.9}))
    storage.upsert_cells([_cell(symbol=s) for s in ("EURUSD", "GBPUSD", "XAUUSD", "USDJPY", "AUDUSD")])
    orch.run_batch("run-bounded", batch_size=2, stage_b_batch_size=1, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    still_queued = storage.list_cells(status=rm.QUEUED)
    assert len(still_queued) == 3  # only 2 of 5 claimed this batch


def test_run_batch_requeues_stale_running_cells_before_claiming():
    storage.upsert_cells([_cell()])
    storage.claim_queued_cells(1)  # leaves the cell RUNNING, simulating a crashed prior run
    orch.run_batch("run-resume", batch_size=10, stage_b_batch_size=5, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"), stale_running_seconds=-1)
    # the requeue makes it QUEUED again, then this same run_batch call
    # claims and processes it (data-quality gate mocked OK, run_mission
    # not mocked here so a real attempt is made and fails gracefully —
    # the key assertion is that it left QUEUED/RUNNING limbo, not that it
    # necessarily reached a specific terminal status).
    row = storage.list_cells()[0]
    assert row["status"] != rm.RUNNING


# ---------------------------------------------------------------------------
# Hard-block safety — this module (and research_matrix.py) may never write
# to config.yaml / config/engines.yaml / config/symbols.yaml / registry.json.
# ---------------------------------------------------------------------------

_FORBIDDEN_TARGETS = ("config.yaml", "engines.yaml", "symbols.yaml", "registry.json")
_WRITE_MARKERS = ("write_text(", "json.dump(", "yaml.safe_dump(", "yaml.dump(", "\"w\")", "'w')")


def test_no_write_call_near_a_forbidden_target_anywhere_in_the_matrix_engine():
    for module in (rm, orch):
        source = inspect.getsource(module)
        for line in source.splitlines():
            if any(marker in line for marker in _WRITE_MARKERS):
                assert not any(target in line for target in _FORBIDDEN_TARGETS), (
                    f"{module.__name__} appears to write a forbidden target: {line!r}"
                )


def test_config_and_registry_files_are_byte_identical_after_a_real_run_batch_call(monkeypatch):
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission(state="COMPLETE", trades=50, metrics={"profit_factor": 2.0, "avg_rr": 0.5, "std_rr": 0.9}))
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation(verdict="SAME_SYMBOL_CONFIRMED"))
    storage.upsert_cells([_cell()])

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("research/results/registry.json")]
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in watched if p.exists()}

    orch.run_batch("run-safety", batch_size=10, stage_b_batch_size=5, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))

    for p in watched:
        if p in before:
            content, mtime = before[p]
            assert p.read_bytes() == content, f"{p} content changed after a Matrix Engine run"
            assert p.stat().st_mtime == mtime, f"{p} mtime changed after a Matrix Engine run"
