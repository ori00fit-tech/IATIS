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
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
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
    # Hypothesis A/B runners (Research Workspace, 2026-07-24) — each is an
    # existing, already-tested, pre-registered measurement script
    # (research/results/registry.json). Deliberately NOT parameterized
    # like `backtest`: these run their PRE-REGISTERED fixed parameters
    # (ACTIVE_SYMBOLS, step 8, warmup 220) — a dashboard click choosing
    # different symbols/step would be silently changing a hypothesis's
    # registered method (CLAUDE.md rule 1), not a legitimate research
    # workspace feature. Re-running an already-CLOSED hypothesis
    # (H019/H023/H024/H033/H037 all have a committed verdict) just
    # reproduces the same measurement on the same frozen local CSVs — it
    # does not reopen or contest the committed evidence (rule 4); H103 is
    # the one still-PLANNED entry this newly makes runnable from the
    # dashboard instead of a hand-fed VPS command.
    "hypothesis_H019": [sys.executable, "-m", "research.experiments.H019_crypto_positioning_ab", "--step", "8"],
    "hypothesis_H023": [sys.executable, "-m", "research.experiments.H023_wyckoff_volume_gating", "--all", "--step", "8"],
    "hypothesis_H024": [sys.executable, "-m", "scripts.H024_regime_gate_ab", "--all", "--step", "8"],
    "hypothesis_H033": [sys.executable, "-m", "research.experiments.H033_meta_confidence_gate", "--all", "--step", "8"],
    "hypothesis_H037": [sys.executable, "-m", "research.experiments.H037_decision_delay", "--all", "--step", "8"],
    "hypothesis_H103": [sys.executable, "-m", "research.experiments.H103_meta_decision_gate_ab", "--all", "--step", "8"],
    # Walk-Forward / Robustness (Research Workspace Phase 4, 2026-07-24) —
    # same parameterization pattern as `backtest`: --symbols validated
    # against the configured universe before touching argv. Both reuse the
    # SAME production-aligned engine (backtesting.backtest_engine) as
    # `backtest`, just with a different evaluation methodology layered on
    # top — walk_forward.py does disjoint chronological OOS windows,
    # robustness.py does a parameter-perturbation sensitivity sweep. See
    # each module's docstring for what it does and does NOT claim.
    "walk_forward": [sys.executable, "-m", "backtest.walk_forward"],
    "robustness": [sys.executable, "-m", "backtest.robustness"],
    # AI Research Lab / Mission Center Phase 2 (2026-07-28) — a whole
    # mission is ONE subprocess (fits this same job-whitelist model
    # unchanged), looping over many trials in-process internally
    # (backtest/mission_runner.py). Full argv is built per-request in
    # execution/routes/missions.py (mission_id, search space, sampler,
    # etc. — all server-validated before reaching argv, same pattern as
    # `backtest`'s own --symbols validation below).
    "research_mission": [sys.executable, "-m", "backtest.mission_runner"],
    # AI Research Lab / Mission Center Phase 3 (2026-07-30) — multi-stage
    # validation (Monte Carlo + walk-forward + robustness sweep, per
    # operator-chosen validation symbol) of ONE operator-chosen mission
    # trial. Same one-subprocess-per-job-slot model as research_mission.
    # Full argv is built per-request in execution/routes/missions.py.
    "mission_validate": [sys.executable, "-m", "backtest.mission_validator"],
    # Provider Benchmark & Data Quality Lab Phase 1 (2026-08-08) — a whole
    # benchmark run is ONE subprocess (same one-job-slot-per-run model as
    # research_mission/mission_validate), looping over every
    # (symbol, timeframe, provider) point in-process internally
    # (backtest/price_benchmark.py). Measurement/advisory only — never
    # writes config.yaml's provider_chains. Full argv is built per-request
    # in execution/routes/provider_benchmark.py.
    "price_benchmark": [sys.executable, "-m", "backtest.price_benchmark"],
    # Provider Benchmark & Data Quality Lab Phase 2 (2026-08-08) — same
    # one-job-slot-per-run model as price_benchmark, looping over every
    # (symbol, provider) point in-process internally (backtest/
    # news_benchmark.py). Two real providers (MarketAux, Finnhub),
    # measurement/advisory only. Full argv is built per-request in
    # execution/routes/news_benchmark.py.
    "news_benchmark": [sys.executable, "-m", "backtest.news_benchmark"],
    # Provider Benchmark & Data Quality Lab Phase 3 (2026-08-XX) — same
    # one-job-slot-per-run model as price_benchmark/news_benchmark,
    # looping over every (series, provider) point in-process internally
    # (backtest/macro_benchmark.py). Macro has no symbol/timeframe
    # dimension — a small, fixed series catalog, almost all single-
    # provider (FRED), with a real second source (Alpha Vantage) only for
    # VIX/US10Y/US02Y. Full argv is built per-request in
    # execution/routes/macro_benchmark.py.
    "macro_benchmark": [sys.executable, "-m", "backtest.macro_benchmark"],
    # Provider Benchmark & Data Quality Lab Phase 4 (2026-08-XX) — same
    # one-job-slot-per-run model, looping over every (symbol, provider)
    # point in-process internally (backtest/analytics_benchmark.py).
    # Deliberately reproducibility-only, single provider (marketaux) — no
    # predictive/subsequent-outcome dimension (that's a trading hypothesis,
    # not a provider benchmark; see the module's own docstring). Full argv
    # built per-request in execution/routes/analytics_benchmark.py.
    "analytics_benchmark": [sys.executable, "-m", "backtest.analytics_benchmark"],
    # Engine Benchmark (2026-08-09) — a whole benchmark run is ONE
    # subprocess (same one-job-slot-per-run model as the Provider
    # Benchmark Lab jobs above), looping over every (engine, symbol)
    # point in-process internally (backtest/engine_benchmark.py). Each
    # point is a real standalone single-engine backtest (confluence
    # quorum overridden to 1 via build_engine_config_override — the same
    # ephemeral override channel every other ad-hoc run already uses,
    # never a config.yaml/config/engines.yaml write). Measurement/
    # advisory only, and deliberately NOT a ranking tool — no composite
    # score, no auto-selected "best engine" (CLAUDE.md's dead list
    # already buried "Enabling more engines (any)" twice). Full argv is
    # built per-request in execution/routes/engine_benchmark.py.
    "engine_benchmark": [sys.executable, "-m", "backtest.engine_benchmark"],
}
_JOB_DESCRIPTIONS: dict[str, str] = {
    "verify_data_integrity": "Audit every historical CSV for completeness/corruption/synthetic-data heuristics. Local file read, no network.",
    "forward_review": "Evaluate registry.json's pre-registered D001/D002 forward decision rules against closed outcomes. One D1 read, no network.",
    "backup_d1": "Dump every D1 table + decisions.jsonl to backups/, gzip, verify row counts, rotate old backups. Writes to local disk only, no network beyond the D1 proxy already in use.",
    "backtest": "Cost-inclusive backtest (backtest.runner: real measured spreads, gap-aware exits, Monte Carlo) on local H1 datasets. Symbols validated against the configured universe. CPU-minutes on the VPS; writes reports/.",
    "hypothesis_H019": "H019 (crypto positioning modulator) A/B — FAILED, committed 2026-07-24. Re-running reproduces the committed measurement, does not reopen it.",
    "hypothesis_H023": "H023 (Wyckoff volume gating) A/B — NULL, committed 2026-07-24.",
    "hypothesis_H024": "H024 (hard regime gate) A/B — NULL, committed 2026-07-22.",
    "hypothesis_H033": "H033 (meta-confidence gate) A/B — FAILED, committed 2026-07-22.",
    "hypothesis_H037": "H037 (decision delay) A/B — NULL, committed 2026-07-24.",
    "hypothesis_H103": "H103 (meta_decision gate removal) A/B — PLANNED, not yet run. Only still-open hypothesis job in this whitelist.",
    "walk_forward": "Disjoint chronological OOS window consistency test (backtest/walk_forward.py). Requires --symbols. Fixed production parameters, no per-window optimization.",
    "robustness": "Parameter-sensitivity sweep (backtest/robustness.py): perturbs one cost/risk parameter at a time around its frozen production value. Requires --symbols. NOT out-of-sample validation, does not itself justify changing a parameter.",
    "research_mission": "AI Research Lab mission (backtest/mission_runner.py): Optuna-sampled joint search over timeframes/engines/indicators/risk params per symbol. Launched via POST /research/missions, not this endpoint directly — every result is EXPLORATORY, never auto-registered.",
    "mission_validate": "Multi-stage validation (backtest/mission_validator.py) of one operator-chosen mission trial across operator-chosen validation symbols: re-evaluation + Monte Carlo + walk-forward + robustness sweep per symbol. Launched via POST /research/missions/{id}/validate, not this endpoint directly — result is a LEAD (NO_EDGE/WEAK_LEAD/STRONG_LEAD), never a registry.json promotion.",
    "price_benchmark": "Provider Benchmark & Data Quality Lab (backtest/price_benchmark.py): scores every FX/metals/crypto/indices provider's fetched candles for completeness, per-field correctness vs. a median consensus, timestamp integrity, OHLC integrity, cross-provider agreement, freshness, and latency. Launched via POST /research/provider-benchmark, not this endpoint directly — measurement/advisory only, never auto-changes config.yaml's provider_chains.",
    "news_benchmark": "Provider Benchmark & Data Quality Lab Phase 2 (backtest/news_benchmark.py): scores MarketAux and Finnhub news feeds per symbol for coverage, source diversity, duplicate-headline rate, freshness, latency, sentiment availability, and cross-provider coverage agreement. Launched via POST /research/news-benchmark, not this endpoint directly — measurement/advisory only, never influences H021's own pre-registered process or config.yaml.",
    "macro_benchmark": "Provider Benchmark & Data Quality Lab Phase 3 (backtest/macro_benchmark.py): scores FRED/CBOE/Alpha Vantage macro series (VIX, DXY, yields, credit spread, Fed balance sheet, CPI, GDP, ...) for completeness, freshness, timestamp integrity, latency, and — for VIX/US10Y/US02Y only, the 3 series with a genuine second source — cross-provider agreement. Launched via POST /research/macro-benchmark, not this endpoint directly — measurement/advisory only, never touches core.alt_data_loader.load_macro_snapshot() (the Macro engine's live source) or config.yaml.",
    "analytics_benchmark": "Provider Benchmark & Data Quality Lab Phase 4 (backtest/analytics_benchmark.py): scores MarketAux's sentiment API on reproducibility — fetches the same query twice and checks whether the sentiment value for the same underlying article stays identical — plus coverage, freshness, and latency. Deliberately single-provider (TAAPI's real rate limit rules it out) and reproducibility-only (no predictive/subsequent-outcome-tracking dimension — that's a trading hypothesis, not a provider benchmark). Launched via POST /research/analytics-benchmark, not this endpoint directly — measurement/advisory only, never touches config.yaml.",
    "engine_benchmark": "Engine Benchmark (backtest/engine_benchmark.py): runs every confluence engine standalone (confluence quorum overridden to 1) against real local OHLCV history and reports raw per-(engine, symbol) backtest KPIs (trades, win rate, profit factor, Sharpe, drawdown, expectancy). Launched via POST /research/engine-benchmark, not this endpoint directly — measurement/advisory only, deliberately not a ranking tool (no composite score, no auto-selected 'best engine'), never writes config.yaml/config/engines.yaml/registry.json.",
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
    "hypothesis_H019": "research",
    "hypothesis_H023": "research",
    "hypothesis_H024": "research",
    "hypothesis_H033": "research",
    "hypothesis_H037": "research",
    "hypothesis_H103": "research",
    "walk_forward": "research",
    "robustness": "research",
    "research_mission": "research",
    "mission_validate": "research",
    "price_benchmark": "research",
    "news_benchmark": "research",
    "macro_benchmark": "research",
    "analytics_benchmark": "research",
    "engine_benchmark": "research",
}
_JOB_TIMEOUT_SECONDS = 600  # default; kills a runaway process rather than leaking it forever
_JOB_TIMEOUTS: dict[str, int] = {
    "backtest": 1800,  # full multi-symbol runs are legitimately CPU-minutes
    # Same 20-symbol full-pipeline cost class as `backtest` — H024/H033's
    # own VPS runs this session took real wall-clock minutes.
    "hypothesis_H019": 1800,
    "hypothesis_H023": 1800,
    "hypothesis_H024": 1800,
    "hypothesis_H033": 1800,
    "hypothesis_H037": 1800,
    "hypothesis_H103": 1800,
    # Both run several full backtest passes per symbol (walk_forward: one
    # per window; robustness: one per param x multiplier — up to 20 with
    # the default 4 params x 5 multipliers) — costlier than a single
    # `backtest` run.
    "walk_forward": 2400,
    "robustness": 2400,
    # Last-resort net only — backtest/mission_runner.py's own
    # --max-wall-clock-seconds budget (set per-request, checked every
    # trial) is the PRIMARY, graceful stop mechanism for a mission; this
    # ceiling exists purely so a misbehaving/unbounded mission can't
    # occupy a job slot forever if that internal check is ever bypassed.
    "research_mission": 21_600,  # 6h hard ceiling
    # AI Research Lab / Mission Center Phase 3 (2026-07-30) — worst case
    # per validation symbol is ~1 (direct eval) + wf_windows (default 3)
    # + len(rb_params)*len(rb_multipliers) (default 4x5=20) backtests, so
    # a multi-symbol validation is the same cost class as walk_forward/
    # robustness's own 2400s timeouts, multiplied by symbol count. 3600s
    # is a starting point, not a measurement — revisit if real runs need
    # more headroom.
    "mission_validate": 3_600,
    # Provider Benchmark & Data Quality Lab Phase 1 (2026-08-08) — last-
    # resort net only, same framing as research_mission's own ceiling: a
    # Deep-profile run (~50 in-scope symbols x 4 timeframes x ~4 avg
    # providers) is several hundred independent network fetches; no
    # internal wall-clock budget exists yet in backtest/price_benchmark.py
    # (Phase 1 has no resume loop), so this is a starting point, not a
    # measurement — recalibrate after a real Deep run.
    "price_benchmark": 10_800,
    # Provider Benchmark & Data Quality Lab Phase 2 (2026-08-08) — a Deep
    # profile is ~14 symbols x 2 providers = 28 HTTP calls, far cheaper
    # than price_benchmark's Deep run (no per-timeframe multiplication) —
    # a much smaller ceiling reflects the real, much lower cost class.
    "news_benchmark": 900,
    # Provider Benchmark & Data Quality Lab Phase 3 (2026-08-XX) — a Deep
    # profile is 16 series x up to 2 providers = ~18 fetches, most of them
    # single-provider — the cheapest of the three benchmarks in this lab.
    "macro_benchmark": 600,
    # Provider Benchmark & Data Quality Lab Phase 4 (2026-08-XX) — every
    # symbol needs 2 fetches (the determinism check), still the cheapest
    # cost class here: Deep is ~14 symbols x 2 fetches = 28 HTTP calls.
    "analytics_benchmark": 900,
    # Engine Benchmark (2026-08-09) — each (engine, symbol) point is a
    # REAL standalone backtest (a walk-forward-style bar loop), not a
    # network fetch — a much heavier cost class than any Provider
    # Benchmark Lab job above. A Deep profile (full ~50-symbol universe x
    # 9 backtest-runnable engines) is ~450 real backtests, the same order
    # of magnitude as research_mission's own hundreds-of-trials cost
    # class — same 6h last-resort ceiling, not a measurement.
    "engine_benchmark": 21_600,
}

