"""
execution/routes/research.py
-------------------------------
Research Center (Mission Control modules 4, 9, 10): manifests, hypothesis
registry, philosophy audit, research integrity checks (leakage guard,
survivorship checker, manifest validator), per-hypothesis drill-down, and
downloadable reports. Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from execution.api_core import _check_auth, _executor, _get_config, logger
from execution.api_shared_helpers import _data_health_snapshot, _load_manifests
# /reports/{kind} calls these other routers' handlers directly as plain
# in-process function calls (not HTTP) to reuse their logic — imported
# here rather than reimplemented. No circular import: none of these
# modules import from execution.routes.research.
from execution.routes.data_providers import provider_chains_endpoint
from execution.routes.forward_review import forward_review_endpoint
from execution.routes.health import system_health_full
from execution.routes.outcomes import get_outcomes

router = APIRouter()


@router.get("/research/manifests")
async def research_manifests(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Git-tracked evidence manifests (research/manifest.py, audit item H2).

    Each manifest binds one research run to the exact git commit, a config
    hash, and per-dataset SHA256 fingerprints. The dashboard renders these
    as the system's auditable evidence trail — including the honest
    `reproducible: false` flag for runs from a dirty working tree.
    """
    _check_auth(x_api_key, iatis_session)
    manifests = _load_manifests()
    return {"count": len(manifests), "manifests": manifests}


