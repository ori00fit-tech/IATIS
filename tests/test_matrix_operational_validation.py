"""
tests/test_matrix_operational_validation.py
------------------------------------------------
Hypothesis Discovery Engine, Phase 3A — Matrix Operational Validation.

Unit/integration tests already prove the pipeline's per-stage LOGIC is
correct (tests/test_matrix_orchestrator.py) and that D1 persistence is
correct in isolation (tests/test_research_matrix_storage.py). This file
proves the ORCHESTRATOR is safe to run unattended: resumable after a
crash at any pipeline point, safe under real concurrent workers, and that
evidence + family identity stay immutable/closed exactly as designed.
Every scenario here runs the REAL orchestrator functions (run_batch,
apply_matrix_wide_correction, _run_stage_a_cell, _run_stage_b_cell)
against the real (fake_d1-backed) storage layer — only run_mission()/
run_validation() themselves are stubbed (same convention as
test_matrix_orchestrator.py: a real backtest needs real OHLCV data this
suite doesn't have).
"""
from __future__ import annotations

import threading
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


def _family(family_id="fam1", planned_n=1, family_alpha=0.05) -> str:
    storage.upsert_family(family_id, planned_n=planned_n, family_alpha=family_alpha)
    return family_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stub_run_mission_healthy(call_log: list[str] | None = None):
    """A deterministic stub whose p-value (avg_rr=0.5, std_rr=0.9, n=50 ->
    p ~ 8.5e-5) comfortably survives Bonferroni correction even at
    planned_n=20 (alpha=0.0025) -- reliably reaches CANDIDATE, never a
    NOT_SIGNIFICANT rejection, across every family size used in this file
    (matches test_matrix_orchestrator.py's own test_run_batch_full_
    pipeline_queued_to_validated stub values). If call_log is given,
    appends the mission_id on every call — used to detect double
    execution under concurrency."""

    def _fn(mc):
        if call_log is not None:
            call_log.append(mc.mission_id)
        research_missions.upsert_mission(
            mission_id=mc.mission_id, name=mc.name, sampler=mc.sampler, objective_metric=mc.objective_metric,
            symbols=list(mc.symbols), n_trials_per_symbol=mc.n_trials_per_symbol, min_trades=mc.min_trades,
            seed=mc.seed, search_space={}, config={}, status="running",
        )
        research_missions.record_trial(
            mission_id=mc.mission_id, trial_number=0, symbol=mc.symbols[0], state="COMPLETE",
            objective_value=2.0, params={}, metrics={"profit_factor": 2.0, "avg_rr": 0.5, "std_rr": 0.9},
            trades=50, error=None, started_at=_now(), finished_at=_now(),
        )
    return _fn


def _stub_run_validation_confirmed(call_log: list[str] | None = None):
    def _fn(vc):
        if call_log is not None:
            call_log.append(vc.validation_id)
        research_mission_validations.upsert_validation(
            validation_id=vc.validation_id, mission_id=vc.mission_id, trial_number=vc.trial_number,
            trial_symbol=vc.trial_symbol, validation_symbols=list(vc.validation_symbols),
            objective_metric="profit_factor", criteria={}, status="running", validation_mode=vc.validation_mode,
        )
        research_mission_validations.set_validation_status(
            vc.validation_id, "finished", finished=True, overall_verdict="SAME_SYMBOL_CONFIRMED", passing_symbols=1, total_symbols=1,
        )
    return _fn


@pytest.fixture(autouse=True)
def _no_real_data_quality_gate(monkeypatch):
    monkeypatch.setattr(rm, "check_data_quality", lambda *a, **k: rm.DataQualityResult(True, None, {"completeness_pct": 100.0}, "deadbeef"))


def _run_batch(run_id: str, family_id: str, *, batch_size=10, stage_b_batch_size=10, stale_running_seconds=orch.DEFAULT_STALE_RUNNING_SECONDS):
    orch.run_batch(
        run_id, family_id=family_id, batch_size=batch_size, stage_b_batch_size=stage_b_batch_size,
        data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"),
        stale_running_seconds=stale_running_seconds,
    )


# ---------------------------------------------------------------------------
# 1. Resume test — kill/restart, resume, no duplicate cells/execution,
#    same correction family, same alpha.
# ---------------------------------------------------------------------------


