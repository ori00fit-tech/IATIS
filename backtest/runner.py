"""
backtest/runner.py
------------------
Orchestrates a full local backtest run: data loading → engine →
metrics → Monte Carlo → reports. This is the single entry point that
ties the validated engine (``backtesting.backtest_engine``) to the
analytics layer (``backtest.metrics`` / ``monte_carlo`` / ``report``).

Design (SOLID / composition over duplication):
- ``backtesting.backtest_engine`` remains the ONLY simulation engine
  (gap-aware exits, slippage, production-aligned parameters).
- ``backtest.metrics`` remains the ONLY metrics implementation.
- This module composes them through an explicit adapter
  (``trade_to_record``) instead of duplicating either model.
- All inputs are injected; no global state, no hidden I/O paths.

Integrity guarantees:
- Data is validated (``validate_ohlcv``) before simulation.
- A run where every pipeline call errored raises loudly upstream
  (engine guarantee) — it can never be reported as "0 trades".
- Results include the exact engine config used, so every report is
  reproducible.

CLI:
    python -m backtest.runner --symbols EURUSD GBPUSD --data-dir data
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.metrics import BacktestMetrics, TradeRecord, calculate_metrics
from backtest.monte_carlo import MonteCarloResult, run_monte_carlo
from backtest.report import generate_html_report
from backtesting.backtest_engine import (
    BacktestConfig,
    BacktestResult,
    Trade,
    run_backtest,
)
from core.data_validator import validate_ohlcv
from utils.logger import get_logger

logger = get_logger(__name__)

_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RunnerConfig:
    """Configuration for a multi-symbol backtest run.

    Attributes:
        symbols: internal symbol names (e.g. "EURUSD", "XAUUSD").
        data_dir: directory containing ``{SYMBOL}_H1_*.csv`` files
            produced by ``scripts/download_all_symbols.py``.
        output_dir: where JSON summaries are written.
        start / end: optional ISO dates to slice the dataset (inclusive).
        run_mc: run Monte Carlo robustness analysis per symbol.
        write_html: generate the HTML report per symbol.
        engine_overrides: per-run overrides applied to every symbol's
            ``BacktestConfig`` (e.g. {"min_rr": 2.0}). Symbol and
            pip_size are always set per symbol and cannot be overridden.
    """

    symbols: tuple[str, ...]
    data_dir: Path = Path("data")
    output_dir: Path = Path("reports")
    start: str | None = None
    end: str | None = None
    run_mc: bool = True
    write_html: bool = True
    engine_overrides: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SymbolRunResult:
    """Outcome of one symbol's backtest, fully self-describing."""

    symbol: str
    engine_result: BacktestResult
    metrics: BacktestMetrics
    trade_records: list[TradeRecord]
    monte_carlo: MonteCarloResult | None
    html_report: Path | None
    data_start: str
    data_end: str
    bars: int


# ─────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────

def find_symbol_csv(symbol: str, data_dir: Path) -> Path:
    """Locate the H1 CSV for ``symbol`` under ``data_dir``.

    Matches the ``{SYMBOL}_H1_*.csv`` pattern written by
    ``scripts/download_all_symbols.py``. If several files match
    (e.g. 1y and 2y downloads), the largest file is chosen and the
    choice is logged.

    Raises:
        FileNotFoundError: with the exact expected pattern, so a missing
            dataset is an actionable error rather than a silent skip.
    """
    matches = sorted(data_dir.glob(f"{symbol}_H1_*.csv"))
    if not matches:
        raise FileNotFoundError(
            f"No dataset for {symbol}: expected '{data_dir}/{symbol}_H1_*.csv' "
            f"(run: python scripts/download_all_symbols.py)"
        )
    chosen = max(matches, key=lambda p: p.stat().st_size)
    if len(matches) > 1:
        logger.info(f"{symbol}: {len(matches)} datasets found, using {chosen.name}")
    return chosen


