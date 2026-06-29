"""
backtest/metrics.py
--------------------
Professional trading metrics calculation.

All metrics a quant fund would expect:
  Risk-adjusted returns: Sharpe, Sortino, Calmar, MAR
  Drawdown analysis: Max DD, Average DD, DD Duration
  Trade statistics: WR, PF, Expectancy, Avg RR, MFE, MAE
  Distribution: Monthly returns, Yearly returns, Regime performance
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any
import pandas as pd
import numpy as np


@dataclass
class TradeRecord:
    """Single trade record with full context."""
    trade_id:     str
    symbol:       str
    direction:    str        # BUY / SELL
    entry_time:   pd.Timestamp
    exit_time:    pd.Timestamp | None
    entry_price:  float
    exit_price:   float | None
    stop_loss:    float
    take_profit:  float
    position_size: float
    pnl_usd:      float = 0.0
    pnl_pips:     float = 0.0
    rr_actual:    float = 0.0    # actual R:R achieved
    rr_planned:   float = 0.0    # planned R:R at entry
    mfe:          float = 0.0    # Maximum Favorable Excursion
    mae:          float = 0.0    # Maximum Adverse Excursion
    holding_bars: int = 0
    exit_reason:  str = ""       # TP / SL / TIMEOUT / MANUAL
    regime:       str = ""
    session:      str = ""
    cf_score:     float = 0.0    # confluence score at entry
    engine_votes: dict = field(default_factory=dict)
    is_win:       bool = False


@dataclass
class BacktestMetrics:
    """Complete backtest metrics report."""
    # Basic
    total_trades:     int   = 0
    winning_trades:   int   = 0
    losing_trades:    int   = 0
    win_rate:         float = 0.0

    # P&L
    net_profit:       float = 0.0
    gross_profit:     float = 0.0
    gross_loss:       float = 0.0
    profit_factor:    float = 0.0
    expectancy:       float = 0.0     # avg $ per trade
    expectancy_r:     float = 0.0     # avg R per trade

    # Risk-adjusted
    sharpe_ratio:     float = 0.0
    sortino_ratio:    float = 0.0
    calmar_ratio:     float = 0.0
    mar_ratio:        float = 0.0

    # Drawdown
    max_drawdown:     float = 0.0     # %
    max_drawdown_usd: float = 0.0
    avg_drawdown:     float = 0.0
    max_dd_duration:  int   = 0       # bars

    # Trade stats
    avg_rr:           float = 0.0
    avg_holding_bars: float = 0.0
    avg_win:          float = 0.0
    avg_loss:         float = 0.0
    largest_win:      float = 0.0
    largest_loss:     float = 0.0
    max_consecutive_wins:  int = 0
    max_consecutive_losses:int = 0

    # MFE / MAE
    avg_mfe:          float = 0.0
    avg_mae:          float = 0.0

    # Returns
    total_return_pct: float = 0.0
    annual_return:    float = 0.0
    monthly_returns:  dict  = field(default_factory=dict)
    yearly_returns:   dict  = field(default_factory=dict)

    # Exposure
    exposure_pct:     float = 0.0    # % time in market

    # By category
    by_direction:     dict  = field(default_factory=dict)
    by_session:       dict  = field(default_factory=dict)
    by_regime:        dict  = field(default_factory=dict)
    by_symbol:        dict  = field(default_factory=dict)
    by_engine:        dict  = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    def summary(self) -> str:
        return (
            f"Trades: {self.total_trades} | "
            f"WR: {self.win_rate:.1f}% | "
            f"PF: {self.profit_factor:.2f} | "
            f"Sharpe: {self.sharpe_ratio:.2f} | "
            f"MaxDD: {self.max_drawdown:.1f}% | "
            f"Net: ${self.net_profit:,.0f}"
        )


def calculate_metrics(
    trades: list[TradeRecord],
    initial_capital: float = 10_000.0,
    risk_free_rate: float = 0.04,   # 4% annual
    bars_per_year: int = 8760,       # H1 bars
) -> BacktestMetrics:
    """
    Calculate comprehensive backtest metrics from trade list.

    Args:
        trades: list of TradeRecord
        initial_capital: starting capital
        risk_free_rate: annual risk-free rate for Sharpe
        bars_per_year: number of bars in a year (8760 for H1, 252 for daily)

    Returns:
        BacktestMetrics with all statistics
    """
    m = BacktestMetrics()

    if not trades:
        return m

    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        return m

    m.total_trades    = len(closed)
    m.winning_trades  = sum(1 for t in closed if t.is_win)
    m.losing_trades   = m.total_trades - m.winning_trades
    m.win_rate        = m.winning_trades / m.total_trades * 100

    # P&L
    pnls = [t.pnl_usd for t in closed]
    m.net_profit   = sum(pnls)
    m.gross_profit = sum(p for p in pnls if p > 0)
    m.gross_loss   = abs(sum(p for p in pnls if p < 0))
    m.profit_factor = (m.gross_profit / m.gross_loss) if m.gross_loss > 0 else float("inf")
    m.expectancy   = m.net_profit / m.total_trades
    m.avg_win      = m.gross_profit / m.winning_trades if m.winning_trades > 0 else 0
    m.avg_loss     = m.gross_loss / m.losing_trades if m.losing_trades > 0 else 0
    m.largest_win  = max(pnls)
    m.largest_loss = min(pnls)

    # Expectancy in R
    rrs = [t.rr_actual for t in closed if t.rr_actual != 0]
    m.avg_rr = sum(rrs) / len(rrs) if rrs else 0
    win_rrs  = [t.rr_actual for t in closed if t.is_win and t.rr_actual > 0]
    loss_rrs = [abs(t.rr_actual) for t in closed if not t.is_win and t.rr_actual != 0]
    wr = m.win_rate / 100
    avg_win_r  = sum(win_rrs) / len(win_rrs) if win_rrs else 0
    avg_loss_r = sum(loss_rrs) / len(loss_rrs) if loss_rrs else 1
    m.expectancy_r = wr * avg_win_r - (1 - wr) * avg_loss_r

    # MFE / MAE
    m.avg_mfe = sum(t.mfe for t in closed) / len(closed)
    m.avg_mae = sum(t.mae for t in closed) / len(closed)

    # Holding time
    m.avg_holding_bars = sum(t.holding_bars for t in closed) / len(closed)

    # Equity curve
    equity = [initial_capital]
    peak   = initial_capital
    dds    = []
    dd_starts = []

    for t in closed:
        equity.append(equity[-1] + t.pnl_usd)
        if equity[-1] > peak:
            peak = equity[-1]
        dd = (peak - equity[-1]) / peak * 100
        dds.append(dd)

    m.max_drawdown     = max(dds) if dds else 0
    m.max_drawdown_usd = m.max_drawdown / 100 * initial_capital
    m.avg_drawdown     = sum(dds) / len(dds) if dds else 0
    m.total_return_pct = (equity[-1] - initial_capital) / initial_capital * 100

    # Consecutive wins/losses
    max_cw = max_cl = cur_cw = cur_cl = 0
    for t in closed:
        if t.is_win:
            cur_cw += 1; cur_cl = 0
            max_cw = max(max_cw, cur_cw)
        else:
            cur_cl += 1; cur_cw = 0
            max_cl = max(max_cl, cur_cl)
    m.max_consecutive_wins   = max_cw
    m.max_consecutive_losses = max_cl

    # Sharpe / Sortino
    if len(pnls) >= 2:
        pnl_arr = np.array(pnls)
        avg_pnl = np.mean(pnl_arr)
        std_pnl = np.std(pnl_arr, ddof=1)
        rf_per_trade = risk_free_rate / bars_per_year * m.avg_holding_bars

        if std_pnl > 0:
            m.sharpe_ratio = (avg_pnl - rf_per_trade * initial_capital) / std_pnl * math.sqrt(m.total_trades)

        neg_pnls = pnl_arr[pnl_arr < 0]
        downside_std = np.std(neg_pnls, ddof=1) if len(neg_pnls) >= 2 else std_pnl
        if downside_std > 0:
            m.sortino_ratio = (avg_pnl - rf_per_trade * initial_capital) / downside_std * math.sqrt(m.total_trades)

    # Calmar
    if m.max_drawdown > 0:
        m.calmar_ratio = m.total_return_pct / m.max_drawdown

    # Monthly / Yearly returns
    sorted_trades = sorted(closed, key=lambda t: t.entry_time)
    monthly: dict[str, float] = {}
    yearly:  dict[int, float] = {}
    for t in sorted_trades:
        if t.entry_time:
            month_key = t.entry_time.strftime("%Y-%m")
            year_key  = t.entry_time.year
            monthly[month_key] = monthly.get(month_key, 0) + t.pnl_usd
            yearly[year_key]   = yearly.get(year_key, 0) + t.pnl_usd

    m.monthly_returns = monthly
    m.yearly_returns  = yearly

    # Annual return
    if sorted_trades and sorted_trades[0].entry_time and sorted_trades[-1].entry_time:
        days = (sorted_trades[-1].entry_time - sorted_trades[0].entry_time).days or 1
        m.annual_return = m.total_return_pct * 365 / days

    # By direction
    for direction in ("BUY", "SELL"):
        subset = [t for t in closed if t.direction == direction]
        if subset:
            wins = sum(1 for t in subset if t.is_win)
            pnl  = sum(t.pnl_usd for t in subset)
            m.by_direction[direction] = {
                "trades": len(subset), "wins": wins,
                "win_rate": wins/len(subset)*100, "pnl": pnl,
            }

    # By session
    for t in closed:
        s = t.session or "Unknown"
        if s not in m.by_session:
            m.by_session[s] = {"trades": 0, "wins": 0, "pnl": 0.0}
        m.by_session[s]["trades"] += 1
        m.by_session[s]["pnl"]    += t.pnl_usd
        if t.is_win:
            m.by_session[s]["wins"] += 1

    # By regime
    for t in closed:
        r = t.regime or "Unknown"
        if r not in m.by_regime:
            m.by_regime[r] = {"trades": 0, "wins": 0, "pnl": 0.0}
        m.by_regime[r]["trades"] += 1
        m.by_regime[r]["pnl"]    += t.pnl_usd
        if t.is_win:
            m.by_regime[r]["wins"] += 1

    # Win rates per category
    for cat_dict in (m.by_session, m.by_regime, m.by_direction):
        for v in cat_dict.values():
            if isinstance(v, dict) and v.get("trades", 0) > 0:
                v["win_rate"] = v.get("wins", 0) / v["trades"] * 100

    return m
