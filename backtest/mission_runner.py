"""
backtest/mission_runner.py
------------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — resumable CLI
orchestrator for a "mission": a search over backtest/optimizer.py's
joint MissionSearchSpace, one independent Optuna Study PER SYMBOL (never
a joint search dimension — see optimizer.py's module docstring), driving
backtesting/backtest_engine.py's real run_backtest() for every trial.

HARD SAFETY GUARANTEE, enforced structurally, not just documented: this
module, backtest/optimizer.py, and backtest/multiple_testing.py contain
NO code path that ever writes to research/results/registry.json,
config.yaml, or config/engines.yaml. A mission's output is a LEAD, never
itself evidence — see the EXPLORATORY banner every report carries
(backtest/multiple_testing.mission_significance_summary) and CLAUDE.md
rule 1. tests/test_mission_runner.py's
test_registry_json_byte_identical_before_and_after_mission_run and
test_never_touches_config_yaml_or_engines_yaml pin this with both a
source-code scan and a real live-run byte comparison.

Mirrors backtest/robustness.py's / backtest/walk_forward.py's own
main() CLI convention. A whole mission is intentionally ONE subprocess
(fits the existing execution/routes/experiments.py job-whitelist model
unchanged in Phase 2 — one job slot for the mission's entire duration,
not one slot per trial), looping over many trials IN-PROCESS (no
subprocess-per-trial overhead). Each trial's result is written to D1
(storage/research_missions.py) as it completes, so a killed-and-resumed
mission with the same --mission-id continues exactly where it left off
via Optuna's own add_trial() replay — verified live against the real
installed optuna that trial numbering stays contiguous across a
replay+continue cycle (no duplicate or skipped trial_number).

Each trial runs ONE full-period backtest, not a full walk-forward (would
multiply compute cost by n_windows, making thousands of trials
infeasible) — see optimizer.py's module docstring for the alternative
(--oos-holdout-fraction, a cheap per-trial overfit early-warning) and
CLAUDE.md's stance that only backtest/walk_forward.py's existing,
human-gated pipeline is ever allowed to produce real chronological-OOS
evidence.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import optuna

from backtest.metrics import json_safe
from backtest.multiple_testing import mission_significance_summary
from backtest.optimizer import (
    OPTIMIZABLE_METRICS,
    SAMPLER_KEYS,
    MissionSearchSpace,
    _CONTEXT_IDX_KEY,
    _ENGINE_VARIANTS_IDX_KEY,
    distributions_for,
    evaluate_point,
    make_sampler,
    resolve_point,
    suggest_point,
)
from backtest.runner import load_symbol_data
from storage import research_missions
from utils.logger import get_logger

logger = get_logger(__name__)

optuna.logging.set_verbosity(optuna.logging.WARNING)

# The exact benign RuntimeError optuna's GridSampler raises from
# study.tell() the instant its search space is fully exhausted (verified
# live against the installed optuna — Study.stop() is only supposed to
# be called from inside study.optimize()'s own machinery, which this
# module's manual ask()/tell() loop never uses). The trial itself is
# still fully recorded (state/value intact) before this fires — verified
# live — so catching and ignoring it here loses nothing.
_GRID_EXHAUSTED_MESSAGE = "Study.stop"


@dataclass(frozen=True)
class MissionConfig:
    mission_id: str
    name: str
    symbols: tuple[str, ...]
    data_dir: Path
    start: str | None
    end: str | None
    sampler: str
    n_trials_per_symbol: int
    objective_metric: str
    min_trades: int
    seed: int
    search_space: MissionSearchSpace
    oos_holdout_fraction: float | None
    max_wall_clock_seconds: float | None
    output_dir: Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _search_space_dict(space: MissionSearchSpace) -> dict:
    return {
        "timeframes_choices": [list(tf) for tf in space.timeframes_choices],
        "engine_set_choices": [list(e) for e in space.engine_set_choices],
        "indicator_set_choices": [list(i) for i in space.indicator_set_choices],
        "context_filter_set_choices": [list(c) for c in space.context_filter_set_choices],
        "engine_variant_choices": [dict(v) for v in space.engine_variant_choices],
        "hypothesis_bundle_choices": (
            [dict(b) for b in space.hypothesis_bundle_choices] if space.hypothesis_bundle_choices else None
        ),
        "risk_param_ranges": space.risk_param_ranges,
        "risk_param_grid": space.risk_param_grid,
    }


def _tell_safely(study: optuna.Study, trial: optuna.trial.Trial, **kwargs) -> None:
    """study.tell() wrapped for the one benign, verified-live exhaustion
    RuntimeError (see module header). Any OTHER RuntimeError is a real
    error and must propagate."""
    try:
        study.tell(trial, **kwargs)
    except RuntimeError as exc:
        if _GRID_EXHAUSTED_MESSAGE not in str(exc):
            raise
        logger.info("Grid sampler exhausted its search space — trial still recorded.")


def _is_cancelled(mission_id: str) -> bool:
    """Whether the operator marked this mission cancelled — fails OPEN
    (returns False) on any read error. A transient D1 hiccup checking
    "was I cancelled?" must never be treated as "yes", since that would
    silently abort (or mislabel "failed") an otherwise healthy mission
    over a monitoring blip rather than a real cancellation."""
    try:
        mission = research_missions.get_mission(mission_id)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning(f"Mission {mission_id}: cancellation check failed (non-fatal, assuming not cancelled): {exc}")
        return False
    return bool(mission and mission.get("status") == "cancelled")


def _split_train_holdout(df, holdout_fraction: float):
    split_idx = int(len(df) * (1 - holdout_fraction))
    split_idx = max(1, min(split_idx, len(df) - 1))
    return df.iloc[:split_idx], df.iloc[split_idx:]


def run_mission(mc: MissionConfig) -> None:
    research_missions.upsert_mission(
        mission_id=mc.mission_id, name=mc.name, sampler=mc.sampler,
        objective_metric=mc.objective_metric, symbols=list(mc.symbols),
        n_trials_per_symbol=mc.n_trials_per_symbol, min_trades=mc.min_trades,
        seed=mc.seed, search_space=_search_space_dict(mc.search_space),
        config={
            "data_dir": str(mc.data_dir), "start": mc.start, "end": mc.end,
            "oos_holdout_fraction": mc.oos_holdout_fraction,
            "max_wall_clock_seconds": mc.max_wall_clock_seconds,
        },
        status="running",
    )
    research_missions.set_mission_status(mc.mission_id, "running", started=True)

    grid_mode = mc.sampler == "grid"
    n_target = mc.n_trials_per_symbol
    if grid_mode:
        grid_size = mc.search_space.grid_size()
        if n_target > grid_size:
            logger.info(
                f"Mission {mc.mission_id}: n_trials_per_symbol ({n_target}) "
                f"exceeds the grid's Cartesian size ({grid_size}) — capped, "
                f"re-asking past exhaustion just cycles/duplicates points."
            )
            n_target = grid_size

    t0 = time.monotonic()
    mission_error: str | None = None

    try:
        for symbol in mc.symbols:
            _run_symbol(mc, symbol, n_target, grid_mode, t0)
            if mc.max_wall_clock_seconds is not None and (
                time.monotonic() - t0
            ) >= mc.max_wall_clock_seconds:
                logger.info(f"Mission {mc.mission_id}: wall-clock budget reached, stopping.")
                break
            if _is_cancelled(mc.mission_id):
                logger.info(f"Mission {mc.mission_id}: cancelled, stopping.")
                break
    except Exception as exc:  # noqa: BLE001 — a mission-level crash must still finalize status
        mission_error = str(exc)
        logger.error(f"Mission {mc.mission_id} failed: {exc}")

    # A transient D1 read hiccup here must never overwrite an otherwise-
    # fully-successful mission's status with "failed" (found live,
    # 2026-07-30: a mission with 24/24 trials COMPLETE, 0 pruned/failed,
    # still showed status="failed" — traced to this exact cancellation-
    # check call being unguarded). _is_cancelled() fails OPEN (treats a
    # read error as "not cancelled") — the same "monitoring must not kill
    # the run" contract execution/reconciliation.py and scheduler.py
    # already apply everywhere else in this codebase.
    final_status = "failed" if mission_error else "finished"
    if _is_cancelled(mc.mission_id):
        final_status = "cancelled"
    research_missions.set_mission_status(mc.mission_id, final_status, error=mission_error, finished=True)

    _write_report(mc)


def _run_symbol(mc: MissionConfig, symbol: str, n_target: int, grid_mode: bool, t0: float) -> None:
    try:
        df = load_symbol_data(symbol, mc.data_dir, mc.start, mc.end)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error(f"{symbol}: mission sweep failed to load data — {exc}")
        return

    if mc.oos_holdout_fraction:
        train_df, holdout_df = _split_train_holdout(df, mc.oos_holdout_fraction)
    else:
        train_df, holdout_df = df, None

    sampler = make_sampler(mc.sampler, mc.seed, mc.search_space, grid_mode)
    study = optuna.create_study(sampler=sampler, direction="maximize")

    dists = distributions_for(mc.search_space, grid_mode)
    existing = research_missions.existing_trials(mc.mission_id, symbol)
    for row in existing:
        row_params = json.loads(row["params_json"])
        # Context Filters (2026-07-30) — a trial recorded before this
        # dimension existed has no __context_idx key in its stored
        # params_json. optuna.trial.create_trial() requires params and
        # distributions to have identical key sets, so backfill it with
        # index 0 — always the empty-choice entry
        # MissionSearchSpace.context_filter_set_choices defaults to,
        # meaning "no context filter" for that replayed trial, matching
        # what it actually ran with.
        row_params.setdefault(_CONTEXT_IDX_KEY, 0)
        # Track C (Phase 4) — same backfill, same reasoning: a trial
        # recorded before engine variants existed has no
        # __engine_variants_idx key; index 0 is always the all-v1 entry
        # MissionSearchSpace.engine_variant_choices defaults to.
        row_params.setdefault(_ENGINE_VARIANTS_IDX_KEY, 0)
        study.add_trial(
            optuna.trial.create_trial(
                state=optuna.trial.TrialState[row["state"]],
                value=row["objective_value"],
                params=row_params,
                distributions=dists,
            )
        )

    remaining = n_target - len(existing)
    for _ in range(max(0, remaining)):
        if mc.max_wall_clock_seconds is not None and (
            time.monotonic() - t0
        ) >= mc.max_wall_clock_seconds:
            return
        if _is_cancelled(mc.mission_id):
            return

        trial = study.ask()
        raw_params = suggest_point(trial, mc.search_space, grid_mode)
        point = resolve_point(mc.search_space, raw_params)
        started_at = _now_iso()

        try:
            result = evaluate_point(symbol, train_df, point, mc.min_trades, mc.objective_metric)
        except Exception as exc:  # noqa: BLE001 — one trial's crash must never abort the mission
            _tell_safely(study, trial, state=optuna.trial.TrialState.FAIL)
            research_missions.record_trial(
                mission_id=mc.mission_id, trial_number=trial.number, symbol=symbol,
                state="FAIL", objective_value=None, params=raw_params, metrics=None,
                trades=0, error=str(exc), started_at=started_at, finished_at=_now_iso(),
            )
            logger.warning(f"{symbol} trial {trial.number}: evaluation failed — {exc}")
            continue

        metrics_payload = json_safe(result.metrics.to_dict())
        if holdout_df is not None and len(holdout_df) > 0 and not result.insufficient:
            try:
                holdout_result = evaluate_point(symbol, holdout_df, point, 1, mc.objective_metric)
                metrics_payload["holdout"] = json_safe(holdout_result.metrics.to_dict())
            except Exception as exc:  # noqa: BLE001 — holdout eval is reporting-only, never fatal
                logger.debug(f"{symbol} trial {trial.number}: holdout evaluation skipped — {exc}")

        if result.insufficient:
            _tell_safely(study, trial, state=optuna.trial.TrialState.PRUNED)
            research_missions.record_trial(
                mission_id=mc.mission_id, trial_number=trial.number, symbol=symbol,
                state="PRUNED", objective_value=None, params=raw_params,
                metrics=metrics_payload, trades=result.trades, error=None,
                started_at=started_at, finished_at=_now_iso(),
            )
        else:
            _tell_safely(study, trial, values=result.objective_value)
            research_missions.record_trial(
                mission_id=mc.mission_id, trial_number=trial.number, symbol=symbol,
                state="COMPLETE", objective_value=result.objective_value, params=raw_params,
                metrics=metrics_payload, trades=result.trades, error=None,
                started_at=started_at, finished_at=_now_iso(),
            )


def _write_report(mc: MissionConfig) -> None:
    mc.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = mc.output_dir / f"mission_{mc.mission_id}_{stamp}.json"

    per_symbol: dict = {}
    all_trials_for_significance: list[dict] = []
    for symbol in mc.symbols:
        trials = research_missions.leaderboard(mc.mission_id, symbol=symbol)
        significance_input = [
            {
                "mean_r": json.loads(t["metrics_json"]).get("avg_rr", 0.0) if t["metrics_json"] else 0.0,
                "std_r": json.loads(t["metrics_json"]).get("std_rr", 0.0) if t["metrics_json"] else 0.0,
                "n": t["trades"],
            }
            for t in trials if t["state"] == "COMPLETE"
        ]
        all_trials_for_significance.extend(significance_input)
        per_symbol[symbol] = {
            "trials": trials,
            "significance": mission_significance_summary(significance_input),
        }

    payload = {
        "mission_id": mc.mission_id, "name": mc.name, "generated_utc": stamp,
        "sampler": mc.sampler, "objective_metric": mc.objective_metric,
        "symbols": list(mc.symbols),
        "mission_wide_significance": mission_significance_summary(all_trials_for_significance),
        "per_symbol": per_symbol,
        "note": (
            "EXPLORATORY — NOT EVIDENCE. See mission_wide_significance/"
            "per_symbol.*.significance for what pure noise would produce at "
            "this trial count. No result here is registered or promoted "
            "automatically — a promising trial must be manually re-registered "
            "with its own falsification criteria and re-tested via the normal "
            "chronological-OOS pipeline before it can ever be called PASSED "
            "(CLAUDE.md rule 1)."
        ),
    }
    path.write_text(json.dumps(json_safe(payload), indent=2))
    logger.info(f"Mission report: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="IATIS AI Research Lab — mission runner")
    parser.add_argument("--mission-id", default=None, help="auto-generated if omitted")
    parser.add_argument("--name", default=None)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--start", default=None, help="ISO date, inclusive")
    parser.add_argument("--end", default=None, help="ISO date, inclusive")
    parser.add_argument("--sampler", choices=SAMPLER_KEYS, default="tpe")
    parser.add_argument("--n-trials-per-symbol", type=int, required=True)
    parser.add_argument("--objective-metric", choices=OPTIMIZABLE_METRICS, default="profit_factor")
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeframes-choices", type=str, required=True,
                        help='JSON, e.g. \'[["H1"],["H4","D1","H1"]]\'')
    parser.add_argument("--engine-set-choices", type=str, required=True,
                        help='JSON, e.g. \'[["nnfx","price_action"],["smc","wyckoff"]]\'')
    parser.add_argument("--indicator-set-choices", type=str, default="[[]]",
                        help='JSON, e.g. \'[[],[{"name":"rsi","mode":"entry_filter","params":{},"weight":0}]]\'')
    parser.add_argument("--context-filter-set-choices", type=str, default="[[]]",
                        help='JSON, e.g. \'[[],[{"name":"session","mode":"entry_filter","params":{},"weight":0}]]\'')
    parser.add_argument("--engine-variant-choices", type=str, default="[{}]",
                        help='JSON list of {engine_key: variant} maps (Track C, Phase 4), e.g. '
                             '\'[{},{"price_action":"v2"},{"wyckoff":"v2"}]\' — every engine not listed in a '
                             'given map stays v1. May also be carried per-bundle via --hypothesis-bundle-choices\' '
                             '"engine_variants" key.')
    parser.add_argument("--hypothesis-bundle-choices", type=str, default=None,
                        help='JSON list of named bundles, e.g. \'[{"name":"SMC only","timeframes":["H1"],'
                             '"engines":["smc"],"indicators":[],"context_filters":[]}]\' — when set, REPLACES '
                             '--timeframes-choices/--engine-set-choices/--indicator-set-choices/'
                             '--context-filter-set-choices/--engine-variant-choices with one shared index over '
                             'complete bundles.')
    parser.add_argument("--risk-param-ranges", type=str, default="{}",
                        help='JSON, e.g. \'{"sl_atr_multiplier":[1.0,4.0]}\' (random/tpe/nsga2)')
    parser.add_argument("--risk-param-grid", type=str, default="{}",
                        help='JSON, e.g. \'{"sl_atr_multiplier":[1.5,2.0,2.5]}\' (grid only)')
    parser.add_argument("--oos-holdout-fraction", type=float, default=None)
    parser.add_argument("--max-wall-clock-seconds", type=float, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    mission_id = args.mission_id or uuid.uuid4().hex[:12]
    name = args.name or mission_id

    def _tuplify_choices(raw: list) -> tuple:
        return tuple(tuple(choice) for choice in raw)

    try:
        space = MissionSearchSpace(
            timeframes_choices=_tuplify_choices(json.loads(args.timeframes_choices)),
            engine_set_choices=_tuplify_choices(json.loads(args.engine_set_choices)),
            indicator_set_choices=_tuplify_choices(json.loads(args.indicator_set_choices)),
            context_filter_set_choices=_tuplify_choices(json.loads(args.context_filter_set_choices)),
            engine_variant_choices=tuple(dict(v) for v in json.loads(args.engine_variant_choices)),
            hypothesis_bundle_choices=(
                tuple(dict(b) for b in json.loads(args.hypothesis_bundle_choices))
                if args.hypothesis_bundle_choices else None
            ),
            risk_param_ranges={k: tuple(v) for k, v in json.loads(args.risk_param_ranges).items()},
            risk_param_grid={k: tuple(v) for k, v in json.loads(args.risk_param_grid).items()},
        )
    except (ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
        return

    mc = MissionConfig(
        mission_id=mission_id, name=name, symbols=tuple(s.upper() for s in args.symbols),
        data_dir=args.data_dir, start=args.start, end=args.end, sampler=args.sampler,
        n_trials_per_symbol=args.n_trials_per_symbol, objective_metric=args.objective_metric,
        min_trades=args.min_trades, seed=args.seed, search_space=space,
        oos_holdout_fraction=args.oos_holdout_fraction,
        max_wall_clock_seconds=args.max_wall_clock_seconds, output_dir=args.output_dir,
    )
    run_mission(mc)


if __name__ == "__main__":
    main()
