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


@dataclass
class BacktestConfig:
    symbol: str = "EURUSD"
    initial_balance: float = 10_000.0
    risk_per_trade: float = 0.01
    # Aligned with production config.yaml (risk.min_risk_reward) — was 3.0,
    # which meant the backtest validated a different system than production.
    min_rr: float = 2.0
    commission_pips: float = 0.5
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
        }
    )

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
                self.sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(252))

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

    _ENGINE_MAP = {
        "smc": SMCEngine, "price_action": PriceActionEngine,
        "ict": ICTEngine, "nnfx": NNFXEngine,
        "quant": QuantEngine, "wyckoff": WyckoffEngine,
        "divergence": DivergenceEngine,
        "market_structure": MarketStructureEngine,
        "sentiment": SentimentEngine,
    }
    enabled = engine_config.get("engines", {}).get("enabled", {})
    for key, cls in _ENGINE_MAP.items():
        if enabled.get(key, key in ("smc","price_action","ict","nnfx","quant","wyckoff")):
            engine = cls()
            # Same decision timeframe the production pipeline uses
            # (main.build_active_engines) — gate/vote parity.
            engine.decision_tf = timeframes[0] if timeframes else "H1"
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
    )
    result.equity_curve.append(balance)

    total = len(df) - config.warmup_bars - 1
    logger.info(f"Backtest: {config.symbol} | {total} bars to process")

    slip = config.slippage_pips * config.pip_size

    def _close_trade(trade: Trade, exit_price: float, exit_reason: str,
                     bar_idx: int, bar_time) -> float:
        """Finalize a trade at ``exit_price`` and return its pnl_usd.

        Single close path (was duplicated 4× for BUY/SELL × SL/TP).
        Commission is charged once per round trip, consistently via
        _pip_value_usd for forex.
        """
        sign = 1.0 if trade.direction == "BUY" else -1.0
        diff = sign * (exit_price - trade.entry_price)
        trade.exit_bar, trade.exit_time = bar_idx, bar_time
        trade.exit_price = exit_price
        trade.pnl_pips = diff / config.pip_size - config.commission_pips
        trade.pnl_usd = _calc_pnl_usd(diff, trade.position_size, trade.entry_price)
        if ac == "forex":
            trade.pnl_usd -= config.commission_pips * _pip_value_usd(
                trade.entry_price, trade.position_size
            )
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
                and vote.winning_bias.value != "NEUTRAL"
                and info_share_ok
            )
            if not ok:
                result.no_trade_count += 1
                if veto_blocked:
                    result.gate_rejections["reversal_veto"] += 1
                elif adjusted_score < min_score:
                    result.gate_rejections["score"] += 1
                elif contradiction.blocked:
                    result.gate_rejections["contradiction"] += 1
                elif not info_share_ok:
                    result.gate_rejections["info_share"] += 1
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

            open_trade = Trade(
                entry_bar=i+1, entry_time=next_bar.name,
                direction="BUY" if direction == "BULLISH" else "SELL",
                entry_price=entry, stop_loss=sl, take_profit=tp,
                risk_pct=config.risk_per_trade, position_size=size,
            )
            result.execute_count += 1

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
