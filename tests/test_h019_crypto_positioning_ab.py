"""tests/test_h019_crypto_positioning_ab.py — the pre-registered verdict
logic, pinned to the registry text. Mirrors the H023/H037 test discipline:
only the pure decision function is unit-tested here (no market data
needed); the actual A/B backtest loop needs real price + funding-rate
data and only runs on the VPS."""
from __future__ import annotations

from research.experiments.H019_crypto_positioning_ab import (
    DECISION,
    positioning_verdict,
)


def test_adopt_when_both_symbols_improve_enough():
    v, checks, reasons = positioning_verdict({"BTCUSD": 0.10, "ETHUSD": 0.06})
    assert v.startswith("ADOPT")
    assert all(checks.values())


def test_reject_when_mean_delta_too_small():
    v, checks, _ = positioning_verdict({"BTCUSD": 0.02, "ETHUSD": 0.01})
    assert v.startswith("FAILED")
    assert checks["1_mean_dPF>=0.05"] is False


def test_reject_when_one_symbol_regresses_even_if_mean_passes():
    """n=2 is too small for the '>=1 losing symbol tolerated' pattern
    used elsewhere in this registry — a single loser fails outright."""
    v, checks, reasons = positioning_verdict({"BTCUSD": 0.30, "ETHUSD": -0.05})
    assert v.startswith("FAILED")
    assert checks["2_zero_losing_symbols"] is False
    assert any("ETHUSD" in r for r in reasons)


def test_boundary_exactly_at_threshold_passes():
    v, checks, _ = positioning_verdict({"BTCUSD": 0.05, "ETHUSD": 0.05})
    assert v.startswith("ADOPT")


def test_both_symbols_flat_fails():
    v, _, _ = positioning_verdict({"BTCUSD": 0.0, "ETHUSD": 0.0})
    assert v.startswith("FAILED")


def test_decision_constants_match_registry_text():
    assert DECISION["min_mean_dPF"] == 0.05
    assert DECISION["max_losing_symbols"] == 0
