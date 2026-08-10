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
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest.feature_mining import compute_feature_mining
from backtest.metrics import TradeRecord, json_safe
from backtest.monte_carlo import run_monte_carlo
from backtest.multiple_testing import effective_sample_size, trial_p_value
from backtest.optimizer import evaluate_point, resolve_point, search_space_from_dict
from backtest.robustness import DEFAULT_MULTIPLIERS, SWEEP_PARAMS, RobustnessConfig, run_robustness
from backtest.runner import find_symbol_csv, load_symbol_data, physical_load_timeframe
from backtest.walk_forward import SymbolVerdict, WalkForwardConfig, run_walk_forward
from research.manifest import dataset_fingerprint, git_state
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

# Forensic Audit Phase 1, item D (2026-08-02) — Validation Mode
# Explicitness. Confirmed real gap: nothing anywhere tied trial_symbol to
# validation_symbols; the only enforced rule was a symbol COUNT (>=2),
# never identity, so a trial could be "validated" against symbols that
# structurally excluded its own. SAME_SYMBOL is the new default (confirms
# ONLY the trial's own symbol, using its own COMPLETE trial data on the
# SAME instrument — not cross-symbol generalization evidence).
# CROSS_SYMBOL is today's exact, unchanged behavior (validation_symbols
# is an independent operator-chosen list, no membership requirement
# against trial_symbol — deliberately, since testing generalization to
# OTHER instruments is the whole point of that mode).
SAME_SYMBOL = "SAME_SYMBOL"
CROSS_SYMBOL = "CROSS_SYMBOL"
VALIDATION_MODES: tuple[str, ...] = (SAME_SYMBOL, CROSS_SYMBOL)

# Deliberately distinct vocabulary from NO_EDGE/WEAK_LEAD/STRONG_LEAD,
# which are inherently about CROSS-SYMBOL generalization
# (MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD structurally requires >=3 OTHER
# symbols). Conflating "confirmed on its own training data" with a
# cross-symbol LEAD would be exactly the false-confidence failure mode
# this whole audit phase exists to catch. Checked against every existing
# status token in this repo for zero collision risk.
SAME_SYMBOL_CONFIRMED = "SAME_SYMBOL_CONFIRMED"
SAME_SYMBOL_NOT_CONFIRMED = "SAME_SYMBOL_NOT_CONFIRMED"

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
    validation_mode: str = SAME_SYMBOL
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


def _compute_candidate_lock(trial: dict, symbol: str, data_dir: Path) -> dict:
    """Diagnostic Infrastructure Phase 1 (2026-08-02) — compares the
    trial's stored fingerprint (recorded once at trial time by
    mission_runner.py) against a freshly computed one for the SAME
    symbol, right now. Informational only — never blocks a validation
    run; a legitimately growing dataset (new bars appended since the
    trial ran) shows as dataset drift without invalidating anything."""
    original_raw = trial.get("fingerprint_json")
    if not original_raw:
        return {"available": False, "note": "Trial predates fingerprint tracking."}
    try:
        original = json.loads(original_raw)
    except (TypeError, ValueError):
        return {"available": False, "note": "Trial fingerprint could not be parsed."}

    try:
        csv_path = find_symbol_csv(symbol, data_dir)
        current_dataset = dataset_fingerprint(csv_path)
    except (FileNotFoundError, OSError):
        current_dataset = None
    current = {"git": git_state(), "dataset": current_dataset}

    diffs: list[str] = []
    if (original.get("git") or {}).get("commit") != current["git"]["commit"]:
        diffs.append("code changed (different git commit)")
    if current["git"]["dirty"]:
        diffs.append("current working tree is dirty (uncommitted changes)")
    orig_ds = original.get("dataset") or {}
    cur_ds = current["dataset"] or {}
    if orig_ds.get("sha256") != cur_ds.get("sha256"):
        diffs.append("dataset file content changed (different SHA256)")

    return {
        "available": True, "original": original, "current": current,
        "matches": len(diffs) == 0, "diffs": diffs,
    }


