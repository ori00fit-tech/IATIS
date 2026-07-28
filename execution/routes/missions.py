"""
execution/routes/missions.py
--------------------------------
AI Research Lab / Mission Center Phase 2 (2026-07-28) — API surface over
backtest/mission_runner.py, reusing execution/routes/experiments.py's
exact job-execution engine (_Job/_job_executor/_jobs) rather than
inventing a second one: a whole mission is ONE subprocess/one job slot
for its entire duration (see experiments.py's "research_mission"
_JOB_COMMANDS/_JOB_TIMEOUTS entries), looping over many trials
in-process internally.

Progress is read from storage/research_missions.py's structured D1
counts (GET /research/missions/{id}), not log-line parsing — a real
robustness improvement over the single-job inferStage pattern, made
possible because mission_runner.py writes one row per trial as it goes.

Every validation here either reuses an existing validator
(_configured_symbol_universe, _validate_iso_date, _RISK_OVERRIDE_BOUNDS
from experiments.py) or delegates to backtest/optimizer.py's own
MissionSearchSpace.__post_init__ (timeframes/engines/indicators/risk-
field-name validation) — never duplicated.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel

from execution.api_core import _check_auth, _get_config
from execution.routes.experiments import (
    _JOB_COMMANDS,
    _RISK_OVERRIDE_BOUNDS,
    _Job,
    _configured_symbol_universe,
    _job_executor,
    _job_summary,
    _jobs,
    _jobs_lock,
    _run_job,
    _validate_iso_date,
)

router = APIRouter()

_MAX_TRIALS_PER_SYMBOL = 2000  # sane worst-case ceiling — a typo'd extra
                                 # zero must not silently launch a mission
                                 # that runs for days unbounded.
_MAX_WALL_CLOCK_SECONDS_CAP = 21_600.0  # matches experiments.py's outer
                                          # research_mission job timeout


class _MissionRequest(BaseModel):
    """Ad-hoc mission definition. Every risk/engine/indicator/timeframe
    override here is EPHEMERAL — backtest/optimizer.py's
    MissionSearchSpace and backtest/mission_runner.py never write to
    config.yaml/config/engines.yaml, and this endpoint never writes to
    research/results/registry.json. See backtest/mission_runner.py's
    module docstring for the full safety guarantee."""
    name: str | None = None
    symbols: list[str]
    sampler: str = "tpe"
    n_trials_per_symbol: int
    objective_metric: str = "profit_factor"
    min_trades: int = 10
    seed: int = 42
    start: str | None = None
    end: str | None = None
    timeframes_choices: list[list[str]]
    engine_set_choices: list[list[str]]
    indicator_set_choices: list[list[dict]] = [[]]
    risk_param_ranges: dict[str, tuple[float, float]] = {}
    risk_param_grid: dict[str, tuple[float, ...]] = {}
    oos_holdout_fraction: float | None = None
    max_wall_clock_seconds: float | None = None


_NAME_SAFE_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,80}$")


def _validate_risk_bounds(ranges_or_grid: dict[str, tuple], label: str) -> None:
    for field_name, values in ranges_or_grid.items():
        bounds = _RISK_OVERRIDE_BOUNDS.get(field_name)
        if bounds is None:
            continue  # MissionSearchSpace.__post_init__ already rejects unknown field names
        lo, hi = bounds
        for v in values:
            if not (lo <= v <= hi):
                raise HTTPException(
                    status_code=400,
                    detail=f"{label}.{field_name}: {v} outside allowed range [{lo}, {hi}].",
                )


@router.post("/research/missions")
async def missions_create(
    body: _MissionRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    symbols = [str(s).upper().strip() for s in body.symbols if str(s).strip()]
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols must contain at least one entry.")
    if len(symbols) > 20:
        raise HTTPException(status_code=400, detail="at most 20 symbols per mission.")
    universe = _configured_symbol_universe()
    unknown = sorted(set(symbols) - universe)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown symbol(s) {unknown} — must be in the configured universe.")

    if body.name is not None and not _NAME_SAFE_RE.match(body.name):
        raise HTTPException(status_code=400, detail="name must be 1-80 chars (letters/digits/space/._- only).")

    from backtest.optimizer import OPTIMIZABLE_METRICS, SAMPLER_KEYS, MissionSearchSpace

    if body.sampler not in SAMPLER_KEYS:
        raise HTTPException(status_code=400, detail=f"sampler must be one of {SAMPLER_KEYS}.")
    if body.objective_metric not in OPTIMIZABLE_METRICS:
        raise HTTPException(status_code=400, detail=f"objective_metric must be one of {OPTIMIZABLE_METRICS}.")
    if not (1 <= body.n_trials_per_symbol <= _MAX_TRIALS_PER_SYMBOL):
        raise HTTPException(status_code=400, detail=f"n_trials_per_symbol must be 1-{_MAX_TRIALS_PER_SYMBOL}.")
    if body.min_trades < 1:
        raise HTTPException(status_code=400, detail="min_trades must be >= 1.")

    if body.start is not None:
        _validate_iso_date(body.start, "start")
    if body.end is not None:
        _validate_iso_date(body.end, "end")
    if body.start and body.end and body.start > body.end:
        raise HTTPException(status_code=400, detail="start must be <= end.")

    if body.oos_holdout_fraction is not None and not (0.0 < body.oos_holdout_fraction < 1.0):
        raise HTTPException(status_code=400, detail="oos_holdout_fraction must be between 0 and 1 (exclusive).")
    if body.max_wall_clock_seconds is not None and not (0.0 < body.max_wall_clock_seconds <= _MAX_WALL_CLOCK_SECONDS_CAP):
        raise HTTPException(status_code=400, detail=f"max_wall_clock_seconds must be 0-{_MAX_WALL_CLOCK_SECONDS_CAP}.")

    _validate_risk_bounds(body.risk_param_ranges, "risk_param_ranges")
    _validate_risk_bounds(body.risk_param_grid, "risk_param_grid")

    try:
        MissionSearchSpace(
            timeframes_choices=tuple(tuple(c) for c in body.timeframes_choices),
            engine_set_choices=tuple(tuple(c) for c in body.engine_set_choices),
            indicator_set_choices=tuple(tuple(c) for c in body.indicator_set_choices),
            risk_param_ranges=body.risk_param_ranges,
            risk_param_grid=body.risk_param_grid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    mission_id = uuid.uuid4().hex[:12]
    name = body.name or mission_id

    argv = list(_JOB_COMMANDS["research_mission"]) + [
        "--mission-id", mission_id,
        "--name", name,
        "--symbols", *symbols,
        "--sampler", body.sampler,
        "--n-trials-per-symbol", str(body.n_trials_per_symbol),
        "--objective-metric", body.objective_metric,
        "--min-trades", str(body.min_trades),
        "--seed", str(body.seed),
        "--timeframes-choices", json.dumps(body.timeframes_choices),
        "--engine-set-choices", json.dumps(body.engine_set_choices),
        "--indicator-set-choices", json.dumps(body.indicator_set_choices),
    ]
    if body.start:
        argv += ["--start", body.start]
    if body.end:
        argv += ["--end", body.end]
    if body.risk_param_ranges:
        argv += ["--risk-param-ranges", json.dumps(body.risk_param_ranges)]
    if body.risk_param_grid:
        argv += ["--risk-param-grid", json.dumps(body.risk_param_grid)]
    if body.oos_holdout_fraction is not None:
        argv += ["--oos-holdout-fraction", str(body.oos_holdout_fraction)]
    if body.max_wall_clock_seconds is not None:
        argv += ["--max-wall-clock-seconds", str(body.max_wall_clock_seconds)]

    # Deliberately NOT enforcing experiments.py's "already_running" single-
    # instance-per-name block: unlike the fixed pre-registered hypothesis/
    # backtest jobs, missions are naturally expected to run several at
    # once (different symbol sets/search spaces) — bounded only by the
    # shared job-slot pool size (_job_executor), not a name collision.
    with _jobs_lock:
        job = _Job(mission_id, "research_mission", argv=argv)
        _jobs[mission_id] = job

    from storage.audit_log import log_action
    log_action(
        "mission_create", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"research_mission ({mission_id}) symbols={symbols} sampler={body.sampler} "
               f"n_trials_per_symbol={body.n_trials_per_symbol}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"mission_id": mission_id, **_job_summary(job)}


@router.get("/research/missions")
async def missions_list(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    with _jobs_lock:
        mission_jobs = sorted(
            (j for j in _jobs.values() if j.name == "research_mission"),
            key=lambda j: j.created_at, reverse=True,
        )
        return {"missions": [_job_summary(j) for j in mission_jobs]}


@router.get("/research/missions/{mission_id}")
async def missions_status(
    mission_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import research_missions

    mission = research_missions.get_mission(mission_id)
    job = _jobs.get(mission_id)
    if mission is None and job is None:
        raise HTTPException(status_code=404, detail="Mission not found.")

    progress = research_missions.mission_progress(mission_id) if mission else {"by_symbol": {}, "total": 0}
    return {
        "mission_id": mission_id,
        "mission": mission,
        "progress": progress,
        "job_status": job.status if job else None,
    }


@router.get("/research/missions/{mission_id}/leaderboard")
async def missions_leaderboard(
    mission_id: str,
    symbol: str | None = None,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Every trial for this mission (optionally scoped to one symbol),
    never filtered to "winners" — same convention as backtest/
    robustness.py's report-every-point sweep output."""
    _check_auth(x_api_key, iatis_session)
    from storage import research_missions

    trials = research_missions.leaderboard(mission_id, symbol=symbol)
    return {"mission_id": mission_id, "symbol": symbol, "trials": trials}


