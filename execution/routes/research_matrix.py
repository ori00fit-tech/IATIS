"""
execution/routes/research_matrix.py
---------------------------------------
Hypothesis Discovery Engine, Phase 1 — API surface over backtest/
research_matrix.py + backtest/matrix_orchestrator.py, reusing execution/
routes/experiments.py's job-execution engine exactly the way execution/
routes/missions.py already does (a whole batch is ONE subprocess/one job
slot, looping over many cells in-process internally).

Every write here is confined to storage/research_matrix.py's own three
tables (families/cells/runs). This router never writes research/results/
registry.json, config.yaml, config/engines.yaml, or config/symbols.yaml —
cell generation and batch runs are both ephemeral, advisory research
artifacts, exactly like every other Mission Center endpoint.

Phase 2A (Evidence & Matrix Read Model) added two READ-ONLY endpoints
(`/families/{id}/summary`, `/cells/{id}/evidence`) that call into
backtest/matrix_evidence.py's pure aggregation functions — they never
write anything and never compute a new verdict, only re-present already-
decided D1 evidence in joined/aggregated shapes.

Phase 2B (Matrix Dashboard) added two more READ-ONLY list endpoints
(`GET /families`, `GET /runs`) — thin index views over storage.
research_matrix.list_families()/list_runs() so the dashboard can browse
without a caller already knowing a family_id/run_id. Same guarantee:
list-only, no aggregation, no verdict, no write.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel

from backtest import matrix_evidence as evidence
from backtest import research_matrix as rm
from execution.api_core import _check_auth
from execution.routes.experiments import (
    _JOB_COMMANDS,
    _Job,
    _configured_symbol_universe,
    _job_executor,
    _job_summary,
    _jobs,
    _jobs_lock,
    _prune_old_jobs,
    _run_job,
    _validate_iso_date,
)

router = APIRouter()

_MAX_CELLS_PER_GENERATE = 5_000  # a typo'd bundle/symbol list must not silently enqueue an unbounded matrix
_MAX_BATCH_SIZE = 200
_MAX_STAGE_B_BATCH_SIZE = 50


class _BundleSpec(BaseModel):
    name: str
    timeframes: list[str] = []
    engines: list[str] = []
    indicators: list[dict[str, Any]] = []
    context_filters: list[dict[str, Any]] = []


class _MatrixGenerateRequest(BaseModel):
    """Cell generation is pure specification — no run happens here. See
    POST /research/matrix/run-batch for that, which itself only ever
    claims already-QUEUED cells in a bounded batch (operator's condition
    #5: never 24 symbols x everything x thousands of trials in one
    call)."""
    symbols: list[str] | None = None  # None -> every configured symbol (operator's own "24 configured symbols" wording)
    bundles: list[_BundleSpec]
    risk_presets: list[str] | None = None  # None -> all 3 named presets
    confluence_overrides: dict[str, float] | None = None
    engine_variants: dict[str, str] | None = None
    data_provider: str | None = None


def validate_symbols_against_universe(symbols: list[str]) -> list[str]:
    """Uppercase + reject any symbol not in the CURRENT configured
    universe. The exact check matrix_generate() has always applied inline
    — pulled out to a module-level function so Phase 3C's conversion path
    (execution/routes/matrix_ai.py) revalidates a recommendation's
    proposed_next_cells against the SAME rule a hand-typed request would
    hit, never a separately-written (and possibly looser) copy. Pure
    refactor: matrix_generate()'s own behavior is unchanged byte-for-byte."""
    universe = _configured_symbol_universe()
    normed = [s.upper() for s in symbols]
    unknown = sorted(set(normed) - universe)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown symbol(s): {unknown}")
    return normed


def validate_risk_presets_against_known(risk_presets: list[str]) -> None:
    """Same reasoning as validate_symbols_against_universe() above —
    extracted from matrix_generate()'s own inline check, reused verbatim
    by Phase 3C's conversion path."""
    unknown = sorted(set(risk_presets) - set(rm.RISK_PRESET_NAMES))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown risk_preset(s) {unknown} — choose from {rm.RISK_PRESET_NAMES}")


class _MatrixRunBatchRequest(BaseModel):
    family_id: str
    batch_size: int = 20
    stage_b_batch_size: int = 5
    data_dir: str = "data"
    start: str | None = None
    end: str | None = None
    max_wall_clock_seconds: float | None = None