# Jobs that take a --symbols argv extension, validated server-side against
# the configured universe before being appended (security-equivalent to a
# whitelist member, per `backtest`'s original 2026-07-16 precedent).
_PARAMETERIZED_JOBS = frozenset({"backtest", "walk_forward", "robustness"})

# AI Research Lab / Mission Center Phase 2 (2026-07-28): bumped 2->3.
# Phase 3 (2026-07-30): bumped 3->4 — an operator now often wants to run
# a mission AND validate one of its trials (mission_validate) at the
# same time, on top of ordinary ad-hoc jobs. 4 keeps at least 2 slots
# free for short jobs while one mission + one validation both run,
# without unbounding the pool.
_job_executor = ThreadPoolExecutor(max_workers=4)
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
        self.status = "queued"  # queued -> running -> finished | failed | timeout | cancelled
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.returncode: int | None = None
        self.log_lines: list[str] = []
        self.lock = threading.Lock()
        # Queue Manager (2026-07-24): set by experiments_run() right after
        # submit(), read by experiments_cancel(). future.cancel() only
        # succeeds while the job is still sitting in the executor's
        # internal queue (status=="queued") — once _run_job actually
        # starts, cancel must kill `proc` instead (set under `lock` once
        # Popen succeeds).
        self.future: Any = None
        self.proc: Any = None


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

    with job.lock:
        if job.status == "cancelled":  # cancelled while still queued
            job.finished_at = datetime.now(timezone.utc).isoformat()
            return
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
        with job.lock:
            if job.status == "cancelled":  # cancel() raced Popen — kill immediately
                proc.kill()
                return
            job.proc = proc
        watchdog = threading.Timer(job.timeout, _hard_kill, args=(proc,))
        watchdog.daemon = True
        watchdog.start()
        try:
            start = time.monotonic()
            assert proc.stdout is not None
            for line in proc.stdout:
                with job.lock:
                    if job.status == "cancelled":
                        proc.kill()
                        break
                    job.log_lines.append(line.rstrip("\n"))
                if time.monotonic() - start > job.timeout:
                    proc.kill()
                    job.status = "timeout"
                    break
            proc.wait(timeout=10)
        finally:
            done.set()
            watchdog.cancel()
        with job.lock:
            if job.status not in ("timeout", "cancelled"):
                job.returncode = proc.returncode
                job.status = "finished" if proc.returncode == 0 else "failed"
    except Exception as exc:
        done.set()
        with job.lock:
            if job.status != "cancelled":
                job.log_lines.append(f"[runner error] {exc}")
                job.status = "failed"
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()


