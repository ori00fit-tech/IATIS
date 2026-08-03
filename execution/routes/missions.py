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
    context_filter_set_choices: list[list[dict]] = [[]]
    # Track C (Phase 4, 2026-08-01) — ad-hoc PriceAction v2/Wyckoff v2
    # selection. Each entry is a COMPLETE {engine_key: variant} map,
    # index-sampled — same convention as indicator_set_choices/
    # context_filter_set_choices. Still 100% ephemeral: never activates
    # a variant in config/engines.yaml's live default, see
    # backtesting.backtest_engine.build_engine_config_override's
    # engine_variants docstring.
    engine_variant_choices: list[dict[str, str]] = [{}]
    # Hypothesis Bundles (2026-07-30) — when set, REPLACES independent
    # sampling of timeframes_choices/engine_set_choices/indicator_set_choices/
    # context_filter_set_choices/engine_variant_choices with one shared
    # index over complete named bundles. See backtest/optimizer.py's
    # module-level comment on _HYPOTHESIS_IDX_KEY for why this is a
    # separate field rather than giving the existing fields multiple
    # choices each.
    hypothesis_bundle_choices: list[dict] | None = None
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
            context_filter_set_choices=tuple(tuple(c) for c in body.context_filter_set_choices),
            engine_variant_choices=tuple(dict(v) for v in body.engine_variant_choices),
            hypothesis_bundle_choices=(
                tuple(dict(b) for b in body.hypothesis_bundle_choices)
                if body.hypothesis_bundle_choices else None
            ),
            risk_param_ranges=body.risk_param_ranges,
            risk_param_grid=body.risk_param_grid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Forensic Audit follow-up (2026-08-03) — operator-reported "50 trials,
    # 1 effective configuration" (mission fff9806b90c2). Traced end-to-end
    # (UI -> API -> MissionSearchSpace -> suggest_point/resolve_point ->
    # mission_runner) and confirmed the pipeline correctly preserves and
    # independently samples 2+ hypothesis bundles when given them (see
    # tests/test_optimizer.py::test_hypothesis_bundles_are_actually_sampled_not_stuck_on_index_zero
    # and tests/test_mission_runner.py::test_mission_run_with_hypothesis_bundles_uses_both_bundles,
    # both real end-to-end proofs, not assumptions). MissionSearchSpace
    # already rejects an EMPTY hypothesis_bundle_choices list — but a
    # LIST OF EXACTLY ONE was silently accepted, producing a mathematically
    # correct but wasteful "search" with nothing to search (every trial
    # samples the sole choice) and no warning before n_trials_per_symbol
    # runs execute. Fail fast here instead, matching the UI's own framing
    # ("search across named hypotheses" implies 2+). Deliberately NOT added
    # to MissionSearchSpace.__post_init__ itself — that shared dataclass is
    # also used to reconstruct ALREADY-COMPLETED missions (mission_validator.py,
    # the meta-analysis endpoint) that may legitimately have this exact
    # shape; the guard belongs only at mission-CREATION time.
    if body.hypothesis_bundle_choices is not None and len(body.hypothesis_bundle_choices) == 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "hypothesis_bundle_choices has only 1 entry — hypothesis-search "
                "mode requires 2+ named hypotheses to search across, or every "
                "trial will sample the same one. Add another hypothesis, or omit "
                "hypothesis_bundle_choices to run a fixed-configuration mission."
            ),
        )

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
        "--context-filter-set-choices", json.dumps(body.context_filter_set_choices),
        "--engine-variant-choices", json.dumps(body.engine_variant_choices),
    ]
    if body.start:
        argv += ["--start", body.start]
    if body.end:
        argv += ["--end", body.end]
    if body.risk_param_ranges:
        argv += ["--risk-param-ranges", json.dumps(body.risk_param_ranges)]
    if body.risk_param_grid:
        argv += ["--risk-param-grid", json.dumps(body.risk_param_grid)]
    if body.hypothesis_bundle_choices:
        argv += ["--hypothesis-bundle-choices", json.dumps(body.hypothesis_bundle_choices)]
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

    search_space_kind = None
    if mission and mission.get("search_space_json"):
        # Forensic Audit Phase 1, item C (2026-08-02) — surfaces which axis
        # this mission's trials actually vary across (SIGNAL_VARIATION /
        # RISK_ONLY_VARIATION / MIXED / NONE), proactively, instead of an
        # operator only discovering "risk-only" after the fact in
        # Meta-Analysis.
        from backtest.optimizer import classify_search_space_variation, search_space_from_dict

        space = search_space_from_dict(json.loads(mission["search_space_json"]))
        search_space_kind = classify_search_space_variation(space)

    return {
        "mission_id": mission_id,
        "mission": mission,
        "progress": progress,
        "job_status": job.status if job else None,
        "search_space_kind": search_space_kind,
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


# ── Phase 3 (2026-07-30): Meta-Analysis + Multi-Stage Validation ──────────
#
# Meta-analysis is pure, read-only, computed fresh on every call from
# already-stored trials (backtest/meta_analysis.py) — never persisted, no
# new backtests. Validation is a new background job (backtest/
# mission_validator.py) re-evaluating ONE operator-chosen COMPLETE trial
# across operator-chosen validation symbols via Monte Carlo/walk-forward/
# robustness — never auto-picked, never writing to registry.json/
# config.yaml/config/engines.yaml (see mission_validator.py's own
# module docstring for the full guarantee). A validation verdict
# (NO_EDGE/WEAK_LEAD/STRONG_LEAD) is a LEAD, never a promotion.

_MAX_VALIDATION_SYMBOLS = 10


class _ValidationRequest(BaseModel):
    """Validates ONE operator-chosen COMPLETE trial across operator-
    chosen validation symbols — never auto-picked. See backtest/
    mission_validator.py's module docstring for the full methodology and
    safety guarantee."""
    trial_number: int
    trial_symbol: str
    validation_symbols: list[str]
    # Forensic Audit Phase 1, item D (2026-08-02) — SAME_SYMBOL is the new
    # default: confirms ONLY the trial's own symbol. CROSS_SYMBOL keeps
    # today's exact, unchanged semantics (an independent operator-chosen
    # symbol list, no membership requirement against trial_symbol).
    validation_mode: str = "SAME_SYMBOL"
    start: str | None = None
    end: str | None = None
    wf_windows: int = 3
    wf_min_trades_per_window: int = 10
    wf_warmup_bars: int = 210
    # Mirrors backtest.robustness.DEFAULT_MULTIPLIERS/SWEEP_PARAMS — kept
    # as literals here since backtest.* imports stay lazy in this file
    # (optuna/pandas import cost only paid when a mission/validation
    # route is actually hit).
    rb_multipliers: list[float] = [0.5, 0.8, 1.0, 1.2, 1.5]
    rb_params: list[str] = ["sl_atr_multiplier", "commission_pips", "slippage_pips", "min_rr"]
    rb_min_trades: int = 10
    mc_n_simulations: int = 1000
    mc_seed: int = 42


@router.post("/research/missions/{mission_id}/validate")
async def missions_validate(
    mission_id: str,
    body: _ValidationRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import research_missions

    mission = research_missions.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found.")

    trial_symbol = str(body.trial_symbol).upper().strip()
    trial = research_missions.get_trial(mission_id, body.trial_number, trial_symbol)
    if trial is None:
        raise HTTPException(status_code=404, detail="Trial not found.")
    if trial["state"] != "COMPLETE":
        raise HTTPException(
            status_code=400,
            detail=f"Trial state is {trial['state']!r} — only COMPLETE trials can be validated.",
        )

    from backtest.mission_validator import SAME_SYMBOL, VALIDATION_MODES

    validation_mode = str(body.validation_mode).strip().upper()
    if validation_mode not in VALIDATION_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown validation_mode {validation_mode!r} — choose from {VALIDATION_MODES}.")

    validation_symbols = [str(s).upper().strip() for s in body.validation_symbols if str(s).strip()]

    if validation_mode == SAME_SYMBOL:
        # FAIL HARD — never silently substitute or widen the symbol list.
        # An empty list defaults to [trial_symbol]; anything else that
        # isn't exactly [trial_symbol] is a 400, per the operator's
        # explicit "this must become an invariant in the code" requirement.
        if not validation_symbols:
            validation_symbols = [trial_symbol]
        elif validation_symbols != [trial_symbol]:
            raise HTTPException(
                status_code=400,
                detail=f"validation_mode=SAME_SYMBOL requires validation_symbols to be omitted/empty or "
                       f"exactly [{trial_symbol!r}] (the trial's own symbol) — got {validation_symbols}. "
                       f"Use validation_mode=CROSS_SYMBOL to validate against other symbols.",
            )
    else:  # CROSS_SYMBOL — today's exact, unchanged rules
        if len(validation_symbols) < 2:
            raise HTTPException(
                status_code=400,
                detail="validation_symbols must include at least 2 symbols — a single-symbol "
                       "validation cannot distinguish an edge from curve-fitting.",
            )
        if len(validation_symbols) > _MAX_VALIDATION_SYMBOLS:
            raise HTTPException(status_code=400, detail=f"at most {_MAX_VALIDATION_SYMBOLS} validation symbols per run.")
    universe = _configured_symbol_universe()
    unknown = sorted(set(validation_symbols) - universe)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown symbol(s) {unknown} — must be in the configured universe.")

    if body.start is not None:
        _validate_iso_date(body.start, "start")
    if body.end is not None:
        _validate_iso_date(body.end, "end")
    if body.start and body.end and body.start > body.end:
        raise HTTPException(status_code=400, detail="start must be <= end.")

    if body.wf_windows < 2:
        raise HTTPException(status_code=400, detail="wf_windows must be >= 2.")
    if body.wf_min_trades_per_window < 1:
        raise HTTPException(status_code=400, detail="wf_min_trades_per_window must be >= 1.")
    if body.wf_warmup_bars < 1:
        raise HTTPException(status_code=400, detail="wf_warmup_bars must be >= 1.")
    if body.rb_min_trades < 1:
        raise HTTPException(status_code=400, detail="rb_min_trades must be >= 1.")
    if not (100 <= body.mc_n_simulations <= 20_000):
        raise HTTPException(status_code=400, detail="mc_n_simulations must be 100-20000.")
    if not body.rb_params:
        raise HTTPException(status_code=400, detail="rb_params must have at least one entry.")
    if 1.0 not in body.rb_multipliers:
        raise HTTPException(status_code=400, detail="rb_multipliers must include 1.0 as the baseline point.")

    from backtest.robustness import SWEEP_PARAMS
    unknown_params = sorted(set(body.rb_params) - set(SWEEP_PARAMS))
    if unknown_params:
        raise HTTPException(status_code=400, detail=f"Unknown rb_params {unknown_params} — choose from {SWEEP_PARAMS}.")

    validation_id = uuid.uuid4().hex[:12]
    argv = list(_JOB_COMMANDS["mission_validate"]) + [
        "--validation-id", validation_id,
        "--mission-id", mission_id,
        "--trial-number", str(body.trial_number),
        "--trial-symbol", trial_symbol,
        "--validation-symbols", *validation_symbols,
        "--validation-mode", validation_mode,
        "--wf-windows", str(body.wf_windows),
        "--wf-min-trades-per-window", str(body.wf_min_trades_per_window),
        "--wf-warmup-bars", str(body.wf_warmup_bars),
        "--rb-multipliers", *[str(m) for m in body.rb_multipliers],
        "--rb-params", *body.rb_params,
        "--rb-min-trades", str(body.rb_min_trades),
        "--mc-simulations", str(body.mc_n_simulations),
        "--mc-seed", str(body.mc_seed),
    ]
    if body.start:
        argv += ["--start", body.start]
    if body.end:
        argv += ["--end", body.end]

    with _jobs_lock:
        job = _Job(validation_id, "mission_validate", argv=argv)
        _jobs[validation_id] = job

    from storage.audit_log import log_action
    log_action(
        "mission_validate_create", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"mission_validate ({validation_id}) mission={mission_id} trial={body.trial_number} "
               f"({trial_symbol}) mode={validation_mode} validation_symbols={validation_symbols}",
    )

    job.future = _job_executor.submit(_run_job, job)
    return {"validation_id": validation_id, **_job_summary(job)}


@router.get("/research/missions/{mission_id}/validations")
async def missions_validations_list(
    mission_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Every validation ever run for this mission, pass or fail — never
    filtered to only the ones that reached STRONG_LEAD."""
    _check_auth(x_api_key, iatis_session)
    from storage import research_mission_validations

    return {"mission_id": mission_id, "validations": research_mission_validations.list_validations(mission_id)}


@router.get("/research/missions/{mission_id}/validations/{validation_id}")
async def missions_validation_detail(
    mission_id: str,
    validation_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import research_mission_validations

    validation = research_mission_validations.get_validation(validation_id)
    job = _jobs.get(validation_id)
    if (validation is None or validation["mission_id"] != mission_id) and job is None:
        raise HTTPException(status_code=404, detail="Validation not found.")

    results = research_mission_validations.validation_results(validation_id) if validation else []
    return {
        "validation_id": validation_id,
        "validation": validation,
        "results": results,
        "job_status": job.status if job else None,
    }


@router.get("/research/missions/{mission_id}/meta-analysis")
async def missions_meta_analysis(
    mission_id: str,
    symbol: str | None = None,
    top_fraction: float = 0.20,
    n_bins: int = 5,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Retrospective pattern-spotting over an already-completed mission's
    stored trials — see backtest/meta_analysis.py's module docstring for
    the full caveat. Computed fresh on every call, never persisted."""
    _check_auth(x_api_key, iatis_session)
    from storage import research_missions

    mission = research_missions.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found.")
    if not (0.0 < top_fraction <= 1.0):
        raise HTTPException(status_code=400, detail="top_fraction must be in (0, 1].")
    if not (1 <= n_bins <= 20):
        raise HTTPException(status_code=400, detail="n_bins must be 1-20.")

    from backtest.meta_analysis import compute_meta_analysis
    from backtest.metrics import json_safe
    from backtest.optimizer import search_space_from_dict

    space = search_space_from_dict(json.loads(mission["search_space_json"]))
    trials = research_missions.leaderboard(mission_id, symbol=symbol, limit=2000)
    result = compute_meta_analysis(
        space, trials, sampler=mission["sampler"], mission_id=mission_id, symbol=symbol,
        top_fraction=top_fraction, n_bins=n_bins,
    )
    # Edge Discovery (2026-07-31): pooled_breakdown/opportunity_candidates
    # can carry a real, correct float('inf') profit_factor (zero losing
    # trades) — json.dumps emits the bare token `Infinity` for that by
    # default, which is not valid JSON (see json_safe()'s own docstring).
    return json_safe(result.to_dict())


@router.get("/research/missions/{mission_id}/feature-mining")
async def missions_feature_mining(
    mission_id: str,
    validation_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Aggregates the per-symbol feature_mining_json blobs already stored
    for one validation run (backtest/mission_validator.py) — computed at
    validation time, never re-run here, never a new backtest.
    `validation_id` is required (unlike meta-analysis's optional `symbol`)
    because feature mining is validation-scoped, not mission-trial-scoped:
    research_mission_trials_v2 stores only aggregated metrics, never
    trade-level detail, so there is nothing to mine at the mission level —
    see backtest/mission_validator.py's module docstring for the full
    validation architecture."""
    _check_auth(x_api_key, iatis_session)
    from storage import research_mission_validations

    validation = research_mission_validations.get_validation(validation_id)
    if validation is None or validation["mission_id"] != mission_id:
        raise HTTPException(status_code=404, detail="Validation not found.")

    results = research_mission_validations.validation_results(validation_id)
    blobs = [json.loads(r["feature_mining_json"]) for r in results if r.get("feature_mining_json")]

    from backtest.feature_mining import pool_feature_mining_results
    pooled = pool_feature_mining_results(blobs)
    return {"mission_id": mission_id, "validation_id": validation_id, **pooled}
