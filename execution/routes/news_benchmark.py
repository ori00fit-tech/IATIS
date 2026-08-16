"""
execution/routes/news_benchmark.py
--------------------------------------
Provider Benchmark & Data Quality Lab Phase 2 — API surface over
backtest/news_benchmark.py, reusing execution/routes/experiments.py's
job-execution engine exactly like execution/routes/provider_benchmark.py
(Phase 1) does: one whole benchmark run is one subprocess/one job slot
for its entire duration, looping over every (symbol, provider) point
in-process internally.

MEASUREMENT / ADVISORY LAYER ONLY — no endpoint here ever writes to
config.yaml, config/symbols.yaml, or research/results/registry.json. See
backtest/news_benchmark.py's module docstring for the full safety
guarantee.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel

from execution.api_core import _check_auth
from execution.routes.experiments import (
    _JOB_COMMANDS,
    _Job,
    _job_executor,
    _job_summary,
    _jobs,
    _jobs_lock,
    _prune_old_jobs,
    _run_job,
)

router = APIRouter()

_MAX_HOURS_BACK = 720  # 30 days — well past any free-tier provider's real retention window
_MAX_LIMIT = 500


class _NewsBenchmarkRequest(BaseModel):
    profile: str
    symbols: list[str] | None = None
    providers: list[str] | None = None
    hours_back: int | None = None
    limit: int | None = None


@router.post("/research/news-benchmark")
async def news_benchmark_create(
    body: _NewsBenchmarkRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    from backtest.news_benchmark import PROFILES, PROVIDERS

    if body.profile not in PROFILES:
        raise HTTPException(status_code=400, detail=f"profile must be one of {sorted(PROFILES)}.")

    symbols: list[str] | None = None
    if body.symbols is not None:
        symbols = [str(s).upper().strip() for s in body.symbols if str(s).strip()]
        if not symbols:
            raise HTTPException(status_code=400, detail="symbols, if provided, must contain at least one entry.")

    providers: list[str] | None = None
    if body.providers is not None:
        providers = [str(p).strip() for p in body.providers if str(p).strip()]
        if not providers:
            raise HTTPException(status_code=400, detail="providers, if provided, must contain at least one entry.")
        unknown = sorted(set(providers) - set(PROVIDERS))
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown provider(s) {unknown} — choose from {sorted(PROVIDERS)}.")

    if body.hours_back is not None and not (1 <= body.hours_back <= _MAX_HOURS_BACK):
        raise HTTPException(status_code=400, detail=f"hours_back must be 1-{_MAX_HOURS_BACK}.")
    if body.limit is not None and not (1 <= body.limit <= _MAX_LIMIT):
        raise HTTPException(status_code=400, detail=f"limit must be 1-{_MAX_LIMIT}.")

    run_id = uuid.uuid4().hex[:12]
    argv = list(_JOB_COMMANDS["news_benchmark"]) + ["--run-id", run_id, "--profile", body.profile]
    if symbols:
        argv += ["--symbols", *symbols]
    if providers:
        argv += ["--providers", *providers]
    if body.hours_back is not None:
        argv += ["--hours-back", str(body.hours_back)]
    if body.limit is not None:
        argv += ["--limit", str(body.limit)]

    with _jobs_lock:
        _prune_old_jobs()
        job = _Job(run_id, "news_benchmark", argv=argv)
        _jobs[run_id] = job

    from storage.audit_log import log_action
    log_action(
        "news_benchmark_create", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"news_benchmark ({run_id}) profile={body.profile} symbols={symbols} providers={providers}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"run_id": run_id, **_job_summary(job)}


@router.get("/research/news-benchmark")
async def news_benchmark_list(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    with _jobs_lock:
        runs = sorted(
            (j for j in _jobs.values() if j.name == "news_benchmark"),
            key=lambda j: j.created_at, reverse=True,
        )
        return {"runs": [_job_summary(j) for j in runs]}


@router.get("/research/news-benchmark/{run_id}")
async def news_benchmark_status(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import news_benchmark

    run = news_benchmark.get_run(run_id)
    job = _jobs.get(run_id)
    if run is None and job is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found.")
    progress = news_benchmark.run_progress(run_id) if run else {"total_results": 0, "fetch_ok": 0, "fetch_failed": 0}
    return {"run_id": run_id, "run": run, "progress": progress, "job_status": job.status if job else None}


@router.get("/research/news-benchmark/{run_id}/results")
async def news_benchmark_results(
    run_id: str,
    symbol: str | None = None,
    provider: str | None = None,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import news_benchmark

    return {"run_id": run_id, "results": news_benchmark.run_results(run_id, symbol=symbol, provider=provider)}


@router.post("/research/news-benchmark/{run_id}/cancel")
async def news_benchmark_cancel(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import news_benchmark

    run = news_benchmark.get_run(run_id)
    if run is not None:
        news_benchmark.set_run_status(run_id, "cancelled")

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
    log_action("news_benchmark_cancel", x_api_key=x_api_key, session_id=iatis_session, detail=run_id)
    with job.lock:
        if job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc).isoformat()
        return {"run_id": run_id, **_job_summary(job)}
