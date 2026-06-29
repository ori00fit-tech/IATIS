"""
backtest/monte_carlo.py
------------------------
Monte Carlo simulation for risk analysis.

Runs N simulations by shuffling trade sequence to estimate:
  - Worst case drawdown
  - Expected return distribution
  - Risk of Ruin
  - Confidence intervals
"""
from __future__ import annotations
import random
from dataclasses import dataclass
import numpy as np
from backtest.metrics import TradeRecord, calculate_metrics


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo simulation."""
    simulations:        int
    # Return distribution
    median_return:      float
    mean_return:        float
    p5_return:          float    # 5th percentile (pessimistic)
    p95_return:         float    # 95th percentile (optimistic)
    # Drawdown distribution
    median_max_dd:      float
    worst_max_dd:       float
    p95_max_dd:         float    # 95th percentile drawdown
    # Risk metrics
    risk_of_ruin:       float    # % simulations that lost >50%
    probability_profit: float    # % simulations with positive return
    # Sharpe distribution
    median_sharpe:      float
    p5_sharpe:          float


def run_monte_carlo(
    trades: list[TradeRecord],
    initial_capital: float = 10_000.0,
    n_simulations: int = 1000,
    ruin_threshold: float = 0.50,   # 50% loss = ruin
    seed: int | None = 42,
) -> MonteCarloResult:
    """
    Run Monte Carlo by shuffling trade order N times.

    Args:
        trades: original trade list (closed trades only)
        initial_capital: starting capital
        n_simulations: number of Monte Carlo runs
        ruin_threshold: fraction of capital loss = ruin (0.5 = 50%)
        seed: random seed for reproducibility

    Returns:
        MonteCarloResult with distribution statistics
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    closed = [t for t in trades if t.exit_price is not None]
    if len(closed) < 5:
        return MonteCarloResult(
            simulations=0, median_return=0, mean_return=0,
            p5_return=0, p95_return=0, median_max_dd=0,
            worst_max_dd=0, p95_max_dd=0, risk_of_ruin=0,
            probability_profit=0, median_sharpe=0, p5_sharpe=0,
        )

    returns    = []
    max_dds    = []
    sharpes    = []
    ruins      = 0

    pnls_base = [t.pnl_usd for t in closed]

    for _ in range(n_simulations):
        # Shuffle trade order
        sim_pnls = pnls_base.copy()
        random.shuffle(sim_pnls)

        # Calculate equity curve
        equity = initial_capital
        peak   = initial_capital
        max_dd = 0.0

        for pnl in sim_pnls:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        total_return = (equity - initial_capital) / initial_capital * 100
        returns.append(total_return)
        max_dds.append(max_dd * 100)

        if (initial_capital - equity) / initial_capital >= ruin_threshold:
            ruins += 1

        # Sharpe (simplified)
        arr = np.array(sim_pnls)
        if arr.std() > 0:
            sharpes.append(arr.mean() / arr.std() * np.sqrt(len(arr)))
        else:
            sharpes.append(0.0)

    returns_arr = np.array(returns)
    dds_arr     = np.array(max_dds)
    sharpes_arr = np.array(sharpes)

    return MonteCarloResult(
        simulations        = n_simulations,
        median_return      = float(np.median(returns_arr)),
        mean_return        = float(np.mean(returns_arr)),
        p5_return          = float(np.percentile(returns_arr, 5)),
        p95_return         = float(np.percentile(returns_arr, 95)),
        median_max_dd      = float(np.median(dds_arr)),
        worst_max_dd       = float(np.max(dds_arr)),
        p95_max_dd         = float(np.percentile(dds_arr, 95)),
        risk_of_ruin       = ruins / n_simulations * 100,
        probability_profit = float(np.mean(returns_arr > 0) * 100),
        median_sharpe      = float(np.median(sharpes_arr)),
        p5_sharpe          = float(np.percentile(sharpes_arr, 5)),
    )

    def print_summary(self) -> None:
        print(f"\n{'='*50}")
        print(f"Monte Carlo ({self.simulations} simulations)")
        print(f"{'='*50}")
        print(f"Return:    median={self.median_return:.1f}%  "
              f"[{self.p5_return:.1f}% — {self.p95_return:.1f}%]")
        print(f"Max DD:    median={self.median_max_dd:.1f}%  "
              f"worst={self.worst_max_dd:.1f}%  95th={self.p95_max_dd:.1f}%")
        print(f"Risk Ruin: {self.risk_of_ruin:.1f}%")
        print(f"P(Profit): {self.probability_profit:.1f}%")
        print(f"Sharpe:    median={self.median_sharpe:.2f}  "
              f"5th pct={self.p5_sharpe:.2f}")
