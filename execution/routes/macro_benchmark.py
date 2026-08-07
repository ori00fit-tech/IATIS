"""
execution/routes/macro_benchmark.py
--------------------------------------
Provider Benchmark & Data Quality Lab Phase 3 — API surface over
backtest/macro_benchmark.py, reusing execution/routes/experiments.py's
job-execution engine exactly like execution/routes/provider_benchmark.py
(Phase 1) and execution/routes/news_benchmark.py (Phase 2) do: one whole
benchmark run is one subprocess/one job slot for its entire duration,
looping over every (series, provider) point in-process internally.

MEASUREMENT / ADVISORY LAYER ONLY — no endpoint here ever writes to
config.yaml, config/symbols.yaml, config/engines.yaml, or
research/results/registry.json. See backtest/macro_benchmark.py's module
docstring for the full safety guarantee.
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
    _run_job,
)

router = APIRouter()

_KNOWN_PROVIDERS = ("cboe", "fred", "alpha_vantage")
_MAX_MONTHS = 60  # 5 years — well past any of this benchmark's series' real lookback needs
_MIN_TOLERANCE_PCT = 0.0001
_MAX_TOLERANCE_PCT = 50.0


class _MacroBenchmarkRequest(BaseModel):
    profile: str
    series: list[str] | None = None
    providers: list[str] | None = None
    months: int | None = None
    tolerance_pct: float = 1.0


@router.post("/research/macro-benchmark")
async def macro_benchmark_create(
    body: _MacroBenchmarkRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    from backtest.macro_benchmark import PROFILES, SERIES_PROVIDERS

    if body.profile not in PROFILES:
        raise HTTPException(status_code=400, detail=f"profile must be one of {sorted(PROFILES)}.")

    series: list[str] | None = None
    if body.series is not None:
        series = [str(s).upper().strip() for s in body.series if str(s).strip()]
        if not series:
            raise HTTPException(status_code=400, detail="series, if provided, must contain at least one entry.")
        unknown = sorted(set(series) - set(SERIES_PROVIDERS))
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown series {unknown} — choose from {sorted(SERIES_PROVIDERS)}.")

    providers: list[str] | None = None
    if body.providers is not None:
        providers = [str(p).strip() for p in body.providers if str(p).strip()]
        if not providers:
            raise HTTPException(status_code=400, detail="providers, if provided, must contain at least one entry.")
        unknown_p = sorted(set(providers) - set(_KNOWN_PROVIDERS))
        if unknown_p:
            raise HTTPException(status_code=400, detail=f"Unknown provider(s) {unknown_p} — choose from {sorted(_KNOWN_PROVIDERS)}.")

    if body.months is not None and not (1 <= body.months <= _MAX_MONTHS):
        raise HTTPException(status_code=400, detail=f"months must be 1-{_MAX_MONTHS}.")
    if not (_MIN_TOLERANCE_PCT <= body.tolerance_pct <= _MAX_TOLERANCE_PCT):
        raise HTTPException(status_code=400, detail=f"tolerance_pct must be {_MIN_TOLERANCE_PCT}-{_MAX_TOLERANCE_PCT}.")

    run_id = uuid.uuid4().hex[:12]
    argv = list(_JOB_COMMANDS["macro_benchmark"]) + [
        "--run-id", run_id, "--profile", body.profile, "--tolerance-pct", str(body.tolerance_pct),
    ]
    if series:
        argv += ["--series", *series]
    if providers:
        argv += ["--providers", *providers]
    if body.months is not None:
        argv += ["--months", str(body.months)]

    with _jobs_lock:
        job = _Job(run_id, "macro_benchmark", argv=argv)
        _jobs[run_id] = job

    from storage.audit_log import log_action
    log_action(
        "macro_benchmark_create", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"macro_benchmark ({run_id}) profile={body.profile} series={series} providers={providers}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"run_id": run_id, **_job_summary(job)}


@router.get("/research/macro-benchmark")
async def macro_benchmark_list(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    with _jobs_lock:
        runs = sorted(
            (j for j in _jobs.values() if j.name == "macro_benchmark"),
            key=lambda j: j.created_at, reverse=True,
        )
        return {"runs": [_job_summary(j) for j in runs]}


@router.get("/research/macro-benchmark/{run_id}")
async def macro_benchmark_status(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import macro_benchmark

    run = macro_benchmark.get_run(run_id)
    job = _jobs.get(run_id)
    if run is None and job is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found.")
    progress = macro_benchmark.run_progress(run_id) if run else {"total_results": 0, "fetch_ok": 0, "fetch_failed": 0}
    return {"run_id": run_id, "run": run, "progress": progress, "job_status": job.status if job else None}


@router.get("/research/macro-benchmark/{run_id}/results")
async def macro_benchmark_results(
    run_id: str,
    series: str | None = None,
    provider: str | None = None,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import macro_benchmark

    return {"run_id": run_id, "results": macro_benchmark.run_results(run_id, series=series, provider=provider)}


@router.post("/research/macro-benchmark/{run_id}/cancel")
async def macro_benchmark_cancel(
    run_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import macro_benchmark

    run = macro_benchmark.get_run(run_id)
    if run is not None:
        macro_benchmark.set_run_status(run_id, "cancelled")

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
    log_action("macro_benchmark_cancel", x_api_key=x_api_key, session_id=iatis_session, detail=run_id)
    with job.lock:
        if job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc).isoformat()
        return {"run_id": run_id, **_job_summary(job)}