@router.post("/research/missions/{mission_id}/cancel")
async def missions_cancel(
    mission_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Sets the D1 status to "cancelled" first (mission_runner.py's own
    per-trial check picks this up gracefully, finishing the current
    trial cleanly) and falls back to the same hard-kill path
    experiments.py's own cancel endpoint uses, so a mission that's
    between D1 checks (or whose subprocess never got that far) is still
    guaranteed to stop."""
    _check_auth(x_api_key, iatis_session)
    from storage import research_missions

    mission = research_missions.get_mission(mission_id)
    if mission is not None:
        research_missions.set_mission_status(mission_id, "cancelled")

    job = _jobs.get(mission_id)
    if job is None:
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission not found.")
        return {"mission_id": mission_id, "status": "cancelled"}

    with job.lock:
        if job.status not in ("queued", "running"):
            return {"mission_id": mission_id, **_job_summary(job)}
        job.status = "cancelled"
        proc = job.proc
    if job.future is not None:
        job.future.cancel()
    if proc is not None:
        proc.kill()

    from datetime import datetime, timezone
    from storage.audit_log import log_action
    log_action("mission_cancel", x_api_key=x_api_key, session_id=iatis_session, detail=mission_id)
    with job.lock:
        if job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc).isoformat()
        return {"mission_id": mission_id, **_job_summary(job)}
