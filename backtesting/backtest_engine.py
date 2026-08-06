"""
backtesting/backtest_engine.py
----------------------------------
Real walk-forward backtesting engine — Phase 5.

No lookahead bias: at bar N, pipeline only sees bars 0..N.
Realistic: entries on next-bar open, fixed risk sizing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Real broker spread per symbol, in the backtest's pip units (spread in
# PRICE ÷ pip_size), measured from cTrader / IC Markets demo on
# 2026-07-06 (scripts/measure_ctrader_spread.py; pip_size 0.0001 FX /
# 0.01 JPY,metal,energy,index,crypto). These are quiet-hour floors — real
# fills during signals (often volatile) run wider, which forward paper
# trading on the cTrader demo will reveal. Used as the commission_pips
# default in from_profile() so PF numbers reflect real trading cost.
# FX spreads measured 0.0-0.4 pips (below the old 0.5 default) are left at
# the conservative 0.5 rather than lowered — never make a backtest look
# better on an unverified assumption.
REAL_SPREAD_PIPS: dict[str, float] = {
    "XAUUSD": 12.0,    # $0.12 spread ÷ 0.01
    "XAGUSD": 3.7,     # $0.037 ÷ 0.01
    "USOIL": 2.0,      # $0.02 ÷ 0.01
    "US30": 120.0,     # 1.2 index points ÷ 0.01
    "NAS100": 100.0,   # 1.0 ÷ 0.01
    "SPX500": 50.0,    # 0.5 ÷ 0.01
    "BTCUSD": 1200.0,  # $12 ÷ 0.01
    "ETHUSD": 290.0,   # $2.90 ÷ 0.01
}

# Backtesting Lab Pro, Phase A (2026-07-27) — the exact BacktestConfig
# fields exposed as free per-run overrides from the API/CLI. Deliberately
# excludes asset_class/dollar_per_point/pip_size/symbol: those are engine-
# correctness fields auto-derived per symbol by from_profile(), not risk/
# cost knobs — overriding asset_class wrongly would silently corrupt P&L
# math, a bug surface, not a legitimate research variable.
RISK_OVERRIDE_FIELDS: tuple[str, ...] = (
    "min_rr", "sl_atr_multiplier", "risk_per_trade", "commission_pips",
    "slippage_pips", "swap_pips_per_night", "initial_balance",
    "warmup_bars", "step_bars",
)

# BUG-012 (2026-08-XX) — a recurrence, at the backtest-report layer, of the
# same "hardcoded 365/252-day annualization ignores the actual bar cadence"
# family already fixed once this session in engines/quant_engine.py
# (BUG-009). BacktestResult.equity_curve appends once per BASE-timeframe
# bar (backtesting/backtest_engine.py's main loop), not once per trading
# day — so Sharpe's sqrt(periods/year) must scale with the base timeframe's
# actual bars/day, or it silently understates Sharpe by sqrt(bars_per_day)
# for any base timeframe coarser than D1 (H4's 6 bars/day -> ~sqrt(6)=2.45x
# understatement).
_TF_MINUTES_FOR_SHARPE: dict[str, int] = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}
_CRYPTO_TICKERS_FOR_SHARPE = ("BTC", "ETH", "XRP", "LTC", "SOL")


def _periods_per_year(timeframe: str, symbol: str) -> float:
    """Trading periods/year for Sharpe annualization, matching the base
    (decision) timeframe's real bar cadence rather than a flat daily
    assumption. 24/7 assets (crypto) use 365 calendar days/year; everything
    else uses 252 trading days/year (mirrors engines/quant_engine.py's own
    trading_days_per_year_fx default) — the same distinction BUG-009 already
    established for realized-vol annualization."""
    bar_minutes = _TF_MINUTES_FOR_SHARPE.get(timeframe, 1440)
    days_per_year = 365.0 if any(c in symbol.upper() for c in _CRYPTO_TICKERS_FOR_SHARPE) else 252.0
    bars_per_day = 1440.0 / bar_minutes
    return days_per_year * bars_per_day


@dataclass
class BacktestConfig:
    symbol: str = "EURUSD"
    initial_balance: float = 10_000.0
    risk_per_trade: float = 0.01
    # Aligned with production config.yaml (risk.min_risk_reward) — was 3.0,
    # which meant the backtest validated a different system than production.
    min_rr: float = 2.0
    commission_pips: float = 0.5
    # Swap/rollover cost in PIPS PER NIGHT HELD, charged on exit for every
    # UTC-day boundary the trade crossed (philosophy audit tier-2 gap #7:
    # H4 trades hold for days — the 168h time stop allows 7 — and at FX
    # PF 1.03-1.10 an unmodeled nightly financing cost can flip the sign).
    # Default 0.0 = old behavior. Fill real per-symbol values in
    # data/swap_rates.json (template + provenance notes there) and
    # from_profile() picks them up. Simplifications, documented: same cost
    # both directions (real swaps are signed per side) and no Wednesday
    # triple — both make this a CONSERVATIVE-to-neutral floor, not an
    # exact broker statement.
    swap_pips_per_night: float = 0.0
    # Slippage applied against the trader on entry AND on SL exits
    # (limit-like TP exits are assumed filled at price). 0 to disable.
    slippage_pips: float = 0.5
    # SL distance = ATR * this multiplier. Aligned with production
    # config.yaml risk.sl_atr_multiplier (was hardcoded 1.5 in the loop).
    sl_atr_multiplier: float = 2.5
    warmup_bars: int = 210
    step_bars: int = 4
    pip_size: float = 0.0001    # 0.01 for JPY pairs, 0.0001 for most FX
    # ── Gate parity with production (main.py) ─────────────────────────
    # Default ON: the backtest must simulate the SAME system that trades.
    # Individual flags exist ONLY for ablation studies (measuring each
    # gate's contribution). Tuning gate on/off combinations to make a
    # walk-forward pass is curve fitting — results produced with any
    # gate disabled are labeled as ablations in the result manifest.
    use_mqs_gate: bool = True           # Gate 1: Market Quality Score
    use_regime_weights: bool = True     # regime-adaptive engine weights
    use_mtf_confirmation: bool = True   # D1/H1 alignment score adjustment
    use_reversal_veto: bool = True      # H013 hard/soft veto

    # Asset class controls how P&L is calculated:
    # 'forex': pnl_usd = pips * pip_size * lot_size * 100000
    # 'metal': pnl_usd = price_diff * lot_size * contract_size
    # 'index': pnl_usd = price_diff * lot_size * multiplier
    asset_class: str = "forex"
    # For metals/indices: dollar value per 1-point move per 1 lot
    # Gold: 1 USD/point/lot, Silver: 50 USD/point, Crude: 10 USD/point
    dollar_per_point: float = 1.0   # only used when asset_class != 'forex'

    @classmethod
    def from_profile(cls, symbol: str, **kwargs) -> "BacktestConfig":
        """Create config from asset profile automatically.

        Commission defaults to the REAL measured broker spread per symbol
        (REAL_SPREAD_PIPS) so backtests are cost-accurate out of the box.
        Callers can still override commission_pips explicitly (e.g. for
        ablation / sensitivity runs)."""
        # Real spread as the commission floor, unless the caller overrides.
        if "commission_pips" not in kwargs and symbol.upper() in REAL_SPREAD_PIPS:
            kwargs = {**kwargs, "commission_pips": REAL_SPREAD_PIPS[symbol.upper()]}
        # Swap/rollover per night from data/swap_rates.json when present.
        if "swap_pips_per_night" not in kwargs:
            try:
                import json as _json
                from pathlib import Path as _Path
                rates = _json.loads(
                    (_Path(__file__).resolve().parent.parent
                     / "data" / "swap_rates.json").read_text())
                if symbol.upper() in rates.get("pips_per_night", {}):
                    kwargs = {**kwargs, "swap_pips_per_night":
                              float(rates["pips_per_night"][symbol.upper()])}
            except Exception:
                pass  # no rates file = swap stays 0.0 (old behavior)
        try:
            from core.asset_profiles import get_profile
            profile = get_profile(symbol.upper())
            ac = profile.asset_class.lower()

            # Map asset class to calculation method
            if ac == "forex":
                return cls(symbol=symbol, asset_class="forex",
                           pip_size=0.01 if "JPY" in symbol else 0.0001, **kwargs)
            elif ac == "metals":
                # Gold: $1 per point per 0.01 lot → $100/point/lot
                dppt = 100.0 if symbol in ("XAUUSD",) else 500.0  # XAGUSD
                return cls(symbol=symbol, asset_class="metal",
                           pip_size=0.01, dollar_per_point=dppt, **kwargs)
            elif ac == "energy":
                return cls(symbol=symbol, asset_class="metal",
                           pip_size=0.01, dollar_per_point=100.0, **kwargs)
            elif ac in ("indices", "crypto"):
                return cls(symbol=symbol, asset_class="index",
                           pip_size=0.01, dollar_per_point=1.0, **kwargs)
        except (KeyError, ImportError) as exc:
            logger.warning(
                f"{symbol}: no asset profile ({exc}) — falling back to FOREX "
                f"P&L math. For metals/indices/crypto this MISPRICES results; "
                f"add the symbol to core/asset_profiles.py."
            )
        return cls(symbol=symbol, **kwargs)


@dataclass
class Trade:
    entry_bar: int
    entry_time: Any
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_pct: float
    position_size: float
    exit_bar: int = -1
    exit_time: Any = None
    exit_price: float = 0.0
    pnl_pips: float = 0.0
    pnl_usd: float = 0.0
    exit_reason: str = ""
    # Entry-time decision snapshot (Interactive Charts / Backtesting Lab,
    # 2026-07-25): per-engine bias+confidence, vote tally, and score —
    # exactly what was already computed to gate this trade, just captured
    # instead of discarded. None for any Trade built outside this loop
    # (e.g. test fixtures) — callers must not assume it's present.
    decision: dict | None = None


def check_exit(trade: "Trade", bar, slip: float) -> tuple[float, str] | None:
    """Determine exit on this bar, modeling gaps and SL slippage.

    Pure function (no side effects) so its assumptions are unit-testable.

    Rules (conservative, deterministic):
    - Gap through SL at the open → filled at the OPEN (worse than SL),
      not at the SL price. Stop orders cannot fill better than the market;
      the previous code exited at the exact SL price, overstating results.
    - Gap through TP at the open → filled at the open as well
      (symmetric treatment; favorable gaps do occur for limit exits).
    - Intrabar: SL is checked BEFORE TP. When both are touched within one
      bar the true sequence is unknowable from OHLC, so we take the
      pessimistic assumption.
    - SL fills incur ``slip`` (price units) against the trader; TP fills
      are limit-like and assumed filled at price.

    Args:
        trade: the open trade (direction, stop_loss, take_profit).
        bar: OHLC row supporting ``bar["open"|"high"|"low"]``.
        slip: slippage in PRICE units (slippage_pips * pip_size).

    Returns:
        (exit_price, exit_reason) or None if no exit on this bar.
    """
    o = float(bar["open"])
    h, l = float(bar["high"]), float(bar["low"])
    if trade.direction == "BUY":
        if o <= trade.stop_loss:
            return o - slip, "SL_GAP"
        if o >= trade.take_profit:
            return o, "TP_GAP"
        if l <= trade.stop_loss:
            return trade.stop_loss - slip, "SL"
        if h >= trade.take_profit:
            return trade.take_profit, "TP"
    else:  # SELL
        if o >= trade.stop_loss:
            return o + slip, "SL_GAP"
        if o <= trade.take_profit:
            return o, "TP_GAP"
        if h >= trade.stop_loss:
            return trade.stop_loss + slip, "SL"
        if l <= trade.take_profit:
            return trade.take_profit, "TP"
    return None


@dataclass
class BacktestResult:
    config: BacktestConfig
    symbol: str
    start_date: str
    end_date: str
    total_bars: int
    # BUG-012 (2026-08-XX): equity_curve gets one point per BASE (decision)
    # timeframe bar (see the unconditional result.equity_curve.append(balance)
    # in the main loop below), not one point per calendar day — so annualizing
    # Sharpe needs the actual bar cadence, not a flat daily assumption.
    # Defaults to "D1" (bars_per_day=1), which reproduces the OLD sqrt(252)
    # behavior exactly for any caller that never sets this.
    timeframe: str = "D1"

    total_runs: int = 0
    execute_count: int = 0
    no_trade_count: int = 0
    # Pipeline exceptions are NOT the same as a genuine NO_TRADE decision.
    # Counting them separately prevents a structurally broken run (e.g. bad
    # input schema) from silently reporting as "0 trades, all NO_TRADE".
    error_count: int = 0
    # Which gate rejected how many bars — turns "0/4 CONSISTENT" from a
    # dead end into a diagnosable funnel (mqs / score / votes /
    # contradiction / reversal_veto).
    gate_rejections: dict = field(
        default_factory=lambda: {
            "mqs": 0, "score": 0, "votes": 0,
            "contradiction": 0, "reversal_veto": 0, "info_share": 0,
            "indicator_filter": 0, "context_filter": 0, "neutral_bias": 0,
        }
    )
    # Backtesting Lab Pro Phase D (2026-07-27) — which indicator (by
    # name) vetoed how many bars, when indicator_filter is the gate
    # that rejected. Separate from gate_rejections["indicator_filter"]
    # (the total count) since more than one indicator can be configured
    # per run.
    indicator_rejections: dict = field(default_factory=dict)
    # Context Filters (2026-07-30) — same shape as indicator_rejections,
    # for confluence/context_filters.py (session/day/volatility/regime/
    # direction) — a separate filter family with its own gate, so a
    # STRONG_LEAD investigation can distinguish which family blocked a
    # bar.
    context_rejections: dict = field(default_factory=dict)

    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)

    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    total_return_pct: float = 0.0

    def compute(self) -> "BacktestResult":
        closed = [t for t in self.trades if t.exit_bar >= 0]
        if not closed:
            return self

        wins = [t for t in closed if t.pnl_usd > 0]
        losses = [t for t in closed if t.pnl_usd <= 0]

        self.win_rate = len(wins) / len(closed) if closed else 0
        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))
        self.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        if self.equity_curve:
            equity = np.array(self.equity_curve)
            peak = np.maximum.accumulate(equity)
            dd = (equity - peak) / peak
            self.max_drawdown_pct = float(abs(dd.min()))
            self.total_return_pct = float((equity[-1] - equity[0]) / equity[0])
            returns = np.diff(equity) / equity[:-1]
            if len(returns) > 1 and returns.std() > 0:
                periods_per_year = _periods_per_year(self.timeframe, self.symbol)
                self.sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(periods_per_year))

        return self

    def summary(self) -> str:
        closed = [t for t in self.trades if t.exit_bar >= 0]
        execute_rate = self.execute_count / max(self.total_runs, 1)
        return (
            f"\n{'='*55}\n"
            f"IATIS Backtest — {self.symbol}\n"
            f"{'='*55}\n"
            f"Period:        {self.start_date} → {self.end_date}\n"
            f"Total bars:    {self.total_bars}\n\n"
            f"Pipeline runs: {self.total_runs}\n"
            f"  EXECUTE:     {self.execute_count} ({execute_rate:.1%})\n"
            f"  NO_TRADE:    {self.no_trade_count} ({1-execute_rate:.1%})\n\n"
            f"Trades:        {len(closed)}\n"
            f"Win rate:      {self.win_rate:.1%}\n"
            f"Profit factor: {self.profit_factor:.2f}\n"
            f"Max drawdown:  {self.max_drawdown_pct:.1%}\n"
            f"Total return:  {self.total_return_pct:.1%}\n"
            f"Sharpe ratio:  {self.sharpe_ratio:.2f}\n"
            f"{'='*55}"
        )

    def save(self, path: str | Path) -> None:
        closed = [t for t in self.trades if t.exit_bar >= 0]
        data = {
            "symbol": self.symbol,
            "period": f"{self.start_date} to {self.end_date}",
            "metrics": {
                "total_runs": self.total_runs,
                "execute_count": self.execute_count,
                "win_rate": round(self.win_rate, 4),
                "profit_factor": round(self.profit_factor, 3),
                "max_drawdown_pct": round(self.max_drawdown_pct, 4),
                "total_return_pct": round(self.total_return_pct, 4),
                "sharpe_ratio": round(self.sharpe_ratio, 3),
                "trades_closed": len(closed),
            },
            "equity_curve": self.equity_curve,
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))
        logger.info(f"Backtest saved to {path}")


# Backtesting Lab Pro Phase C (2026-07-27) — the exact engines
# run_backtest can execute. Deliberately excludes "macro" (a real
# confluence.weights entry, forced to 0.0 per CLAUDE.md's frozen-state
# notes, but with no runnable engine class wired into this backtest path
# at all — toggling it would be a silent no-op, not a legitimate
# ablation). Single source of truth for _ENGINE_MAP below.
ENGINE_KEYS: tuple[str, ...] = (
    "smc", "price_action", "ict", "nnfx", "quant", "wyckoff",
    "divergence", "market_structure", "sentiment",
)

# Track C (Phase 4, 2026-08-01) — the ad-hoc, Mission-Center-only engine
# variants that exist today. Every ENGINE_KEYS entry not listed here has
# ONLY "v1" — no variant exists yet. Deliberately just strings (no class
# refs) so optimizer.py/missions.py can validate an engine_variants
# payload without importing engine modules. The variant class itself is
# resolved lazily inside run_backtest() below.
ENGINE_VARIANT_KEYS: dict[str, tuple[str, ...]] = {
    "price_action": ("v1", "v2"),
    "wyckoff": ("v1", "v2"),
}


# Mission Center Research Rigor Phase 1 (2026-08-06) — the only two keys
# an ad-hoc confluence_overrides dict may set, and their valid ranges.
# min_engines_agreeing is bounded by how many engines actually exist
# (config.yaml's live default is 2, frozen per CLAUDE.md — this override
# exists so a single-engine ablation, e.g. "does SMC alone have edge?",
# can lower it to 1 for that one ad-hoc run without ever touching the
# live config).
_CONFLUENCE_OVERRIDE_BOUNDS: dict[str, tuple[float, float]] = {
    "min_engines_agreeing": (1, len(ENGINE_KEYS)),
    "min_informative_weight_share": (0.0, 1.0),
}


def build_engine_config_override(
    timeframes: list[str] | None = None,
    engines_enabled: dict[str, bool] | None = None,
    indicators: list[dict] | None = None,
    context_filters: list[dict] | None = None,
    engine_variants: dict[str, str] | None = None,
    confluence_overrides: dict[str, float] | None = None,
) -> dict | None:
    """Backtesting Lab Pro Phase B/C/D (2026-07-27) — ad-hoc per-run
    overrides, merged over a real load_config() snapshot so every other
    confluence/engine setting (weights, min_score_to_trade, ...) stays
    exactly as configured. Returns None when no override is requested,
    preserving run_backtest's own load_config() default path
    byte-for-byte — zero behavior change for every existing caller that
    never passes any of these. Ephemeral — never writes to config.yaml.

    engines_enabled (Phase C): an explicit {engine_key: is_enabled} map
    merged over config/engines.yaml's real enabled dict. Only the 9
    ENGINE_KEYS are meaningful here — any other key is silently inert,
    matching run_backtest's own enabled.get(key, ...) lookup, which only
    ever consults ENGINE_KEYS.

    indicators (Phase D): a list of confluence.indicator_filters.
    IndicatorSpec-shaped dicts ({"name","mode","params","weight"}).
    Stored verbatim under engine_config["indicators"]["filters"] —
    run_backtest reconstructs IndicatorSpec objects from it. Indicators
    only ever filter/confirm/weight a decision the engine vote already
    produced (see confluence/indicator_filters.py's module docstring);
    they can never set direction/bias themselves.

    context_filters (Context Filters, 2026-07-30): a list of confluence.
    context_filters.ContextSpec-shaped dicts ({"name","mode","params",
    "weight"}) — session/day-of-week/volatility-regime/market-regime/
    direction. Stored verbatim under engine_config["context_filters"]
    ["filters"]; same filter/confirm/weight-only constraint as
    indicators, never a direction/bias source.

    engine_variants (Track C, Phase 4): an explicit {engine_key: variant}
    map (e.g. {"price_action": "v2"}), stored under
    engine_config["engines"]["variants"] — a NEW sub-key, separate from
    "enabled", so engine on/off and variant selection compose
    independently. Raises ValueError on an unknown engine or an unknown
    variant for that engine (fail loudly here, at build time, rather than
    surfacing a confusing error deep inside run_backtest's instantiation
    loop). This NEVER touches config/engines.yaml's live default — v1
    keeps loading unconditionally unless a caller explicitly passes this.

    confluence_overrides (Mission Center Research Rigor Phase 1,
    2026-08-06): an explicit {"min_engines_agreeing": int,
    "min_informative_weight_share": float} override, merged over
    config.yaml's confluence block. Restricted to exactly the two keys in
    _CONFLUENCE_OVERRIDE_BOUNDS above — any other key or an out-of-bounds
    value raises ValueError at build time. Ephemeral — never written to
    config.yaml; run_backtest's live default path (this whole function
    returning None) is unaffected when this is omitted.
    """
    if (timeframes is None and engines_enabled is None and indicators is None
            and context_filters is None and engine_variants is None
            and confluence_overrides is None):
        return None
    from utils.helpers import load_config
    base = load_config()
    merged = dict(base)
    if timeframes is not None:
        merged["data"] = {**base["data"], "timeframes": list(timeframes)}

    engines_block = dict(base.get("engines", {}))
    engines_changed = False
    if engines_enabled is not None:
        engines_block["enabled"] = {**base.get("engines", {}).get("enabled", {}), **engines_enabled}
        engines_changed = True
    if engine_variants is not None:
        unknown_engines = set(engine_variants) - set(ENGINE_KEYS)
        if unknown_engines:
            raise ValueError(f"unknown engine(s) in engine_variants: {unknown_engines} — choose from {ENGINE_KEYS}")
        for eng_key, variant in engine_variants.items():
            allowed = ENGINE_VARIANT_KEYS.get(eng_key, ("v1",))
            if variant not in allowed:
                raise ValueError(f"engine {eng_key!r} has no variant {variant!r} — choose from {allowed}")
        engines_block["variants"] = {**base.get("engines", {}).get("variants", {}), **engine_variants}
        engines_changed = True
    if engines_changed:
        merged["engines"] = engines_block

    if indicators is not None:
        merged["indicators"] = {"filters": list(indicators)}
    if context_filters is not None:
        merged["context_filters"] = {"filters": list(context_filters)}

    if confluence_overrides is not None:
        unknown_keys = set(confluence_overrides) - set(_CONFLUENCE_OVERRIDE_BOUNDS)
        if unknown_keys:
            raise ValueError(
                f"unknown confluence_overrides key(s): {sorted(unknown_keys)} — "
                f"choose from {sorted(_CONFLUENCE_OVERRIDE_BOUNDS)}"
            )
        for key, value in confluence_overrides.items():
            lo, hi = _CONFLUENCE_OVERRIDE_BOUNDS[key]
            if not (lo <= value <= hi):
                raise ValueError(f"confluence_overrides.{key} must be between {lo} and {hi}, got {value}")
        merged["confluence"] = {**base.get("confluence", {}), **confluence_overrides}
    return merged


def run_backtest(
    df: pd.DataFrame,
    config: BacktestConfig | None = None,
    engine_config: dict | None = None,
) -> BacktestResult:
    """Walk-forward backtest on historical OHLCV data — no lookahead."""
    from utils.helpers import load_config
    from core.timeframe_sync import build_multi_timeframe_view
    from engines.smc_engine import SMCEngine
    from engines.price_action_engine import PriceActionEngine
    from engines.ict_engine import ICTEngine
    from engines.nnfx_engine import NNFXEngine
    from engines.quant_engine import QuantEngine
    from engines.wyckoff_engine import WyckoffEngine
    from confluence.voting_system import informative_weight_share, tally_votes
    from confluence.score_calculator import calculate_score
    from confluence.contradiction_engine import check_contradictions
    from confluence.mtf_confirmation import check_mtf_confirmation
    from confluence.regime_weights import apply_regime_weights
    from confluence.reversal_veto import check_reversal_veto
    from core.market_quality import assess_market_quality
    from regimes.regime_detector import detect_regime
    from regimes.volatility_classifier import atr as compute_atr

    if config is None:
        config = BacktestConfig()
    if engine_config is None:
        engine_config = load_config()

    weights = engine_config["confluence"]["weights"]
    min_score = engine_config["confluence"]["min_score_to_trade"]
    min_engines = engine_config["confluence"]["min_engines_agreeing"]
    # Axis-8 gate parity with main.py (0.0 = disabled).
    min_info_share = engine_config["confluence"].get("min_informative_weight_share", 0.0)
    timeframes = engine_config["data"]["timeframes"]

    engines_list = []

    # Use ALL enabled engines from config (not just 6 hardcoded)
    from engines.divergence_engine import DivergenceEngine
    from engines.market_structure_engine import MarketStructureEngine
    from engines.sentiment_engine import SentimentEngine
    # Track C (Phase 4) — ad-hoc-only variants, lazily imported so a
    # normal backtest (no engine_variants requested) never pays for
    # importing these modules.
    from engines.price_action_engine_v2 import PriceActionEngineV2
    from engines.wyckoff_engine_v2 import WyckoffEngineV2

    _ENGINE_MAP = dict(zip(ENGINE_KEYS, (
        SMCEngine, PriceActionEngine, ICTEngine, NNFXEngine, QuantEngine, WyckoffEngine,
        DivergenceEngine, MarketStructureEngine, SentimentEngine,
    )))
    # Track C — class resolution for a variant. Kept local to
    # run_backtest (class refs, unlike ENGINE_VARIANT_KEYS' plain
    # strings) so this file's module-level import graph never has to
    # import the variant engine modules until a backtest actually runs.
    _ENGINE_VARIANT_CLASS_MAP: dict[str, dict[str, type]] = {
        "price_action": {"v2": PriceActionEngineV2},
        "wyckoff": {"v2": WyckoffEngineV2},
    }
    enabled = engine_config.get("engines", {}).get("enabled", {})
    all_thresholds = engine_config.get("engines", {}).get("thresholds", {})
    variant_selection = engine_config.get("engines", {}).get("variants", {})
    for key, cls in _ENGINE_MAP.items():
        if enabled.get(key, key in ("smc","price_action","ict","nnfx","quant","wyckoff")):
            variant = variant_selection.get(key, "v1")
            if variant != "v1":
                variant_cls = _ENGINE_VARIANT_CLASS_MAP.get(key, {}).get(variant)
                if variant_cls is None:
                    raise ValueError(
                        f"unknown variant {variant!r} for engine {key!r} — choose from "
                        f"{list(_ENGINE_VARIANT_CLASS_MAP.get(key, {}).keys()) or ['v1']}"
                    )
                cls = variant_cls
            engine = cls()
            # Same decision timeframe the production pipeline uses
            # (main.build_active_engines) — gate/vote parity.
            engine.decision_tf = timeframes[0] if timeframes else "H1"
            # Confluence Engine Overhaul Phase 1 — same
            # config.engines.thresholds source, gate/vote parity with
            # main.build_active_engines. Track C: a variant reads ITS OWN
            # thresholds sub-key (thresholds.price_action_v2, never
            # thresholds.price_action) — prevents a v2 class silently
            # running on v1's tuned numbers, which would make any v2
            # research result meaningless.
            thresholds_key = f"{key}_{variant}" if variant != "v1" else key
            engine.thresholds = all_thresholds.get(thresholds_key, {})
            if key == "smc":
                # H017 flag parity with main.build_active_engines — the A/B
                # (scripts/smc_fullspec_ab.py) flips this through the config.
                engine.full_spec = bool(
                    engine_config.get("engines", {}).get("smc_full_spec", False)
                )
            engines_list.append(engine)

    atr_series = compute_atr(df, period=14)
    balance = config.initial_balance
    open_trade: Trade | None = None
    ac = config.asset_class
    dpp = config.dollar_per_point

    def _pip_value_usd(entry_price: float, size: float) -> float:
        """USD value of 1 pip movement for given position size.

        USD-quoted (EURUSD, GBPUSD): 1 pip = pip_size × size × 100,000
          = 0.0001 × 1 lot × 100,000 = $10/lot
        JPY-quoted (USDJPY, EURJPY): 1 pip = (pip_size / price) × size × 100,000
          = (0.01 / 150) × 1 lot × 100,000 = $6.67/lot
        """
        if ac != "forex":
            return dpp * size
        if config.pip_size == 0.01:  # JPY pairs
            return (config.pip_size / max(entry_price, 1.0)) * size * 100_000
        return config.pip_size * size * 100_000

    def _calc_pnl_usd(price_diff: float, size: float, entry_price: float = 1.0) -> float:
        """Calculate P&L in USD — consistent with position sizing."""
        if ac != "forex":
            return price_diff * size * dpp
        pips = price_diff / config.pip_size
        pip_val = _pip_value_usd(entry_price, size)
        return pips * pip_val

    def _calc_position_size(sl_dist: float, risk_amount: float,
                            entry_price: float) -> float:
        """Position size in lots consistent with _calc_pnl_usd."""
        if ac != "forex":
            return max(0.01, min(round(risk_amount / (sl_dist * dpp), 4), 10.0))
        # pip_value_per_lot depends on price for JPY
        if config.pip_size == 0.01:  # JPY
            pip_val_per_lot = (config.pip_size / max(entry_price, 1.0)) * 100_000
        else:
            pip_val_per_lot = config.pip_size * 100_000  # = 10 USD for standard
        sl_pips = sl_dist / config.pip_size
        size = risk_amount / (sl_pips * pip_val_per_lot)
        return max(0.01, min(round(size, 2), 10.0))

    result = BacktestResult(
        config=config, symbol=config.symbol,
        start_date=str(df.index[config.warmup_bars].date()),
        end_date=str(df.index[-1].date()),
        total_bars=len(df),
        timeframe=timeframes[0] if timeframes else "D1",
    )
    result.equity_curve.append(balance)

    total = len(df) - config.warmup_bars - 1
    logger.info(f"Backtest: {config.symbol} | {total} bars to process")

    slip = config.slippage_pips * config.pip_size

    def _close_trade(trade: Trade, exit_price: float, exit_reason: str,
                     bar_idx: int, bar_time) -> float:
        """Finalize a trade at ``exit_price`` and return its pnl_usd.

        Single close path (was duplicated 4× for BUY/SELL × SL/TP).
        Commission and swap are charged once per round trip, via
        _pip_value_usd for forex and via config.pip_size * dpp (the same
        scaling _calc_pnl_usd uses) for non-forex (metal/index).
        """
        sign = 1.0 if trade.direction == "BUY" else -1.0
        diff = sign * (exit_price - trade.entry_price)
        trade.exit_bar, trade.exit_time = bar_idx, bar_time
        trade.exit_price = exit_price
        # Swap/rollover: pips-per-night x UTC-day boundaries crossed. Zero
        # unless configured (data/swap_rates.json or explicit kwarg).
        swap_pips = 0.0
        if config.swap_pips_per_night and trade.entry_time is not None and bar_time is not None:
            try:
                nights = max(0, (bar_time.date() - trade.entry_time.date()).days)
                swap_pips = config.swap_pips_per_night * nights
            except (AttributeError, TypeError):
                pass
        trade.pnl_pips = diff / config.pip_size - config.commission_pips - swap_pips
        trade.pnl_usd = _calc_pnl_usd(diff, trade.position_size, trade.entry_price)
        if ac == "forex":
            trade.pnl_usd -= (config.commission_pips + swap_pips) * _pip_value_usd(
                trade.entry_price, trade.position_size
            )
        else:
            # BUG-004 fix (2026-08-04): commission_pips was previously
            # never subtracted here at all for non-forex assets (metal/
            # index — XAUUSD, BTCUSD, ETHUSD, XAGUSD, USOIL, US30, NAS100,
            # SPX500), and the swap-only branch it replaces was also
            # missing the `* dpp` scale factor _calc_pnl_usd uses
            # everywhere else for non-forex. See
            # reports/forensic/13_CONFIRMED_BUGS.md BUG-004.
            cost_pips = config.commission_pips + swap_pips
            if cost_pips:
                trade.pnl_usd -= cost_pips * config.pip_size * trade.position_size * dpp
        trade.exit_reason = exit_reason
        return trade.pnl_usd

    for i in range(config.warmup_bars, len(df) - 1):
        next_bar = df.iloc[i + 1]

        # --- Check open trade (gap-aware, slippage-aware) ---
        if open_trade is not None:
            exit_hit = check_exit(open_trade, next_bar, slip)
            if exit_hit is not None:
                exit_price, reason = exit_hit
                balance += _close_trade(open_trade, exit_price, reason,
                                        i + 1, next_bar.name)
                result.trades.append(open_trade)
                open_trade = None

        result.equity_curve.append(balance)

        # Skip if in trade or not on step
        if open_trade is not None or (i - config.warmup_bars) % config.step_bars != 0:
            continue

        # --- Run pipeline (gate parity with main.py) ---
        result.total_runs += 1
        try:
            window = df.iloc[:i+1]
            bar_time = window.index[-1].to_pydatetime()

            # Gate 1 — Market Quality Score. CRITICAL: pass the BAR time,
            # not wall-clock now; session/Friday/Monday penalties must be
            # evaluated at the data's timestamp or the whole gate is noise.
            if config.use_mqs_gate:
                mqs = assess_market_quality(
                    df=window, symbol=config.symbol, now=bar_time,
                    timeframe=timeframes[0] if timeframes else "H1",
                )
                if not mqs.should_trade:
                    result.no_trade_count += 1
                    result.gate_rejections["mqs"] += 1
                    continue

            mtf = build_multi_timeframe_view(window, timeframes)
            outputs = [e.safe_analyze(mtf) for e in engines_list]

            # Regime-adaptive weights (same call chain as production).
            active_weights = weights
            if config.use_regime_weights:
                regime = detect_regime(window)
                active_weights = apply_regime_weights(
                    weights, regime.regime.value, regime.volatility
                )

            vote = tally_votes(outputs, active_weights)
            # Same Axis-6 unification as main.py: score follows the vote.
            score = calculate_score(outputs, active_weights, vote.winning_bias)
            contradiction = check_contradictions(outputs)

            # MTF confirmation — D1/H1 alignment adjusts the score
            # exactly as in main.py (clamped to [0, 100]).
            adjusted_score = score.final_score
            if config.use_mtf_confirmation:
                mtf_res = check_mtf_confirmation(
                    h1_bias=vote.winning_bias.value, mtf_data=mtf,
                    signal_tf=timeframes[0] if timeframes else "H1",
                )
                adjusted_score = round(
                    max(0.0, min(100.0, adjusted_score + mtf_res.score_adjustment)), 2
                )

            # Indicator filters (Backtesting Lab Pro Phase D, 2026-07-27)
            # — ad-hoc, per-run RSI/MACD/EMA/ADX/ATR filters that can
            # only veto an otherwise-valid EXECUTE (entry_filter) or
            # nudge adjusted_score (confirmation/score_weight). Never
            # sets direction/bias — see confluence/indicator_filters.py.
            # Absent for every existing caller (engine_config has no
            # "indicators" key), so this is a no-op unless explicitly
            # configured.
            indicator_veto_blocked = False
            indicator_result = None
            indicator_specs = engine_config.get("indicators", {}).get("filters")
            if indicator_specs:
                from confluence.indicator_filters import IndicatorSpec, evaluate_indicator_filters
                specs = [IndicatorSpec(**s) for s in indicator_specs]
                indicator_result = evaluate_indicator_filters(window, vote.winning_bias.value, specs)
                adjusted_score = round(
                    max(0.0, min(100.0, adjusted_score + indicator_result.score_adjustment)), 2
                )
                if indicator_result.vetoed:
                    indicator_veto_blocked = True

            # Context filters (2026-07-30) — ad-hoc, per-run session/
            # day-of-week/volatility-regime/market-regime/direction
            # filters. Same veto-or-nudge-only constraint as indicator
            # filters — see confluence/context_filters.py's module
            # docstring. Absent for every existing caller (engine_config
            # has no "context_filters" key), so this is a no-op unless
            # explicitly configured.
            context_veto_blocked = False
            context_result = None
            context_specs = engine_config.get("context_filters", {}).get("filters")
            if context_specs:
                from confluence.context_filters import ContextSpec, evaluate_context_filters
                c_specs = [ContextSpec(**s) for s in context_specs]
                context_result = evaluate_context_filters(window, vote.winning_bias.value, c_specs)
                adjusted_score = round(
                    max(0.0, min(100.0, adjusted_score + context_result.score_adjustment)), 2
                )
                if context_result.vetoed:
                    context_veto_blocked = True

            # H013 reversal veto — hard veto blocks, soft veto scales the
            # score by confidence_multiplier (identical to production).
            veto_blocked = False
            if config.use_reversal_veto:
                veto = check_reversal_veto(outputs, vote.winning_bias)
                if veto.vetoed:
                    veto_blocked = True
                elif veto.soft_veto:
                    adjusted_score = round(
                        adjusted_score * veto.confidence_multiplier, 2
                    )

            # Axis-8 gate parity with main.py: confluence requires a
            # speaking panel, not a quorum of the only two fed engines.
            info_share_ok = True
            if min_info_share > 0:
                info_share_ok = (
                    informative_weight_share(outputs, active_weights) >= min_info_share
                )

            ok = (
                adjusted_score >= min_score
                and vote.agree_count >= min_engines
                and not contradiction.blocked
                and not veto_blocked
                and not indicator_veto_blocked
                and not context_veto_blocked
                and vote.winning_bias.value != "NEUTRAL"
                and info_share_ok
            )
            if not ok:
                result.no_trade_count += 1
                if veto_blocked:
                    result.gate_rejections["reversal_veto"] += 1
                elif indicator_veto_blocked:
                    result.gate_rejections["indicator_filter"] += 1
                    name = indicator_result.veto_indicator
                    result.indicator_rejections[name] = result.indicator_rejections.get(name, 0) + 1
                elif context_veto_blocked:
                    result.gate_rejections["context_filter"] += 1
                    cname = context_result.veto_context
                    result.context_rejections[cname] = result.context_rejections.get(cname, 0) + 1
                elif adjusted_score < min_score:
                    result.gate_rejections["score"] += 1
                elif contradiction.blocked:
                    result.gate_rejections["contradiction"] += 1
                elif not info_share_ok:
                    result.gate_rejections["info_share"] += 1
                elif vote.winning_bias.value == "NEUTRAL":
                    # Distinct from "votes" (insufficient agree_count):
                    # engines DID agree enough, they just agreed on
                    # nothing directional. Diagnostics-only split — `ok`
                    # already ANDs both conditions identically either way.
                    result.gate_rejections["neutral_bias"] += 1
                else:
                    result.gate_rejections["votes"] += 1
                continue

            direction = vote.winning_bias.value
            # Market entry at next bar open, with slippage AGAINST the trader.
            raw_entry = float(next_bar["open"])
            entry = raw_entry + slip if direction == "BULLISH" else raw_entry - slip
            atr_val = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else 0.001
            # Aligned with production (config.yaml risk.sl_atr_multiplier).
            sl_dist = atr_val * config.sl_atr_multiplier
            tp_dist = sl_dist * config.min_rr

            sl = entry - sl_dist if direction == "BULLISH" else entry + sl_dist
            tp = entry + tp_dist if direction == "BULLISH" else entry - tp_dist

            risk_amount = balance * config.risk_per_trade
            size = _calc_position_size(sl_dist, risk_amount, entry)

            # Decision snapshot (Interactive Charts / Backtesting Lab,
            # 2026-07-25): the per-engine votes/score that already gated
            # this trade above, captured instead of discarded — every
            # value here was computed either way, this only keeps it.
            decision = {
                "engines": [o.to_dict() for o in outputs],
                "winning_bias": vote.winning_bias.value,
                "agree_count": vote.agree_count,
                "score": round(score.final_score, 2),
                "adjusted_score": adjusted_score,
                "regime": regime.regime.value if config.use_regime_weights else None,
                "indicator_filters": indicator_result.per_indicator if indicator_result else None,
                "context_filters": context_result.per_context if context_result else None,
                # Feature Mining Phase 1 (2026-07-30) — real values already
                # computed above for gating, previously discarded once their
                # boolean check was done. Each guarded exactly like "regime"
                # above: mtf_res/veto/mqs are local vars scoped inside their
                # own "if config.use_X:" block, so referencing them
                # unconditionally would NameError when that gate is off for
                # an ablation run.
                "volatility": regime.volatility if config.use_regime_weights else None,
                "atr_value": atr_val,
                "info_share": (
                    informative_weight_share(outputs, active_weights) if min_info_share > 0 else None
                ),
                "mtf": {
                    "d1_bias": mtf_res.d1_bias, "d1_adx": mtf_res.d1_adx,
                    "d1_ema20": mtf_res.d1_ema20, "d1_ema50": mtf_res.d1_ema50,
                    "confirming": mtf_res.confirming,
                } if config.use_mtf_confirmation else None,
                "reversal_veto": {
                    "reversal_count": veto.reversal_count, "reversal_engines": veto.reversal_engines,
                    "trend_bias": veto.trend_bias, "reversal_bias": veto.reversal_bias,
                    "confidence_multiplier": veto.confidence_multiplier, "soft_veto": veto.soft_veto,
                } if config.use_reversal_veto else None,
                "contradiction_reasons": contradiction.reasons,
                "mqs": mqs.to_dict() if config.use_mqs_gate else None,
                "session": mqs.session if config.use_mqs_gate else "",
            }

            open_trade = Trade(
                entry_bar=i+1, entry_time=next_bar.name,
                direction="BUY" if direction == "BULLISH" else "SELL",
                entry_price=entry, stop_loss=sl, take_profit=tp,
                risk_pct=config.risk_per_trade, position_size=size,
                decision=decision,
            )
            result.execute_count += 1

            # Same-bar exit check (forensic fix, 2026-08-03 — BUG-002): a
            # resting SL/TP order is live from the instant of entry, so the
            # entry bar's OWN remaining high/low range (the excursion after
            # its open, where the trade was just entered) must be checked
            # too — not just bars strictly after it. The next loop
            # iteration's own exit check only ever looks at df.iloc[i+2]
            # onward, permanently skipping df.iloc[i+1] (this trade's own
            # entry bar). Confirmed this can silently erase a real stop-out
            # entirely: a same-bar stop-hunt wick that recovers before the
            # next-checked bar was previously invisible to the simulation,
            # capable of flipping a real loss into a reported win — see
            # reports/forensic/13_CONFIRMED_BUGS.md BUG-002.
            same_bar_exit = check_exit(open_trade, next_bar, slip)
            if same_bar_exit is not None:
                exit_price, reason = same_bar_exit
                balance += _close_trade(open_trade, exit_price, reason, i + 1, next_bar.name)
                result.trades.append(open_trade)
                open_trade = None
                # BUG-003 fix (2026-08-03): this bar's equity_curve point was
                # already appended above (line ~644) BEFORE this same-bar
                # close updated `balance` — that entry is stale, still
                # showing the pre-trade balance. Patch it in place so the
                # trade's PnL shows up on the bar it actually happened on,
                # not one entry late (see reports/forensic/13_CONFIRMED_BUGS.md).
                result.equity_curve[-1] = balance

        except Exception as exc:
            logger.debug(f"Bar {i} skipped: {exc}")
            result.error_count += 1
            # First error is logged at WARNING with detail so structural
            # problems (e.g. missing columns) surface immediately instead
            # of masquerading as thousands of silent NO_TRADEs.
            if result.error_count == 1:
                logger.warning(f"First pipeline error at bar {i}: {exc!r}")

    # Force-close any position still open at the end of data
    if open_trade is not None:
        last = df.iloc[-1]
        balance += _close_trade(open_trade, float(last["close"]),
                                "FORCED_CLOSE", len(df) - 1, last.name)
        result.trades.append(open_trade)

    result.equity_curve.append(balance)
    result.compute()
    logger.info(
        f"Backtest done: {result.execute_count} trades, WR={result.win_rate:.1%}, "
        f"errors={result.error_count}/{result.total_runs}"
    )
    # A structurally broken run must not masquerade as a valid "0 trades"
    # result — that would silently invalidate any walk-forward conclusion.
    if result.total_runs > 0 and result.error_count == result.total_runs:
        raise RuntimeError(
            f"Backtest invalid: all {result.total_runs} pipeline runs raised "
            f"exceptions (see first WARNING above). Check input data schema."
        )
    return result


# NOTE (2026-07-02 review): legacy module-level _close_trade,
# _orig_close_trade and _close_trade_v2 removed — dead code superseded by
# the asset-class-aware close path inside run_backtest().
