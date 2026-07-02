"""
backtest/walk_forward.py
------------------------
Walk-forward (multi-window out-of-sample) validation on local datasets.

Methodology — and its honest limits
===================================
The dataset is split into N chronologically ordered, **disjoint** test
windows. Each window is simulated independently by the production-aligned
engine. A symbol is CONSISTENT only if every evaluable window clears the
profit-factor bar with enough trades to mean anything.

Anti-leakage measures (Quantitative Standards):
- **Disjoint windows**: no bar belongs to two test windows.
- **Embargo**: each window additionally receives the ``warmup_bars``
  immediately preceding it for indicator warmup ONLY — the engine never
  trades inside warmup, so no trade can span or straddle two windows,
  and no window's trades are influenced by another window's bars beyond
  read-only indicator history (unavoidable and legitimate: at bar N the
  live system also sees bars < N).
- **No optimization here**: parameters are FIXED (production config).
  This is therefore multi-period OOS *consistency* testing, not
  train/optimize walk-forward. That is the honest name for it. When
  ``backtest/optimizer.py`` exists, pass a ``parameter_selector`` to
  turn each window's preceding data into a training set; the interface
  is already in place.

Verdict semantics (no p-hacking):
- A window with fewer than ``min_trades_per_window`` closed trades is
  INSUFFICIENT — it neither passes nor fails, and the symbol as a whole
  cannot be CONSISTENT with any insufficient window. Reporting a PF
  computed over 2 trades as evidence would be fabrication by another name.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

import pandas as pd

from backtest.metrics import calculate_metrics
from backtest.runner import load_symbol_data, trade_to_record
from backtesting.backtest_engine import BacktestConfig, run_backtest
from utils.logger import get_logger

logger = get_logger(__name__)


class WindowVerdict(str, Enum):
    PASS = "PASS"                    # PF ≥ threshold with enough trades
    FAIL = "FAIL"                    # PF < threshold with enough trades
    INSUFFICIENT = "INSUFFICIENT"    # too few trades to judge


class SymbolVerdict(str, Enum):
    CONSISTENT = "CONSISTENT"        # every window PASS
    INCONSISTENT = "INCONSISTENT"    # at least one window FAIL
    INSUFFICIENT = "INSUFFICIENT"    # no FAIL, but ≥1 window unjudgeable


class ParameterSelector(Protocol):
    """Future optimizer hook: given the data PRECEDING a test window,
    return the engine config to use for that window. Receives only past
    data by construction — the runner slices it, the selector cannot
    reach forward."""

    def __call__(self, train_df: pd.DataFrame, symbol: str) -> BacktestConfig: ...


@dataclass(frozen=True)
class WalkForwardConfig:
    """Configuration for one walk-forward run.

    Attributes:
        n_windows: number of disjoint test windows (chronological).
        min_pf: profit-factor bar a window must clear to PASS.
        min_trades_per_window: below this, a window is INSUFFICIENT.
        warmup_bars: indicator warmup prepended to each window
            (embargo zone — never traded). Must be ≥ the engine's own
            warmup so window 1 behaves identically to the others.
        engine_overrides: applied to the BacktestConfig of every window
            (ignored for windows where a parameter_selector is used).
    """

    n_windows: int = 3
    min_pf: float = 1.5
    min_trades_per_window: int = 10
    warmup_bars: int = 210
    engine_overrides: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.n_windows < 2:
            raise ValueError("walk-forward needs at least 2 windows")
        if self.min_trades_per_window < 1:
            raise ValueError("min_trades_per_window must be >= 1")


@dataclass(frozen=True)
class WindowResult:
    """One test window's outcome, self-describing for the audit trail."""

    index: int
    start: str
    end: str
    bars: int
    trades: int
    profit_factor: float
    win_rate: float
    max_drawdown_pct: float
    expectancy_usd: float
    pipeline_errors: int
    verdict: WindowVerdict


@dataclass(frozen=True)
class WalkForwardResult:
    symbol: str
    windows: list[WindowResult]
    verdict: SymbolVerdict
    config: WalkForwardConfig

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict.value,
            "min_pf": self.config.min_pf,
            "min_trades_per_window": self.config.min_trades_per_window,
            "windows": [vars(w) | {"verdict": w.verdict.value} for w in self.windows],
        }


def split_windows(
    df: pd.DataFrame, n_windows: int, warmup_bars: int
) -> list[tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]]:
    """Split ``df`` into N disjoint test windows, each prepended with its
    embargo/warmup slice.

    Returns:
        List of (window_frame, test_start, test_end). ``window_frame``
        includes ``warmup_bars`` of prior history; ``test_start`` marks
        where evaluable bars begin. Window 1's warmup comes from the
        head of the dataset, so its tradeable span is shorter — this is
        stated in results rather than papered over.

    Raises:
        ValueError: if the dataset cannot yield N windows each with at
            least ``warmup_bars`` tradeable bars — a run on inadequate
            data must fail loudly, not produce hollow verdicts.
    """
    usable = len(df) - warmup_bars
    per_window = usable // n_windows
    if per_window < warmup_bars:
        raise ValueError(
            f"Dataset too small for {n_windows} windows: {len(df)} bars gives "
            f"{per_window} tradeable bars/window; need ≥ {warmup_bars}."
        )

    windows: list[tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]] = []
    for k in range(n_windows):
        test_lo = warmup_bars + k * per_window
        test_hi = warmup_bars + (k + 1) * per_window if k < n_windows - 1 else len(df)
        frame = df.iloc[test_lo - warmup_bars : test_hi]
        windows.append((frame, df.index[test_lo], df.index[test_hi - 1]))
    return windows