def load_symbol_data(
    symbol: str,
    data_dir: Path,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Load and validate one symbol's OHLCV history.

    Returns a UTC-indexed, schema-validated DataFrame. Slicing by
    ``start``/``end`` happens BEFORE validation so the validated frame
    is exactly what the engine will see.

    Raises:
        ValueError: on schema problems or an empty post-slice frame.
    """
    path = find_symbol_csv(symbol, data_dir)
    df = pd.read_csv(path, index_col=0, parse_dates=True)

    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()

    if start is not None:
        df = df.loc[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df.loc[df.index <= pd.Timestamp(end, tz="UTC")]
    if df.empty:
        raise ValueError(f"{symbol}: no bars in range [{start}, {end}]")

    validate_ohlcv(df)
    return df


# ─────────────────────────────────────────────────────────────────────────
# Adapter: engine Trade → analytics TradeRecord
# ─────────────────────────────────────────────────────────────────────────

def trade_to_record(trade: Trade, symbol: str) -> TradeRecord:
    """Convert an engine ``Trade`` into an analytics ``TradeRecord``.

    Pure function; derives fields the analytics layer needs (planned
    and achieved R:R, win flag, holding period) from the engine's
    ground-truth prices — never from re-simulated values.
    """
    risk = abs(trade.entry_price - trade.stop_loss)
    reward = abs(trade.take_profit - trade.entry_price)
    rr_planned = reward / risk if risk > 0 else 0.0

    rr_actual = 0.0
    if trade.exit_price and risk > 0:
        signed = (
            trade.exit_price - trade.entry_price
            if trade.direction == "BUY"
            else trade.entry_price - trade.exit_price
        )
        rr_actual = signed / risk

    return TradeRecord(
        trade_id=f"{symbol}-{trade.entry_bar}",
        symbol=symbol,
        direction=trade.direction,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price if trade.exit_reason else None,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        position_size=trade.position_size,
        pnl_usd=trade.pnl_usd,
        pnl_pips=trade.pnl_pips,
        rr_planned=round(rr_planned, 2),
        rr_actual=round(rr_actual, 2),
        holding_bars=max(trade.exit_bar - trade.entry_bar, 0),
        exit_reason=trade.exit_reason,
        is_win=trade.pnl_usd > 0,
    )


# ─────────────────────────────────────────────────────────────────────────
# Per-symbol run
# ─────────────────────────────────────────────────────────────────────────

def _pip_size_for(symbol: str) -> float:
    """Pip size from the asset profile registry (asset-aware math)."""
    try:
        from core.asset_profiles import get_profile
        return float(get_profile(symbol).pip_size)
    except Exception:  # noqa: BLE001 — profile registry optional per symbol
        return 0.01 if symbol.endswith("JPY") else 0.0001


def run_symbol(
    symbol: str,
    df: pd.DataFrame,
    runner_config: RunnerConfig,
) -> SymbolRunResult:
    """Run the full backtest → metrics → MC → report chain for one symbol."""
    engine_cfg = BacktestConfig(
        symbol=symbol,
        pip_size=_pip_size_for(symbol),
        **runner_config.engine_overrides,
    )
    result = run_backtest(df, engine_cfg)

    records = [trade_to_record(t, symbol) for t in result.trades]
    metrics = calculate_metrics(records, initial_capital=engine_cfg.initial_balance)

    mc: MonteCarloResult | None = None
    if runner_config.run_mc:
        mc = run_monte_carlo(records, initial_capital=engine_cfg.initial_balance)

    html: Path | None = None
    if runner_config.write_html:
        html = generate_html_report(metrics, records, mc=mc, symbol=symbol)

    return SymbolRunResult(
        symbol=symbol,
        engine_result=result,
        metrics=metrics,
        trade_records=records,
        monte_carlo=mc,
        html_report=html,
        data_start=str(df.index[0]),
        data_end=str(df.index[-1]),
        bars=len(df),
    )


# ─────────────────────────────────────────────────────────────────────────
# Multi-symbol orchestration
# ─────────────────────────────────────────────────────────────────────────

def run_all(config: RunnerConfig) -> dict[str, SymbolRunResult]:
    """Run every configured symbol; one symbol's failure never aborts
    the rest (isolation mirrors the production scheduler's behavior).

    Returns:
        Mapping of symbol → SymbolRunResult for symbols that completed.
        Failures are logged with their cause and excluded.
    """
    results: dict[str, SymbolRunResult] = {}
    for symbol in config.symbols:
        try:
            df = load_symbol_data(symbol, config.data_dir, config.start, config.end)
            results[symbol] = run_symbol(symbol, df, config)
            m = results[symbol].metrics
            logger.info(
                f"{symbol}: trades={m.total_trades} WR={m.win_rate:.1%} "
                f"PF={m.profit_factor:.2f} maxDD={m.max_drawdown:.1f}%"
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            logger.error(f"{symbol}: run failed — {exc}")
    return results


def write_summary(results: dict[str, SymbolRunResult], output_dir: Path) -> Path:
    """Persist a machine-readable summary of the whole run (audit trail)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"backtest_summary_{stamp}.json"

    payload = {
        "generated_utc": stamp,
        "symbols": {
            s: {
                "data_start": r.data_start,
                "data_end": r.data_end,
                "bars": r.bars,
                "pipeline_runs": r.engine_result.total_runs,
                "pipeline_errors": r.engine_result.error_count,
                "trades": r.metrics.total_trades,
                "win_rate": round(r.metrics.win_rate, 4),
                "profit_factor": round(r.metrics.profit_factor, 3),
                "max_drawdown_pct": round(r.metrics.max_drawdown, 2),
                "expectancy_usd": round(r.metrics.expectancy, 2),
                "sharpe": round(r.metrics.sharpe_ratio, 3),
                "engine_config": {
                    "min_rr": r.engine_result.config.min_rr,
                    "sl_atr_multiplier": r.engine_result.config.sl_atr_multiplier,
                    "slippage_pips": r.engine_result.config.slippage_pips,
                    "commission_pips": r.engine_result.config.commission_pips,
                },
                "monte_carlo": (
                    {
                        "risk_of_ruin": r.monte_carlo.risk_of_ruin,
                        "p5_return": r.monte_carlo.p5_return,
                        "median_return": r.monte_carlo.median_return,
                    }
                    if r.monte_carlo and r.monte_carlo.simulations
                    else None
                ),
                "html_report": str(r.html_report) if r.html_report else None,
            }
            for s, r in results.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    logger.info(f"Summary written: {path}")
    return path


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="IATIS full backtest runner")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--start", default=None, help="ISO date, inclusive")
    parser.add_argument("--end", default=None, help="ISO date, inclusive")
    parser.add_argument("--no-mc", action="store_true")
    parser.add_argument("--no-html", action="store_true")
    args = parser.parse_args()

    config = RunnerConfig(
        symbols=tuple(args.symbols),
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        run_mc=not args.no_mc,
        write_html=not args.no_html,
    )
    results = run_all(config)
    if not results:
        raise SystemExit("No symbol completed — see errors above.")
    write_summary(results, config.output_dir)


if __name__ == "__main__":
    main()
