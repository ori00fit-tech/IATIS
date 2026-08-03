"""
tests/test_monte_carlo_forensics.py
-------------------------------------
Forensic Audit (2026-08-04) — measurement-instrument audit of
backtest/monte_carlo.py, continuing downstream of the already-fixed
backtest_engine.py (BUG-002 through BUG-006).

BUG-007 (this file): `risk_of_ruin` was computed from FINAL equity vs.
starting capital — an order-independent statistic that is IDENTICAL for
every permutation of a fixed trade-PnL multiset (sum is order-invariant).
The already-computed, genuinely path-dependent `max_dd` was sitting right
there in scope and unused for this purpose. Fixed by reusing `max_dd`
directly: `if max_dd >= ruin_threshold: ruins += 1`.

Also pins the `print_summary` dead-code fix (previously defined after
`run_monte_carlo()`'s own `return`, at the same indentation as the
function body — i.e. nested INSIDE the function, never reachable as a
`MonteCarloResult` method).
"""
from __future__ import annotations

import pandas as pd

from backtest.metrics import TradeRecord
from backtest.monte_carlo import MonteCarloResult, run_monte_carlo


def _trade(pnl: float, i: int) -> TradeRecord:
    return TradeRecord(
        trade_id=f"T{i}",
        symbol="EURUSD",
        direction="BUY",
        entry_time=pd.Timestamp("2024-01-01"),
        exit_time=pd.Timestamp("2024-01-02"),
        entry_price=1.1000,
        exit_price=1.1000 + pnl / 100000.0,
        stop_loss=1.0950,
        take_profit=1.1100,
        position_size=100000.0,
        pnl_usd=pnl,
    )


def test_print_summary_is_a_real_method_of_monte_carlo_result():
    """Regression pin: print_summary() was previously defined at the same
    indentation as run_monte_carlo()'s own body (after its `return`),
    making it dead code — never an actual dataclass method."""
    result = MonteCarloResult(
        simulations=10, median_return=1.0, mean_return=1.0,
        p5_return=-1.0, p95_return=3.0, median_max_dd=2.0,
        worst_max_dd=5.0, p95_max_dd=4.5, risk_of_ruin=0.0,
        probability_profit=60.0, median_sharpe=1.2, p5_sharpe=0.5,
    )
    assert hasattr(result, "print_summary")
    result.print_summary()  # must not raise


def test_risk_of_ruin_detects_intra_sequence_drawdown_not_just_final_loss():
    """BUG-007 reproduction: a trade sequence that is profitable OVERALL
    (positive final equity vs. start) can still contain simulated
    orderings that breach an intra-sequence drawdown threshold. The
    pre-fix code (checking final equity vs. initial_capital) could never
    detect this for an overall-profitable sequence; the fix (checking the
    already-computed max_dd) can.

    Construct trades whose SUM is positive but whose PnL magnitudes are
    large enough, relative to a small ruin_threshold, that some shuffled
    orderings must pass through a large intra-sequence drawdown before
    recovering.
    """
    pnls = [3000, -2500, 2500, -2500, 2500, -2500, 3000, -1000]
    assert sum(pnls) > 0  # overall profitable, by construction

    trades = [_trade(p, i) for i, p in enumerate(pnls)]
    result = run_monte_carlo(
        trades, initial_capital=10_000.0, n_simulations=500,
        ruin_threshold=0.20, seed=7,
    )

    assert result.mean_return > 0  # confirms the "overall profitable" premise held under shuffling too
    assert result.risk_of_ruin > 0.0, (
        "risk_of_ruin must detect at least some shuffled orderings breaching "
        "a 20% intra-sequence drawdown, even though the sequence is "
        "overall profitable — this is exactly what the pre-fix, "
        "final-equity-based check could never detect."
    )


def test_risk_of_ruin_matches_a_direct_recomputation_from_max_dd():
    """Cross-check run_monte_carlo()'s risk_of_ruin against an independent,
    hand-rolled recomputation using the same seed/shuffle sequence and the
    documented max_dd >= ruin_threshold criterion — proves the fix is
    exactly this criterion, not some other approximation."""
    import random

    pnls = [500, -400, 600, -450, 700, -300, -600, 800, -700, 550]
    trades = [_trade(p, i) for i, p in enumerate(pnls)]
    ruin_threshold = 0.05
    n_sims = 300

    result = run_monte_carlo(
        trades, initial_capital=10_000.0, n_simulations=n_sims,
        ruin_threshold=ruin_threshold, seed=123,
    )

    random.seed(123)
    manual_ruins = 0
    for _ in range(n_sims):
        sim_pnls = pnls.copy()
        random.shuffle(sim_pnls)
        equity = 10_000.0
        peak = equity
        max_dd = 0.0
        for pnl in sim_pnls:
            equity += pnl
            peak = max(peak, equity)
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
        if max_dd >= ruin_threshold:
            manual_ruins += 1

    assert result.risk_of_ruin == (manual_ruins / n_sims * 100)


def test_risk_of_ruin_zero_when_no_path_ever_breaches_threshold():
    """Sanity/regression floor: trades too small to ever breach a large
    ruin_threshold correctly report 0% risk of ruin under either the old
    or new formula — the fix must not manufacture false positives."""
    pnls = [10, -5, 8, -6, 12, -4, 9, -7]
    trades = [_trade(p, i) for i, p in enumerate(pnls)]
    result = run_monte_carlo(
        trades, initial_capital=10_000.0, n_simulations=200,
        ruin_threshold=0.50, seed=1,
    )
    assert result.risk_of_ruin == 0.0


def test_insufficient_trades_short_circuits_before_any_stat():
    """Existing short-circuit (fewer than 5 closed trades) must still
    return a well-formed, all-zero result — not touched by this fix, but
    pinned so future edits to the ruin logic can't silently break it."""
    trades = [_trade(100, i) for i in range(3)]
    result = run_monte_carlo(trades, n_simulations=100)
    assert result.simulations == 0
    assert result.risk_of_ruin == 0.0