@router.get("/research/symbols")
async def research_symbols(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Symbol Manager + Timeframe Selector (Research Workspace, 2026-07-24):
    the FULL symbol universe from config/symbols.yaml, grouped by asset
    class, including disabled/WATCHLIST/RETIRED entries with their
    governance metadata (status/status_reason) — unlike /symbol-health and
    /provider-chains, which only report on the live-enabled subset. Also
    surfaces each provider's native timeframe coverage so the frontend can
    restrict the Timeframe Selector to what a chosen symbol can actually
    serve.
    """
    _check_auth(x_api_key, iatis_session)
    from core.data_providers import DEFAULT_CHAINS, _NATIVE_TF, provider_chain_for, symbol_class

    config = _get_config()
    overrides = config.get("data", {}).get("provider_chains") or {}
    symbols_cfg = config.get("data", {}).get("twelve_data_symbols", [])

    by_asset_class: dict[str, list[dict[str, Any]]] = {}
    for s in symbols_cfg:
        internal = s.get("internal", "")
        entry = {
            "internal": internal,
            "symbol": s.get("symbol", internal),
            "enabled": bool(s.get("enabled", False)),
            "status": s.get("status", "UNKNOWN"),
            "status_reason": s.get("status_reason", ""),
            "status_since": s.get("status_since"),
            "min_score": s.get("min_score"),
            "rr": s.get("rr"),
            "provider_chain": provider_chain_for(internal, overrides) if internal else [],
        }
        asset_class = s.get("asset_class") or symbol_class(internal) or "unknown"
        by_asset_class.setdefault(asset_class, []).append(entry)

    return {
        "asset_classes": by_asset_class,
        "native_timeframes": {p: sorted(tfs) for p, tfs in _NATIVE_TF.items()},
        "chains": {cls: (overrides.get(cls) or chain) for cls, chain in DEFAULT_CHAINS.items()},
    }


# The frozen prod4 activation set (CLAUDE.md, config/engines.yaml): do not
# read this as a suggestion to enable more — "Enabling more engines (any)"
# is on the dead list (H015, run twice). This constant only labels what
# /research/engines reports; it changes nothing about what runs.
_PROD4_ENGINES = frozenset({"smc", "price_action", "nnfx", "wyckoff"})


@router.get("/research/engines")
async def research_engines(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Engine Selector (Research Workspace, 2026-07-24): every engine's
    activation state, confluence weight, and version, sourced from
    config/engines.yaml + confluence.weights — not reimplemented here.
    Read-only: this endpoint reports the frozen prod4 configuration, it
    does not let a caller toggle an engine (CLAUDE.md: enabling another
    engine needs a new pre-registered hypothesis, not a dashboard click).
    """
    _check_auth(x_api_key, iatis_session)
    config = _get_config()
    engines_cfg = config.get("engines", {})
    enabled = engines_cfg.get("enabled", {})
    versions = engines_cfg.get("versions", {})
    weights = config.get("confluence", {}).get("weights", {})

    all_names = sorted(set(enabled) | set(versions) | set(weights))
    return {
        "engines": [
            {
                "name": name,
                "enabled": bool(enabled.get(name, False)),
                "prod4": name in _PROD4_ENGINES,
                "weight": weights.get(name),
                "version": versions.get(name),
            }
            for name in all_names
        ],
        "smc_full_spec": bool(engines_cfg.get("smc_full_spec", False)),
        "crypto_positioning_modulator": bool(engines_cfg.get("crypto_positioning_modulator", False)),
    }


# Technical Indicator catalog (Dataset Builder, 2026-07-24) — a read-only
# inventory of the indicator math that ALREADY exists in this codebase
# (grep-verified against the source, not guessed), for the Dataset
# Builder / Engine Selector UI to show what each engine's numbers are
# actually built from. This does NOT compute anything new and does NOT
# change any engine's live formula — see utils/indicators.py's own
# consolidation note: two ATR variants are deliberately different
# (range_atr is NOT a bug), and "upgrading" a variant is a strategy
# change requiring a new pre-registered hypothesis (CLAUDE.md rule 6).
# The two independent RSI implementations below (SMA-smoothed vs
# EWM/Wilder-smoothed) are the same kind of intentional-until-measured
# divergence — listed, not merged.
_INDICATOR_CATALOG: list[dict[str, Any]] = [
    {
        "id": "atr_true_range",
        "name": "ATR (true-range)",
        "category": "volatility",
        "description": "Rolling mean of true range (max of H-L, |H-prevC|, |L-prevC|) over `period` bars.",
        "default_params": {"period": 14},
        "source": "utils/indicators.py:atr",
        "used_by": ["regimes/volatility_classifier.py:atr (re-export)", "quant_engine (_atr_percentile)", "nnfx_engine (_adx)"],
    },
    {
        "id": "atr_range_mean",
        "name": "ATR (simplified range mean)",
        "category": "volatility",
        "description": "Mean of (high-low) over the last `period` bars, as a scalar. NOT true ATR — ignores gaps via prev-close. Deliberately different from atr_true_range; frozen per prod4 measurement, see source docstring.",
        "default_params": {"period": 14},
        "source": "utils/indicators.py:range_atr",
        "used_by": ["smc_engine", "wyckoff_engine", "price_action_engine"],
    },
    {
        "id": "atr_percentile",
        "name": "ATR percentile",
        "category": "volatility",
        "description": "Current ATR's percentile rank within its own recent history (lookback bars).",
        "default_params": {"period": 14, "lookback": 100},
        "source": "engines/quant_engine.py:_atr_percentile",
        "used_by": ["quant_engine (disabled in prod4)"],
    },
    {
        "id": "volatility_classification",
        "name": "Volatility regime (low/normal/high/extreme)",
        "category": "volatility",
        "description": "Classifies each bar's ATR relative to its own recent history into 4 labels.",
        "default_params": {"period": 14, "lookback": 100},
        "source": "regimes/volatility_classifier.py:classify_volatility",
        "used_by": ["regime layer (soft regime weighting only — features.regime_gate hard gate is OFF, H024 NULL)"],
    },
    {
        "id": "rsi_sma",
        "name": "RSI (SMA-smoothed gain/loss)",
        "category": "momentum",
        "description": "Classic RSI with a simple rolling mean of gains/losses.",
        "default_params": {"period": 14},
        "source": "engines/price_action_engine.py:_rsi (identical copy in engines/quant_engine.py:_rsi)",
        "used_by": ["price_action_engine", "quant_engine (disabled in prod4)"],
    },
    {
        "id": "rsi_ewm",
        "name": "RSI (EWM/Wilder-style smoothed gain/loss)",
        "category": "momentum",
        "description": "RSI variant using exponentially-weighted (alpha=1/period) gain/loss smoothing instead of a simple rolling mean — a different number from rsi_sma on the same series.",
        "default_params": {"period": 14},
        "source": "engines/divergence_engine.py:_rsi",
        "used_by": ["divergence_engine (disabled in prod4)"],
    },
    {
        "id": "macd",
        "name": "MACD (line + signal)",
        "category": "momentum",
        "description": "EMA(fast)-EMA(slow) as the MACD line, EMA(signal) of that line as the signal line.",
        "default_params": {"fast": 12, "slow": 26, "signal": 9},
        "source": "engines/divergence_engine.py:_macd",
        "used_by": ["divergence_engine (disabled in prod4)"],
    },
    {
        "id": "bollinger_bands",
        "name": "Bollinger Bands",
        "category": "volatility",
        "description": "Rolling mean +/- (std_mult * rolling std).",
        "default_params": {"period": 20, "std": 2.0},
        "source": "engines/price_action_engine.py:_bollinger",
        "used_by": ["price_action_engine"],
    },
    {
        "id": "ema",
        "name": "EMA (exponential moving average)",
        "category": "trend",
        "description": "Standard exponential moving average, span=period.",
        "default_params": {"period": None},
        "source": "engines/nnfx_engine.py:_ema",
        "used_by": ["nnfx_engine"],
    },
    {
        "id": "adx",
        "name": "ADX (Average Directional Index)",
        "category": "trend",
        "description": "Trend-strength (not direction) from smoothed +DI/-DI derived from true range.",
        "default_params": {"period": 14},
        "source": "engines/nnfx_engine.py:_adx",
        "used_by": ["nnfx_engine"],
    },
    {
        "id": "roc",
        "name": "ROC (rate of change)",
        "category": "momentum",
        "description": "Percent change over `period` bars.",
        "default_params": {"period": 10},
        "source": "engines/quant_engine.py:_roc",
        "used_by": ["quant_engine (disabled in prod4)"],
    },
]


@router.get("/research/indicators")
async def research_indicators(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Technical Indicator catalog (Dataset Builder, 2026-07-24) — see
    _INDICATOR_CATALOG's module comment: a read-only inventory of the
    indicator math already implemented in this codebase, grouped by
    category, for the Dataset Builder to let a researcher see/select
    which indicators back a chosen engine. Adds no new computation."""
    _check_auth(x_api_key, iatis_session)
    by_category: dict[str, list[dict[str, Any]]] = {}
    for ind in _INDICATOR_CATALOG:
        by_category.setdefault(ind["category"], []).append(ind)
    return {
        "count": len(_INDICATOR_CATALOG),
        "categories": by_category,
        "indicators": _INDICATOR_CATALOG,
    }


# Dataset Builder / Scenario Testing config schema (2026-07-24) — describes
# the REAL cost/scenario parameters backtest.runner.RunnerConfig and
# backtesting.backtest_engine.BacktestConfig already accept, so the
# frontend can build a form against actual fields instead of inventing
# ones this system doesn't support. Two groups matter for interpreting a
# scenario run:
#   - cost/scenario fields: legitimate per-run overrides (spread via
#     commission_pips, slippage_pips, swap, RR, sizing).
#   - gate ablation flags: default ON because the backtest must simulate
#     the SAME system that trades live; turning one off is an ABLATION
#     STUDY, not a production tuning knob, and any result produced with a
#     gate disabled must be labeled as an ablation in the result manifest
#     (backtesting/backtest_engine.py's own docstring).
_SCENARIO_CONFIG_FIELDS: list[dict[str, Any]] = [
    {"field": "commission_pips", "group": "cost", "default": 0.5,
     "description": "Spread/commission cost in pips, charged per trade. from_profile() defaults it to the REAL measured broker spread per symbol (REAL_SPREAD_PIPS) unless overridden."},
    {"field": "slippage_pips", "group": "cost", "default": 0.5,
     "description": "Slippage applied against the trader on entry and on SL exits (TP exits assumed filled at price). 0 disables it."},
    {"field": "swap_pips_per_night", "group": "cost", "default": 0.0,
     "description": "Rollover/financing cost in pips per UTC-day boundary held. Ships at 0.0 system-wide (data/swap_rates.json all zeros) until real per-symbol rates are filled in."},
    {"field": "min_rr", "group": "risk", "default": 2.0,
     "description": "Minimum reward:risk required for a setup to be taken. Aligned with production config.yaml risk.min_risk_reward."},
    {"field": "sl_atr_multiplier", "group": "risk", "default": 2.5,
     "description": "Stop-loss distance = ATR * this multiplier. Aligned with production config.yaml risk.sl_atr_multiplier."},
    {"field": "risk_per_trade", "group": "risk", "default": 0.01,
     "description": "Fraction of account balance risked per trade (fractional position sizing)."},
    {"field": "initial_balance", "group": "risk", "default": 10000.0,
     "description": "Starting simulated account balance."},
    {"field": "warmup_bars", "group": "structural", "default": 210,
     "description": "Bars consumed before the engine starts producing decisions (NNFX needs 210+ for EMA200)."},
    {"field": "step_bars", "group": "structural", "default": 4,
     "description": "Bar stride between simulated decision points."},
    {"field": "asset_class", "group": "structural", "default": "forex",
     "description": "Controls P&L math: 'forex' (pips*pip_size*lot*100000), 'metal'/'index' (price_diff*lot*dollar_per_point)."},
    {"field": "start", "group": "dataset", "default": None,
     "description": "Optional ISO date to slice the dataset's start (inclusive)."},
    {"field": "end", "group": "dataset", "default": None,
     "description": "Optional ISO date to slice the dataset's end (inclusive)."},
]
_SCENARIO_GATE_FLAGS: list[dict[str, Any]] = [
    {"field": "use_mqs_gate", "default": True, "description": "Market Quality Score gate (session/volatility/day-of-week filter)."},
    {"field": "use_regime_weights", "default": True, "description": "Regime-adaptive engine weighting. NOT the hard regime gate (features.regime_gate, OFF — H024 NULL)."},
    {"field": "use_mtf_confirmation", "default": True, "description": "D1/H1 multi-timeframe alignment score adjustment."},
    {"field": "use_reversal_veto", "default": True, "description": "H013 hard/soft reversal veto."},
]
# core/market_quality.py SESSIONS — the real session windows the MQS gate
# scores against (UTC hour ranges, end < start means it crosses midnight).
_SESSION_TEMPLATES: dict[str, dict[str, int]] = {
    "Sydney": {"start_utc": 21, "end_utc": 6},
    "Tokyo": {"start_utc": 23, "end_utc": 8},
    "London": {"start_utc": 7, "end_utc": 16},
    "NewYork": {"start_utc": 12, "end_utc": 21},
}


@router.get("/research/scenario-config")
async def research_scenario_config(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Dataset Builder / Scenario Testing (2026-07-24): the real cost,
    risk, and structural parameters a backtest scenario can vary
    (backtest.runner.RunnerConfig / backtesting.backtest_engine.
    BacktestConfig), the gate ablation flags (default ON — disabling one
    is an ablation study, not a tuning knob), and the real session
    templates the MQS gate scores against.

    Two fields from the originally proposed field set are intentionally
    absent because this system does not support them: tick-level data
    (OHLC bars only, H1 native with H4/D1 resampled) and a configurable
    timezone (every internal timestamp is UTC; SESSIONS below are how
    session-of-day is actually derived). Data Provider / Data Quality are
    already covered by /provider-chains and /research/symbols.
    """
    _check_auth(x_api_key, iatis_session)
    return {
        "scenario_fields": _SCENARIO_CONFIG_FIELDS,
        "gate_flags": _SCENARIO_GATE_FLAGS,
        "session_templates": _SESSION_TEMPLATES,
        "data_mode": "ohlc_only",
        "timezone": "UTC",
        "not_supported": [
            "tick_level_data — this system only ever simulates OHLC bars, no tick replay exists",
            "configurable_timezone — every internal timestamp is UTC; use session_templates for session-of-day instead",
        ],
    }


# Local historical-dataset directory (Dataset Builder — Date Range,
# 2026-07-24). Matches backtest.runner.RunnerConfig.data_dir's default
# and scripts/download_all_symbols.py's write location. Module-level so
# tests can monkeypatch it instead of chdir()ing the whole process.
_DATA_DIR = Path("data")


@router.get("/research/datasets")
async def research_datasets(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Dataset Builder — Date Range picker (2026-07-24): which local
    historical CSVs actually exist for backtesting, and the real date
    range/row count each one covers.

    Reuses backtest.runner.load_symbol_data's exact read path (index_col=0,
    parse_dates=True, tz-localize UTC if naive) rather than a new parser,
    so the reported range matches what a real scenario run would see —
    it does NOT run validate_ohlcv (that's a scenario-run-time concern,
    not a listing concern), so a malformed file is reported per-entry
    with readable=false instead of failing the whole endpoint.

    Only matches the `{SYMBOL}_{TIMEFRAME}_*.csv` convention
    find_symbol_csv()/download_all_symbols.py already use — the
    headerless/tab-separated one-off format documented in data/README.md
    is a different, non-standard loading path and is intentionally not
    listed here.
    """
    _check_auth(x_api_key, iatis_session)
    import re

    import pandas as pd

    config = _get_config()
    known_symbols = {
        str(s.get("internal", "")).upper()
        for s in config.get("data", {}).get("twelve_data_symbols", [])
        if s.get("internal")
    }

    datasets: list[dict[str, Any]] = []
    if _DATA_DIR.exists():
        for path in sorted(_DATA_DIR.glob("*.csv")):
            m = re.match(r"^([A-Z0-9]+)_(M1|M5|M15|M30|H1|H4|D1|W1)_", path.name)
            if not m:
                continue
            symbol, timeframe = m.group(1), m.group(2)
            entry: dict[str, Any] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "file": path.name,
                "size_bytes": path.stat().st_size,
                "known_symbol": symbol in known_symbols,
            }
            try:
                df = pd.read_csv(path, index_col=0, parse_dates=True)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                entry["readable"] = True
                entry["rows"] = int(len(df))
                entry["start"] = df.index.min().isoformat() if len(df) else None
                entry["end"] = df.index.max().isoformat() if len(df) else None
            except Exception as exc:
                entry["readable"] = False
                entry["error"] = str(exc)
            datasets.append(entry)

    return {
        "data_dir": str(_DATA_DIR),
        "count": len(datasets),
        "datasets": datasets,
    }


@router.get("/research/validation-config")
async def research_validation_config(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Walk-Forward / Monte Carlo / Robustness (Phase 4, 2026-07-24): the
    real default parameters backtest.walk_forward.WalkForwardConfig and
    backtest.robustness.RobustnessConfig ship with, backtest.monte_carlo's
    defaults (already run automatically inside the `backtest` job — no
    separate Monte Carlo job exists), and the codified promotion bar
    (research/edge_gate.py PROMOTION_CRITERIA) these measurements exist
    to feed. A PASSED hypothesis without evidence meeting this bar is
    flagged at every boot (CLAUDE.md rule 3) — this endpoint is what a
    Walk-Forward/Robustness UI panel checks a fresh run's numbers against.
    """
    _check_auth(x_api_key, iatis_session)
    from backtest.robustness import DEFAULT_MULTIPLIERS, SWEEP_PARAMS
    from backtest.walk_forward import WalkForwardConfig
    from research.edge_gate import PROMOTION_CRITERIA

    wf_defaults = WalkForwardConfig()
    return {
        "walk_forward": {
            "n_windows": wf_defaults.n_windows,
            "min_pf": wf_defaults.min_pf,
            "min_trades_per_window": wf_defaults.min_trades_per_window,
            "warmup_bars": wf_defaults.warmup_bars,
            "methodology": "Disjoint chronological OOS windows with an untraded warmup embargo. Fixed production parameters — no per-window optimization.",
        },
        "monte_carlo": {
            "n_simulations": 1000,
            "ruin_threshold": 0.50,
            "note": "Runs automatically inside the `backtest` job (backtest.runner, run_mc=True by default) — no separate Monte Carlo job exists.",
        },
        "robustness": {
            "params": list(SWEEP_PARAMS),
            "multipliers": list(DEFAULT_MULTIPLIERS),
            "min_trades": 10,
            "stable_band_pct": 30,
            "methodology": "Parameter-perturbation sensitivity screen, NOT out-of-sample validation. Does not itself justify changing a live parameter.",
        },
        "promotion_criteria": PROMOTION_CRITERIA,
    }


@router.get("/research")
async def research_center(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Center — hypothesis status, engine performance, backtest results."""
    _check_auth(x_api_key, iatis_session)
    import json as _json
    from pathlib import Path

    registry_path = Path("research/results/registry.json")
    hypotheses_raw = {}
    if registry_path.exists():
        try:
            hypotheses_raw = _json.loads(registry_path.read_text()).get("hypotheses", {})
        except Exception:
            pass

    # Trust audit: which PASSED entries actually clear the codified
    # promotion criteria (research/edge_gate.py) — the dashboard must never
    # render an under-evidenced PASSED as green.
    try:
        from research.edge_gate import PROMOTION_CRITERIA, audit_passed_hypotheses
        trust_warnings = audit_passed_hypotheses(hypotheses_raw)
        flagged_ids = {w.split(" ", 1)[0] for w in trust_warnings}
        promotion_criteria = PROMOTION_CRITERIA
    except Exception:
        trust_warnings, flagged_ids, promotion_criteria = [], set(), {}

    hypotheses = []
    for h_id, h_data in hypotheses_raw.items():
        entry = {
            "id": h_id,
            "title": h_data.get("title", ""),
            "status": h_data.get("status", "UNKNOWN"),
            "description": h_data.get("description", "")[:120],
            "last_updated": h_data.get("last_updated", ""),
            "conclusion": (h_data.get("conclusion") or h_data.get("lesson") or "")[:300],
            "trusted": h_data.get("status") != "PASSED" or h_id not in flagged_ids,
        }
        # Load result file if exists
        result_file = h_data.get("result_file")
        if result_file:
            rp = Path("research") / result_file
            if rp.exists():
                try:
                    r = _json.loads(rp.read_text())
                    entry["sample_size"] = (r.get("n_fvg_entries") or
                        r.get("qualified_n") or r.get("total_n"))
                    entry["win_rate"] = (r.get("win_rate") or
                        r.get("qualified_win_rate"))
                    entry["p_value"] = r.get("p_value")
                except Exception:
                    pass
        hypotheses.append(entry)

    try:
        from storage.engine_tracker import engine_stats
        stats = engine_stats(min_votes=1)
    except Exception:
        stats = []

    try:
        from storage.outcome_tracker import performance_summary
        outcomes = performance_summary()
    except Exception:
        outcomes = {"total_closed": 0, "win_rate": 0}

    backtest_files = sorted(Path("storage").glob("full_pipeline_backtest_*.json"), reverse=True)
    latest_backtest = None
    if backtest_files:
        try:
            bt = _json.loads(backtest_files[0].read_text())
            valid = [r for r in bt.get("results", [])
                     if not r.get("error") and r.get("trades", 0) >= 10]
            latest_backtest = {
                "file": backtest_files[0].name,
                "generated_at": bt.get("generated_at", ""),
                "summary": bt.get("summary", {}),
                "avg_wr": round(sum(r.get("win_rate",0) for r in valid)/len(valid), 1) if valid else 0,
                "avg_pf": round(sum(r.get("profit_factor",0) for r in valid)/len(valid), 2) if valid else 0,
                "top_symbols": sorted(valid, key=lambda x: x.get("profit_factor",0), reverse=True)[:5],
            }
        except Exception:
            pass

    return {
        "hypothesis_summary": {
            "total": len(hypotheses),
            "passed": sum(1 for h in hypotheses if h["status"] == "PASSED"),
            "failed": sum(1 for h in hypotheses if "FAILED" in h["status"]),
            "research": sum(1 for h in hypotheses if h["status"] == "RESEARCH"),
            "needs_data": sum(1 for h in hypotheses if h["status"] == "NEEDS_MORE_DATA"),
        },
        "hypotheses": hypotheses,
        "trust_audit": {
            "criteria": promotion_criteria,
            "warnings": trust_warnings,
        },
        "engine_performance": stats,
        "outcome_summary": outcomes,
        "latest_backtest": latest_backtest,
    }


@router.get("/philosophy-audit")
async def philosophy_audit_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """System Philosophy Audit — the same 29 checks as
    `python -m scripts.philosophy_audit`, on demand from the dashboard.

    Read-only (SELECTs against the decisions DB). Takes ~10-20s because it
    issues multiple D1 round-trips; the frontend calls it from a button,
    never on a poll."""
    _check_auth(x_api_key, iatis_session)

    def _run() -> dict[str, Any]:
        from scripts.philosophy_audit import run_all
        from storage import d1_client
        # Ensure the audited tables exist (CREATE IF NOT EXISTS) — a fresh
        # DB (or the tests' fake D1) has none until a first decision lands.
        from storage.decision_db import init_db as _init_decisions
        from storage.outcome_tracker import _init_db as _init_outcomes
        _init_decisions()
        _init_outcomes()
        with d1_client.d1_connection() as con:
            checks = run_all(con)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(checks),
                "fail": sum(1 for c in checks if c.status == "FAIL"),
                "warn": sum(1 for c in checks if c.status == "WARN"),
                "pass": sum(1 for c in checks if c.status == "PASS"),
                "info": sum(1 for c in checks if c.status == "INFO"),
            },
            "checks": [
                {"axis": c.axis, "name": c.name, "status": c.status,
                 "detail": c.detail,
                 "evidence": [str(e) for e in c.evidence[:12]]}
                for c in checks
            ],
        }

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_executor, _run)
    except Exception as exc:
        logger.error(f"Philosophy audit failed: {exc}")
        raise HTTPException(status_code=503,
                            detail="Audit unavailable — decisions DB unreachable.")


# ---------------------------------------------------------------------------
# Research Integrity (Mission Control module 9) — on-demand, read-only
# checks alongside the philosophy audit above. Deliberately excludes
# cross-provider diff (scripts/cross_provider_diff.py): that tool makes
# live provider API calls and burns rate-limited quota (see /budget), so
# it belongs in the Experiment Runner (module 5) where a human explicitly
# kicks off a job with visible cost, not a casual dashboard click.
# ---------------------------------------------------------------------------
def _leakage_guard_report() -> dict[str, Any]:
    """Static leakage scan (research/guards/static_scan.py) over every
    research/experiment script. Advisory only, by that module's own
    design — CLEAN or WARNINGS_FOUND, never a hard FAIL; see its
    docstring for why a heuristic AST scan must never claim proof.
    """
    from research.guards.static_scan import scan_paths

    paths: list[Path] = []
    for d in ("research", "scripts"):
        paths.extend(sorted(Path(d).rglob("*.py")))
    paths.extend(sorted(Path(".").glob("run_h*.py")))

    report = scan_paths(paths)
    return {"status": "PASS" if report["verdict"] == "CLEAN" else "WARNING", **report}


def _survivorship_report() -> dict[str, Any]:
    """Symbol-evidence + selection-disclosure gate
    (research/survivorship_checker.py) — matches that module's own
    return-code convention: an enabled symbol with zero committed
    evidence is a FAIL, everything else advisory-only WARNING/PASS.
    """
    from research.survivorship_checker import check_selection_disclosure, check_symbol_evidence

    config = _get_config()
    symbol_report = check_symbol_evidence(config)
    selection_report = check_selection_disclosure()
    if symbol_report["enabled_no_evidence"]:
        status = "FAIL"
    elif (symbol_report["disabled_no_evidence"] or selection_report["undisclosed"]
          or selection_report["invalid_label"]):
        status = "WARNING"
    else:
        status = "PASS"
    return {"status": status, "symbol_evidence": symbol_report, "selection_disclosure": selection_report}


def _manifest_validator_report() -> dict[str, Any]:
    """Which evidence manifests are reproducible=false — reuses
    _load_manifests() (also backing /research/manifests and /alerts) so
    this never drifts from what those already show.
    """
    manifests = _load_manifests()
    non_reproducible = [m for m in manifests if m.get("reproducible") is False]
    return {
        "status": "WARNING" if non_reproducible else "PASS",
        "total": len(manifests),
        "reproducible_count": len(manifests) - len(non_reproducible),
        "non_reproducible": [
            {"file": m["file"], "kind": m.get("kind"), "git_dirty": m.get("git_dirty")}
            for m in non_reproducible
        ],
    }


@router.get("/research/integrity")
async def research_integrity(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Integrity — leakage guard, survivorship checker, and
    manifest validator, on demand. Read-only, no network calls, never
    modifies research evidence. See module docstring above for what's
    deliberately excluded and why.
    """
    _check_auth(x_api_key, iatis_session)

    def _run() -> dict[str, Any]:
        checks: dict[str, Any] = {}
        for name, fn in (
            ("leakage_guard", _leakage_guard_report),
            ("survivorship", _survivorship_report),
            ("manifest_validator", _manifest_validator_report),
        ):
            try:
                checks[name] = fn()
            except Exception as exc:
                checks[name] = {"status": "ERROR", "error": str(exc)[:300]}

        statuses = {c.get("status") for c in checks.values()}
        overall = (
            "FAIL" if "FAIL" in statuses else
            "ERROR" if "ERROR" in statuses else
            "WARNING" if "WARNING" in statuses else
            "PASS"
        )
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "overall": overall, "checks": checks}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run)


@router.get("/research/{hypothesis_id}")
async def research_hypothesis_detail(
    hypothesis_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Center drill-down (module 4) — the complete registry.json
    entry for one hypothesis (untruncated, unlike /research's summary
    list) plus every manifest linked to it and its declared result
    file(s).

    Manifest linking uses two sources, kept separate and labeled rather
    than merged into one list pretending to be equally certain:
      - "exact": the hypothesis's own `manifest` field in registry.json
        (a real field some hypotheses declare — H008c, H015, etc. — the
        authoritative link where it exists).
      - "heuristic": any other manifest whose filename or `kind` contains
        the hypothesis ID as a case-insensitive substring. A guess, not
        a fact — many manifest kinds (crypto_volume_experiment,
        ctrader_spread_measurement) don't embed a hypothesis ID at all.

    MUST stay registered after /research/manifests and /research/integrity
    (both literal paths) — Starlette/FastAPI match routes in registration
    order, so a path-param route registered earlier would silently shadow
    them (hit exactly this bug once while building this route; pinned by
    tests/test_api_contract.py::test_research_hypothesis_detail_route_does_not_shadow_literal_routes).
    """
    _check_auth(x_api_key, iatis_session)
    import json as _json

    registry_path = Path("research/results/registry.json")
    if not registry_path.exists():
        raise HTTPException(status_code=404, detail="Registry not found.")
    hypotheses_raw = _json.loads(registry_path.read_text()).get("hypotheses", {})
    hyp = hypotheses_raw.get(hypothesis_id)
    if hyp is None:
        raise HTTPException(status_code=404, detail=f"Hypothesis '{hypothesis_id}' not found.")

    manifests = _load_manifests()
    declared_manifest = hyp.get("manifest")
    declared_name = Path(declared_manifest).name if declared_manifest else None

    exact_links, heuristic_links = [], []
    needle = hypothesis_id.lower()
    for m in manifests:
        if declared_name and m["file"] == declared_name:
            exact_links.append(m)
        elif needle in m["file"].lower() or (m.get("kind") and needle in str(m["kind"]).lower()):
            heuristic_links.append(m)

    # Result file(s) — path + existence check only. Never dumps arbitrary
    # file content through this endpoint; that's File Explorer's job.
    result_paths: list[str] = []
    if isinstance(hyp.get("result_file"), str):
        result_paths.append(hyp["result_file"])
    result_files_field = hyp.get("result_files")
    if isinstance(result_files_field, dict):
        result_paths.extend(v for v in result_files_field.values() if isinstance(v, str))

    return {
        "id": hypothesis_id,
        "hypothesis": hyp,
        "manifests": {"exact": exact_links, "heuristic": heuristic_links},
        "result_files": [
            {"path": p, "exists": (Path("research") / p).exists()}
            for p in result_paths
        ],
    }


# ---------------------------------------------------------------------------
# Reports (Mission Control module 10) — on-demand snapshots assembled from
# data other endpoints already compute; never a second implementation of
# the same numbers. Markdown or JSON only — no PDF dependency exists in
# this project's requirements.txt, and we don't claim functionality that
# isn't real (docs/VISION_v2.md's "no future phase functionality
# pretending to be complete" rule).
# ---------------------------------------------------------------------------
_REPORT_TITLES: dict[str, str] = {
    "research": "IATIS Research Report",
    "manifest_summary": "IATIS Manifest Summary",
    "system": "IATIS System Health Report",
    "provider": "IATIS Data Provider Report",
    "forward": "IATIS Forward Demo Report",
    "data_quality": "IATIS Data Quality Report",
}


def _dict_to_md(title: str, data: dict[str, Any], generated_at: str) -> str:
    """Generic dict → Markdown for report kinds without a dedicated table
    formatter (system/provider/forward): a titled doc with the exact data
    as a JSON block. Honest about being a snapshot, not hand-formatted
    prose — good enough for an operator to read or paste elsewhere."""
    import json as _json

    return "\n".join([
        f"# {title}", "", f"Generated {generated_at}.", "",
        "```json", _json.dumps(data, indent=2, default=str), "```", "",
    ])


def _build_manifest_summary_md(manifests: dict[str, dict]) -> str:
    from scripts.generate_research_report import build_manifest_table

    n_total = len(manifests)
    n_repro = sum(1 for m in manifests.values() if m.get("reproducible"))
    return "\n".join([
        "# IATIS Manifest Summary", "",
        f"Generated {datetime.now(timezone.utc).isoformat()}.", "",
        f"{n_total} manifests, {n_repro} reproducible, {n_total - n_repro} NOT reproducible.", "",
        build_manifest_table(manifests), "",
    ])


@router.get("/reports/{kind}")
async def generate_report(
    kind: str,
    format: str = Query(default="md", pattern="^(md|json)$"),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> Any:
    _check_auth(x_api_key, iatis_session)
    if kind not in _REPORT_TITLES:
        raise HTTPException(status_code=404, detail=f"Unknown report kind '{kind}'. Choose from: {sorted(_REPORT_TITLES)}")

    generated_at = datetime.now(timezone.utc).isoformat()
    title = _REPORT_TITLES[kind]

    if kind == "research":
        from scripts.generate_research_report import build_report, load_manifests, load_registry
        registry = load_registry()
        manifests = load_manifests()
        markdown = build_report(registry, manifests)
        data: dict[str, Any] = {"registry": registry, "manifests": manifests}
    elif kind == "manifest_summary":
        from scripts.generate_research_report import load_manifests
        manifests = load_manifests()
        data = {"manifests": manifests}
        markdown = _build_manifest_summary_md(manifests)
    elif kind == "system":
        data = await system_health_full(x_api_key, iatis_session)
        markdown = _dict_to_md(title, data, generated_at)
    elif kind == "provider":
        data = await provider_chains_endpoint(x_api_key, iatis_session)
        markdown = _dict_to_md(title, data, generated_at)
    elif kind == "data_quality":
        data = _data_health_snapshot()
        markdown = _dict_to_md(title, data, generated_at)
    else:  # "forward"
        data = {
            "forward_review": await forward_review_endpoint(x_api_key, iatis_session),
            "outcomes_summary": (await get_outcomes(x_api_key, iatis_session))["summary"],
        }
        markdown = _dict_to_md(title, data, generated_at)

    if format == "json":
        return {"kind": kind, "title": title, "generated_at": generated_at, "data": data}
    return PlainTextResponse(
        markdown, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="iatis_{kind}_report.md"'},
    )
