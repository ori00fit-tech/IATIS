"""
execution/routes/engine_benchmark.py
----------------------------------------
Engine Benchmark — API surface over backtest/engine_benchmark.py, reusing
execution/routes/experiments.py's job-execution engine exactly like
execution/routes/provider_benchmark.py does: one whole benchmark run is
one subprocess/one job slot for its entire duration, looping over every
(engine, symbol) point in-process internally.

MEASUREMENT / ADVISORY LAYER ONLY, NOT a ranking surface — no endpoint
here ever writes to config.yaml, config/engines.yaml, or
research/results/registry.json. See backtest/engine_benchmark.py's
module docstring for the full safety guarantee and the CLAUDE.md
dead-list reasoning behind why this deliberately never ranks engines.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel

from execution.api_core import _check_auth
from execution.routes.experiments import (
    _JOB_COMMANDS,
    _Job,
    _configured_symbol_universe,
    _job_executor,
    _job_summary,
    _jobs,
    _jobs_lock,
    _run_job,
)

router = APIRouter()

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _BenchmarkRequest(BaseModel):
    profile: str
    symbols: list[str] | None = None
    engines: list[str] | None = None
    start: str | None = None
    end: str | None = None


@router.post("/research/engine-benchmark")
async def engine_benchmark_create(
    body: _BenchmarkRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    from backtest.engine_benchmark import PROFILES
    from backtesting.backtest_engine import ENGINE_KEYS

    if body.profile not in PROFILES:
        raise HTTPException(status_code=400, detail=f"profile must be one of {sorted(PROFILES)}.")

    symbols: list[str] | None = None
    if body.symbols is not None:
        symbols = [str(s).upper().strip() for s in body.symbols if str(s).strip()]
        if not symbols:
            raise HTTPException(status_code=400, detail="symbols, if provided, must contain at least one entry.")
        universe = _configured_symbol_universe()
        unknown = sorted(set(symbols) - universe)
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown symbol(s) {unknown} — must be in the configured universe.")

    engines: list[str] | None = None
    if body.engines is not None:
        engines = [str(e).strip() for e in body.engines if str(e).strip()]
        if not engines:
            raise HTTPException(status_code=400, detail="engines, if provided, must contain at least one entry.")
        unknown_engines = sorted(set(engines) - set(ENGINE_KEYS))
        if unknown_engines:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown engine(s) {unknown_engines} — choose from {sorted(ENGINE_KEYS)}.",
            )

    if body.start is not None and not _ISO_DATE_RE.match(body.start):
        raise HTTPException(status_code=400, detail="start must be an ISO date (YYYY-MM-DD).")
    if body.end is not None and not _ISO_DATE_RE.match(body.end):
        raise HTTPException(status_code=400, detail="end must be an ISO date (YYYY-MM-DD).")
    if body.start and body.end and body.start > body.end:
        raise HTTPException(status_code=400, detail="start must be <= end.")

    run_id = uuid.uuid4().hex[:12]
    argv = list(_JOB_COMMANDS["engine_benchmark"]) + [
        "--run-id", run_id,
        "--profile", body.profile,
    ]
    if symbols:
        argv += ["--symbols", *symbols]
    if engines:
        argv += ["--engines", *engines]
    if body.start:
        argv += ["--start", body.start]
    if body.end:
        argv += ["--end", body.end]

    with _jobs_lock:
        job = _Job(run_id, "engine_benchmark", argv=argv)
        _jobs[run_id] = job

    from storage.audit_log import log_action
    log_action(
        "engine_benchmark_create", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"engine_benchmark ({run_id}) profile={body.profile} symbols={symbols} engines={engines}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"run_id": run_id, **_job_summary(job)}


@router.get("/research/engine-benchmark")
async def engine_benchmark_list(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    # Mirrors execution/routes/provider_benchmark.py's own listing route:
    # the in-memory job registry (_jobs), not storage — a benchmark run's
    # D1 row is only written once its own subprocess (backtest/
    # engine_benchmark.py's CLI) actually starts, which lags job
    # submission. _jobs is authoritative for "what's queued/running/
    # finished right now" regardless of how far the subprocess has gotten.
    with _jobs_lock:
        runs = sorted(
            (j for j in _jobs.values() if j.name == "engine_benchmark"),
            key=lambda j: j.created_at, reverse=True,
        )
        return {"runs": [_job_summary(j) for j in runs]}


@router.get("/research/engine-benchmark/{run_id}")
async def engine_benchmark_status(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import engine_benchmark

    run = engine_benchmark.get_run(run_id)
    job = _jobs.get(run_id)
    if run is None and job is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found.")
    progress = engine_benchmark.run_progress(run_id) if run else {"total_results": 0, "run_ok": 0, "run_failed": 0}
    return {"run_id": run_id, "run": run, "progress": progress, "job_status": job.status if job else None}


@router.get("/research/engine-benchmark/{run_id}/results")
async def engine_benchmark_results(
    run_id: str,
    symbol: str | None = None,
    engine: str | None = None,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import engine_benchmark as storage_mod

    return {"run_id": run_id, "results": storage_mod.run_results(run_id, symbol=symbol, engine=engine)}


@router.post("/research/engine-benchmark/{run_id}/cancel")
async def engine_benchmark_cancel(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import engine_benchmark

    run = engine_benchmark.get_run(run_id)
    if run is not None:
        engine_benchmark.set_run_status(run_id, "cancelled")

    job = _jobs.get(run_id)
    if job is None:
        if run is None:
            raise HTTPException(status_code=404, detail="Benchmark run not found.")
        return {"run_id": run_id, "status": "cancelled"}

    with job.lock:
        if job.status not in ("queued", "running"):
            return {"run_id": run_id, **_job_summary(job)}
        job.status = "cancelled"
        proc = job.proc
    if job.future is not None:
        job.future.cancel()
    if proc is not None:
        proc.kill()

    from datetime import datetime, timezone

    from storage.audit_log import log_action
    log_action("engine_benchmark_cancel", x_api_key=x_api_key, session_id=iatis_session, detail=run_id)
    with job.lock:
        if job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc).isoformat()
        return {"run_id": run_id, **_job_summary(job)}