def _compute_date_overlap(mission: dict, vc: "ValidationConfig") -> dict:
    """Diagnostic Infrastructure Phase 1 (2026-08-02) — does the
    validation's requested date range overlap the ORIGINAL mission's
    training window (mission.config_json)? Informational only: an
    overlap means this run is NOT genuinely out-of-sample evidence, but
    an operator may deliberately want an in-sample sanity check."""
    try:
        mission_config = json.loads(mission.get("config_json") or "{}")
    except (TypeError, ValueError):
        mission_config = {}
    original_start = mission_config.get("start")
    original_end = mission_config.get("end")
    lo = max(original_start or "0000-01-01", vc.start or "0000-01-01")
    hi = min(original_end or "9999-12-31", vc.end or "9999-12-31")
    overlaps = lo <= hi
    return {
        "original_start": original_start, "original_end": original_end,
        "validation_start": vc.start, "validation_end": vc.end, "overlaps": overlaps,
        "note": (
            "Validation date range overlaps the original trial's training window — "
            "NOT genuinely out-of-sample evidence." if overlaps else
            "Validation date range does not overlap the trial's training window — "
            "genuinely out-of-sample."
        ),
    }


def _compute_effective_sample_size_diagnostic(trades: list[TradeRecord]) -> dict:
    """Mission Center Research Rigor Phase 2 (2026-08-XX) — an
    autocorrelation-adjusted significance check on this symbol's own
    closed-trade R-multiple sequence, computed alongside feature_mining
    above (same insertion point: raw eval_result.trade_records are only
    ever available here, at validation time, never persisted at
    mission-trial scale — see backtest/feature_mining.py's own docstring
    for why retrofitting mission_runner.py to keep trade-level detail for
    every trial would be an unbounded new storage cost).

    Diagnostic only, never a VALIDATION_CRITERIA entry: trial_p_value()'s
    nominal p-value (used nowhere in this codebase's gating logic either)
    assumes i.i.d. trades, which consecutive backtest trades on one
    symbol structurally are not (shared regime/volatility conditions).
    effective_sample_size() is always <= n_trades, so ess_adjusted_p_value
    is always >= nominal_p_value — this can only ever make a result look
    LESS significant than the naive trade count, never more. A second,
    honest check against exactly the false-confidence failure mode this
    whole research-rigor arc exists to catch.
    """
    r_multiples = [t.rr_actual for t in trades if t.rr_actual is not None]
    n = len(r_multiples)
    if n < 5:
        return {
            "n_trades": n, "effective_sample_size": None, "autocorrelation_ratio": None,
            "nominal_p_value": None, "ess_adjusted_p_value": None,
            "note": "Too few closed trades (<5) to estimate serial correlation.",
        }
    mean_r = statistics.fmean(r_multiples)
    std_r = statistics.stdev(r_multiples) if n >= 2 else 0.0
    ess = effective_sample_size(r_multiples)
    nominal_p = trial_p_value(mean_r, std_r, n)
    ess_p = trial_p_value(mean_r, std_r, int(ess)) if ess is not None else None
    return {
        "n_trades": n,
        "effective_sample_size": round(ess, 1) if ess is not None else None,
        "autocorrelation_ratio": round(ess / n, 3) if ess is not None else None,
        "nominal_p_value": nominal_p,
        "ess_adjusted_p_value": ess_p,
        "note": (
            "Autocorrelation-adjusted significance of this symbol's mean "
            "R-multiple vs. 0 — diagnostic only, never a VALIDATION_CRITERIA "
            "entry. ess_adjusted_p_value is always >= nominal_p_value "
            "(more conservative), reflecting that consecutive backtest "
            "trades are not independent samples."
        ),
    }


MIN_TRADES_PER_REGIME_FOR_ROBUSTNESS = 10