class _RiskOverrides(BaseModel):
    """Ad-hoc per-run BacktestConfig overrides (Backtesting Lab Pro Phase
    A, 2026-07-27). Every field optional; only fields actually set are
    forwarded as CLI flags, so an omitted field keeps from_profile()'s
    real default (which may itself be a measured per-symbol value, e.g.
    commission_pips) instead of being silently clobbered. This is a
    process-local, ephemeral engine_overrides dict — it is NEVER written
    to config/engines.yaml or config/risk.yaml, so it can never affect
    the live decision pipeline or any other run."""
    min_rr: float | None = None
    sl_atr_multiplier: float | None = None
    risk_per_trade: float | None = None
    commission_pips: float | None = None
    slippage_pips: float | None = None
    swap_pips_per_night: float | None = None
    initial_balance: float | None = None
    warmup_bars: int | None = None
    step_bars: int | None = None


class _IndicatorFilterSpec(BaseModel):
    """One ad-hoc indicator filter/confirmation/score-weight spec
    (Backtesting Lab Pro Phase D, 2026-07-27). Validated server-side
    against confluence.indicator_filters.INDICATOR_KEYS/FILTER_MODES —
    never written to config.yaml/config/engines.yaml, forwarded as a
    single JSON-encoded --indicators-json CLI argument (structured data
    doesn't fit the nargs="+" flag pattern used for timeframes/engines)."""
    name: str
    mode: str
    params: dict[str, float] = {}
    weight: float = 0.0