def test_resume_after_partial_batch_never_reprocesses_a_cell_and_reuses_the_same_alpha(monkeypatch):
    mission_calls: list[str] = []
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission_healthy(mission_calls))
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation_confirmed())

    fam = _family(planned_n=5)
    storage.upsert_cells([_cell(symbol=s) for s in ("EURUSD", "GBPUSD", "XAUUSD", "USDJPY", "AUDUSD")], fam)

    # "Run 1": only 2 of 5 cells fit this batch's budget (simulating a
    # bounded first pass that then "crashes"/exits before finishing).
    _run_batch("run-1", fam, batch_size=2, stage_b_batch_size=2)
    alpha_after_run_1 = orch.apply_matrix_wide_correction(fam)["bonferroni_alpha"]
    still_queued_after_run_1 = storage.list_cells(status=rm.QUEUED, family_id=fam)
    assert len(still_queued_after_run_1) == 3

    # "Resume": a fresh run_id claims and finishes the remaining 3 cells.
    _run_batch("run-2", fam, batch_size=10, stage_b_batch_size=10)
    alpha_after_run_2 = orch.apply_matrix_wide_correction(fam)["bonferroni_alpha"]

    assert alpha_after_run_1 == alpha_after_run_2  # same fixed family denominator, both times

    all_cells = storage.list_cells(family_id=fam)
    assert len(all_cells) == 5
    assert all(c["status"] not in (rm.QUEUED, rm.RUNNING, rm.CANDIDATE, rm.VALIDATING) for c in all_cells)  # nothing left mid-pipeline
    assert len(mission_calls) == len(set(mission_calls)) == 5  # every cell's Stage A ran EXACTLY once, never twice

    runs = storage.list_runs(family_id=fam)
    assert {r["run_id"] for r in runs} == {"run-1", "run-2"}
    assert all(r["family_id"] == fam for r in runs)


# ---------------------------------------------------------------------------
# 2. Crash injection at named pipeline points.
# ---------------------------------------------------------------------------


def test_crash_after_claim_before_stage_a_is_recovered_by_the_next_run(monkeypatch):
    """Process died the instant after claim_queued_cells() flipped the
    cell to RUNNING, before _run_stage_a_cell ever ran — simulated by
    calling claim_queued_cells() directly and never processing it."""
    call_log: list[str] = []
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission_healthy(call_log))
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation_confirmed())
    fam = _family()
    storage.upsert_cells([_cell()], fam)

    storage.claim_queued_cells(fam, 1)  # "crash" right here — cell is RUNNING, nothing else ran
    assert storage.list_cells(family_id=fam)[0]["status"] == rm.RUNNING

    _run_batch("run-recover", fam, stale_running_seconds=-1)  # -1s: everything RUNNING is "stale"

    row = storage.list_cells(family_id=fam)[0]
    # run_batch() always runs the whole pipeline in one call (Stage A ->
    # correction -> Stage B), so the single-cell family reaches VALIDATED
    # directly, not just SCREENED/CANDIDATE — the load-bearing assertion
    # is that it reached a real terminal outcome at all (proving Stage A
    # genuinely re-ran after recovery, not that it stalled RUNNING/QUEUED).
    assert row["status"] == rm.VALIDATED
    assert len(call_log) == 1  # Stage A ran exactly once, not zero, not twice


def test_crash_after_screened_before_correction_is_recovered_by_a_later_run(monkeypatch):
    """Stage A finished (cell is SCREENED) but the process died before
    apply_matrix_wide_correction ran — simulated by calling
    _run_stage_a_cell directly, bypassing run_batch's own correction step
    entirely."""
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission_healthy())
    fam = _family(planned_n=1)
    storage.upsert_cells([_cell()], fam)
    cell = storage.claim_queued_cells(fam, 1)[0]
    orch._run_stage_a_cell(cell, data_dir=Path("data"), start=None, end=None, output_dir=Path("reports"))
    assert storage.get_cell(cell["cell_id"])["status"] == rm.SCREENED

    # A later, real run_batch() call (no new QUEUED work at all) must still
    # apply correction to this earlier-orphaned SCREENED cell.
    _run_batch("run-correct-later", fam)
    row = storage.get_cell(cell["cell_id"])
    assert row["status"] in (rm.CANDIDATE, rm.VALIDATING, rm.VALIDATED, rm.REJECTED)
    assert row["status"] != rm.SCREENED