def _compute_regime_robustness_diagnostic(by_regime: dict, min_trades_per_regime: int = MIN_TRADES_PER_REGIME_FOR_ROBUSTNESS) -> dict:
    """Mission Center Research Rigor Phase 3 (2026-08-XX) — does this
    symbol's edge hold across the market regimes it actually traded in,
    or is it concentrated entirely in one regime? The same "if it only
    works on one X it's probably curve-fitting" principle
    MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD already enforces for symbols,
    applied here to regimes. Reuses BacktestMetrics.by_regime (already
    computed by calculate_metrics() from each trade's TradeRecord.regime)
    — no new trade-level access needed, unlike the ESS diagnostic above.

    "Unknown" (the fallback label calculate_metrics() assigns when a
    trade's regime gate was off — backtest/metrics.py:432) is excluded
    from the material/profitable counts, matching Edge Discovery's own
    established precedent (_rank_opportunities() excludes "Unknown"
    dimension values) — it isn't a real regime, just an absence of data.

    Diagnostic only, never a VALIDATION_CRITERIA entry.
    """
    real_regimes = {r: b for r, b in by_regime.items() if r != "Unknown"}
    total_trades = sum(b.get("trades", 0) for b in real_regimes.values())
    if total_trades == 0:
        return {
            "regimes_traded": 0, "regimes_material": 0, "regimes_profitable": 0,
            "regime_robustness_score": None, "dominant_regime": None,
            "dominant_regime_share": None,
            "note": "No closed trades with a known regime to assess regime robustness.",
        }
    dominant_regime, dominant_bucket = max(real_regimes.items(), key=lambda kv: kv[1].get("trades", 0))
    dominant_share = dominant_bucket.get("trades", 0) / total_trades
    material = {r: b for r, b in real_regimes.items() if b.get("trades", 0) >= min_trades_per_regime}
    if not material:
        return {
            "regimes_traded": len(real_regimes), "regimes_material": 0, "regimes_profitable": 0,
            "regime_robustness_score": None, "dominant_regime": dominant_regime,
            "dominant_regime_share": round(dominant_share, 3),
            "note": f"No regime reached the {min_trades_per_regime}-trade floor — too few trades per regime to assess.",
        }
    profitable = {r: b for r, b in material.items() if b.get("profit_factor", 0.0) >= 1.0}
    score = len(profitable) / len(material)
    return {
        "regimes_traded": len(real_regimes), "regimes_material": len(material),
        "regimes_profitable": len(profitable),
        "regime_robustness_score": round(score, 3),
        "dominant_regime": dominant_regime, "dominant_regime_share": round(dominant_share, 3),
        "note": (
            f"{len(profitable)} of {len(material)} regime(s) with >= {min_trades_per_regime} "
            f"trades were profitable (PF >= 1.0). Diagnostic only — never a VALIDATION_CRITERIA "
            f"entry. An edge concentrated in a single regime "
            f"(dominant share {dominant_share:.0%}) carries the same curve-fitting risk a "
            f"single-symbol result does."
        ),
    }


def _compute_stability_score_diagnostic(rb_sweeps: list) -> dict:
    """Mission Center Research Rigor Phase 4 (2026-08-XX) — a fractional
    companion to VALIDATION_CRITERIA's all-or-nothing "robustness_all_
    stable" boolean: what fraction of the candidate's own swept risk
    parameters are STABLE, not just whether ALL of them are. Reuses
    backtest.robustness.run_robustness()'s already-computed per-parameter
    verdicts (STABLE/SENSITIVE/INSUFFICIENT, backtest/robustness.py's own
    ParamSweepResult) — no new sweep, no new computation.

    INSUFFICIENT-verdict parameters (too few trades at some swept point to
    judge) are excluded from both numerator and denominator — an
    unmeasurable parameter should not silently count as either stable or
    unstable, the same "don't fabricate a number for missing data"
    principle every diagnostic in this file already follows.

    Diagnostic only, never itself a VALIDATION_CRITERIA entry — the
    existing binary "robustness_all_stable" criterion is unchanged.
    """
    measurable = [s for s in rb_sweeps if s.verdict != "INSUFFICIENT"]
    stable = [s for s in measurable if s.verdict == "STABLE"]
    sensitive = [s for s in measurable if s.verdict == "SENSITIVE"]
    insufficient = [s for s in rb_sweeps if s.verdict == "INSUFFICIENT"]
    if not measurable:
        return {
            "params_swept": len(rb_sweeps), "params_measurable": 0, "params_stable": 0,
            "params_sensitive": 0, "params_insufficient": len(insufficient),
            "stability_score": None,
            "note": "No swept parameter had enough trades to judge stability.",
        }
    score = len(stable) / len(measurable)
    return {
        "params_swept": len(rb_sweeps), "params_measurable": len(measurable),
        "params_stable": len(stable), "params_sensitive": len(sensitive),
        "params_insufficient": len(insufficient),
        "stability_score": round(score, 3),
        "note": (
            f"{len(stable)} of {len(measurable)} measurable swept parameter(s) are STABLE "
            f"(profit factor stays within +/-30% of baseline across the sweep). Diagnostic "
            f"only — never a VALIDATION_CRITERIA entry; the existing 'robustness_all_stable' "
            f"criterion still requires ALL measurable parameters to be STABLE to pass."
        ),
    }