class _RunJobRequest(BaseModel):
    job: str
    # backtest only: symbols validated against the configured universe.
    symbols: list[str] | None = None
    # robustness only (Parameter Sweep, 2026-07-25): which cost/risk
    # parameters to sweep and what relative multipliers to apply around
    # each one's real frozen production value — validated against
    # backtest.robustness.SWEEP_PARAMS/DEFAULT_MULTIPLIERS server-side, the
    # exact same guardrail backtest/robustness.py's CLI already enforces
    # (1.0 baseline required, no arbitrary parameter names). Never lets a
    # caller choose a "winning" value — the job always reports every
    # point, same as running it from the CLI.
    params: list[str] | None = None
    multipliers: list[float] | None = None
    # Backtesting Lab Pro Phase A (2026-07-27) — dataset date-range slice
    # + BacktestConfig overrides, valid for the same _PARAMETERIZED_JOBS
    # set that already accepts symbols (backtest/walk_forward/robustness).
    start: str | None = None
    end: str | None = None
    risk_overrides: _RiskOverrides | None = None
    # Backtesting Lab Pro Phase B (2026-07-27) — ad-hoc per-run
    # data.timeframes override (decision TF first, e.g. ["H1"] or
    # ["H4","D1","H1"]). Different override channel than risk_overrides
    # (run_backtest's engine_config keyword, not BacktestConfig) — see
    # backtesting.backtest_engine.build_engine_config_override.
    timeframes: list[str] | None = None
    # Backtesting Lab Pro Phase C (2026-07-27) — ad-hoc per-run engine
    # selection: an explicit, complete list of which engines are ON for
    # this run (not a partial patch — absent = production config/
    # engines.yaml enabled set, unchanged). No registration gate, per an
    # explicit operator decision — but every such result must be labeled
    # exploratory (frontend concern) and this can never write back to
    # config/engines.yaml or registry.json (backend guarantee: it only
    # ever builds an in-memory dict, see build_engine_config_override).
    engines: list[str] | None = None
    # Backtesting Lab Pro Phase D (2026-07-27) — ad-hoc per-run
    # indicator filter/confirmation/score-weight specs. See
    # confluence.indicator_filters's module docstring: an indicator can
    # only veto/nudge a decision the engine vote already produced, never
    # set direction itself. Ephemeral — see _indicators_argv below.
    indicators: list[_IndicatorFilterSpec] | None = None


