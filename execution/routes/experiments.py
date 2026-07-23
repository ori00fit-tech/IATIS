"""
execution/routes/experiments.py
----------------------------------
Experiment Runner (Mission Control module 5) — whitelisted job execution
only, plus /ops/reload-config and /audit-log. No arbitrary shell, no
user-supplied arguments: `job` selects a key into a hardcoded argv list.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query
from pydantic import BaseModel

from execution.api_core import _REPO_ROOT, _check_auth, _get_config, _reset_config_cache

router = APIRouter()


# ---------------------------------------------------------------------------
# Experiment Runner (Mission Control module 5) — whitelisted job execution
# only. No arbitrary shell, no user-supplied arguments: `job` selects a key
# into a hardcoded argv list, exactly the pattern already used by /logs
# (journalctl) and /files/diff (git diff) — never shell=True, never string
# interpolation of request data into a command line.
#
# SCOPE, DELIBERATELY NARROW: only jobs that are fast, local, and don't
# spend anything are whitelisted here — verify_data_integrity (reads local
# CSVs) and forward_review (one D1 read). Genuinely long-running jobs
# (walk_forward_validation, engine_subset_search — CPU-heavy, minutes+) and
# anything that burns rate-limited provider API quota (cross_provider_diff)
# are NOT included; widening this whitelist changes what a dashboard click
# can cost on a live VPS and should be a deliberate operator decision, not
# something inferred here. See MISSION_CONTROL_AUDIT.md's progress log.
# ---------------------------------------------------------------------------
_JOB_COMMANDS: dict[str, list[str]] = {
    "verify_data_integrity": [sys.executable, "-m", "scripts.verify_data_integrity"],
    "forward_review": [sys.executable, "-m", "scripts.forward_review"],
    "backup_d1": [sys.executable, "-m", "scripts.backup_d1"],
    # Parameterized job (the ONE exception to no-args, added 2026-07-16 on
    # operator request): --symbols values are validated against the
    # config-defined universe before touching the argv — a member of a
    # server-side whitelist is security-equivalent to a job key. Runs the
    # real cost-inclusive engine on LOCAL H1 datasets; no provider spend.
    "backtest": [sys.executable, "-m", "backtest.runner"],
}
_JOB_DESCRIPTIONS: dict[str, str] = {
    "verify_data_integrity": "Audit every historical CSV for completeness/corruption/synthetic-data heuristics. Local file read, no network.",
    "forward_review": "Evaluate registry.json's pre-registered D001/D002 forward decision rules against closed outcomes. One D1 read, no network.",
    "backup_d1": "Dump every D1 table + decisions.jsonl to backups/, gzip, verify row counts, rotate old backups. Writes to local disk only, no network beyond the D1 proxy already in use.",
    "backtest": "Cost-inclusive backtest (backtest.runner: real measured spreads, gap-aware exits, Monte Carlo) on local H1 datasets. Symbols validated against the configured universe. CPU-minutes on the VPS; writes reports/.",
}
# Categorizes each whitelisted job for the frontend (Experiment Runner
# shows "research", VPS Operations shows "ops") — same underlying
# job-execution engine either way, per MISSION_CONTROL_AUDIT.md's note
# that module 12 should reuse module 5's primitive rather than duplicate it.
_JOB_CATEGORIES: dict[str, str] = {
    "verify_data_integrity": "research",
    "forward_review": "research",
    "backup_d1": "ops",
    "backtest": "research",
}
_JOB_TIMEOUT_SECONDS = 600  # default; kills a runaway process rather than leaking it forever
_JOB_TIMEOUTS: dict[str, int] = {
    "backtest": 1800,  # full multi-symbol runs are legitimately CPU-minutes
}

_job_executor = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, "_Job"] = {}
_jobs_lock = threading.Lock()


class _Job:
    def __init__(self, job_id: str, name: str, argv: list[str] | None = None):
        self.id = job_id
        self.name = name
        # Frozen at creation: request-derived args (backtest symbols) are
        # validated in the endpoint and baked in here, never re-read.
        self.argv = list(argv) if argv is not None else list(_JOB_COMMANDS[name])
        self.timeout = _JOB_TIMEOUTS.get(name, _JOB_TIMEOUT_SECONDS)
        self.status = "queued"  # queued -> running -> finished | failed | timeout
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.returncode: int | None = None
        self.log_lines: list[str] = []
        self.lock = threading.Lock()


def _job_summary(job: "_Job") -> dict[str, Any]:
    return {
        "job_id": job.id,
        "job": job.name,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "returncode": job.returncode,
        "log_lines": len(job.log_lines),
    }


def _run_job(job: "_Job") -> None:
    import subprocess

    job.status = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()
    argv = job.argv
    # done/watchdog: the read loop below only checks the timeout between
    # lines it receives, so a child whose stdout is block-buffered (any
    # non-TTY `python3 -m ...` process without PYTHONUNBUFFERED) or that
    # hangs producing no output at all could run past `job.timeout`
    # indefinitely — the loop just blocks on the next readline (audit
    # docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P1-3). PYTHONUNBUFFERED=1
    # fixes the common case (Python children flush every line); the
    # watchdog timer is the real, unconditional wall-clock enforcement.
    done = threading.Event()

    def _hard_kill(proc: "subprocess.Popen[str]") -> None:
        if not done.is_set():
            job.status = "timeout"
            proc.kill()

    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=_REPO_ROOT, bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        watchdog = threading.Timer(job.timeout, _hard_kill, args=(proc,))
        watchdog.daemon = True
        watchdog.start()
        try:
            start = time.monotonic()
            assert proc.stdout is not None
            for line in proc.stdout:
                with job.lock:
                    job.log_lines.append(line.rstrip("\n"))
                if time.monotonic() - start > job.timeout:
                    proc.kill()
                    job.status = "timeout"
                    break
            proc.wait(timeout=10)
        finally:
            done.set()
            watchdog.cancel()
        if job.status != "timeout":
            job.returncode = proc.returncode
            job.status = "finished" if proc.returncode == 0 else "failed"
    except Exception as exc:
        done.set()
        with job.lock:
            job.log_lines.append(f"[runner error] {exc}")
        job.status = "failed"
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()


class _RunJobRequest(BaseModel):
    job: str
    # backtest only: symbols validated against the configured universe.
    symbols: list[str] | None = None


def _configured_symbol_universe() -> set[str]:
    """Every internal symbol name config.yaml knows (enabled or not) —
    the server-side whitelist request symbols must be members of."""
    config = _get_config()
    return {
        str(s.get("internal", "")).upper()
        for s in config.get("data", {}).get("twelve_data_symbols", [])
        if s.get("internal")
    }


@router.get("/experiments/jobs")
async def experiment_job_catalog(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """The whitelisted set of jobs /experiments/run will execute. Nothing
    else — see the module docstring above for why this list is short."""
    _check_auth(x_api_key, iatis_session)
    return {
        "jobs": [
            {
                "id": k,
                "description": _JOB_DESCRIPTIONS.get(k, ""),
                "category": _JOB_CATEGORIES.get(k, "research"),
                # The frontend needs to know to collect symbols BEFORE
                # posting — running "backtest" bare is a guaranteed 400.
                "requires_symbols": k == "backtest",
            }
            for k in _JOB_COMMANDS
        ]
    }


@router.post("/experiments/run")
async def experiments_run(
    body: _RunJobRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    if body.job not in _JOB_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown job '{body.job}'. See /experiments/jobs.")

    argv = list(_JOB_COMMANDS[body.job])
    if body.job == "backtest":
        symbols = [str(s).upper().strip() for s in (body.symbols or []) if str(s).strip()]
        if not symbols:
            raise HTTPException(status_code=400, detail="backtest requires at least one symbol.")
        if len(symbols) > 20:
            raise HTTPException(status_code=400, detail="backtest: at most 20 symbols per run.")
        universe = _configured_symbol_universe()
        unknown = sorted(set(symbols) - universe)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown symbol(s) {unknown} — must be in the configured universe.",
            )
        argv += ["--symbols", *symbols]
    elif body.symbols:
        raise HTTPException(status_code=400, detail=f"'{body.job}' takes no symbols.")

    with _jobs_lock:
        already_running = any(
            j.name == body.job and j.status in ("queued", "running") for j in _jobs.values()
        )
        if already_running:
            raise HTTPException(status_code=409, detail=f"'{body.job}' is already running.")
        job_id = uuid.uuid4().hex[:12]
        job = _Job(job_id, body.job, argv=argv)
        _jobs[job_id] = job

    from storage.audit_log import log_action
    log_action(
        "experiment_run", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"{body.job} ({job_id})" + (f" symbols={body.symbols}" if body.job == "backtest" else ""),
    )

    _job_executor.submit(_run_job, job)
    return _job_summary(job)


@router.get("/experiments")
async def experiments_list(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
        return {"jobs": [_job_summary(j) for j in jobs]}


@router.get("/experiments/{job_id}")
async def experiments_status(
    job_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with job.lock:
        return {**_job_summary(job), "log": list(job.log_lines)}


# ---------------------------------------------------------------------------
# VPS Operations (Mission Control module 12) — controlled operations only.
# "Diagnostics"/"Health check" reuse GET /health/full directly (the
# frontend calls it, no new endpoint needed). "Backup" reuses the
# Experiment Runner's job engine above via the "backup_d1" whitelist entry
# (category "ops"). This section only adds what neither of those already
# covers: an in-process config-cache reload.
#
# DELIBERATELY EXCLUDED: restarting iatis-api/iatis-scheduler. Restarting
# the live scheduler mid-cycle on what may be a production trading VPS is
# a materially different risk than anything else in this dashboard restart
# — it stays an explicit `systemctl restart` over SSH until an operator
# deliberately asks for it to be wired up. See MISSION_CONTROL_AUDIT.md.
# ---------------------------------------------------------------------------
@router.post("/ops/reload-config")
async def ops_reload_config(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Clear the in-process config.yaml cache so the next request reloads
    it from disk — e.g. after editing config.yaml on the VPS. Does not
    itself change any threshold/engine/trading value, only cache staleness.
    """
    _check_auth(x_api_key, iatis_session)
    _reset_config_cache()
    from storage.audit_log import log_action
    log_action("reload_config", x_api_key=x_api_key, session_id=iatis_session)
    return {"success": True, "message": "Config cache cleared — next request reloads config.yaml from disk."}


# ---------------------------------------------------------------------------
# Security (Mission Control module 15) — audit log for every mutating
# action (login, job triggers, config reload, outcome mutation). Every
# job-execution and file-serving route in this file is already whitelist-
# only with fixed argv, satisfying "no arbitrary command execution" and
# "whitelisted jobs only" — see the Experiment Runner/File Explorer/Live
# Logs module docstrings above.
#
# DELIBERATELY NOT INCLUDED: role-based access control. Today's auth is a
# single shared API key (hmac.compare_digest) plus rotating session
# cookies — one key grants full access, there is no user/role model. RBAC
# is a real multi-user architecture change (accounts, role assignment,
# per-endpoint permission checks) that changes how every operator
# authenticates; it should be a deliberate, scoped decision made with the
# operator, not inferred and built unilaterally this late in a large
# session. Documented as an open gap in MISSION_CONTROL_AUDIT.md.
# ---------------------------------------------------------------------------
@router.get("/audit-log")
async def audit_log_endpoint(
    limit: int = Query(default=200, ge=1, le=1000),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage.audit_log import read_actions
    entries = read_actions(limit=limit)
    return {"count": len(entries), "entries": entries}
