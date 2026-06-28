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


@dataclass
class BacktestConfig:
    symbol: str = "EURUSD"
    initial_balance: float = 10_000.0
    risk_per_trade: float = 0.01
    min_rr: float = 3.0
    commission_pips: float = 0.5
    warmup_bars: int = 210
    step_bars: int = 4
    pip_size: float = 0.0001    # 0.01 for JPY pairs, 0.0001 for most FX

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
        """Create config from asset profile automatically."""
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
        except (KeyError, ImportError):
            pass
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
    from confluence.voting_system import tally_votes
    from confluence.score_calculator import calculate_score
    from confluence.contradiction_engine import check_contradictions
    from regimes.volatility_classifier import atr as compute_atr

    if config is None:
        config = BacktestConfig()
    if engine_config is None:
        engine_config = load_config()

    weights = engine_config["confluence"]["weights"]
    min_score = engine_config["confluence"]["min_score_to_trade"]
    min_engines = engine_config["confluence"]["min_engines_agreeing"]
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
            engines_list.append(cls())

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

    for i in range(config.warmup_bars, len(df) - 1):
        next_bar = df.iloc[i + 1]

        # --- Check open trade ---
        if open_trade is not None:
            h, l = float(next_bar["high"]), float(next_bar["low"])
            if open_trade.direction == "BUY":
                if l <= open_trade.stop_loss:
                    diff = open_trade.stop_loss - open_trade.entry_price
                    open_trade.exit_bar, open_trade.exit_time = i+1, next_bar.name
                    open_trade.exit_price = open_trade.stop_loss
                    open_trade.pnl_pips = diff / config.pip_size - config.commission_pips
                    open_trade.pnl_usd = _calc_pnl_usd(diff, open_trade.position_size, open_trade.entry_price)
                    # Commission: use _pip_value_usd for consistency
                    open_trade.pnl_usd -= config.commission_pips * _pip_value_usd(open_trade.entry_price, open_trade.position_size) if ac == "forex" else 0
                    open_trade.exit_reason = "SL"
                    balance += open_trade.pnl_usd
                    result.trades.append(open_trade); open_trade = None
                elif h >= open_trade.take_profit:
                    diff = open_trade.take_profit - open_trade.entry_price
                    open_trade.exit_bar, open_trade.exit_time = i+1, next_bar.name
                    open_trade.exit_price = open_trade.take_profit
                    open_trade.pnl_pips = diff / config.pip_size - config.commission_pips
                    open_trade.pnl_usd = _calc_pnl_usd(diff, open_trade.position_size, open_trade.entry_price)
                    open_trade.pnl_usd -= config.commission_pips * _pip_value_usd(open_trade.entry_price, open_trade.position_size) if ac == "forex" else 0
                    open_trade.exit_reason = "TP"
                    balance += open_trade.pnl_usd
                    result.trades.append(open_trade); open_trade = None
            else:  # SELL
                if h >= open_trade.stop_loss:
                    diff = open_trade.entry_price - open_trade.stop_loss
                    open_trade.exit_bar, open_trade.exit_time = i+1, next_bar.name
                    open_trade.exit_price = open_trade.stop_loss
                    open_trade.pnl_pips = diff / config.pip_size - config.commission_pips
                    open_trade.pnl_usd = _calc_pnl_usd(diff, open_trade.position_size, open_trade.entry_price)
                    open_trade.pnl_usd -= config.commission_pips * _pip_value_usd(open_trade.entry_price, open_trade.position_size) if ac == "forex" else 0
                    open_trade.exit_reason = "SL"
                    balance += open_trade.pnl_usd
                    result.trades.append(open_trade); open_trade = None
                elif l <= open_trade.take_profit:
                    diff = open_trade.entry_price - open_trade.take_profit
                    open_trade.exit_bar, open_trade.exit_time = i+1, next_bar.name
                    open_trade.exit_price = open_trade.take_profit
                    open_trade.pnl_pips = diff / config.pip_size - config.commission_pips
                    open_trade.pnl_usd = _calc_pnl_usd(diff, open_trade.position_size, open_trade.entry_price)
                    open_trade.pnl_usd -= config.commission_pips * _pip_value_usd(open_trade.entry_price, open_trade.position_size) if ac == "forex" else 0
                    open_trade.exit_reason = "TP"
                    balance += open_trade.pnl_usd
                    result.trades.append(open_trade); open_trade = None

        result.equity_curve.append(balance)

        # Skip if in trade or not on step
        if open_trade is not None or (i - config.warmup_bars) % config.step_bars != 0:
            continue

        # --- Run pipeline ---
        result.total_runs += 1
        try:
            window = df.iloc[:i+1]
            mtf = build_multi_timeframe_view(window, timeframes)
            outputs = [e.safe_analyze(mtf) for e in engines_list]
            vote = tally_votes(outputs, weights)
            score = calculate_score(outputs, weights)
            contradiction = check_contradictions(outputs)

            ok = (
                score.final_score >= min_score
                and vote.agree_count >= min_engines
                and not contradiction.blocked
                and vote.winning_bias.value != "NEUTRAL"
            )
            if not ok:
                result.no_trade_count += 1
                continue

            entry = float(next_bar["open"])
            atr_val = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else 0.001
            sl_dist = atr_val * 1.5
            tp_dist = sl_dist * config.min_rr
            direction = vote.winning_bias.value

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
            result.no_trade_count += 1

    # Force-close
    if open_trade is not None:
        last = df.iloc[-1]
        exit_p = float(last["close"])
        if open_trade.direction == "BUY":
            diff = exit_p - open_trade.entry_price
        else:
            diff = open_trade.entry_price - exit_p
        open_trade.exit_bar = len(df)-1
        open_trade.exit_time = last.name
        open_trade.exit_price = exit_p
        open_trade.pnl_pips = diff / config.pip_size - config.commission_pips
        open_trade.pnl_usd = _calc_pnl_usd(diff, open_trade.position_size, open_trade.entry_price)
        open_trade.pnl_usd -= config.commission_pips * _pip_value_usd(open_trade.entry_price, open_trade.position_size) if ac == "forex" else 0
        open_trade.exit_reason = "FORCED_CLOSE"
        balance += open_trade.pnl_usd
        result.trades.append(open_trade)

    result.equity_curve.append(balance)
    result.compute()
    logger.info(f"Backtest done: {result.execute_count} trades, WR={result.win_rate:.1%}")
    return result


def _close_trade(trade: Trade, bar: int, bar_data, exit_price: float,
                 pnl_pips: float, pip_size: float, reason: str) -> None:
    trade.exit_bar = bar
    trade.exit_time = bar_data.name
    trade.exit_price = exit_price
    trade.pnl_pips = pnl_pips
    trade.pnl_usd = pnl_pips * pip_size * trade.position_size * 100000
    trade.exit_reason = reason


# Monkey-patch: override _close_trade with asset-class-aware version
_orig_close_trade = _close_trade

def _close_trade_v2(trade: Trade, bar: int, bar_data, exit_price: float,
                    pnl_pips: float, pip_size: float, reason: str,
                    asset_class: str = "forex",
                    dollar_per_point: float = 1.0) -> None:
    trade.exit_bar = bar
    trade.exit_time = bar_data.name
    trade.exit_price = exit_price
    trade.pnl_pips = pnl_pips
    if asset_class == "forex":
        trade.pnl_usd = pnl_pips * pip_size * trade.position_size * 100_000
    else:
        # Metals/Indices: price_diff (in asset units) × lots × dollar_per_point
        trade.pnl_usd = pnl_pips * trade.position_size * dollar_per_point
    trade.exit_reason = reason
