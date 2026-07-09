"""
tests/test_risk_rr_boundary.py
-------------------------------
Floating-point boundary regression for the risk/reward floor.

SL/TP are constructed as entry ± atr·mult(·rr) (main._risk_gate), so a
signal at the configured floor has rr == min_rr exactly in real
arithmetic. The float add/subtract round-trip leaves rr ~1e-14 short on
~25% of price/ATR combinations, and the old strict `<` comparison
rejected those valid signals with the self-contradicting reason
"Risk/reward 2.00 below minimum required 2.00" (17 observed live).
"""

import numpy as np
import pytest

from risk.risk_engine import RiskInputs, evaluate_risk

CONFIG = {"risk": {
    "min_risk_reward": 2.0, "max_exposure": 0.05,
    "max_drawdown_reduce": 0.10, "max_drawdown_stop": 0.15,
    "risk_per_trade_min": 0.0025, "risk_per_trade_max": 0.01,
}}


def _inputs(entry, atr, direction=1, rr=2.0):
    """Exactly how main._risk_gate constructs the levels."""
    stop = entry - direction * atr * 2.5
    target = entry + direction * atr * 2.5 * rr
    return RiskInputs(
        account_balance=10_000.0,
        entry_price=float(entry), stop_loss_price=float(stop),
        take_profit_price=float(target),
    )


def test_exact_floor_construction_passes_despite_float_dust():
    # Sweep price/ATR magnitudes; every exact-floor construction must pass.
    rng = np.random.default_rng(7)
    for _ in range(2000):
        entry = np.float64(rng.uniform(0.5, 50_000))
        atr = np.float64(entry * rng.uniform(0.0005, 0.02))
        for d in (1, -1):
            res = evaluate_risk(_inputs(entry, atr, d), CONFIG)
            assert res.passed, (
                f"exact-floor signal rejected: entry={entry!r} atr={atr!r} "
                f"dir={d}: {res.reasons}"
            )


def test_genuinely_low_rr_still_rejected():
    res = evaluate_risk(_inputs(np.float64(1.0850), np.float64(0.0012), rr=1.5), CONFIG)
    assert not res.passed
    assert any("Risk/reward" in r for r in res.reasons)


def test_marginally_low_rr_still_rejected():
    # 1% below the floor is a real violation, far outside the tolerance.
    res = evaluate_risk(_inputs(np.float64(1.0850), np.float64(0.0012), rr=1.98), CONFIG)
    assert not res.passed
    assert any("Risk/reward" in r for r in res.reasons)
