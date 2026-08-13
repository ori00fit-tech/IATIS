"""
backtest/robustness.py
------------------------
Parameter-sensitivity ("robustness") testing on local datasets.

Not out-of-sample validation — see backtest/walk_forward.py for that.
This holds the dataset FIXED and perturbs one production cost/risk
parameter at a time around its actual per-symbol frozen value
(BacktestConfig.from_profile), measuring how much profit factor moves.
A configuration that collapses under a small, realistic perturbation
(e.g. spread 20% worse than measured) is fragile even if its point
estimate looks good; a configuration whose PF barely moves across the
sweep is robust to the modeling uncertainty inherent in these
parameters (the exact spread paid varies by broker/session, the ATR
stop multiplier is itself a frozen research choice, not a law of
nature).

This does NOT change any live parameter and does NOT propose a new
value — CLAUDE.md rule 6 (no entries/exits/thresholds changes
mid-sample) governs what actually trades, not a read-only sensitivity
report. A parameter found fragile here is an INPUT to a new
pre-registered hypothesis, never license to change it directly.

Verdict semantics (no p-hacking):
- A parameter whose baseline point (multiplier 1.0) has fewer than
  ``min_trades`` closed trades is INSUFFICIENT — there is nothing to
  judge sensitivity against.
- STABLE: profit factor stays within +/-30% (relative) of the baseline
  PF at every sweep point that itself has enough trades to be
  meaningful. 30% is a deliberately loose band — this is a coarse
  fragility screen, not a precision estimate.
- SENSITIVE: at least one sufficiently-traded sweep point falls outside
  that band.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backtest.metrics import calculate_metrics, json_safe
from backtest.runner import load_symbol_data, physical_load_timeframe, trade_to_record
from backtesting.backtest_engine import ENGINE_KEYS, BacktestConfig, build_engine_config_override, run_backtest
from utils.logger import get_logger

logger = get_logger(__name__)

# Relative multipliers applied to each parameter's actual frozen value
# for this symbol. 1.0 (the baseline / no perturbation) must always be
# present so every sweep has something to compare against.
DEFAULT_MULTIPLIERS: tuple[float, ...] = (0.5, 0.8, 1.0, 1.2, 1.5)

# Only cost/risk knobs that /research/scenario-config already documents
# as real, legitimate per-run overrides — never a gate flag (disabling a
# gate is an ablation, not a sensitivity perturbation) and never a
# structural field like warmup_bars.
SWEEP_PARAMS: tuple[str, ...] = ("sl_atr_multiplier", "commission_pips", "slippage_pips", "min_rr")

_STABLE_BAND = 0.30  # +/-30% relative PF tolerance — see module docstring


@dataclass(frozen=True)
class RobustnessConfig:
    multipliers: tuple[float, ...] = DEFAULT_MULTIPLIERS
    params: tuple[str, ...] = SWEEP_PARAMS
    min_trades: int = 10
    engine_overrides: dict = field(default_factory=dict)
    # Backtesting Lab Pro Phase B (2026-07-27) — ad-hoc per-run
    # data.timeframes override (decision TF first). None = production
    # config.yaml timeframes, unchanged.
    timeframes: tuple[str, ...] | None = None
    # Backtesting Lab Pro Phase C (2026-07-27) — ad-hoc per-run engine
    # selection (explicit complete list). None = production config/
    # engines.yaml enabled set, unchanged.
    engines: tuple[str, ...] | None = None
    # Backtesting Lab Pro Phase D (2026-07-27) — ad-hoc per-run
    # indicator filter/confirmation/score-weight specs. None = no
    # indicator layer, unchanged from every prior phase's behavior.
    indicators: tuple[dict, ...] | None = None
    # Context Filters (2026-07-30) — ad-hoc per-run session/day-of-week/
    # volatility-regime/market-regime/direction filter/confirmation/
    # score-weight specs. None = no context filter layer, unchanged from
    # every prior phase's behavior.
    context_filters: tuple[dict, ...] | None = None
    # Mission Center Research Rigor Phase 1 (2026-08-06) — ad-hoc
    # {"min_engines_agreeing", "min_informative_weight_share"} override.
    # None = production config.yaml confluence block, unchanged. Needed
    # so a single-engine candidate (quorum lowered below production 2)
    # can be re-swept without every point PRUNING with zero trades.
    confluence_overrides: dict | None = None

    def __post_init__(self) -> None:
        if 1.0 not in self.multipliers:
            raise ValueError("multipliers must include 1.0 as the baseline point")
        unknown = set(self.params) - set(SWEEP_PARAMS)
        if unknown:
            raise ValueError(f"unsupported sweep param(s) {sorted(unknown)} — choose from {SWEEP_PARAMS}")
        if self.min_trades < 1:
            raise ValueError("min_trades must be >= 1")


@dataclass(frozen=True)
class SweepPoint:
    multiplier: float
    value: float
    trades: int
    profit_factor: float
    win_rate: float
    max_drawdown_pct: float
    sufficient: bool  # trades >= min_trades


@dataclass(frozen=True)
class ParamSweepResult:
    param: str
    baseline_value: float
    baseline_pf: float
    points: list[SweepPoint]
    verdict: str  # STABLE | SENSITIVE | INSUFFICIENT

    def to_dict(self) -> dict:
        return {
            "param": self.param,
            "baseline_value": self.baseline_value,
            "baseline_pf": self.baseline_pf,
            "verdict": self.verdict,
            "points": [vars(p) for p in self.points],
        }


@dataclass(frozen=True)
class RobustnessResult:
    symbol: str
    sweeps: list[ParamSweepResult]
    config: RobustnessConfig

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "min_trades": self.config.min_trades,
            "multipliers": list(self.config.multipliers),
            "sweeps": [s.to_dict() for s in self.sweeps],
        }


def _run_point(
    df, symbol: str, param: str, value: float, engine_overrides: dict,
    engine_config: dict | None = None,
) -> tuple[int, float, float, float]:
    """Run one sweep point, returning (trades, profit_factor, win_rate, max_drawdown_pct)."""
    cfg = BacktestConfig.from_profile(symbol, **{**engine_overrides, param: value})
    bt = run_backtest(df, cfg, engine_config=engine_config)
    records = [trade_to_record(t, symbol) for t in bt.trades]
    m = calculate_metrics(records, initial_capital=cfg.initial_balance)
    return m.total_trades, m.profit_factor, m.win_rate, m.max_drawdown


def run_param_sweep(
    symbol, df, param: str, rc: RobustnessConfig,
) -> ParamSweepResult:
    """Sweep one parameter across ``rc.multipliers`` for one symbol."""
    baseline_cfg = BacktestConfig.from_profile(symbol, **rc.engine_overrides)
    baseline_value = getattr(baseline_cfg, param)
    # Computed once per sweep — identical for every point in it.
    engine_config = build_engine_config_override(
        timeframes=list(rc.timeframes) if rc.timeframes else None,
        engines_enabled={e: (e in rc.engines) for e in ENGINE_KEYS} if rc.engines else None,
        indicators=list(rc.indicators) if rc.indicators else None,
        context_filters=list(rc.context_filters) if rc.context_filters else None,
        confluence_overrides=rc.confluence_overrides,
    )

    points: list[SweepPoint] = []
    baseline_pf = 0.0
    for mult in rc.multipliers:
        value = baseline_value * mult
        trades, pf, wr, mdd = _run_point(df, symbol, param, value, rc.engine_overrides, engine_config)
        sufficient = trades >= rc.min_trades
        points.append(SweepPoint(
            multiplier=mult, value=round(value, 6), trades=trades,
            profit_factor=round(pf, 3), win_rate=round(wr, 4),
            max_drawdown_pct=round(mdd, 2), sufficient=sufficient,
        ))
        if mult == 1.0:
            baseline_pf = pf

    baseline_point = next(p for p in points if p.multiplier == 1.0)
    if not baseline_point.sufficient:
        verdict = "INSUFFICIENT"
    else:
        lo, hi = baseline_pf * (1 - _STABLE_BAND), baseline_pf * (1 + _STABLE_BAND)
        sensitive = any(
            p.sufficient and not (lo <= p.profit_factor <= hi)
            for p in points
        )
        verdict = "SENSITIVE" if sensitive else "STABLE"

    logger.info(f"{symbol} {param}: baseline_pf={baseline_pf:.2f} -> {verdict}")
    return ParamSweepResult(
        param=param, baseline_value=round(baseline_value, 6),
        baseline_pf=round(baseline_pf, 3), points=points, verdict=verdict,
    )


def run_robustness(symbol: str, df, rc: RobustnessConfig) -> RobustnessResult:
    """Run every configured parameter sweep for one symbol."""
    sweeps = [run_param_sweep(symbol, df, param, rc) for param in rc.params]
    return RobustnessResult(symbol=symbol, sweeps=sweeps, config=rc)


def run_robustness_suite(
    symbols: list[str], data_dir: Path, rc: RobustnessConfig,
    output_dir: Path = Path("reports"),
    start: str | None = None, end: str | None = None,
) -> dict[str, RobustnessResult]:
    """Run robustness sweeps across symbols and persist a JSON report.

    One symbol's failure (missing data, invalid schema) is logged and
    excluded; it never aborts the suite. start/end (Backtesting Lab Pro
    Phase A, 2026-07-27): optional ISO-date dataset slice, same semantics
    as backtest.runner.load_symbol_data.
    """
    out: dict[str, RobustnessResult] = {}
    for symbol in symbols:
        try:
            df = load_symbol_data(symbol, data_dir, start, end, timeframe=physical_load_timeframe(rc.timeframes))
            out[symbol] = run_robustness(symbol, df, rc)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            logger.error(f"{symbol}: robustness sweep failed — {exc}")

    if out:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"robustness_{stamp}.json"
        stable = sum(
            1 for r in out.values()
            if all(s.verdict == "STABLE" for s in r.sweeps)
        )
        payload = {
            "generated_utc": stamp,
            "all_params_stable": stable,
            "evaluated": len(out),
            "note": (
                "Parameter-sensitivity screen, NOT out-of-sample validation "
                "(see backtest/walk_forward.py for that). Perturbs one cost/"
                "risk parameter at a time around its frozen production "
                f"value; +/-{int(_STABLE_BAND * 100)}% relative PF band = STABLE. "
                "Does not change any live parameter and does not itself "
                "justify changing one — CLAUDE.md rule 6."
            ),
            "start": start,
            "end": end,
            "engine_overrides": rc.engine_overrides,
            "symbols": {s: r.to_dict() for s, r in out.items()},
        }
        path.write_text(json.dumps(json_safe(payload), indent=2))
        logger.info(f"Robustness report: {path} — {stable}/{len(out)} all-STABLE")
    return out


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    from backtesting.backtest_engine import RISK_OVERRIDE_FIELDS

    parser = argparse.ArgumentParser(description="IATIS parameter-sensitivity (robustness) sweep")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--params", nargs="+", default=list(SWEEP_PARAMS), choices=SWEEP_PARAMS)
    parser.add_argument("--multipliers", nargs="+", type=float, default=list(DEFAULT_MULTIPLIERS))
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--start", default=None, help="ISO date, inclusive")
    parser.add_argument("--end", default=None, help="ISO date, inclusive")
    # Backtesting Lab Pro Phase A (2026-07-27) — ad-hoc per-run
    # BacktestConfig overrides, same 9-field surface as backtest.runner.
    parser.add_argument("--min-rr", type=float, default=None)
    parser.add_argument("--sl-atr-multiplier", type=float, default=None)
    parser.add_argument("--risk-per-trade", type=float, default=None)
    parser.add_argument("--commission-pips", type=float, default=None)
    parser.add_argument("--slippage-pips", type=float, default=None)
    parser.add_argument("--swap-pips-per-night", type=float, default=None)
    parser.add_argument("--initial-balance", type=float, default=None)
    parser.add_argument("--warmup-bars", type=int, default=None)
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
        f: getattr(args, f) for f in RISK_OVERRIDE_FIELDS if getattr(args, f) is not None
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

    results = run_robustness_suite(
        symbols=args.symbols,
        data_dir=args.data_dir,
        rc=RobustnessConfig(
            multipliers=tuple(args.multipliers),
            params=tuple(args.params),
            min_trades=args.min_trades,
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