# Mission Center Research Rigor Phase 5 (2026-08-XX) — "spread widens 50%/
# doubles/triples", the same order-of-magnitude stress framing this
# codebase already uses for risk assessment (backtest.monte_carlo's fixed
# risk-of-ruin threshold). Not an opinion-based composite — three
# deterministic multipliers applied to the candidate's own REAL, measured
# baseline cost values (BacktestConfig.from_profile's REAL_SPREAD_PIPS-
# derived defaults, not a guess).
COST_STRESS_MULTIPLIERS: tuple[float, ...] = (1.5, 2.0, 3.0)


def _compute_cost_stress_diagnostic(symbol: str, df, point: dict) -> dict:
    """Mission Center Research Rigor Phase 5 (2026-08-XX) — does the
    candidate's edge survive a broker's real-world execution costs
    (spread/commission, slippage, overnight swap) getting materially
    worse than what was measured, rather than only being validated at
    the exact cost level it happened to backtest well on? Re-runs
    backtest.optimizer.evaluate_point() — the exact same primitive every
    other evaluation in this pipeline uses, never a separate, parallel
    backtest implementation — with the candidate's own resolved baseline
    commission_pips/slippage_pips/swap_pips_per_night (via
    BacktestConfig.from_profile(), the same real per-symbol defaults the
    baseline evaluation itself used) scaled by COST_STRESS_MULTIPLIERS.

    Diagnostic only, never a VALIDATION_CRITERIA entry.
    """
    from backtesting.backtest_engine import BacktestConfig

    baseline_cfg = BacktestConfig.from_profile(symbol, **point["risk_overrides"])
    baseline_commission = baseline_cfg.commission_pips
    baseline_slippage = baseline_cfg.slippage_pips
    baseline_swap = baseline_cfg.swap_pips_per_night

    levels = []
    for multiplier in COST_STRESS_MULTIPLIERS:
        stressed_overrides = {
            **point["risk_overrides"],
            "commission_pips": baseline_commission * multiplier,
            "slippage_pips": baseline_slippage * multiplier,
            "swap_pips_per_night": baseline_swap * multiplier,
        }
        stressed_point = {**point, "risk_overrides": stressed_overrides}
        result = evaluate_point(symbol, df, stressed_point, min_trades=1, objective_metric="profit_factor")
        pf = result.metrics.profit_factor
        levels.append({
            "multiplier": multiplier,
            "commission_pips": round(stressed_overrides["commission_pips"], 3),
            "slippage_pips": round(stressed_overrides["slippage_pips"], 3),
            "trades": result.trades,
            "profit_factor": pf,
            "edge_survives": bool(pf >= 1.0) if result.trades > 0 else None,
        })

    measurable = [lv for lv in levels if lv["edge_survives"] is not None]
    survives_all = all(lv["edge_survives"] for lv in measurable) if measurable else None
    return {
        "baseline_commission_pips": round(baseline_commission, 3),
        "baseline_slippage_pips": round(baseline_slippage, 3),
        "levels": levels,
        "survives_all_stress_levels": survives_all,
        "note": (
            "Re-evaluates the candidate with execution costs (commission, "
            "slippage, swap) multiplied "
            + ", ".join(f"{m}x" for m in COST_STRESS_MULTIPLIERS)
            + " to simulate a broker's costs getting worse than measured. "
            "Diagnostic only — never a VALIDATION_CRITERIA entry."
        ),
    }


# Mission Center Research Rigor Phase 6 (2026-08-XX) — a single, transparent
# at-a-glance triage number over the four diagnostics already computed above
# (significance, regime robustness, stability, cost stress). Deliberately
# NOT a new statistic and NOT an opinion-based weighting: every component is
# either read verbatim from a diagnostic that's already a 0.0-1.0 fraction
# (regime_robustness_score, stability_score), a plain fraction derived the
# same way (cost-stress survival rate), or a standard, textbook significance
# bucket (p<0.05 / p<0.10 / else) — no invented thresholds beyond those
# already used elsewhere in this codebase's own statistics. A missing
# component (insufficient data for that one diagnostic) is EXCLUDED from
# the average, never treated as a 0 — mirrors _compute_stability_score_
# diagnostic()'s own "exclude insufficient from denominator" convention.
#
# NEVER a VALIDATION_CRITERIA entry, NEVER used to rank/select/auto-promote
# a candidate — every symbol's full diagnostic detail is still reported
# regardless of this score, exactly like every other diagnostic in this
# module. This is a summary VIEW of already-real numbers, not a new verdict.
DISCOVERY_SCORE_SIGNIFICANT_P = 0.05
DISCOVERY_SCORE_MARGINAL_P = 0.10