def _configured_symbol_universe() -> set[str]:
    """Every internal symbol name config.yaml knows (enabled or not) —
    the server-side whitelist request symbols must be members of."""
    config = _get_config()
    return {
        str(s.get("internal", "")).upper()
        for s in config.get("data", {}).get("twelve_data_symbols", [])
        if s.get("internal")
    }


# Trusted Data Center Phase 1 (2026-08-13) — the only physically
# downloaded intraday timeframes (H4/D1 are always derived, see
# backtest/runner.py::find_symbol_csv's own docstring), so checking these
# two local-CSV/Parquet slots covers every possible on-disk dataset.
_DATA_READINESS_CHECK_TIMEFRAMES: tuple[str, ...] = ("M15", "H1")


def _symbol_has_any_data(symbol: str) -> bool:
    """Pre-flight dataset-readiness check: does ANY usable data exist for
    this symbol at all — a local CSV/Parquet for M15 or H1 (backtest.
    runner.find_symbol_csv, the same loader backtest/robustness/walk_
    forward/mission_runner all actually use), or a READY D1 warehouse
    dataset for any timeframe (storage.market_bars.is_ready, the Trusted
    Data Center Phase 2 warehouse Mission Center already prefers when
    present)?

    Returns True whenever EITHER source is unreachable/misconfigured —
    this check exists to turn a *guaranteed* future failure (today: a
    bare FileNotFoundError raised deep inside the subprocess, often
    minutes into a mission after Optuna/D1 setup) into an immediate,
    clear one — never to introduce a NEW way to block a run that would
    otherwise have succeeded. Only a clean, unambiguous "not found
    anywhere" from both checks returns False."""
    from pathlib import Path

    from backtest.runner import find_symbol_csv

    data_dir = Path("data")
    for tf in _DATA_READINESS_CHECK_TIMEFRAMES:
        try:
            find_symbol_csv(symbol, data_dir, timeframe=tf)
            return True
        except FileNotFoundError:
            continue
        except Exception:
            return True  # a lookup error is not evidence of "no data" — don't block on it

    try:
        from storage import market_bars

        for tf in ("M15", "H1", "H4", "D1"):
            if market_bars.is_ready(symbol, tf):
                return True
    except Exception:
        return True  # D1 unconfigured/unreachable — advisory-only, never blocks

    return False


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso_date(value: str, field: str) -> None:
    """Real calendar validation, not just YYYY-MM-DD shape — the regex
    alone would accept a value like 2024-13-40, which date.fromisoformat
    correctly rejects."""
    if not _ISO_DATE_RE.match(value):
        raise HTTPException(status_code=400, detail=f"{field} must be an ISO date (YYYY-MM-DD).")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be an ISO date (YYYY-MM-DD).")