def run_walk_forward(
    symbol: str,
    df: pd.DataFrame,
    wf_config: WalkForwardConfig,
    parameter_selector: ParameterSelector | None = None,
    pip_size: float = 0.0001,
) -> WalkForwardResult:
    """Run walk-forward validation for one symbol.

    Args:
        symbol: internal symbol name.
        df: full validated OHLCV history (UTC index).
        wf_config: windowing and verdict thresholds.
        parameter_selector: optional optimizer hook (see Protocol). When
            provided, it receives ONLY the data strictly before each test
            window — enforced here, not trusted to the selector.
        pip_size: asset pip size for the engine.
    """
    windows = split_windows(df, wf_config.n_windows, wf_config.warmup_bars)
    results: list[WindowResult] = []

    for k, (frame, test_start, test_end) in enumerate(windows):
        if parameter_selector is not None:
            train_df = df.loc[: test_start].iloc[:-1]  # strictly past data
            engine_cfg = parameter_selector(train_df, symbol)
        else:
            engine_cfg = BacktestConfig(
                symbol=symbol,
                pip_size=pip_size,
                warmup_bars=wf_config.warmup_bars,
                **wf_config.engine_overrides,
            )

        bt = run_backtest(frame, engine_cfg)
        records = [trade_to_record(t, symbol) for t in bt.trades]
        m = calculate_metrics(records, initial_capital=engine_cfg.initial_balance)

        if m.total_trades < wf_config.min_trades_per_window:
            verdict = WindowVerdict.INSUFFICIENT
        elif m.profit_factor >= wf_config.min_pf:
            verdict = WindowVerdict.PASS
        else:
            verdict = WindowVerdict.FAIL

        results.append(
            WindowResult(
                index=k + 1,
                start=str(test_start),
                end=str(test_end),
                bars=len(frame) - wf_config.warmup_bars,
                trades=m.total_trades,
                profit_factor=round(m.profit_factor, 3),
                win_rate=round(m.win_rate, 4),
                max_drawdown_pct=round(m.max_drawdown, 2),
                expectancy_usd=round(m.expectancy, 2),
                pipeline_errors=bt.error_count,
                verdict=verdict,
            )
        )
        logger.info(
            f"{symbol} W{k+1} [{test_start:%Y-%m-%d} → {test_end:%Y-%m-%d}]: "
            f"trades={m.total_trades} PF={m.profit_factor:.2f} → {verdict.value}"
        )

    if any(w.verdict is WindowVerdict.FAIL for w in results):
        symbol_verdict = SymbolVerdict.INCONSISTENT
    elif any(w.verdict is WindowVerdict.INSUFFICIENT for w in results):
        symbol_verdict = SymbolVerdict.INSUFFICIENT
    else:
        symbol_verdict = SymbolVerdict.CONSISTENT

    return WalkForwardResult(symbol, results, symbol_verdict, wf_config)


def run_walk_forward_suite(
    symbols: list[str],
    data_dir: Path,
    wf_config: WalkForwardConfig,
    output_dir: Path = Path("reports"),
    pip_size_fn: Callable[[str], float] | None = None,
) -> dict[str, WalkForwardResult]:
    """Run walk-forward across symbols and persist a JSON report.

    One symbol's failure (missing data, invalid schema) is logged and
    excluded; it never aborts the suite.
    """
    from backtest.runner import _pip_size_for

    pip_fn = pip_size_fn or _pip_size_for
    out: dict[str, WalkForwardResult] = {}
    for symbol in symbols:
        try:
            df = load_symbol_data(symbol, data_dir)
            out[symbol] = run_walk_forward(
                symbol, df, wf_config, pip_size=pip_fn(symbol)
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            logger.error(f"{symbol}: walk-forward failed — {exc}")

    if out:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"walk_forward_{stamp}.json"
        consistent = sum(
            1 for r in out.values() if r.verdict is SymbolVerdict.CONSISTENT
        )
        payload = {
            "generated_utc": stamp,
            "consistent": consistent,
            "evaluated": len(out),
            "note": (
                "Fixed-parameter multi-period OOS consistency test "
                "(no per-window optimization). Windows are disjoint with "
                f"a {wf_config.warmup_bars}-bar untraded embargo/warmup."
            ),
            "symbols": {s: r.to_dict() for s, r in out.items()},
        }
        path.write_text(json.dumps(payload, indent=2))
        logger.info(
            f"Walk-forward report: {path} — {consistent}/{len(out)} CONSISTENT"
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="IATIS walk-forward validation")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--windows", type=int, default=3)
    parser.add_argument("--min-pf", type=float, default=1.5)
    parser.add_argument("--min-trades", type=int, default=10)
    args = parser.parse_args()

    results = run_walk_forward_suite(
        symbols=args.symbols,
        data_dir=args.data_dir,
        wf_config=WalkForwardConfig(
            n_windows=args.windows,
            min_pf=args.min_pf,
            min_trades_per_window=args.min_trades,
        ),
    )
    if not results:
        raise SystemExit("No symbol completed — see errors above.")


if __name__ == "__main__":
    main()