def _significance_component(significance: dict) -> float | None:
    p = significance.get("ess_adjusted_p_value")
    if p is None:
        return None
    if p < DISCOVERY_SCORE_SIGNIFICANT_P:
        return 1.0
    if p < DISCOVERY_SCORE_MARGINAL_P:
        return 0.5
    return 0.0


def _cost_stress_component(cost_stress: dict) -> float | None:
    measurable = [lv for lv in cost_stress.get("levels", []) if lv.get("edge_survives") is not None]
    if not measurable:
        return None
    survives = sum(1 for lv in measurable if lv["edge_survives"])
    return survives / len(measurable)


def _compute_discovery_score_diagnostic(
    significance: dict, regime_robustness: dict, stability: dict, cost_stress: dict,
) -> dict:
    """Mission Center Research Rigor Phase 6 (2026-08-XX) — see the module-
    level comment above this function for the full design rationale.
    Diagnostic only, never a VALIDATION_CRITERIA entry."""
    components = {
        "significance_component": _significance_component(significance),
        "regime_robustness_component": regime_robustness.get("regime_robustness_score"),
        "stability_component": stability.get("stability_score"),
        "cost_stress_component": _cost_stress_component(cost_stress),
    }
    available = {k: v for k, v in components.items() if v is not None}
    discovery_score = round(statistics.fmean(available.values()), 3) if available else None
    return {
        **components,
        "components_used": len(available),
        "components_total": len(components),
        "discovery_score": discovery_score,
        "note": (
            f"Equally-weighted average of {len(available)} of {len(components)} "
            "already-computed Research Rigor diagnostics (significance, regime "
            "robustness, stability, cost stress) for this symbol — a summary "
            "view for at-a-glance triage, not a new statistic. Missing "
            "components are excluded from the average, never treated as zero. "
            "Diagnostic only — never a VALIDATION_CRITERIA entry, never used "
            "to rank/select/auto-promote a candidate."
        ),
    }


