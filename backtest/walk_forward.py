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
from typing import Protocol

import pandas as pd

from backtest.metrics import calculate_metrics, json_safe
from backtest.runner import dataset_completeness_pct, load_symbol_data, physical_load_timeframe, trade_to_record
from backtesting.backtest_engine import ENGINE_KEYS, BacktestConfig, build_engine_config_override, run_backtest
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
        timeframes: Backtesting Lab Pro Phase B (2026-07-27) — ad-hoc
            per-run data.timeframes override (decision TF first). None
            = production config.yaml timeframes, unchanged.
        engines: Backtesting Lab Pro Phase C (2026-07-27) — ad-hoc
            per-run engine selection (explicit complete list). None =
            production config/engines.yaml enabled set, unchanged.
        indicators: Backtesting Lab Pro Phase D (2026-07-27) — ad-hoc
            per-run indicator filter/confirmation/score-weight specs.
            None = no indicator layer, unchanged from every prior
            phase's behavior.
        context_filters: Context Filters (2026-07-30) — ad-hoc per-run
            session/day-of-week/volatility-regime/market-regime/
            direction filter/confirmation/score-weight specs. None = no
            context filter layer, unchanged from every prior phase's
            behavior.
        confluence_overrides: Mission Center Research Rigor Phase 1
            (2026-08-06) — ad-hoc {"min_engines_agreeing",
            "min_informative_weight_share"} override. None = production
            config.yaml confluence block, unchanged. Needed so a
            single-engine candidate (quorum lowered below production 2)
            can be walk-forward validated without every window PRUNING
            with zero trades.
    """

    n_windows: int = 3
    min_pf: float = 1.5
    min_trades_per_window: int = 10
    warmup_bars: int = 210
    engine_overrides: dict = field(default_factory=dict)
    timeframes: tuple[str, ...] | None = None
    engines: tuple[str, ...] | None = None
    indicators: tuple[dict, ...] | None = None
    context_filters: tuple[dict, ...] | None = None
    confluence_overrides: dict | None = None

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
    gate_rejections: dict
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
        where evaluable bars begin. Every window's tradeable span is
        ``usable // n_windows`` bars except the LAST, which additionally
        absorbs the integer-division remainder (so ``sum(bars per
        window) == usable`` exactly, no bars silently dropped). Window
        1's warmup slice is drawn from the head of the dataset (there is
        no earlier data to draw from) rather than from a prior window's
        trading region — this affects what indicators can "see" going
        into window 1, not the SIZE of its tradeable span.

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
) -> WalkForwardResult:
    """Run walk-forward validation for one symbol.

    Args:
        symbol: internal symbol name.
        df: full validated OHLCV history (UTC index).
        wf_config: windowing and verdict thresholds.
        parameter_selector: optional optimizer hook (see Protocol). When
            provided, it receives ONLY the data strictly before each test
            window — enforced here, not trusted to the selector.
    """
    windows = split_windows(df, wf_config.n_windows, wf_config.warmup_bars)
    results: list[WindowResult] = []
    # Computed once — identical for every window.
    engine_config = build_engine_config_override(
        timeframes=list(wf_config.timeframes) if wf_config.timeframes else None,
        engines_enabled={e: (e in wf_config.engines) for e in ENGINE_KEYS} if wf_config.engines else None,
        indicators=list(wf_config.indicators) if wf_config.indicators else None,
        context_filters=list(wf_config.context_filters) if wf_config.context_filters else None,
        confluence_overrides=wf_config.confluence_overrides,
    )

    for k, (frame, test_start, test_end) in enumerate(windows):
        if parameter_selector is not None:
            train_df = df.loc[: test_start].iloc[:-1]  # strictly past data
            engine_cfg = parameter_selector(train_df, symbol)
        else:
            engine_cfg = BacktestConfig.from_profile(
                symbol,
                **{"warmup_bars": wf_config.warmup_bars, **wf_config.engine_overrides},
            )

        bt = run_backtest(frame, engine_cfg, engine_config=engine_config)
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
                gate_rejections=dict(bt.gate_rejections),
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
    start: str | None = None, end: str | None = None,
) -> dict[str, WalkForwardResult]:
    """Run walk-forward across symbols and persist a JSON report.

    One symbol's failure (missing data, invalid schema) is logged and
    excluded; it never aborts the suite. start/end (Backtesting Lab Pro
    Phase A, 2026-07-27): optional ISO-date dataset slice, same semantics
    as backtest.runner.load_symbol_data.

    Data Integrity Core (2026-08-19): a structurally-valid CSV can still
    be a genuinely PARTIAL dataset (real gaps well beyond weekend/session
    closures) — validate_ohlcv() alone cannot catch that, only
    dataset_completeness_pct() (the same session-aware gap classifier
    already gating the D1 warehouse's READY/INCOMPLETE status,
    storage.market_bars.MIN_COVERAGE_PCT_FOR_READY) can. Below that bar,
    the symbol is excluded from this walk-forward run the exact same way
    a missing/malformed file already is — a PARTIAL dataset must never
    silently become "evidence."
    """
    from storage.market_bars import MIN_COVERAGE_PCT_FOR_READY

    out: dict[str, WalkForwardResult] = {}
    for symbol in symbols:
        try:
            timeframe = physical_load_timeframe(wf_config.timeframes)
            df = load_symbol_data(symbol, data_dir, start, end, timeframe=timeframe)
            coverage_pct, _detail = dataset_completeness_pct(df, symbol, timeframe)
            if coverage_pct < MIN_COVERAGE_PCT_FOR_READY:
                raise ValueError(
                    f"dataset is PARTIAL — {coverage_pct:.1f}% real coverage, "
                    f"below the {MIN_COVERAGE_PCT_FOR_READY:.0f}% bar required "
                    f"to enter walk-forward evidence"
                )
            out[symbol] = run_walk_forward(symbol, df, wf_config)
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
                f"a {wf_config.warmup_bars}-bar untraded embargo/warmup. "
                "Gate parity with production: MQS + regime weights + "
                "MTF confirmation + H013 reversal veto all active unless "
                "engine_overrides disabled them (which marks an ablation)."
            ),
            "start": start,
            "end": end,
            "engine_overrides": wf_config.engine_overrides,
            "symbols": {s: r.to_dict() for s, r in out.items()},
        }
        path.write_text(json.dumps(json_safe(payload), indent=2))
        logger.info(
            f"Walk-forward report: {path} — {consistent}/{len(out)} CONSISTENT"
        )
    return out


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    from backtesting.backtest_engine import RISK_OVERRIDE_FIELDS

    # warmup_bars is deliberately its own dedicated flag, not folded into
    # the generic engine_overrides loop below: it dual-purposes here as
    # both the window-embargo size (WalkForwardConfig.warmup_bars) AND the
    # per-window BacktestConfig value (run_walk_forward already merges
    # them correctly, see the from_profile() call above) — one flag,
    # interpreted once, not two competing override paths.
    _WF_ENGINE_OVERRIDE_FIELDS = tuple(f for f in RISK_OVERRIDE_FIELDS if f != "warmup_bars")

    parser = argparse.ArgumentParser(description="IATIS walk-forward validation")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--windows", type=int, default=3)
    parser.add_argument("--min-pf", type=float, default=1.5)
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--warmup-bars", type=int, default=210)
    parser.add_argument("--start", default=None, help="ISO date, inclusive")
    parser.add_argument("--end", default=None, help="ISO date, inclusive")
    # Backtesting Lab Pro Phase A (2026-07-27) — ad-hoc per-run
    # BacktestConfig overrides, same surface as backtest.runner (minus
    # warmup_bars, its own dedicated flag above).
    parser.add_argument("--min-rr", type=float, default=None)
    parser.add_argument("--sl-atr-multiplier", type=float, default=None)
    parser.add_argument("--risk-per-trade", type=float, default=None)
    parser.add_argument("--commission-pips", type=float, default=None)
    parser.add_argument("--slippage-pips", type=float, default=None)
    parser.add_argument("--swap-pips-per-night", type=float, default=None)
    parser.add_argument("--initial-balance", type=float, default=None)
    parser.add_argument("--step-bars", type=int, default=None)
    # Backtesting Lab Pro Phase B (2026-07-27) — ad-hoc per-run
    # data.timeframes override (decision TF first).
    from core.timeframe_sync import SUPPORTED_TIMEFRAMES
    parser.add_argument("--timeframes", nargs="+", choices=SUPPORTED_TIMEFRAMES, default=None)
    # Backtesting Lab Pro Phase C (2026-07-27) — ad-hoc per-run engine
    # selection (explicit complete list of which engines run).
    parser.add_argument("--engines", nargs="+", choices=ENGINE_KEYS, default=None)
    # Backtesting Lab Pro Phase D (2026-07-27) — ad-hoc per-run indicator
    # filter/confirmation/score-weight specs (JSON-encoded).
    parser.add_argument("--indicators-json", type=str, default=None)
    # Mission Center Research Rigor Phase 1 (2026-08-06) — ad-hoc
    # confluence.min_engines_agreeing/min_informative_weight_share
    # override (JSON-encoded 2-key dict).
    parser.add_argument("--confluence-overrides-json", type=str, default=None)
    args = parser.parse_args()

    engine_overrides = {
        f: getattr(args, f) for f in _WF_ENGINE_OVERRIDE_FIELDS if getattr(args, f) is not None
    }

    indicators = None
    if args.indicators_json:
        from confluence.indicator_filters import parse_indicators_json
        try:
            indicators = parse_indicators_json(args.indicators_json)
        except ValueError as exc:
            parser.error(str(exc))

    confluence_overrides = None
    if args.confluence_overrides_json:
        import json
        try:
            confluence_overrides = json.loads(args.confluence_overrides_json)
        except ValueError as exc:
            parser.error(f"--confluence-overrides-json: invalid JSON: {exc}")

    results = run_walk_forward_suite(
        symbols=args.symbols,
        data_dir=args.data_dir,
        wf_config=WalkForwardConfig(
            n_windows=args.windows,
            min_pf=args.min_pf,
            min_trades_per_window=args.min_trades,
            warmup_bars=args.warmup_bars,
            engine_overrides=engine_overrides,
            timeframes=tuple(args.timeframes) if args.timeframes else None,
            engines=tuple(args.engines) if args.engines else None,
            indicators=indicators,
            confluence_overrides=confluence_overrides,
        ),
        start=args.start, end=args.end,
    )
    if not results:
        raise SystemExit("No symbol completed — see errors above.")


if __name__ == "__main__":
    main()