# Bounds mirror what BacktestConfig's own math assumes — not arbitrary:
# risk_per_trade is a FRACTION of balance, sizing degenerates/blows up
# outside (0, 1]; the rest are sanity floors/ceilings, not measured limits.
_RISK_OVERRIDE_BOUNDS: dict[str, tuple[float, float]] = {
    "min_rr": (0.01, 100.0),
    "sl_atr_multiplier": (0.01, 50.0),
    "risk_per_trade": (0.0001, 1.0),
    "commission_pips": (0.0, 10_000.0),
    "slippage_pips": (0.0, 10_000.0),
    "swap_pips_per_night": (0.0, 10_000.0),
    "initial_balance": (1.0, 1_000_000_000.0),
    "warmup_bars": (1, 100_000),
    "step_bars": (1, 100_000),
}


def _risk_override_argv(ro: "_RiskOverrides") -> list[str]:
    """Backtesting Lab Pro Phase A — build the CLI flags for a
    _RiskOverrides body, validating each set field against
    _RISK_OVERRIDE_BOUNDS. Iterates RISK_OVERRIDE_FIELDS (not the model's
    own field order) so the resulting argv is deterministic regardless of
    how the client serialized the request body — load-bearing for the
    exact-argv-list tests."""
    from backtesting.backtest_engine import RISK_OVERRIDE_FIELDS

    argv: list[str] = []
    for field_name in RISK_OVERRIDE_FIELDS:
        value = getattr(ro, field_name)
        if value is None:
            continue
        lo, hi = _RISK_OVERRIDE_BOUNDS[field_name]
        if not (lo <= value <= hi):
            raise HTTPException(
                status_code=400,
                detail=f"risk_overrides.{field_name} must be between {lo} and {hi}.",
            )
        argv += [f"--{field_name.replace('_', '-')}", str(value)]
    return argv


