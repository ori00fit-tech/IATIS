"""
backtest/matrix_orchestrator.py
-----------------------------------
Hypothesis Discovery Engine, Phase 1 — the bounded, resumable, checkpointed
scheduler that turns QUEUED research_matrix cells into evidence.

RESEARCH-ONLY. This module (and everything it calls) never writes
research/results/registry.json, config.yaml, config/engines.yaml, or
config/symbols.yaml. VALIDATED is the final status this module can ever
assign a cell to — converting a VALIDATED cell into a real, pre-registered
HXXX hypothesis remains an entirely separate, manual, human-governed step
(CLAUDE.md rule 1). No code path here can shortcut it.

Reuses, never rebuilds (operator's explicit "no architectural regression"
instruction): backtest.mission_runner.run_mission(), backtest.
mission_validator.run_validation(), backtest.multiple_testing's Bonferroni
machinery, backtest.lead_id.lead_id(), storage.research_missions,
storage.research_mission_validations. Every Stage A trial IS one call to
the unmodified run_mission() (a single-point grid search of exactly one
hypothesis bundle at one fixed risk preset — deterministic and
reproducible by construction); every Stage B validation IS one call to
the unmodified run_validation().

Pipeline per cell (operator's condition #4 — every intermediate gate is
mandatory, every rejection carries a documented reason):

    QUEUED -> (data quality gate) -> INSUFFICIENT_DATA, or
    QUEUED -> RUNNING -> (Stage A trial via run_mission) -> SCREENED, or
                                                          -> REJECTED (cheap-screen fail)
    SCREENED -> (MATRIX-WIDE Bonferroni correction, across every currently
                 SCREENED cell, not just this batch) -> CANDIDATE, or
                                                      -> REJECTED (did not
                                                         survive correction)
    CANDIDATE -> (Stage B via run_validation, SAME_SYMBOL mode) -> VALIDATED, or
                                                                  -> REJECTED
                                                                     (real verdict)

Matrix-wide correction (operator's condition #3) is a SEPARATE layer on
top of — never a substitute for — each Stage B validation's own
within-validation multiple-testing gate
(mission_validator._compute_mission_family_significance, unchanged): a
cell can only ever reach CANDIDATE by surviving the matrix-wide family
correction FIRST, and can only reach VALIDATED by then also surviving
Stage B's own, separate, already-existing correction.

Bounded + resumable + checkpointed (operator's condition #5): every cell
transition is persisted to D1 the moment it happens (storage.
research_matrix.update_cell); a crashed run leaves cells in RUNNING,
requeued by the next invocation via requeue_stale_running_cells(); nothing
here ever attempts "24 symbols x everything x thousands of trials" in one
call — batch_size/stage_b_batch_size bound exactly how much work one
invocation does.
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

from backtest import research_matrix as rm
from backtest.lead_id import lead_id as build_lead_id
from backtest.mission_runner import MissionConfig, run_mission
from backtest.mission_validator import SAME_SYMBOL, ValidationConfig, run_validation
from backtest.multiple_testing import bonferroni_alpha, classify_significance, trial_p_value
from backtest.optimizer import MissionSearchSpace
from storage import research_matrix as storage
from storage import research_mission_validations
from storage import research_missions
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_STALE_RUNNING_SECONDS = 1800.0  # 30 minutes — generous relative to a single-trial Stage A run


def _mission_id_for_cell(cell: dict) -> str:
    """Deterministic, one mission per cell fingerprint — a crash-retry of
    the SAME cell reuses the SAME mission_id, letting run_mission()'s own
    existing_trials()-replay resume logic handle the retry for free
    instead of building a second resume mechanism here."""
    return f"mtx-{cell['fingerprint']}"


def _build_mission_config(cell: dict, *, data_dir: Path, start: str | None, end: str | None, output_dir: Path) -> MissionConfig:
    import json as _json

    bundle = _json.loads(cell["bundle_json"])
    confluence_overrides = _json.loads(cell["confluence_overrides_json"]) if cell["confluence_overrides_json"] else None
    engine_variants = _json.loads(cell["engine_variants_json"]) if cell["engine_variants_json"] else None

    space = MissionSearchSpace(
        timeframes_choices=(tuple(bundle.get("timeframes", ["H1"])) or ("H1",),),
        engine_set_choices=(tuple(bundle.get("engines", ["nnfx"])) or ("nnfx",),),
        indicator_set_choices=((),),
        hypothesis_bundle_choices=(bundle,),
        engine_variant_choices=(engine_variants or {},),
        confluence_overrides=confluence_overrides,
        risk_param_grid=rm.risk_preset_to_grid(cell["risk_preset"]),
    )
    return MissionConfig(
        mission_id=_mission_id_for_cell(cell),
        name=f"matrix-cell-{cell['cell_id']}",
        symbols=(cell["symbol"],),
        data_dir=data_dir, start=start, end=end,
        sampler="grid", n_trials_per_symbol=1,
        objective_metric="profit_factor", min_trades=rm.STAGE_A_MIN_TRADES,
        seed=42, search_space=space,
        oos_holdout_fraction=None, max_wall_clock_seconds=None,
        output_dir=output_dir,
    )


def _run_stage_a_cell(cell: dict, *, data_dir: Path, start: str | None, end: str | None, output_dir: Path) -> None:
    """Runs the data-quality gate, then (if it passes) Stage A for one
    claimed (RUNNING) cell, persisting the outcome. Never raises — every
    failure mode ends in a documented D1 status update, matching mission_
    runner.py's own per-symbol isolation ("one bad cell never aborts the
    batch")."""
    cell_id = cell["cell_id"]
    timeframe = "H1"  # physical load timeframe; run_mission's own dataset loading resolves the real per-bundle timeframe internally

    dq = rm.check_data_quality(cell["symbol"], timeframe, data_dir, start, end)
    if not dq.ok:
        storage.update_cell(cell_id, status=rm.INSUFFICIENT_DATA, rejection_reason=dq.reason)
        return

    mission_id = _mission_id_for_cell(cell)
    try:
        mc = _build_mission_config(cell, data_dir=data_dir, start=start, end=end, output_dir=output_dir)
        run_mission(mc)
    except Exception as exc:  # noqa: BLE001 — an orchestrator-level crash for this one cell, not a batch abort
        logger.warning(f"Matrix cell {cell_id}: Stage A run_mission() raised: {exc}")
        storage.update_cell(cell_id, status=rm.FAILED, rejection_reason=f"Stage A crashed: {exc}", stage_a_mission_id=mission_id)
        return

    trial = research_missions.get_trial(mission_id, trial_number=0, symbol=cell["symbol"])
    if trial is None:
        storage.update_cell(cell_id, status=rm.FAILED, rejection_reason="Stage A produced no recorded trial", stage_a_mission_id=mission_id)
        return

    import json as _json

    metrics = _json.loads(trial["metrics_json"]) if trial.get("metrics_json") else None
    lead = build_lead_id(mission_id, 0, cell["symbol"])

    if trial["state"] == "FAIL":
        storage.update_cell(
            cell_id, status=rm.REJECTED, rejection_reason=f"Stage A trial failed: {trial.get('error') or 'unknown error'}",
            stage_a_mission_id=mission_id, stage_a_trial_number=0, lead_id=lead,
        )
        return
    if trial["state"] in ("PRUNED", "DUPLICATE") and trial.get("trades", 0) < rm.STAGE_A_MIN_TRADES:
        storage.update_cell(
            cell_id, status=rm.REJECTED,
            rejection_reason=f"Stage A trial state={trial['state']}, only {trial.get('trades', 0)} trade(s) — this combination essentially never fires",
            stage_a_mission_id=mission_id, stage_a_trial_number=0, lead_id=lead,
        )
        return

    screen = rm.screen_stage_a(metrics, trial.get("trades", 0))
    if not screen.passed:
        storage.update_cell(
            cell_id, status=rm.REJECTED, rejection_reason=f"Stage A screen failed: {screen.reason}",
            stage_a_mission_id=mission_id, stage_a_trial_number=0,
            stage_a_metrics_json=_json.dumps(metrics) if metrics else None, lead_id=lead,
        )
        return

    p_value = None
    if metrics is not None:
        p_value = trial_p_value(metrics.get("avg_rr", 0.0), metrics.get("std_rr", 0.0), trial.get("trades", 0))

    storage.update_cell(
        cell_id, status=rm.SCREENED,
        stage_a_mission_id=mission_id, stage_a_trial_number=0,
        stage_a_metrics_json=_json.dumps(metrics) if metrics else None,
        stage_a_p_value=p_value, lead_id=lead,
    )


