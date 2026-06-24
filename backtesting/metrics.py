"""
backtesting/metrics.py
--------------------------
Standalone metrics calculation from backtest results.
"""
from __future__ import annotations
import numpy as np
from backtesting.backtest_engine import BacktestResult


def sharpe_ratio(equity_curve: list, periods_per_year: int = 252) -> float:
    arr = np.array(equity_curve)
    returns = np.diff(arr) / arr[:-1]
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def max_consecutive_losses(result: BacktestResult) -> int:
    closed = [t for t in result.trades if t.exit_bar >= 0]
    max_streak = streak = 0
    for t in closed:
        if t.pnl_usd < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def expectancy(result: BacktestResult) -> float:
    """Average USD profit/loss per trade."""
    closed = [t for t in result.trades if t.exit_bar >= 0]
    if not closed:
        return 0.0
    return sum(t.pnl_usd for t in closed) / len(closed)