def test_crash_after_candidate_before_stage_b_is_recovered_by_a_later_run(monkeypatch):
    """Cell survived correction (CANDIDATE) but the process died before
    Stage B ever claimed it — simulated by promoting it directly via
    apply_matrix_wide_correction and never calling _run_stage_b_cell."""
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation_confirmed())
    fam = _family(planned_n=1)
    storage.upsert_cells([_cell()], fam)
    cell_id = storage.list_cells(family_id=fam)[0]["cell_id"]
    storage.update_cell(cell_id, status=rm.SCREENED, stage_a_p_value=0.001, stage_a_mission_id="m1", stage_a_trial_number=0)
    orch.apply_matrix_wide_correction(fam)
    assert storage.get_cell(cell_id)["status"] == rm.CANDIDATE

    _run_batch("run-stage-b-later", fam)
    row = storage.get_cell(cell_id)
    assert row["status"] == rm.VALIDATED


def test_crash_during_stage_b_rejects_the_cell_without_aborting_the_batch(monkeypatch):
    """run_validation() itself raises mid-Stage-B for one cell — the cell
    must reach a documented REJECTED (never stuck VALIDATING forever), and
    the rest of the batch (a second, healthy cell) must still complete."""
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission_healthy())

    def _flaky_validation(vc):
        if vc.trial_symbol == "EURUSD":
            raise RuntimeError("synthetic stage b crash")
        return _stub_run_validation_confirmed()(vc)

    monkeypatch.setattr(orch, "run_validation", _flaky_validation)
    fam = _family(planned_n=2)
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD")], fam)

    _run_batch("run-mixed", fam)

    by_symbol = {c["symbol"]: c["status"] for c in storage.list_cells(family_id=fam)}
    assert by_symbol["EURUSD"] == rm.REJECTED
    assert by_symbol["GBPUSD"] == rm.VALIDATED
    assert "crashed" in storage.list_cells(status=rm.REJECTED, family_id=fam, symbol="EURUSD")[0]["rejection_reason"]


def test_crash_after_validated_never_touches_that_cells_evidence_again(monkeypatch):
    """A cell that already reached VALIDATED must be byte-for-byte
    untouched by a LATER run_batch() call against the same family
    (processing other, still-QUEUED cells) -- no re-claim, no re-run, no
    update_cell call ever reaches it again."""
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission_healthy())
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation_confirmed())
    fam = _family(planned_n=2)
    storage.upsert_cells([_cell(symbol="EURUSD")], fam)
    _run_batch("run-first", fam)
    validated_row_before = storage.get_cell(_cell(symbol="EURUSD").cell_id)
    assert validated_row_before["status"] == rm.VALIDATED

    # a second family member arrives later and a new batch processes it —
    # the already-VALIDATED cell must not be revisited at all.
    storage.upsert_cells([_cell(symbol="GBPUSD")], fam)
    _run_batch("run-second", fam)

    validated_row_after = storage.get_cell(_cell(symbol="EURUSD").cell_id)
    assert validated_row_after == validated_row_before  # every column, including updated_at, identical


# ---------------------------------------------------------------------------
# 3. Multi-worker stress — real threads, one shared family, retries/
#    requeues, concurrent claims at BOTH Stage A and Stage B.
# ---------------------------------------------------------------------------


def test_four_concurrent_workers_never_double_execute_stage_a_or_stage_b(monkeypatch):
    """4 real threads repeatedly call run_batch() against ONE shared
    20-cell family until all cells reach a terminal status. Every
    mission_id/validation_id must be invoked AT MOST once across all
    threads combined — proving claim_queued_cells()/claim_candidate_
    cells() genuinely prevent double execution under real concurrency,
    not just under a single-threaded call sequence."""
    lock = threading.Lock()
    mission_calls: list[str] = []
    validation_calls: list[str] = []

    def _locked_run_mission(mc):
        with lock:
            mission_calls.append(mc.mission_id)
        _stub_run_mission_healthy()(mc)

    def _locked_run_validation(vc):
        with lock:
            validation_calls.append(vc.validation_id)
        _stub_run_validation_confirmed()(vc)

    monkeypatch.setattr(orch, "run_mission", _locked_run_mission)
    monkeypatch.setattr(orch, "run_validation", _locked_run_validation)

    n_cells = 20
    fam = _family(planned_n=n_cells)
    storage.upsert_cells([_cell(symbol=f"SYM{i}") for i in range(n_cells)], fam)

    errors: list[Exception] = []

    def _worker(worker_id: int) -> None:
        try:
            for round_n in range(3):  # a few passes so retries/requeues are exercised too
                # Deliberately the REAL default stale_running_seconds, not
                # a "-1 = everything is stale" override: a small/negative
                # threshold here would make one worker's requeue pass
                # steal a cell another worker is *genuinely, concurrently*
                # still processing (its updated_at just isn't old enough
                # to be a real crash yet) — a self-inflicted double-claim
                # that has nothing to do with the atomic-claim mechanism
                # actually under test. The default (30 minutes) is what a
                # real fleet of workers would run with.
                _run_batch(f"run-w{worker_id}-{round_n}", fam, batch_size=5, stage_b_batch_size=5)
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"a concurrent run_batch() call raised: {errors}"

    # No mission_id or validation_id was ever invoked more than once,
    # across ALL four workers combined.
    assert len(mission_calls) == len(set(mission_calls))
    assert len(validation_calls) == len(set(validation_calls))

    final = storage.list_cells(family_id=fam, limit=100)
    assert len(final) == n_cells
    stuck = [c for c in final if c["status"] in (rm.QUEUED, rm.RUNNING, rm.CANDIDATE, rm.VALIDATING)]
    assert stuck == [], f"cell(s) stuck mid-pipeline after concurrent workers finished: {stuck}"
    assert all(c["status"] == rm.VALIDATED for c in final)  # every stub call is healthy/confirmed in this test


