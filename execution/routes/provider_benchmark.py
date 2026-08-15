"""
execution/routes/provider_benchmark.py
------------------------------------------
Provider Benchmark & Data Quality Lab Phase 1 — API surface over
backtest/price_benchmark.py, reusing execution/routes/experiments.py's
job-execution engine exactly like execution/routes/missions.py does: one
whole benchmark run is one subprocess/one job slot for its entire
duration, looping over every (symbol, timeframe, provider) point
in-process internally.

MEASUREMENT / ADVISORY LAYER ONLY — no endpoint here ever writes to
config.yaml's data.provider_chains, config/symbols.yaml, or
research/results/registry.json. See backtest/price_benchmark.py's
module docstring for the full safety guarantee.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel

from execution.api_core import _check_auth, _get_config
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
)

router = APIRouter()

_MAX_OUTPUTSIZE = 5000


class _BenchmarkRequest(BaseModel):
    profile: str
    symbols: list[str] | None = None
    timeframes: list[str] | None = None
    providers: list[str] | None = None
    outputsize: int | None = None
    tolerance_pct: float = 0.05


@router.post("/research/provider-benchmark")
async def provider_benchmark_create(
    body: _BenchmarkRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    from backtest.price_benchmark import _IN_SCOPE_ASSET_CLASSES, _TF_MS, PROFILES

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
        config = _get_config()
        by_internal = {
            str(s.get("internal", "")).upper(): s.get("asset_class")
            for s in config.get("data", {}).get("twelve_data_symbols", [])
        }
        out_of_scope = sorted(s for s in symbols if by_internal.get(s) not in _IN_SCOPE_ASSET_CLASSES)
        if out_of_scope:
            raise HTTPException(
                status_code=400,
                detail=f"Symbol(s) {out_of_scope} are outside Phase 1's scope "
                       f"(asset_class must be one of {sorted(_IN_SCOPE_ASSET_CLASSES)}).",
            )

    timeframes: list[str] | None = None
    if body.timeframes is not None:
        timeframes = [str(t).upper().strip() for t in body.timeframes if str(t).strip()]
        if not timeframes:
            raise HTTPException(status_code=400, detail="timeframes, if provided, must contain at least one entry.")
        unknown_tf = sorted(set(timeframes) - set(_TF_MS))
        if unknown_tf:
            raise HTTPException(status_code=400, detail=f"Unknown timeframe(s) {unknown_tf} — choose from {sorted(_TF_MS)}.")

    providers: list[str] | None = None
    if body.providers is not None:
        providers = [str(p).strip() for p in body.providers if str(p).strip()]
        if not providers:
            raise HTTPException(status_code=400, detail="providers, if provided, must contain at least one entry.")

    if body.outputsize is not None and not (10 <= body.outputsize <= _MAX_OUTPUTSIZE):
        raise HTTPException(status_code=400, detail=f"outputsize must be 10-{_MAX_OUTPUTSIZE}.")
    if not (0.0 < body.tolerance_pct <= 50.0):
        raise HTTPException(status_code=400, detail="tolerance_pct must be between 0 and 50.")

    run_id = uuid.uuid4().hex[:12]
    argv = list(_JOB_COMMANDS["price_benchmark"]) + [
        "--run-id", run_id,
        "--profile", body.profile,
        "--tolerance-pct", str(body.tolerance_pct),
    ]
    if symbols:
        argv += ["--symbols", *symbols]
    if timeframes:
        argv += ["--timeframes", *timeframes]
    if providers:
        argv += ["--providers", *providers]
    if body.outputsize is not None:
        argv += ["--outputsize", str(body.outputsize)]

    with _jobs_lock:
        _prune_old_jobs()
        job = _Job(run_id, "price_benchmark", argv=argv)
        _jobs[run_id] = job

    from storage.audit_log import log_action
    log_action(
        "provider_benchmark_create", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"price_benchmark ({run_id}) profile={body.profile} symbols={symbols} timeframes={timeframes}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"run_id": run_id, **_job_summary(job)}


@router.get("/research/provider-benchmark")
async def provider_benchmark_list(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    # Mirrors execution/routes/missions.py's missions_list(): the
    # in-memory job registry (_jobs), not storage — a benchmark run's D1
    # row is only written once its own subprocess (backtest/
    # price_benchmark.py's CLI) actually starts, which lags job
    # submission. _jobs is authoritative for "what's queued/running/
    # finished right now" regardless of how far the subprocess has gotten.
    with _jobs_lock:
        runs = sorted(
            (j for j in _jobs.values() if j.name == "price_benchmark"),
            key=lambda j: j.created_at, reverse=True,
        )
        return {"runs": [_job_summary(j) for j in runs]}


@router.get("/research/provider-benchmark/history")
async def provider_benchmark_history(
    limit: int = 30,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 1c — score-history for every provider across the last `limit`
    FINISHED runs, so the frontend can chart real composite-score/latency/
    coverage trends over time. Registered before /research/provider-
    benchmark/{run_id} (a literal path segment can't collide with that
    route's own {run_id} path param under FastAPI's matching, but kept
    here for readability — grouped with the other run-scoped GETs)."""
    _check_auth(x_api_key, iatis_session)
    if not (1 <= limit <= 200):
        raise HTTPException(status_code=400, detail="limit must be 1-200.")
    from storage import provider_benchmark

    return {"history": provider_benchmark.score_history(limit_runs=limit)}


@router.get("/research/provider-benchmark/{run_id}")
async def provider_benchmark_status(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import provider_benchmark

    run = provider_benchmark.get_run(run_id)
    job = _jobs.get(run_id)
    if run is None and job is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found.")
    progress = provider_benchmark.run_progress(run_id) if run else {"total_results": 0, "fetch_ok": 0, "fetch_failed": 0}
    return {"run_id": run_id, "run": run, "progress": progress, "job_status": job.status if job else None}


@router.get("/research/provider-benchmark/{run_id}/results")
async def provider_benchmark_results(
    run_id: str,
    symbol: str | None = None,
    provider: str | None = None,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import provider_benchmark

    return {"run_id": run_id, "results": provider_benchmark.run_results(run_id, symbol=symbol, provider=provider)}


@router.post("/research/provider-benchmark/{run_id}/cancel")
async def provider_benchmark_cancel(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import provider_benchmark

    run = provider_benchmark.get_run(run_id)
    if run is not None:
        provider_benchmark.set_run_status(run_id, "cancelled")

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
    log_action("provider_benchmark_cancel", x_api_key=x_api_key, session_id=iatis_session, detail=run_id)
    with job.lock:
        if job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc).isoformat()
        return {"run_id": run_id, **_job_summary(job)}