def apply_matrix_wide_correction(family_alpha: float = 0.05) -> dict:
    """The matrix-wide gate (operator's condition #3) — separate from,
    and applied AFTER, each Stage A trial's own (trivial, family-size-1)
    within-mission evaluation. The correction family is every currently
    SCREENED cell across the WHOLE matrix, not just this batch — a cell
    promoted to CANDIDATE in an earlier run still counts toward a later
    run's family size, since it already consumed one of the "how many
    tests have we run" budget.

    A SCREENED cell whose p-value survives Bonferroni correction is
    promoted to CANDIDATE (eligible for Stage B); one that doesn't is
    REJECTED with a documented reason — its fingerprint is fixed, so no
    future evidence can ever accumulate for that exact combination.
    Returns the same significance-summary shape backtest.multiple_testing
    already uses elsewhere in this codebase, for the run's own audit
    record."""
    screened = storage.cells_for_matrix_correction()
    n_trials = len(screened)
    promoted = 0
    rejected = 0
    if n_trials == 0:
        return {"n_trials": 0, "bonferroni_alpha": None, "count_surviving_bonferroni": 0}

    alpha_corrected = bonferroni_alpha(n_trials, family_alpha)
    for cell in screened:
        classification = classify_significance(cell["stage_a_p_value"], n_trials, family_alpha)
        if classification == "SURVIVES_CORRECTION":
            storage.update_cell(cell["cell_id"], status=rm.CANDIDATE)
            promoted += 1
        else:
            storage.update_cell(
                cell["cell_id"], status=rm.REJECTED,
                rejection_reason=(
                    f"did not survive matrix-wide Bonferroni correction (p={cell['stage_a_p_value']:.6f}, "
                    f"family size={n_trials}, corrected alpha={alpha_corrected:.6f}, classification={classification})"
                ),
            )
            rejected += 1
    return {
        "n_trials": n_trials, "bonferroni_alpha": alpha_corrected,
        "count_surviving_bonferroni": promoted, "count_rejected": rejected,
    }