# ---------------------------------------------------------------------------
# 4. Evidence immutability (integration-level — the unit-level guard is
#    already covered directly in tests/test_research_matrix_storage.py).
# ---------------------------------------------------------------------------


def test_a_second_run_batch_on_an_already_closed_family_is_a_safe_no_op(monkeypatch):
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission_healthy())
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation_confirmed())
    fam = _family(planned_n=1)
    storage.upsert_cells([_cell()], fam)
    _run_batch("run-1", fam)
    before = storage.get_cell(_cell().cell_id)
    assert before["status"] == rm.VALIDATED

    _run_batch("run-2", fam)  # nothing left to claim at any stage
    after = storage.get_cell(_cell().cell_id)
    assert after == before


def test_evidence_immutability_guard_would_have_caught_the_pre_fix_stage_b_race():
    """Direct proof that storage.update_cell()'s terminal-status guard
    (Phase 3A) refuses a second write to an already-VALIDATED cell — the
    exact failure mode two concurrent, un-atomically-claimed Stage B runs
    would have hit before claim_candidate_cells() existed."""
    fam = _family()
    storage.upsert_cells([_cell()], fam)
    cell_id = _cell().cell_id
    storage.update_cell(cell_id, status=rm.VALIDATED, stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    with pytest.raises(ValueError, match="terminal"):
        storage.update_cell(cell_id, status=rm.REJECTED, rejection_reason="a second, racing Stage B write")


# ---------------------------------------------------------------------------
# 5. Family closure.
# ---------------------------------------------------------------------------


def test_a_family_id_can_never_be_recreated_once_it_exists():
    """matrix_orchestrator.py's own documented design (point 4): a second
    generate call always creates a NEW family with its own new N, never
    reuses/extends an existing one. execution/routes/research_matrix.py's
    /generate handler enforces this by minting a fresh uuid4 every call
    (never reusing an old family_id) -- this test proves the storage layer
    itself would refuse a reuse attempt even if a caller somehow tried:
    family_id is a real PRIMARY KEY, not an upsert target."""
    storage.upsert_family("fam-fixed", planned_n=5, family_alpha=0.05)
    with pytest.raises(Exception):  # noqa: BLE001 - the underlying D1/sqlite integrity error type is not this module's contract
        storage.upsert_family("fam-fixed", planned_n=999, family_alpha=0.01)
    # the original family's fixed planned_n/alpha survived the attempted reuse untouched
    row = storage.get_family("fam-fixed")
    assert row["planned_n"] == 5
    assert row["family_alpha"] == 0.05


def test_family_cell_set_is_closed_once_its_planned_space_is_generated():
    fam = _family(planned_n=2)
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD")], fam)
    with pytest.raises(ValueError, match="planned_n"):
        storage.upsert_cells([_cell(symbol="XAUUSD")], fam)
    assert len(storage.list_cells(family_id=fam)) == 2  # the illegal 3rd cell was never inserted


def test_run_batch_never_processes_more_cells_than_the_familys_planned_n(monkeypatch):
    """Even under a full pipeline run, a family's total cell count can
    never exceed its own fixed planned_n -- the invariant the whole
    Bonferroni-correction design depends on."""
    monkeypatch.setattr(orch, "run_mission", _stub_run_mission_healthy())
    monkeypatch.setattr(orch, "run_validation", _stub_run_validation_confirmed())
    fam = _family(planned_n=3)
    storage.upsert_cells([_cell(symbol=s) for s in ("EURUSD", "GBPUSD", "XAUUSD")], fam)
    _run_batch("run-full", fam)
    assert len(storage.list_cells(family_id=fam)) == storage.get_family(fam)["planned_n"] == 3