def _evaluate_symbol(symbol: str, point: dict, vc: ValidationConfig) -> dict:
    """Returns the full per-symbol result dict recorded into
    research_mission_validation_results — always populated, pass or
    fail, nothing suppressed."""
    started_at = datetime.now(timezone.utc).isoformat()
    point_timeframes = tuple(point["timeframes"]) if point["timeframes"] else None
    df = load_symbol_data(symbol, vc.data_dir, vc.start, vc.end, timeframe=physical_load_timeframe(point_timeframes))

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
    significance_result = _compute_effective_sample_size_diagnostic(eval_result.trade_records or [])
    regime_robustness_result = _compute_regime_robustness_diagnostic(metrics.by_regime)
    cost_stress_result = _compute_cost_stress_diagnostic(symbol, df, point)

    wf_result = run_walk_forward(symbol, df, WalkForwardConfig(
        n_windows=vc.wf_windows,
        min_pf=VALIDATION_CRITERIA["min_profit_factor"],
        min_trades_per_window=vc.wf_min_trades_per_window,
        warmup_bars=vc.wf_warmup_bars,
        engine_overrides=point["risk_overrides"],
        timeframes=point_timeframes,
        engines=tuple(point["engines"]) if point["engines"] else None,
        indicators=tuple(point["indicators"]) if point["indicators"] else None,
        context_filters=tuple(point["context_filters"]) if point["context_filters"] else None,
        # Wiring-gap fix (Mission Center Research Rigor Phase 8, audit
        # 2026-08-XX): without this, a candidate validated after being
        # found with a lowered confluence quorum (e.g. a single-engine
        # research mission, min_engines_agreeing=1) would silently
        # re-run Walk-Forward at the PRODUCTION quorum of 2 — mathe-
        # matically unreachable with one engine — making a real
        # candidate look entirely broken during validation. Same class
        # of gap already fixed once for context_filters/indicators here.
        confluence_overrides=point["confluence_overrides"] or None,
    ))

    rb_result = run_robustness(symbol, df, RobustnessConfig(
        multipliers=vc.rb_multipliers, params=vc.rb_params, min_trades=vc.rb_min_trades,
        engine_overrides=point["risk_overrides"],
        timeframes=point_timeframes,
        engines=tuple(point["engines"]) if point["engines"] else None,
        indicators=tuple(point["indicators"]) if point["indicators"] else None,
        context_filters=tuple(point["context_filters"]) if point["context_filters"] else None,
        confluence_overrides=point["confluence_overrides"] or None,
    ))
    stability_score_result = _compute_stability_score_diagnostic(rb_result.sweeps)
    discovery_score_result = _compute_discovery_score_diagnostic(
        significance_result, regime_robustness_result, stability_score_result, cost_stress_result,
    )

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
        "significance": json_safe(significance_result),
        "regime_robustness": json_safe(regime_robustness_result),
        "stability": json_safe(stability_score_result),
        "cost_stress": json_safe(cost_stress_result),
        "discovery_score": json_safe(discovery_score_result),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def run_validation(vc: ValidationConfig) -> None:
    mission = research_missions.get_mission(vc.mission_id)
    research_mission_validations.upsert_validation(
        validation_id=vc.validation_id, mission_id=vc.mission_id, trial_number=vc.trial_number,
        trial_symbol=vc.trial_symbol, validation_symbols=list(vc.validation_symbols),
        objective_metric=mission["objective_metric"] if mission else "unknown",
        criteria=VALIDATION_CRITERIA, status="running", validation_mode=vc.validation_mode,
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

    candidate_lock = _compute_candidate_lock(trial, vc.trial_symbol, vc.data_dir)
    date_overlap = _compute_date_overlap(mission, vc)
    research_mission_validations.set_validation_integrity_checks(
        vc.validation_id, candidate_lock=candidate_lock, date_overlap=date_overlap,
    )

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
                feature_mining=result["feature_mining"], significance=result["significance"],
                regime_robustness=result["regime_robustness"], stability=result["stability"],
                cost_stress=result["cost_stress"], discovery_score=result["discovery_score"], error=None,
                started_at=result["started_at"], finished_at=result["finished_at"],
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            logger.error(f"Validation {vc.validation_id}: {symbol} failed — {exc}")
            now = datetime.now(timezone.utc).isoformat()
            research_mission_validations.record_validation_result(
                validation_id=vc.validation_id, symbol=symbol, passed=False,
                metrics=None, monte_carlo=None, walk_forward=None, robustness=None,
                criteria_breakdown={}, feature_mining=None, significance=None, regime_robustness=None,
                stability=None, cost_stress=None, discovery_score=None, error=str(exc), started_at=now, finished_at=now,
            )

    total = len(vc.validation_symbols)
    if vc.validation_mode == SAME_SYMBOL:
        # Deliberately NOT the NO_EDGE/WEAK_LEAD/STRONG_LEAD scale — that
        # vocabulary is inherently about CROSS-SYMBOL generalization.
        # Confirming on the trial's own training symbol is a narrower,
        # weaker claim and must never be presented with the same tokens.
        overall = SAME_SYMBOL_CONFIRMED if (passing == total and total > 0) else SAME_SYMBOL_NOT_CONFIRMED
    elif passing <= 1:
        overall = NO_EDGE
    elif passing == total and total >= MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD:
        overall = STRONG_LEAD
    else:
        overall = WEAK_LEAD

    research_mission_validations.set_validation_status(
        vc.validation_id, "finished", finished=True,
        overall_verdict=overall, passing_symbols=passing, total_symbols=total,
    )
    _write_report(vc, overall, passing, total, candidate_lock, date_overlap)


def _write_report(
    vc: ValidationConfig, overall: str, passing: int, total: int,
    candidate_lock: dict, date_overlap: dict,
) -> None:
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
        "candidate_lock": candidate_lock,
        "date_overlap": date_overlap,
        "validation_mode": vc.validation_mode,
        "results": results,
        "note": (
            f"{overall} — a LEAD, NOT evidence. Passing every criterion here "
            "does not register or promote anything: a human must manually "
            "pre-register a real hypothesis (its own ID, falsification "
            "criteria written before re-testing) and re-run it through "
            "backtest/walk_forward.py's existing chronological-OOS pipeline "
            "before CLAUDE.md rule 1 considers this evidence."
            + (
                " This was a SAME_SYMBOL validation — it only confirms the "
                "trial's own symbol and is NOT cross-symbol evidence. It "
                "does NOT rule out curve-fitting. Run a CROSS_SYMBOL "
                "validation against >=3 other symbols before treating this "
                "as a lead."
                if vc.validation_mode == SAME_SYMBOL else ""
            )
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
    parser.add_argument("--validation-mode", default=SAME_SYMBOL, choices=VALIDATION_MODES)
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
        validation_mode=args.validation_mode,
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