# Bounds for indicator params (Backtesting Lab Pro Phase D, 2026-07-27) —
# sanity floors/ceilings mirroring _RISK_OVERRIDE_BOUNDS's own convention,
# not measured limits. Applies to any param key across all 5 indicators;
# an indicator that doesn't use a given key simply never reads it (see
# confluence/indicator_filters.py's per-indicator param defaults).
_INDICATOR_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "period": (2, 500), "fast": (2, 500), "slow": (2, 500), "signal": (2, 500),
    "buy_above": (0, 100), "sell_below": (0, 100), "min_trend": (0, 100),
    "min_atr": (0, 1_000_000_000.0), "max_atr": (0, 1_000_000_000.0),
}


def _indicators_argv(specs: list[_IndicatorFilterSpec]) -> list[str]:
    """Backtesting Lab Pro Phase D — validate every indicator spec
    against confluence.indicator_filters.INDICATOR_KEYS/FILTER_MODES,
    reject duplicates and out-of-bounds params/weight, then JSON-encode
    the whole list as a single --indicators-json CLI argument (structured
    per-indicator data doesn't fit the nargs="+" flag pattern used for
    timeframes/engines)."""
    import json as _json
    from confluence.indicator_filters import FILTER_MODES, INDICATOR_KEYS

    if len(specs) > len(INDICATOR_KEYS):
        raise HTTPException(status_code=400, detail=f"indicators: at most {len(INDICATOR_KEYS)} entries.")
    seen: set[str] = set()
    for spec in specs:
        if spec.name not in INDICATOR_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown indicator '{spec.name}' — choose from {sorted(INDICATOR_KEYS)}.",
            )
        if spec.name in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate indicator '{spec.name}'.")
        seen.add(spec.name)
        if spec.mode not in FILTER_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown mode '{spec.mode}' for '{spec.name}' — choose from {sorted(FILTER_MODES)}.",
            )
        if not (0.0 <= spec.weight <= 100.0):
            raise HTTPException(status_code=400, detail=f"indicators.{spec.name}.weight must be 0-100.")
        for key, value in spec.params.items():
            bounds = _INDICATOR_PARAM_BOUNDS.get(key)
            if bounds is None:
                raise HTTPException(status_code=400, detail=f"Unknown indicator param '{key}' for '{spec.name}'.")
            lo, hi = bounds
            if not (lo <= value <= hi):
                raise HTTPException(
                    status_code=400,
                    detail=f"indicators.{spec.name}.{key} must be between {lo} and {hi}.",
                )
        if "min_atr" in spec.params and "max_atr" in spec.params and spec.params["min_atr"] > spec.params["max_atr"]:
            raise HTTPException(status_code=400, detail=f"indicators.{spec.name}: min_atr must be <= max_atr.")
    return ["--indicators-json", _json.dumps([s.model_dump() for s in specs])]


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
                "requires_symbols": k in _PARAMETERIZED_JOBS,
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
    if body.job in _PARAMETERIZED_JOBS:
        symbols = [str(s).upper().strip() for s in (body.symbols or []) if str(s).strip()]
        if not symbols:
            raise HTTPException(status_code=400, detail=f"{body.job} requires at least one symbol.")
        if len(symbols) > 20:
            raise HTTPException(status_code=400, detail=f"{body.job}: at most 20 symbols per run.")
        universe = _configured_symbol_universe()
        unknown = sorted(set(symbols) - universe)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown symbol(s) {unknown} — must be in the configured universe.",
            )
        no_data = sorted(s for s in symbols if not _symbol_has_any_data(s))
        if no_data:
            raise HTTPException(
                status_code=400,
                detail=f"No downloaded data found for {no_data} (checked local CSV/Parquet and the D1 "
                       f"warehouse) — download it first (see scripts/download_*_history.py) before running "
                       f"'{body.job}'.",
            )
        argv += ["--symbols", *symbols]
    elif body.symbols:
        raise HTTPException(status_code=400, detail=f"'{body.job}' takes no symbols.")

    if body.job in _PARAMETERIZED_JOBS:
        if body.start is not None:
            _validate_iso_date(body.start, "start")
            argv += ["--start", body.start]
        if body.end is not None:
            _validate_iso_date(body.end, "end")
            argv += ["--end", body.end]
        if body.start and body.end and body.start > body.end:
            raise HTTPException(status_code=400, detail="start must be <= end.")
        if body.risk_overrides is not None:
            argv += _risk_override_argv(body.risk_overrides)
        if body.timeframes is not None:
            from core.timeframe_sync import SUPPORTED_TIMEFRAMES

            if not (1 <= len(body.timeframes) <= 4):
                raise HTTPException(status_code=400, detail="timeframes: 1-4 values per run.")
            unknown_tf = sorted(set(body.timeframes) - set(SUPPORTED_TIMEFRAMES))
            if unknown_tf:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown timeframe(s) {unknown_tf} — choose from {sorted(SUPPORTED_TIMEFRAMES)}.",
                )
            argv += ["--timeframes", *body.timeframes]
        if body.engines is not None:
            from backtesting.backtest_engine import ENGINE_KEYS

            if not (1 <= len(body.engines) <= len(ENGINE_KEYS)):
                raise HTTPException(
                    status_code=400, detail=f"engines: 1-{len(ENGINE_KEYS)} values per run.",
                )
            unknown_engines = sorted(set(body.engines) - set(ENGINE_KEYS))
            if unknown_engines:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown engine(s) {unknown_engines} — choose from {sorted(ENGINE_KEYS)}.",
                )
            argv += ["--engines", *body.engines]
        if body.indicators is not None:
            argv += _indicators_argv(body.indicators)
    elif (
        body.start is not None or body.end is not None
        or body.risk_overrides is not None or body.timeframes is not None
        or body.engines is not None or body.indicators is not None
    ):
        raise HTTPException(
            status_code=400,
            detail=f"'{body.job}' takes no start/end/risk_overrides/timeframes/engines/indicators.",
        )

    if body.job == "robustness":
        from backtest.robustness import SWEEP_PARAMS

        if body.params is not None:
            params = [str(p).strip() for p in body.params if str(p).strip()]
            unknown_params = sorted(set(params) - set(SWEEP_PARAMS))
            if unknown_params:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown sweep param(s) {unknown_params} — choose from {sorted(SWEEP_PARAMS)}.",
                )
            if not params:
                raise HTTPException(status_code=400, detail="params, if provided, must not be empty.")
            argv += ["--params", *params]
        if body.multipliers is not None:
            if 1.0 not in body.multipliers:
                raise HTTPException(status_code=400, detail="multipliers must include 1.0 as the baseline point.")
            if not (1 <= len(body.multipliers) <= 10):
                raise HTTPException(status_code=400, detail="multipliers: 1-10 values per run.")
            if any(m <= 0 for m in body.multipliers):
                raise HTTPException(status_code=400, detail="multipliers must all be positive.")
            argv += ["--multipliers", *[str(m) for m in body.multipliers]]
    elif body.params is not None or body.multipliers is not None:
        raise HTTPException(status_code=400, detail=f"'{body.job}' takes no params/multipliers.")

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
    detail = f"{body.job} ({job_id})"
    if body.job in _PARAMETERIZED_JOBS:
        detail += f" symbols={body.symbols}"
        if body.start:
            detail += f" start={body.start}"
        if body.end:
            detail += f" end={body.end}"
        if body.risk_overrides is not None:
            detail += f" risk_overrides={body.risk_overrides.model_dump(exclude_none=True)}"
        if body.timeframes is not None:
            detail += f" timeframes={body.timeframes}"
        if body.engines is not None:
            detail += f" engines={body.engines}"
        if body.indicators is not None:
            detail += f" indicators={[s.model_dump() for s in body.indicators]}"
    if body.job == "robustness":
        if body.params is not None:
            detail += f" params={body.params}"
        if body.multipliers is not None:
            detail += f" multipliers={body.multipliers}"
    log_action("experiment_run", x_api_key=x_api_key, session_id=iatis_session, detail=detail)

    job.future = _job_executor.submit(_run_job, job)
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


@router.post("/experiments/{job_id}/cancel")
async def experiments_cancel(
    job_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Queue Manager (2026-07-24): cancel a queued or running job.

    Queued: future.cancel() removes it from the executor's internal queue
    before a worker ever picks it up — _run_job() never even starts.
    Running: proc.kill() terminates the child immediately; _run_job()'s
    own checkpoints (see the function) see status=='cancelled' and leave
    it alone rather than overwriting it with 'finished'/'failed'.
    Already-terminal jobs (finished/failed/timeout/cancelled) 409 — there
    is nothing left to cancel."""
    _check_auth(x_api_key, iatis_session)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with job.lock:
        if job.status not in ("queued", "running"):
            raise HTTPException(status_code=409, detail=f"Job is already {job.status}; nothing to cancel.")
        job.status = "cancelled"
        proc = job.proc
    if job.future is not None:
        job.future.cancel()  # no-op (returns False) once already running — harmless
    if proc is not None:
        proc.kill()

    from storage.audit_log import log_action
    log_action("experiment_cancel", x_api_key=x_api_key, session_id=iatis_session,
              detail=f"{job.name} ({job_id})")
    with job.lock:
        if job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc).isoformat()
        return _job_summary(job)


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
