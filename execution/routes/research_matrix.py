"""
execution/routes/research_matrix.py
---------------------------------------
Hypothesis Discovery Engine, Phase 1 — API surface over backtest/
research_matrix.py + backtest/matrix_orchestrator.py, reusing execution/
routes/experiments.py's job-execution engine exactly the way execution/
routes/missions.py already does (a whole batch is ONE subprocess/one job
slot, looping over many cells in-process internally).

Every write here is confined to storage/research_matrix.py's own two
tables. This router never writes research/results/registry.json,
config.yaml, config/engines.yaml, or config/symbols.yaml — cell
generation and batch runs are both ephemeral, advisory research
artifacts, exactly like every other Mission Center endpoint.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel

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


class _MatrixRunBatchRequest(BaseModel):
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

    universe = _configured_symbol_universe()
    symbols = [s.upper() for s in body.symbols] if body.symbols else sorted(universe)
    unknown_symbols = sorted(set(symbols) - universe)
    if unknown_symbols:
        raise HTTPException(status_code=400, detail=f"Unknown symbol(s): {unknown_symbols}")
    if not body.bundles:
        raise HTTPException(status_code=400, detail="bundles must be non-empty.")

    risk_presets = body.risk_presets or list(rm.RISK_PRESET_NAMES)
    unknown_presets = sorted(set(risk_presets) - set(rm.RISK_PRESET_NAMES))
    if unknown_presets:
        raise HTTPException(status_code=400, detail=f"Unknown risk_preset(s) {unknown_presets} — choose from {rm.RISK_PRESET_NAMES}")

    bundles = [b.model_dump() for b in body.bundles]

    try:
        cells = rm.generate_matrix_cells(
            symbols=symbols, bundles=bundles, risk_presets=risk_presets,
            confluence_overrides_choices=(body.confluence_overrides,),
            engine_variants_choices=(body.engine_variants,),
            data_provider=body.data_provider,
        )
    except rm.ResearchMatrixError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if len(cells) > _MAX_CELLS_PER_GENERATE:
        raise HTTPException(
            status_code=400,
            detail=f"This spec would generate {len(cells)} cells, over the {_MAX_CELLS_PER_GENERATE} cap per request — narrow the symbol/bundle/preset lists.",
        )

    from storage import research_matrix as storage
    result = storage.upsert_cells(cells)

    from storage.audit_log import log_action
    log_action(
        "matrix_generate", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"symbols={len(symbols)} bundles={len(bundles)} risk_presets={risk_presets} "
               f"-> inserted={result['inserted']} duplicate={result['duplicate']}",
    )
    return {"cells_considered": len(cells), **result}


@router.get("/research/matrix/cells")
async def matrix_list_cells(
    status: str | None = None,
    symbol: str | None = None,
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
    cells = storage.list_cells(status=status, symbol=symbol.upper() if symbol else None, limit=limit)
    return {"cells": cells, "count": len(cells)}


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


@router.post("/research/matrix/run-batch")
async def matrix_run_batch(
    body: _MatrixRunBatchRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

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
        detail=f"matrix_batch ({run_id}) batch_size={body.batch_size} stage_b_batch_size={body.stage_b_batch_size}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"run_id": run_id, **_job_summary(job)}


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