@router.post("/research/matrix/generate")
async def matrix_generate(
    body: _MatrixGenerateRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    symbols = validate_symbols_against_universe(body.symbols) if body.symbols else sorted(_configured_symbol_universe())
    if not body.bundles:
        raise HTTPException(status_code=400, detail="bundles must be non-empty.")

    risk_presets = body.risk_presets or list(rm.RISK_PRESET_NAMES)
    validate_risk_presets_against_known(risk_presets)

    bundles = [b.model_dump() for b in body.bundles]

    # Finding 2 fix: the research-code commit is part of every cell's
    # fingerprint, so a code change (even with the hypothesis spec held
    # identical) always produces a fresh, distinguishable cell.
    code_state = rm.resolve_research_code_commit()

    try:
        cells = rm.generate_matrix_cells(
            symbols=symbols, bundles=bundles, risk_presets=risk_presets,
            confluence_overrides_choices=(body.confluence_overrides,),
            engine_variants_choices=(body.engine_variants,),
            data_provider=body.data_provider,
            research_code_commit=code_state["commit"],
        )
    except rm.ResearchMatrixError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if len(cells) > _MAX_CELLS_PER_GENERATE:
        raise HTTPException(
            status_code=400,
            detail=f"This spec would generate {len(cells)} cells, over the {_MAX_CELLS_PER_GENERATE} cap per request — narrow the symbol/bundle/preset lists.",
        )

    import json as _json

    from storage import research_matrix as storage

    # Finding 4 fix (Design A — fixed research family): planned_n is fixed
    # HERE, once, to the full planned research space for this call. Every
    # future correction pass against this family_id reuses this exact N —
    # it is never recomputed from "currently SCREENED count," so a cell
    # that fails data-quality/Stage-A screening still counts toward the
    # denominator (it was part of the planned space), and two batches (or
    # a resumed run) within the same family always see the identical
    # Bonferroni threshold. See matrix_orchestrator.py's module docstring
    # for the full semantics.
    family_id = uuid.uuid4().hex[:12]
    storage.upsert_family(
        family_id, planned_n=len(cells), family_alpha=0.05,
        symbols_json=_json.dumps(symbols),
    )
    result = storage.upsert_cells(cells, family_id)

    from storage.audit_log import log_action
    log_action(
        "matrix_generate", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"symbols={len(symbols)} bundles={len(bundles)} risk_presets={risk_presets} "
               f"commit={code_state['commit']} dirty={code_state['dirty']} "
               f"family_id={family_id} planned_n={len(cells)} "
               f"-> inserted={result['inserted']} duplicate={result['duplicate']}",
    )
    return {
        "cells_considered": len(cells),
        "family_id": family_id,
        "planned_n": len(cells),
        "research_code_commit": code_state["commit"],
        "research_code_dirty": code_state["dirty"],
        **result,
    }


@router.get("/research/matrix/families")
async def matrix_list_families(
    limit: int = 50,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 2B Matrix Dashboard — READ-ONLY family browser. Newest first,
    no aggregation (a caller wanting per-family status breakdown/corrected
    alpha follows up with GET /research/matrix/families/{id}/summary)."""
    _check_auth(x_api_key, iatis_session)
    if not (1 <= limit <= 500):
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500.")
    from storage import research_matrix as storage
    families = storage.list_families(limit=limit)
    return {"families": families, "count": len(families)}


@router.get("/research/matrix/families/{family_id}")
async def matrix_get_family(
    family_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import research_matrix as storage
    family = storage.get_family(family_id)
    if family is None:
        raise HTTPException(status_code=404, detail="Matrix family not found.")
    return family


@router.get("/research/matrix/families/{family_id}/summary")
async def matrix_family_summary(
    family_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 2A Evidence Read Model — READ-ONLY. Returns the family's fixed
    planned_n/corrected alpha, a full status breakdown, total resumptions,
    and per-symbol/per-bundle/per-risk-preset breakdowns. Computes nothing
    that isn't already decided: every count here is a plain tally of
    already-persisted cell statuses.

    Phase 1 (Research Matrix Normalization) adds by_engine/by_timeframe/
    by_symbol_engine/by_symbol_timeframe/by_symbol_engine_timeframe —
    "?" groups every cell whose bundle combines multiple engines or
    multiple timeframes (never coerced into a single scalar; see
    backtest.research_matrix.single_engine_identity())."""
    _check_auth(x_api_key, iatis_session)
    from storage import research_matrix as storage

    family = storage.get_family(family_id)
    if family is None:
        raise HTTPException(status_code=404, detail="Matrix family not found.")
    cells = storage.list_cells(family_id=family_id, limit=_MAX_CELLS_PER_GENERATE)
    return {
        "family": evidence.family_evidence_summary(family, cells),
        "by_symbol": evidence.per_symbol_stats(cells),
        "by_bundle": evidence.per_bundle_stats(cells),
        "by_risk_preset": evidence.per_risk_preset_stats(cells),
        "by_engine": evidence.per_engine_stats(cells),
        "by_timeframe": evidence.per_timeframe_stats(cells),
        "by_symbol_engine": evidence.per_symbol_engine_stats(cells),
        "by_symbol_timeframe": evidence.per_symbol_timeframe_stats(cells),
        "by_symbol_engine_timeframe": evidence.per_symbol_engine_timeframe_stats(cells),
    }


@router.get("/research/matrix/cells")
async def matrix_list_cells(
    status: str | None = None,
    symbol: str | None = None,
    family_id: str | None = None,
    limit: int = 500,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    if status is not None and status not in rm.CELL_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status {status!r} — choose from {rm.CELL_STATUSES}")
    if not (1 <= limit <= 5_000):
        raise HTTPException(status_code=400, detail="limit must be between 1 and 5000.")

    from storage import research_matrix as storage
    cells = storage.list_cells(
        status=status, symbol=symbol.upper() if symbol else None,
        family_id=family_id, limit=limit,
    )
    return {"cells": cells, "count": len(cells)}


_MIN_COMPARED_CELLS = 2
_MAX_COMPARED_CELLS = 10


@router.get("/research/matrix/cells/compare")
async def matrix_cells_compare(
    cell_ids: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 2C Evidence Comparison — READ-ONLY, DESCRIPTIVE ONLY.

    `cell_ids` is a comma-separated list (2-10 cells). Returns the exact
    same full evidence join GET /cells/{id}/evidence returns, one entry
    per cell (an unknown cell_id comes back as {"cell_id": ..., "found":
    False} rather than failing the whole request — mirrors GET
    /research/compare's own per-ID `found` convention), plus a
    `provenance` block from backtest.matrix_evidence.compare_cells_
    provenance() over only the cells that were actually found.

    Registered BEFORE GET /cells/{cell_id} on purpose — Starlette matches
    routes in registration order, and "compare" would otherwise be
    swallowed as a literal {cell_id} path value by the more general route.

    NON-NEGOTIABLE: this endpoint computes no ranking, score, or verdict.
    `provenance` is nothing more than same-family/same-commit/same-
    provider/same-hypothesis-lineage equality checks over identity
    columns each cell already carries — every other field is a verbatim
    re-presentation of evidence already decided by backtest/
    matrix_orchestrator.py. A caller (dashboard or AI) must never treat
    any ordering, color, or emphasis derived from this response as a new
    statistical conclusion.
    """
    _check_auth(x_api_key, iatis_session)
    from storage import research_matrix as storage

    ids = [c.strip() for c in cell_ids.split(",") if c.strip()]
    if not (_MIN_COMPARED_CELLS <= len(ids) <= _MAX_COMPARED_CELLS):
        raise HTTPException(
            status_code=400,
            detail=f"cell_ids must contain between {_MIN_COMPARED_CELLS} and {_MAX_COMPARED_CELLS} cell IDs.",
        )

    cells_out: list[dict[str, Any]] = []
    found_rows: list[dict[str, Any]] = []
    for cell_id in ids:
        row = storage.get_cell(cell_id)
        if row is None:
            cells_out.append({"cell_id": cell_id, "found": False})
            continue
        found_rows.append(row)
        cells_out.append({"found": True, **_build_cell_evidence(row)})

    provenance = evidence.compare_cells_provenance(found_rows) if found_rows else None
    return {"cells": cells_out, "count": len(cells_out), "provenance": provenance}


@router.get("/research/matrix/cells/{cell_id}")
async def matrix_get_cell(
    cell_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import research_matrix as storage
    cell = storage.get_cell(cell_id)
    if cell is None:
        raise HTTPException(status_code=404, detail="Matrix cell not found.")
    return cell


def _build_cell_evidence(cell: dict[str, Any]) -> dict[str, Any]:
    """Shared by GET /cells/{id}/evidence and GET /cells/compare — the
    exact same 4-way join (cell row + Stage A trial + Stage B validation
    run + Stage B per-symbol validation result), so a caller never sees a
    different evidence shape depending on which endpoint it hit. Takes an
    already-fetched cell row (never re-fetches it) so callers comparing
    many cells don't refetch each cell twice."""
    from storage import research_mission_validations
    from storage import research_missions

    trial = None
    mission_id = cell.get("stage_a_mission_id")
    trial_number = cell.get("stage_a_trial_number")
    if mission_id and trial_number is not None:
        trial = research_missions.get_trial(mission_id, trial_number=trial_number, symbol=cell["symbol"])

    validation = None
    validation_result_row = None
    validation_id = cell.get("stage_b_validation_id")
    if validation_id:
        validation = research_mission_validations.get_validation(validation_id)
        # Phase 2C: a CROSS_SYMBOL validation has one result row PER
        # validated symbol — find the one matching THIS cell's own symbol,
        # never just the first row (which could be a different symbol
        # entirely).
        for row in research_mission_validations.validation_results(validation_id):
            if row.get("symbol") == cell.get("symbol"):
                validation_result_row = row
                break

    return evidence.cell_evidence(cell, trial=trial, validation=validation, validation_result=validation_result_row)


@router.get("/research/matrix/cells/{cell_id}/evidence")
async def matrix_cell_evidence(
    cell_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 2A Evidence Read Model — READ-ONLY. The full evidence chain
    for one cell in one call: the cell's own row (JSON columns decoded)
    plus, when present, its Stage A trial detail (backtest.mission_runner's
    own recorded trial), its Stage B validation-run detail, and (Phase 2C)
    its Stage B per-symbol validation result (backtest.mission_validator's
    own recorded Monte Carlo/Walk-Forward/robustness/regime-robustness/
    stability/cost-stress diagnostics) — the exact join a caller would
    otherwise need 4 separate round-trips to assemble. Never recomputes or
    overrides any status/verdict/p-value."""
    _check_auth(x_api_key, iatis_session)
    from storage import research_matrix as storage

    cell = storage.get_cell(cell_id)
    if cell is None:
        raise HTTPException(status_code=404, detail="Matrix cell not found.")

    return _build_cell_evidence(cell)


@router.post("/research/matrix/run-batch")
async def matrix_run_batch(
    body: _MatrixRunBatchRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    from storage import research_matrix as storage

    if storage.get_family(body.family_id) is None:
        raise HTTPException(status_code=400, detail=f"Unknown family_id {body.family_id!r} — generate cells for it first via POST /research/matrix/generate.")
    if not (1 <= body.batch_size <= _MAX_BATCH_SIZE):
        raise HTTPException(status_code=400, detail=f"batch_size must be between 1 and {_MAX_BATCH_SIZE}.")
    if not (1 <= body.stage_b_batch_size <= _MAX_STAGE_B_BATCH_SIZE):
        raise HTTPException(status_code=400, detail=f"stage_b_batch_size must be between 1 and {_MAX_STAGE_B_BATCH_SIZE}.")
    if body.start is not None:
        _validate_iso_date(body.start, "start")
    if body.end is not None:
        _validate_iso_date(body.end, "end")

    run_id = uuid.uuid4().hex[:12]
    argv = list(_JOB_COMMANDS["matrix_batch"]) + [
        "--run-id", run_id,
        "--family-id", body.family_id,
        "--batch-size", str(body.batch_size),
        "--stage-b-batch-size", str(body.stage_b_batch_size),
        "--data-dir", body.data_dir,
    ]
    if body.start:
        argv += ["--start", body.start]
    if body.end:
        argv += ["--end", body.end]
    if body.max_wall_clock_seconds is not None:
        argv += ["--max-wall-clock-seconds", str(body.max_wall_clock_seconds)]

    with _jobs_lock:
        _prune_old_jobs()
        job = _Job(run_id, "matrix_batch", argv=argv)
        _jobs[run_id] = job

    from storage.audit_log import log_action
    log_action(
        "matrix_run_batch", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"matrix_batch ({run_id}) family_id={body.family_id} batch_size={body.batch_size} stage_b_batch_size={body.stage_b_batch_size}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"run_id": run_id, **_job_summary(job)}


@router.get("/research/matrix/runs")
async def matrix_list_runs(
    family_id: str | None = None,
    limit: int = 50,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 2B Matrix Dashboard — READ-ONLY recent-batch-runs browser,
    optionally scoped to one family_id. D1-persisted run rows only (no
    in-process job merge — a caller wanting a run's live job state
    already has GET /research/matrix/runs/{run_id} for that)."""
    _check_auth(x_api_key, iatis_session)
    if not (1 <= limit <= 500):
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500.")
    from storage import research_matrix as storage
    runs = storage.list_runs(family_id=family_id, limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/research/matrix/runs/{run_id}")
async def matrix_run_status(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import research_matrix as storage

    run = storage.get_run(run_id)
    job = _jobs.get(run_id)
    if run is None and job is None:
        raise HTTPException(status_code=404, detail="Matrix run not found.")
    return {"run": run, "job": _job_summary(job) if job else None}
