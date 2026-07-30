"""
backtest/mission_validator.py
--------------------------------
AI Research Lab / Mission Center Phase 3 (2026-07-30) — multi-stage
VALIDATION of one operator-chosen mission trial, across operator-chosen
validation symbols. Never auto-picks a candidate (POST /research/
missions/{id}/validate always names an exact trial_number+trial_symbol)
and never auto-declares a real "Edge" — see the vocabulary note below.

HARD SAFETY GUARANTEE, enforced structurally like backtest/
mission_runner.py's own: this module, and everything it calls
(backtest/optimizer.py, backtest/walk_forward.py, backtest/robustness.py,
backtest/monte_carlo.py), contains NO code path that ever writes to
research/results/registry.json, config.yaml, or config/engines.yaml.
overall_verdict values (NO_EDGE/WEAK_LEAD/STRONG_LEAD) are stored ONLY in
storage.research_mission_validations — deliberately distinct vocabulary
from every hypothesis status this repo uses (PASSED/RESEARCH/FAILED/
PLANNED/RESOLVED/ABANDONED) and from mission trial states (COMPLETE/
PRUNED/FAIL), so a validation verdict can never be mistaken for a
registry.json promotion. tests/test_mission_validator.py's
test_registry_json_byte_identical_before_and_after_validation_run and
test_never_touches_config_yaml_or_engines_yaml pin this with both a
source-code scan and a real live-run byte comparison, mirroring
mission_runner.py's own two tests exactly.

A candidate is re-resolved from storage, never re-derived a second way:
get_mission()'s search_space_json rebuilds the MissionSearchSpace,
get_trial()'s params_json + resolve_point() gives back the EXACT point
(timeframes/engines/indicators/risk_overrides) that trial was run with.

For each validation symbol, four independent checks run against that
SAME point (never re-sampled, never re-optimized):
  1. Direct re-evaluation (backtest.optimizer.evaluate_point, with
     return_trades=True) — real metrics (PF, trades, max DD, expectancy,
     Sharpe) on that symbol's own full history.
  2. Monte Carlo (backtest.monte_carlo.run_monte_carlo) on the trades
     from (1) — is the result robust to trade-order luck?
  3. Walk-forward (backtest.walk_forward.run_walk_forward) using the
     candidate's own risk_overrides/timeframes/engines/indicators as the
     fixed configuration for every window — does it hold up out-of-
     sample, chronologically?
  4. Robustness (backtest.robustness.run_robustness) sweeping the
     candidate's own risk params AROUND the candidate's chosen values
     (not production defaults — engine_overrides carries the candidate's
     values through) — is this exact choice on a stable plateau or an
     isolated spike?

VALIDATION_CRITERIA below are code, not opinion, matching this repo's
established "the promotion bar is code" discipline (research/edge_gate.py
PROMOTION_CRITERIA) — but these are a DIFFERENT, stricter-scoped bar, not
that one. Passing every criterion here produces STRONG_LEAD, never
PASSED; a human must still manually pre-register a real hypothesis (with
its own ID and falsification criteria) and re-test it through
backtest/walk_forward.py's existing, human-gated chronological-OOS
pipeline before CLAUDE.md rule 1 considers anything here "evidence".
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest.feature_mining import compute_feature_mining
from backtest.metrics import json_safe
from backtest.monte_carlo import run_monte_carlo
from backtest.optimizer import evaluate_point, resolve_point, search_space_from_dict
from backtest.robustness import DEFAULT_MULTIPLIERS, SWEEP_PARAMS, RobustnessConfig, run_robustness
from backtest.runner import load_symbol_data
from backtest.walk_forward import SymbolVerdict, WalkForwardConfig, run_walk_forward
from storage import research_mission_validations, research_missions
from utils.logger import get_logger

logger = get_logger(__name__)

# Verdict vocabulary — sanity-checked against every status token in this
# repo (hypothesis statuses PASSED/RESEARCH/FAILED/PLANNED/RESOLVED/
# ABANDONED; mission trial states COMPLETE/PRUNED/FAIL; job statuses
# queued/running/finished/failed/cancelled/timeout) — zero collisions.
NO_EDGE = "NO_EDGE"
WEAK_LEAD = "WEAK_LEAD"
STRONG_LEAD = "STRONG_LEAD"

# The operator's own explicit numbers (Profit Factor >= 1.25, Trades >=
# 300, Max Drawdown <= 10%, Expectancy > 0, Sharpe > 0.5), plus three
# open-question defaults NOT independently specified — max_risk_of_ruin_pct,
# min_probability_profit_pct, and requiring ALL swept params STABLE (not
# a majority). Flagged for the operator to revisit; every value here is
# a plain module constant, easy to find and change.
VALIDATION_CRITERIA: dict[str, float | bool] = {
    "min_profit_factor": 1.25,
    "min_trades": 300,
    "max_drawdown_pct": 10.0,
    "min_expectancy": 0.0,
    "min_sharpe": 0.5,
    "max_risk_of_ruin_pct": 5.0,
    "min_probability_profit_pct": 60.0,
    "require_walk_forward_consistent": True,
    "require_all_swept_params_stable": True,
}

# A STRONG_LEAD requires passing on at least this many validation
# symbols, REGARDLESS of how many were validated — a 1- or 2-symbol
# validation run can therefore never reach STRONG_LEAD even if every
# symbol it touched passed, directly enforcing "if it only works on one
# symbol it's probably curve-fitting" at the boundary, not just the
# interior.
MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD = 3


@dataclass(frozen=True)
class ValidationConfig:
    validation_id: str
    mission_id: str
    trial_number: int
    trial_symbol: str
    validation_symbols: tuple[str, ...]
    data_dir: Path
    start: str | None
    end: str | None
    wf_windows: int = 3
    wf_min_trades_per_window: int = 10
    wf_warmup_bars: int = 210
    rb_multipliers: tuple[float, ...] = DEFAULT_MULTIPLIERS
    rb_params: tuple[str, ...] = SWEEP_PARAMS
    rb_min_trades: int = 10
    mc_n_simulations: int = 1000
    mc_seed: int = 42
    output_dir: Path = Path("reports")


def _criterion(actual: Any, threshold: Any, passed: bool) -> dict:
    return {"actual": actual, "threshold": threshold, "passed": bool(passed)}


def _evaluate_symbol(symbol: str, point: dict, vc: ValidationConfig) -> dict:
    """Returns the full per-symbol result dict recorded into
    research_mission_validation_results — always populated, pass or
    fail, nothing suppressed."""
    started_at = datetime.now(timezone.utc).isoformat()
    df = load_symbol_data(symbol, vc.data_dir, vc.start, vc.end)

    eval_result = evaluate_point(
        symbol, df, point, min_trades=1, objective_metric="profit_factor", return_trades=True,
    )
    metrics = eval_result.metrics

    mc_result = run_monte_carlo(
        eval_result.trade_records or [], n_simulations=vc.mc_n_simulations, seed=vc.mc_seed,
    )

    # Feature Mining / Hypothesis Discovery Phase 1 (2026-07-30) — diagnostic
    # only, never participates in criteria_breakdown/passed/overall_verdict
    # below. See backtest/feature_mining.py's module docstring for why this
    # is deliberately non-ML (not the same technique family as H033).
    feature_mining_result = compute_feature_mining(eval_result.trade_records or [])

    wf_result = run_walk_forward(symbol, df, WalkForwardConfig(
        n_windows=vc.wf_windows,
        min_pf=VALIDATION_CRITERIA["min_profit_factor"],
        min_trades_per_window=vc.wf_min_trades_per_window,
        warmup_bars=vc.wf_warmup_bars,
        engine_overrides=point["risk_overrides"],
        timeframes=tuple(point["timeframes"]) if point["timeframes"] else None,
        engines=tuple(point["engines"]) if point["engines"] else None,
        indicators=tuple(point["indicators"]) if point["indicators"] else None,
        context_filters=tuple(point["context_filters"]) if point["context_filters"] else None,
    ))

    rb_result = run_robustness(symbol, df, RobustnessConfig(
        multipliers=vc.rb_multipliers, params=vc.rb_params, min_trades=vc.rb_min_trades,
        engine_overrides=point["risk_overrides"],
        timeframes=tuple(point["timeframes"]) if point["timeframes"] else None,
        engines=tuple(point["engines"]) if point["engines"] else None,
        indicators=tuple(point["indicators"]) if point["indicators"] else None,
        context_filters=tuple(point["context_filters"]) if point["context_filters"] else None,
    ))

    breakdown = {
        "profit_factor": _criterion(metrics.profit_factor, VALIDATION_CRITERIA["min_profit_factor"],
                                     metrics.profit_factor >= VALIDATION_CRITERIA["min_profit_factor"]),
        "trades": _criterion(metrics.total_trades, VALIDATION_CRITERIA["min_trades"],
                              metrics.total_trades >= VALIDATION_CRITERIA["min_trades"]),
        "max_drawdown_pct": _criterion(metrics.max_drawdown, VALIDATION_CRITERIA["max_drawdown_pct"],
                                        metrics.max_drawdown <= VALIDATION_CRITERIA["max_drawdown_pct"]),
        "expectancy": _criterion(metrics.expectancy, VALIDATION_CRITERIA["min_expectancy"],
                                  metrics.expectancy > VALIDATION_CRITERIA["min_expectancy"]),
        "sharpe_ratio": _criterion(metrics.sharpe_ratio, VALIDATION_CRITERIA["min_sharpe"],
                                    metrics.sharpe_ratio >= VALIDATION_CRITERIA["min_sharpe"]),
        "walk_forward": _criterion(wf_result.verdict.value, SymbolVerdict.CONSISTENT.value,
                                    wf_result.verdict == SymbolVerdict.CONSISTENT),
        "monte_carlo_risk_of_ruin": _criterion(
            mc_result.risk_of_ruin, VALIDATION_CRITERIA["max_risk_of_ruin_pct"],
            mc_result.risk_of_ruin <= VALIDATION_CRITERIA["max_risk_of_ruin_pct"]),
        "monte_carlo_probability_profit": _criterion(
            mc_result.probability_profit, VALIDATION_CRITERIA["min_probability_profit_pct"],
            mc_result.probability_profit >= VALIDATION_CRITERIA["min_probability_profit_pct"]),
        "robustness_all_stable": _criterion(
            [s.verdict for s in rb_result.sweeps], "STABLE",
            all(s.verdict == "STABLE" for s in rb_result.sweeps)),
    }
    passed = all(c["passed"] for c in breakdown.values())

    return {
        "symbol": symbol, "passed": passed,
        "metrics": json_safe(metrics.to_dict()),
        "monte_carlo": json_safe(vars(mc_result)),
        "walk_forward": json_safe(wf_result.to_dict()),
        "robustness": json_safe(rb_result.to_dict()),
        "criteria_breakdown": json_safe(breakdown),
        "feature_mining": json_safe(feature_mining_result.to_dict()),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def run_validation(vc: ValidationConfig) -> None:
    mission = research_missions.get_mission(vc.mission_id)
    research_mission_validations.upsert_validation(
        validation_id=vc.validation_id, mission_id=vc.mission_id, trial_number=vc.trial_number,
        trial_symbol=vc.trial_symbol, validation_symbols=list(vc.validation_symbols),
        objective_metric=mission["objective_metric"] if mission else "unknown",
        criteria=VALIDATION_CRITERIA, status="running",
    )
    research_mission_validations.set_validation_status(vc.validation_id, "running", started=True)

    if mission is None:
        research_mission_validations.set_validation_status(
            vc.validation_id, "failed", error=f"Mission {vc.mission_id} not found.", finished=True)
        return
    trial = research_missions.get_trial(vc.mission_id, vc.trial_number, vc.trial_symbol)
    if trial is None:
        research_mission_validations.set_validation_status(
            vc.validation_id, "failed",
            error=f"Trial {vc.trial_number} ({vc.trial_symbol}) not found.", finished=True)
        return
    if trial["state"] != "COMPLETE":
        research_mission_validations.set_validation_status(
            vc.validation_id, "failed",
            error=f"Trial state is {trial['state']!r} — only COMPLETE trials can be validated.",
            finished=True)
        return

    space = search_space_from_dict(json.loads(mission["search_space_json"]))
    point = resolve_point(space, json.loads(trial["params_json"]))

    passing = 0
    for symbol in vc.validation_symbols:
        current = research_mission_validations.get_validation(vc.validation_id)
        if current and current.get("status") == "cancelled":
            logger.info(f"Validation {vc.validation_id}: cancelled, stopping.")
            break
        try:
            result = _evaluate_symbol(symbol, point, vc)
            if result["passed"]:
                passing += 1
            research_mission_validations.record_validation_result(
                validation_id=vc.validation_id, symbol=symbol, passed=result["passed"],
                metrics=result["metrics"], monte_carlo=result["monte_carlo"],
                walk_forward=result["walk_forward"], robustness=result["robustness"],
                criteria_breakdown=result["criteria_breakdown"],
                feature_mining=result["feature_mining"], error=None,
                started_at=result["started_at"], finished_at=result["finished_at"],
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            logger.error(f"Validation {vc.validation_id}: {symbol} failed — {exc}")
            now = datetime.now(timezone.utc).isoformat()
            research_mission_validations.record_validation_result(
                validation_id=vc.validation_id, symbol=symbol, passed=False,
                metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
                criteria_breakdown={}, feature_mining=None, error=str(exc),
                started_at=now, finished_at=now,
            )

    total = len(vc.validation_symbols)
    if passing <= 1:
        overall = NO_EDGE
    elif passing == total and total >= MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD:
        overall = STRONG_LEAD
    else:
        overall = WEAK_LEAD

    research_mission_validations.set_validation_status(
        vc.validation_id, "finished", finished=True,
        overall_verdict=overall, passing_symbols=passing, total_symbols=total,
    )
    _write_report(vc, overall, passing, total)


def _write_report(vc: ValidationConfig, overall: str, passing: int, total: int) -> None:
    vc.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = vc.output_dir / f"mission_validation_{vc.validation_id}_{stamp}.json"

    raw_results = research_mission_validations.validation_results(vc.validation_id)
    results = []
    for r in raw_results:
        parsed = dict(r)
        for key in ("metrics_json", "monte_carlo_json", "walk_forward_json",
                    "robustness_json", "criteria_breakdown_json", "feature_mining_json"):
            value = parsed.pop(key, None)
            parsed[key.removesuffix("_json")] = json.loads(value) if value else None
        results.append(parsed)

    payload = {
        "validation_id": vc.validation_id, "mission_id": vc.mission_id,
        "trial_number": vc.trial_number, "trial_symbol": vc.trial_symbol,
        "validation_symbols": list(vc.validation_symbols),
        "overall_verdict": overall, "passing_symbols": passing, "total_symbols": total,
        "criteria": VALIDATION_CRITERIA,
        "results": results,
        "note": (
            f"{overall} — a LEAD, NOT evidence. Passing every criterion here "
            "does not register or promote anything: a human must manually "
            "pre-register a real hypothesis (its own ID, falsification "
            "criteria written before re-testing) and re-run it through "
            "backtest/walk_forward.py's existing chronological-OOS pipeline "
            "before CLAUDE.md rule 1 considers this evidence."
        ),
    }
    path.write_text(json.dumps(json_safe(payload), indent=2))
    logger.info(f"Validation report: {path} — {overall} ({passing}/{total} symbols passing)")


def main() -> None:
    parser = argparse.ArgumentParser(description="IATIS AI Research Lab — mission trial validator")
    parser.add_argument("--validation-id", required=True)
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--trial-number", type=int, required=True)
    parser.add_argument("--trial-symbol", required=True)
    parser.add_argument("--validation-symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--start", default=None, help="ISO date, inclusive")
    parser.add_argument("--end", default=None, help="ISO date, inclusive")
    parser.add_argument("--wf-windows", type=int, default=3)
    parser.add_argument("--wf-min-trades-per-window", type=int, default=10)
    parser.add_argument("--wf-warmup-bars", type=int, default=210)
    parser.add_argument("--rb-multipliers", nargs="+", type=float, default=list(DEFAULT_MULTIPLIERS))
    parser.add_argument("--rb-params", nargs="+", default=list(SWEEP_PARAMS), choices=SWEEP_PARAMS)
    parser.add_argument("--rb-min-trades", type=int, default=10)
    parser.add_argument("--mc-simulations", type=int, default=1000)
    parser.add_argument("--mc-seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    vc = ValidationConfig(
        validation_id=args.validation_id, mission_id=args.mission_id,
        trial_number=args.trial_number, trial_symbol=args.trial_symbol.upper(),
        validation_symbols=tuple(s.upper() for s in args.validation_symbols),
        data_dir=args.data_dir, start=args.start, end=args.end,
        wf_windows=args.wf_windows, wf_min_trades_per_window=args.wf_min_trades_per_window,
        wf_warmup_bars=args.wf_warmup_bars,
        rb_multipliers=tuple(args.rb_multipliers), rb_params=tuple(args.rb_params),
        rb_min_trades=args.rb_min_trades,
        mc_n_simulations=args.mc_simulations, mc_seed=args.mc_seed,
        output_dir=args.output_dir,
    )
    run_validation(vc)


if __name__ == "__main__":
    main()
