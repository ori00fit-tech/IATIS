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
from typing import Any

import pandas as pd

from backtest.metrics import BacktestMetrics, TradeRecord, calculate_metrics, json_safe
from backtest.monte_carlo import MonteCarloResult, run_monte_carlo
from backtest.report import generate_html_report
from backtesting.backtest_engine import (
    ENGINE_KEYS,
    BacktestConfig,
    BacktestResult,
    Trade,
    build_engine_config_override,
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
        timeframes: Backtesting Lab Pro Phase B (2026-07-27) — ad-hoc
            per-run override of data.timeframes (decision TF first,
            e.g. ("H1",) or ("H4","D1","H1")). None = use the real
            config.yaml timeframes, unchanged.
        engines: Backtesting Lab Pro Phase C (2026-07-27) — ad-hoc
            per-run override of which engines run (explicit complete
            list, e.g. ("nnfx","price_action")). None = use the real
            config/engines.yaml enabled set, unchanged.
        indicators: Backtesting Lab Pro Phase D (2026-07-27) — ad-hoc
            per-run indicator filter/confirmation/score-weight specs
            (confluence.indicator_filters.IndicatorSpec-shaped dicts).
            None = no indicator layer, unchanged from every prior
            phase's behavior. Indicators can only filter/confirm/weight
            a decision the engine vote already produced — see
            confluence/indicator_filters.py's module docstring.
        confluence_overrides: Mission Center Research Rigor Phase 1
            (2026-08-06) — ad-hoc {"min_engines_agreeing",
            "min_informative_weight_share"} override. None = production
            config.yaml confluence block, unchanged. Exists so a
            single-engine run (e.g. testing "SMC alone") can lower the
            quorum below the production default of 2, which would
            otherwise make agree_count>=2 mathematically unreachable and
            every trial would PRUNE with zero trades.
    """

    symbols: tuple[str, ...]
    data_dir: Path = Path("data")
    output_dir: Path = Path("reports")
    start: str | None = None
    end: str | None = None
    run_mc: bool = True
    write_html: bool = True
    engine_overrides: dict = field(default_factory=dict)
    timeframes: tuple[str, ...] | None = None
    engines: tuple[str, ...] | None = None
    indicators: tuple[dict, ...] | None = None
    confluence_overrides: dict | None = None


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
    dataset_descriptor: dict | None = None


# ─────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────

def find_symbol_csv(symbol: str, data_dir: Path, timeframe: str = "H1") -> Path:
    """Locate the ``timeframe`` dataset for ``symbol`` under ``data_dir``.

    ``timeframe`` defaults to "H1" — every pre-2026-08-11 caller omits it
    and keeps loading exactly the same file it always did (zero behavior
    change). Only an explicit "M15" request (Backtesting Lab Pro's
    ``timeframes`` override, when its base is genuinely M15) looks for a
    real M15-native file instead — see ``scripts/download_dukascopy_
    history.py``/``download_ctrader_fx_history.py``/``download_twelve_
    data_history.py`` for M15 producers. H4/D1 are deliberately NOT
    separate physical timeframes here: they are always derived from the
    H1 file by resampling (``core/timeframe_sync.py``), matching this
    project's existing, deliberate "H1 is the only physically-downloaded
    intraday granularity below H4" convention — requesting
    ``timeframe="H4"``/``"D1"`` would be wrong (no such file is ever
    produced) and is never done by any caller.

    Matches the ``{SYMBOL}_{TIMEFRAME}_*.csv`` pattern written by
    ``scripts/download_all_symbols.py`` (H1) / the M15 download scripts
    above, AND ``{SYMBOL}_{TIMEFRAME}_*.parquet`` (Phase 4a upload
    endpoint, ``execution/routes/research.py``'s ``upload_dataset``) —
    both extensions are considered together. If several files match
    (e.g. 1y and 2y downloads, or a CSV and an uploaded Parquet), the
    largest file is chosen and the choice is logged.

    Raises:
        FileNotFoundError: with the exact expected pattern, so a missing
            dataset is an actionable error rather than a silent skip.
    """
    matches = sorted(data_dir.glob(f"{symbol}_{timeframe}_*.csv")) + sorted(data_dir.glob(f"{symbol}_{timeframe}_*.parquet"))
    if not matches:
        hint = "python scripts/download_all_symbols.py" if timeframe == "H1" else f"an M15 download script for {symbol}"
        raise FileNotFoundError(
            f"No {timeframe} dataset for {symbol}: expected '{data_dir}/{symbol}_{timeframe}_*.csv' "
            f"(run: {hint})"
        )
    chosen = max(matches, key=lambda p: p.stat().st_size)
    if len(matches) > 1:
        logger.info(f"{symbol}: {len(matches)} {timeframe} datasets found, using {chosen.name}")
    return chosen


def load_symbol_data(
    symbol: str,
    data_dir: Path,
    start: str | None = None,
    end: str | None = None,
    timeframe: str = "H1",
) -> pd.DataFrame:
    """Load and validate one symbol's OHLCV history at ``timeframe``
    (default "H1" — see ``find_symbol_csv``'s docstring for why H4/D1 are
    never passed here).

    Returns a UTC-indexed, schema-validated DataFrame. Slicing by
    ``start``/``end`` happens BEFORE validation so the validated frame
    is exactly what the engine will see.

    Raises:
        ValueError: on schema problems or an empty post-slice frame.
    """
    path = find_symbol_csv(symbol, data_dir, timeframe=timeframe)
    # Phase 4a: an uploaded dataset may be Parquet — branch the read call
    # on suffix, everything downstream (tz-localize, sort, validate) is
    # identical for both formats.
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
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


def dataset_completeness_pct(
    df: pd.DataFrame, symbol: str, timeframe: str, config: dict | None = None,
) -> tuple[float, dict]:
    """Data Integrity Core (2026-08-19) — the CSV-loading-path equivalent
    of storage/market_bars.py's D1-only completeness gate
    (MIN_COVERAGE_PCT_FOR_READY). Reuses the exact same session-aware gap
    classifier (backtest.price_benchmark.completeness_score, which already
    excludes real weekend/market closures from the gap count) rather than
    a second implementation — a real 40%-gap CSV and a real 40%-gap D1
    dataset are now judged by the identical formula.

    `df` must already be the loaded, structurally-valid frame (call this
    AFTER validate_ohlcv, not instead of it — this checks completeness,
    not structural correctness). `config` defaults to a fresh
    load_config() call when omitted, matching every other config-reading
    helper's default in this codebase.
    """
    from backtest.price_benchmark import asset_class_for_symbol, completeness_score

    if config is None:
        from utils.helpers import load_config

        config = load_config()
    asset_class = asset_class_for_symbol(symbol, config)
    return completeness_score(df, timeframe, asset_class)


def build_dataset_descriptor(
    df: pd.DataFrame, symbol: str, timeframe: str, csv_path: Path, config: dict | None = None,
) -> dict[str, Any]:
    """Data Integrity Core, Slice 2 (Fingerprint Binding). The one
    dataset-identity block every evidence-report writer (write_summary,
    walk_forward's suite writer, robustness's suite writer) embeds, so a
    research result can always answer "what data was this actually built
    on" instead of only carrying a bare filename.

    Combines two ALREADY-EXISTING primitives, neither reimplemented:
    dataset_completeness_pct() (Slice 1, this module) for real gap-aware
    coverage, and research.manifest.dataset_fingerprint() — the house
    reproducibility fingerprint mission_runner.py/mission_validator.py
    already use for per-trial/validation fingerprints — for the SHA256
    identity. `provider` is parsed straight from csv_path's own
    `{SYMBOL}_{TIMEFRAME}_{provider}.csv` naming convention (find_symbol_
    csv's own documented pattern) — no new metadata source invented.
    """
    from research.manifest import dataset_fingerprint

    completeness_pct, _detail = dataset_completeness_pct(df, symbol, timeframe, config=config)
    fingerprint = dataset_fingerprint(csv_path, df)
    stem = csv_path.stem  # "{SYMBOL}_{TIMEFRAME}_{provider}"
    prefix = f"{symbol}_{timeframe}_"
    provider = stem[len(prefix):] if stem.startswith(prefix) else stem

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "provider": provider,
        "from": str(df.index[0]) if len(df) else None,
        "to": str(df.index[-1]) if len(df) else None,
        "row_count": int(len(df)),
        "completeness_pct": round(completeness_pct, 2),
        "dataset_fingerprint": fingerprint["sha256"],
    }


def physical_load_timeframe(timeframes: tuple[str, ...] | None) -> str:
    """The REAL on-disk granularity to load for a run, given its
    (possibly overridden) ``RunnerConfig.timeframes``/``RobustnessConfig.
    timeframes``/``WalkForwardConfig.timeframes``/``ValidationConfig``-
    style decision-timeframe list.

    Only an explicit "M15" base changes what gets loaded — H1/H4/D1 (or
    no override at all, ``timeframes is None``) all still resolve to the
    H1 file, byte-identical to every call site's behavior before this
    function existed: H4/D1 backtesting has always been served by
    resampling H1 (see ``find_symbol_csv``'s docstring), and no producer
    anywhere in this codebase writes a native H4/D1 file, so requesting
    either as the physical load timeframe would only ever raise
    ``FileNotFoundError`` — never done.

    BUG-020 fix (2026-08-12, ``reports/forensic/13_CONFIRMED_BUGS.md``):
    now wired into ``backtest/mission_runner.py`` too — Mission Center's
    Optuna-driven search calls this once per DISTINCT ``timeframes``
    combination in the search space (a mission may mix, e.g., an M15
    hypothesis bundle with an H1 one), loads one physical dataframe per
    result, and picks the correct one per trial via this function applied
    to that trial's own resolved ``point["timeframes"]`` — see
    ``mission_runner._distinct_physical_timeframes``/``_run_symbol``.
    Previously every trial silently reused whichever single H1 dataframe
    was loaded once per symbol regardless of its own declared timeframe,
    making any research that varied timeframe across trials produce
    identical results no matter what was requested."""
    if timeframes and timeframes[0] == "M15":
        return "M15"
    return "H1"


# ─────────────────────────────────────────────────────────────────────────
# Adapter: engine Trade → analytics TradeRecord
# ─────────────────────────────────────────────────────────────────────────

def _build_features(decision: dict, entry_price: float, atr_val: float | None) -> dict:
    """Feature Mining / Hypothesis Discovery Phase 1 (2026-07-30) —
    flattens decision-time context into TradeRecord.features. Every field
    is OMITTED (never fabricated as 0/None-filled) when its source gate
    was off for this run — see TradeRecord.features' own docstring for the
    flat-vs-nested rationale. Pure function, no side effects."""
    features: dict = {}
    engine_votes = {e["engine"]: e for e in decision.get("engines", [])}

    def _raw(engine: str) -> dict:
        return (engine_votes.get(engine) or {}).get("raw") or {}

    if decision.get("regime") is not None:
        features["regime"] = decision["regime"]
    if decision.get("volatility") is not None:
        features["volatility"] = decision["volatility"]
    if decision.get("atr_value") is not None:
        features["atr_value"] = decision["atr_value"]
    if decision.get("info_share") is not None:
        features["info_share"] = decision["info_share"]
    if decision.get("session"):
        features["session"] = decision["session"]

    mtf = decision.get("mtf")
    if mtf:
        features["mtf_d1_bias"] = mtf.get("d1_bias")
        features["mtf_d1_adx"] = mtf.get("d1_adx")
        features["mtf_confirming"] = mtf.get("confirming")

    veto = decision.get("reversal_veto")
    if veto:
        features["veto_reversal_count"] = veto.get("reversal_count")
        features["veto_trend_bias"] = veto.get("trend_bias")
        features["veto_reversal_bias"] = veto.get("reversal_bias")
        features["veto_confidence_multiplier"] = veto.get("confidence_multiplier")
        features["veto_soft_veto"] = veto.get("soft_veto")

    contradiction_reasons = decision.get("contradiction_reasons")
    if contradiction_reasons is not None:
        features["contradiction_count"] = len(contradiction_reasons)

    mqs = decision.get("mqs")
    if mqs:
        features["mqs_score"] = mqs.get("mqs_score")
        features["mqs_grade"] = mqs.get("grade")
        features["mqs_atr_percentile"] = mqs.get("atr_percentile")
        features["mqs_volatility_grade"] = mqs.get("volatility_grade")
        features["mqs_day_penalty"] = mqs.get("day_penalty")

    ms_raw = _raw("MarketStructure")
    if ms_raw:
        features["market_structure_h1_event"] = ms_raw.get("h1_event")
        features["market_structure_h1_strength"] = ms_raw.get("h1_strength")
        features["market_structure_aligned"] = ms_raw.get("aligned")
        features["market_structure_last_high_bar_age"] = ms_raw.get("last_high_bar_age")
        features["market_structure_last_low_bar_age"] = ms_raw.get("last_low_bar_age")

    div_raw = _raw("Divergence")
    if div_raw:
        features["divergence_rsi_divergence"] = div_raw.get("rsi_divergence")
        features["divergence_macd_divergence"] = div_raw.get("macd_divergence")
        features["divergence_rsi_div_strength"] = div_raw.get("rsi_div_strength")

    wy_raw = _raw("Wyckoff")
    if wy_raw:
        features["wyckoff_event"] = wy_raw.get("event")
        features["wyckoff_event_strength_pct"] = wy_raw.get("event_strength_pct")
        vol_analysis = wy_raw.get("volume_analysis") or {}
        if "vol_ratio" in vol_analysis:
            features["wyckoff_vol_ratio"] = vol_analysis.get("vol_ratio")

    ict_raw = _raw("ICT")
    if ict_raw:
        features["ict_zone"] = ict_raw.get("zone")
        features["ict_zone_pct"] = ict_raw.get("zone_pct")
        features["ict_is_killzone"] = ict_raw.get("is_killzone")

    nnfx_raw = _raw("NNFX")
    if nnfx_raw:
        features["nnfx_price_vs_ema200_pct"] = nnfx_raw.get("price_vs_ema200_pct")
        features["nnfx_adx"] = nnfx_raw.get("adx")

    quant_raw = _raw("Quant")
    if quant_raw:
        features["quant_atr_percentile"] = quant_raw.get("atr_percentile")
        features["quant_roc_10"] = quant_raw.get("roc_10")

    # Derived (2026-07-30) — computed here, not stored anywhere else.
    ema200 = nnfx_raw.get("ema200") if nnfx_raw else None
    if ema200 is not None and atr_val is not None and atr_val > 0:
        features["derived_ema200_distance_atr"] = (entry_price - ema200) / atr_val

    last_high = ms_raw.get("last_h1_high") if ms_raw else None
    last_low = ms_raw.get("last_h1_low") if ms_raw else None
    if last_high and last_low and last_high != last_low:
        features["derived_retracement_pct"] = (entry_price - last_low) / (last_high - last_low) * 100.0

    return features


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

    # Entry-time decision snapshot (Interactive Charts, 2026-07-25) — was
    # always computed to gate the trade, previously discarded. Absent for
    # Trades built outside run_backtest's loop (e.g. unit-test fixtures).
    decision = trade.decision or {}
    engine_votes = {e["engine"]: e for e in decision.get("engines", [])}

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
        regime=decision.get("regime") or "",
        # Bug found live (2026-07-30): by_session was always 100% "Unknown"
        # even though decision["session"] is real (backtest_engine.py sets
        # it from the MQS gate's own session detection whenever use_mqs_gate
        # is on, which is the default everywhere and never overridden by
        # Mission Center's ad-hoc runs) — this field was simply never copied
        # from decision into TradeRecord, unlike regime just above.
        session=decision.get("session") or "",
        cf_score=decision.get("adjusted_score", 0.0),
        engine_votes=engine_votes,
        indicator_filters=decision.get("indicator_filters") or {},
        features=_build_features(decision, trade.entry_price, decision.get("atr_value")),
    )


# ─────────────────────────────────────────────────────────────────────────
# Per-symbol run
# ─────────────────────────────────────────────────────────────────────────

def run_symbol(
    symbol: str,
    df: pd.DataFrame,
    runner_config: RunnerConfig,
) -> SymbolRunResult:
    """Run the full backtest → metrics → MC → report chain for one symbol.

    Engine config comes from ``BacktestConfig.from_profile`` — the single
    source of truth for pip size, asset class, and dollar-per-point.
    Bypassing it silently prices metals/crypto P&L with forex math.
    """
    engine_cfg = BacktestConfig.from_profile(
        symbol, **runner_config.engine_overrides
    )
    engine_config = build_engine_config_override(
        timeframes=list(runner_config.timeframes) if runner_config.timeframes else None,
        engines_enabled=(
            {e: (e in runner_config.engines) for e in ENGINE_KEYS} if runner_config.engines else None
        ),
        indicators=list(runner_config.indicators) if runner_config.indicators else None,
        confluence_overrides=runner_config.confluence_overrides,
    )
    result = run_backtest(df, engine_cfg, engine_config=engine_config)

    records = [trade_to_record(t, symbol) for t in result.trades]
    metrics = calculate_metrics(records, initial_capital=engine_cfg.initial_balance)

    mc: MonteCarloResult | None = None
    if runner_config.run_mc:
        mc = run_monte_carlo(records, initial_capital=engine_cfg.initial_balance)

    html: Path | None = None
    if runner_config.write_html:
        html = generate_html_report(metrics, records, mc=mc, symbol=symbol, df=df)

    # Fingerprint Binding (Data Integrity Core, slice 2) — cheap glob,
    # not a re-read of the file (dataset_fingerprint does the one real
    # SHA256 read); mirrors the "call find_symbol_csv a second time"
    # pattern already established in mission_runner.py/mission_validator.py.
    dataset_descriptor: dict | None = None
    try:
        physical_tf = physical_load_timeframe(runner_config.timeframes)
        csv_path = find_symbol_csv(symbol, runner_config.data_dir, timeframe=physical_tf)
        dataset_descriptor = build_dataset_descriptor(df, symbol, physical_tf, csv_path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.warning(f"{symbol}: could not build dataset descriptor (non-fatal): {exc}")

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
        dataset_descriptor=dataset_descriptor,
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
            df = load_symbol_data(
                symbol, config.data_dir, config.start, config.end,
                timeframe=physical_load_timeframe(config.timeframes),
            )
            results[symbol] = run_symbol(symbol, df, config)
            m = results[symbol].metrics
            logger.info(
                # NOTE: backtest.metrics.PerformanceMetrics.win_rate is in
                # PERCENT (0–100), unlike backtesting.backtest_engine which
                # uses a fraction — do not format with ':%'.
                f"{symbol}: trades={m.total_trades} WR={m.win_rate:.1f}% "
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
                "dataset_descriptor": r.dataset_descriptor,
                "pipeline_runs": r.engine_result.total_runs,
                "pipeline_errors": r.engine_result.error_count,
                # Backtesting Lab Pro Phase D (2026-07-27) — already
                # computed by run_backtest, previously discarded here.
                # gate_rejections turns "N trades" into a diagnosable
                # funnel; indicator_rejections is the per-indicator
                # veto-count breakdown the operator explicitly asked for.
                "gate_rejections": dict(r.engine_result.gate_rejections),
                "indicator_rejections": dict(r.engine_result.indicator_rejections),
                "context_rejections": dict(r.engine_result.context_rejections),
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
    path.write_text(json.dumps(json_safe(payload), indent=2, default=str))
    logger.info(f"Summary written: {path}")
    return path


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    from backtesting.backtest_engine import RISK_OVERRIDE_FIELDS

    parser = argparse.ArgumentParser(description="IATIS full backtest runner")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--start", default=None, help="ISO date, inclusive")
    parser.add_argument("--end", default=None, help="ISO date, inclusive")
    parser.add_argument("--no-mc", action="store_true")
    parser.add_argument("--no-html", action="store_true")
    # Backtesting Lab Pro Phase A (2026-07-27) — ad-hoc per-run
    # BacktestConfig overrides, threaded into RunnerConfig.engine_overrides
    # (already consumed by run_symbol's BacktestConfig.from_profile call,
    # backtest/runner.py:244-246 — this only adds the CLI/API surface).
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
    # data.timeframes override (decision TF first), threaded into
    # run_backtest's engine_config keyword via build_engine_config_override.
    from core.timeframe_sync import SUPPORTED_TIMEFRAMES
    parser.add_argument("--timeframes", nargs="+", choices=SUPPORTED_TIMEFRAMES, default=None)
    # Backtesting Lab Pro Phase C (2026-07-27) — ad-hoc per-run engine
    # selection (explicit complete list of which engines run).
    parser.add_argument("--engines", nargs="+", choices=ENGINE_KEYS, default=None)
    # Backtesting Lab Pro Phase D (2026-07-27) — ad-hoc per-run indicator
    # filter/confirmation/score-weight specs (JSON-encoded list of
    # confluence.indicator_filters.IndicatorSpec-shaped dicts) — structured
    # data doesn't fit the nargs="+" pattern the flags above use.
    parser.add_argument("--indicators-json", type=str, default=None)
    # Mission Center Research Rigor Phase 1 (2026-08-06) — ad-hoc per-run
    # confluence.min_engines_agreeing/min_informative_weight_share
    # override, structured (2-key dict), same JSON-transport idiom as
    # --indicators-json above.
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

    config = RunnerConfig(
        symbols=tuple(args.symbols),
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        run_mc=not args.no_mc,
        write_html=not args.no_html,
        engine_overrides=engine_overrides,
        timeframes=tuple(args.timeframes) if args.timeframes else None,
        engines=tuple(args.engines) if args.engines else None,
        indicators=indicators,
        confluence_overrides=confluence_overrides,
    )
    results = run_all(config)
    if not results:
        raise SystemExit("No symbol completed — see errors above.")
    write_summary(results, config.output_dir)


if __name__ == "__main__":
    main()