def _run_stage_b_cell(cell: dict, *, data_dir: Path, start: str | None, end: str | None, output_dir: Path) -> None:
    """Stage B for one CANDIDATE cell — SAME_SYMBOL mode only in Phase 1
    (the safe default the existing validation_mode infrastructure already
    ships; cross-symbol validation is a legitimate but separate
    escalation, not required to prove the Matrix Engine's own evidence
    pipeline). Never raises — a Stage B crash is REJECTED with a real
    reason, not a batch abort."""
    cell_id = cell["cell_id"]
    validation_id = f"mtxval-{cell['fingerprint']}"
    try:
        vc = ValidationConfig(
            validation_id=validation_id, mission_id=cell["stage_a_mission_id"], trial_number=cell["stage_a_trial_number"] or 0,
            trial_symbol=cell["symbol"], validation_symbols=(cell["symbol"],),
            data_dir=data_dir, start=start, end=end, validation_mode=SAME_SYMBOL,
            output_dir=output_dir,
        )
        run_validation(vc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Matrix cell {cell_id}: Stage B run_validation() raised: {exc}")
        storage.update_cell(cell_id, status=rm.REJECTED, rejection_reason=f"Stage B crashed: {exc}", stage_b_validation_id=validation_id)
        return

    validation = research_mission_validations.get_validation(validation_id)
    verdict = validation.get("overall_verdict") if validation else None
    if verdict == "SAME_SYMBOL_CONFIRMED":
        storage.update_cell(cell_id, status=rm.VALIDATED, stage_b_validation_id=validation_id, stage_b_verdict=verdict)
    else:
        storage.update_cell(
            cell_id, status=rm.REJECTED, stage_b_validation_id=validation_id, stage_b_verdict=verdict,
            rejection_reason=f"Stage B verdict: {verdict or 'no verdict recorded'}",
        )


def run_batch(
    run_id: str, *, batch_size: int, stage_b_batch_size: int, data_dir: Path, start: str | None, end: str | None,
    output_dir: Path, stale_running_seconds: float = DEFAULT_STALE_RUNNING_SECONDS,
    max_wall_clock_seconds: float | None = None,
) -> None:
    storage.upsert_run(run_id, status="running", batch_size=batch_size)
    storage.set_run_status(run_id, "running", started=True)
    t0 = time.monotonic()

    requeued = storage.requeue_stale_running_cells(stale_running_seconds)
    if requeued:
        logger.info(f"Matrix run {run_id}: requeued {requeued} stale RUNNING cell(s) from a previous crashed run.")

    claimed = storage.claim_queued_cells(batch_size)
    logger.info(f"Matrix run {run_id}: claimed {len(claimed)} QUEUED cell(s) for Stage A.")

    for cell in claimed:
        if max_wall_clock_seconds is not None and (time.monotonic() - t0) >= max_wall_clock_seconds:
            logger.info(f"Matrix run {run_id}: wall-clock budget reached mid-Stage-A, stopping (remaining cells stay RUNNING for a later requeue).")
            break
        try:
            _run_stage_a_cell(cell, data_dir=data_dir, start=start, end=end, output_dir=output_dir)
        except Exception as exc:  # noqa: BLE001 — isolate one cell's unexpected crash from the whole batch
            logger.warning(f"Matrix cell {cell['cell_id']}: unexpected Stage A failure: {exc}")
            storage.update_cell(cell["cell_id"], status=rm.FAILED, rejection_reason=f"unexpected Stage A failure: {exc}")

    significance = apply_matrix_wide_correction()

    candidates = storage.list_cells(status=rm.CANDIDATE, limit=stage_b_batch_size)
    validated_count = 0
    for cell in candidates:
        if max_wall_clock_seconds is not None and (time.monotonic() - t0) >= max_wall_clock_seconds:
            logger.info(f"Matrix run {run_id}: wall-clock budget reached mid-Stage-B, stopping (remaining CANDIDATE cells wait for a later run).")
            break
        try:
            _run_stage_b_cell(cell, data_dir=data_dir, start=start, end=end, output_dir=output_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Matrix cell {cell['cell_id']}: unexpected Stage B failure: {exc}")
            storage.update_cell(cell["cell_id"], status=rm.REJECTED, rejection_reason=f"unexpected Stage B failure: {exc}")
            continue
        after = storage.get_cell(cell["cell_id"])
        if after and after["status"] == rm.VALIDATED:
            validated_count += 1

    storage.set_run_status(
        run_id, "finished", finished=True,
        cells_claimed=len(claimed), cells_screened=significance.get("n_trials", 0),
        cells_promoted=significance.get("count_surviving_bonferroni", 0), cells_validated=validated_count,
        matrix_significance=significance,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Hypothesis Discovery Engine — bounded Matrix Engine batch runner")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--stage-b-batch-size", type=int, default=10)
    parser.add_argument("--data-dir", type=str, default="data")
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default="reports")
    parser.add_argument("--max-wall-clock-seconds", type=float, default=None)
    args = parser.parse_args(argv)

    run_id = args.run_id or uuid.uuid4().hex[:12]
    run_batch(
        run_id, batch_size=args.batch_size, stage_b_batch_size=args.stage_b_batch_size,
        data_dir=Path(args.data_dir), start=args.start, end=args.end,
        output_dir=Path(args.output_dir), max_wall_clock_seconds=args.max_wall_clock_seconds,
    )
    print(f"Matrix run {run_id} finished.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
    sys.exit(0)
