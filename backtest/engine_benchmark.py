"""
backtest/engine_benchmark.py
--------------------------------
Engine Benchmark — standalone, single-engine ablation backtests.

MEASUREMENT / ADVISORY LAYER ONLY, and explicitly NOT a ranking tool.
This module never writes to config.yaml, config/engines.yaml, or
research/results/registry.json — every number it produces is a
descriptive report an operator reads, never an automatic input to a live
trading decision or a promotion/demotion of any engine.

CLAUDE.md's dead list already buried "Enabling more engines (any)" twice
(engine_activation + H015: "every addition dilutes; subset selection is
universe-dependent noise") and this refinement work's own governing
rules forbid engine ranking outright. This module does NOT compute a
composite score, does NOT sort/highlight a "best" engine, and does NOT
feed any promotion path — it reports raw per-(engine, symbol) backtest
KPIs (trades, win rate, profit factor, Sharpe, drawdown, expectancy) so
an operator can read them, nothing more. Any decision to actually enable,
disable, or re-weight an engine still requires a pre-registered
hypothesis run through Mission Center's own Validation pipeline
(backtest/mission_validator.py) — this benchmark is not that pipeline
and does not substitute for it.

What "standalone" means here, precisely: each engine is run completely
alone (engines_enabled={this engine: True, every other: False}) with the
live confluence quorum overridden to 1 (confluence_overrides={
"min_engines_agreeing": 1}) via backtesting.backtest_engine.
build_engine_config_override — the SAME override channel every other
ad-hoc Mission Center run already uses, never a new mechanism. Without
that override a single engine can never reach config.yaml's live
min_engines_agreeing=2 quorum and would always report zero trades (the
exact bug Mission Center Research Rigor Phase 1 diagnosed and fixed for
missions). This means results here measure "how does this ONE engine's
own signal perform in isolation" — a DIFFERENT question from "how does
this engine contribute to the live multi-engine ensemble" — and are
NOT directly comparable to live/paper trading performance. The frontend
must state this caveat prominently, not bury it.

Reuses rather than duplicates: backtesting.backtest_engine.
build_engine_config_override/run_backtest/BacktestConfig/ENGINE_KEYS,
backtest.runner.load_symbol_data/trade_to_record,
backtest.metrics.calculate_metrics/json_safe.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# config/symbols.yaml's own asset_class field (merged verbatim into
# config['data']['twelve_data_symbols'] by utils.helpers.load_config) —
# same in-scope taxonomy backtest/price_benchmark.py already uses for
# symbol selection, reused here for consistency across the whole
# Provider/Engine benchmark family rather than inventing a second one.
_IN_SCOPE_ASSET_CLASSES = {"fx_major", "fx_minor", "metals", "crypto", "indices"}

_SMOKE_SYMBOL_CAP = 5

DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {"include_retired": False},
    "standard": {"include_retired": False},
    "deep": {"include_retired": True},
}


def _in_scope_symbols(config: dict, *, include_retired: bool = False) -> list[str]:
    excluded = set() if include_retired else {"RETIRED"}
    out: list[str] = []
    for s in config.get("data", {}).get("twelve_data_symbols", []):
        if s.get("asset_class") not in _IN_SCOPE_ASSET_CLASSES:
            continue
        if s.get("status") in excluded:
            continue
        internal = str(s.get("internal", "")).upper()
        if internal:
            out.append(internal)
    return out


@dataclass(frozen=True)
class EngineBenchmarkResult:
    engine: str
    symbol: str
    run_ok: bool
    error: str | None
    total_trades: int
    win_rate: float | None = None
    profit_factor: float | None = None  # float('inf') is a real, correct value here — json_safe() sanitizes at storage/API boundaries, never here
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float | None = None
    expectancy_r: float | None = None
    expectancy: float | None = None
    bars_used: int = 0
    data_start: str | None = None
    data_end: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_symbol(
    symbol: str,
    engines: list[str],
    data_dir: Path,
    start: str | None = None,
    end: str | None = None,
) -> list[EngineBenchmarkResult]:
    """Loads the symbol's real local OHLCV history ONCE and runs every
    requested engine standalone against it. One bad engine (a crash
    inside run_backtest/calculate_metrics) never aborts the rest —
    isolated per-engine try/except, matching backtest/price_benchmark.py's
    per-provider isolation and backtest/robustness.py's "report every
    point" convention. A missing/invalid dataset for this symbol is
    recorded as a real, explicit run_ok=False row for every requested
    engine — never silently skipped."""
    from backtest.runner import load_symbol_data

    try:
        df = load_symbol_data(symbol, data_dir, start, end)
    except (FileNotFoundError, ValueError) as exc:
        return [
            EngineBenchmarkResult(
                engine=e, symbol=symbol, run_ok=False, error=str(exc), total_trades=0,
            )
            for e in engines
        ]

    from backtest.metrics import calculate_metrics
    from backtest.runner import trade_to_record
    from backtesting.backtest_engine import (
        BacktestConfig,
        ENGINE_KEYS,
        build_engine_config_override,
        run_backtest,
    )

    results: list[EngineBenchmarkResult] = []
    for engine in engines:
        try:
            engine_config = build_engine_config_override(
                engines_enabled={e: (e == engine) for e in ENGINE_KEYS},
                confluence_overrides={"min_engines_agreeing": 1},
            )
            cfg = BacktestConfig.from_profile(symbol)
            bt = run_backtest(df, cfg, engine_config=engine_config)
            records = [trade_to_record(t, symbol) for t in bt.trades]
            metrics = calculate_metrics(records, initial_capital=cfg.initial_balance)
            results.append(EngineBenchmarkResult(
                engine=engine, symbol=symbol, run_ok=True, error=None,
                total_trades=metrics.total_trades,
                win_rate=metrics.win_rate,
                profit_factor=metrics.profit_factor,
                sharpe_ratio=metrics.sharpe_ratio,
                sortino_ratio=metrics.sortino_ratio,
                max_drawdown=metrics.max_drawdown,
                expectancy_r=metrics.expectancy_r,
                expectancy=metrics.expectancy,
                bars_used=len(df),
                data_start=str(df.index[0]),
                data_end=str(df.index[-1]),
            ))
        except Exception as exc:  # one bad engine must never crash the whole benchmark
            results.append(EngineBenchmarkResult(
                engine=engine, symbol=symbol, run_ok=False,
                error=f"{type(exc).__name__}: {exc}", total_trades=0,
                bars_used=len(df), data_start=str(df.index[0]), data_end=str(df.index[-1]),
            ))
    return results


def run_benchmark(
    run_id: str,
    profile: str,
    symbols: list[str] | None,
    engines: list[str] | None,
    data_dir: Path,
    start: str | None,
    end: str | None,
    config: dict,
    on_result: Callable[[EngineBenchmarkResult], None] | None = None,
) -> None:
    from backtesting.backtest_engine import ENGINE_KEYS

    prof = PROFILES[profile]
    resolved_symbols = symbols
    if resolved_symbols is None:
        resolved_symbols = _in_scope_symbols(config, include_retired=prof["include_retired"])
        if profile == "smoke":
            resolved_symbols = resolved_symbols[:_SMOKE_SYMBOL_CAP]
    resolved_engines = engines or list(ENGINE_KEYS)

    for symbol in resolved_symbols:
        for result in score_symbol(symbol, resolved_engines, data_dir, start, end):
            if on_result:
                on_result(result)


def main() -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    from utils.helpers import load_config

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", required=True, choices=list(PROFILES))
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--engines", nargs="+", default=None)
    ap.add_argument("--data-dir", type=str, default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--end", type=str, default=None)
    args = ap.parse_args()

    config = load_config()
    from storage import engine_benchmark as storage_mod

    prof = PROFILES[args.profile]
    symbols = args.symbols
    if symbols is None:
        symbols = _in_scope_symbols(config, include_retired=prof["include_retired"])
        if args.profile == "smoke":
            symbols = symbols[:_SMOKE_SYMBOL_CAP]
    from backtesting.backtest_engine import ENGINE_KEYS

    engines = args.engines or list(ENGINE_KEYS)
    data_dir = Path(args.data_dir)

    storage_mod.upsert_run(args.run_id, args.profile, symbols, engines, args.start, args.end)
    storage_mod.set_run_status(args.run_id, "running", started=True)
    try:
        def _on_result(result: EngineBenchmarkResult) -> None:
            storage_mod.record_result(args.run_id, result)
            print(f"{result.engine} {result.symbol}: trades={result.total_trades} run_ok={result.run_ok}")

        run_benchmark(
            args.run_id, args.profile, args.symbols, args.engines,
            data_dir, args.start, args.end, config, on_result=_on_result,
        )
        storage_mod.set_run_status(args.run_id, "finished", finished=True)
    except Exception as exc:
        storage_mod.set_run_status(args.run_id, "failed", error=str(exc), finished=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
