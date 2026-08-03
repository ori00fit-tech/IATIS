"""
tests/test_risk_engine_fuzzing.py
-----------------------------------
Forensic Audit (2026-08-04) — edge-case fuzzing for risk/risk_engine.py,
the "sovereign" risk gate (module docstring: "it has the authority to
make a trade not exist at all"). Confirms BUG-006's fix: the gate must
fail CLOSED on invalid/degenerate/nonsensical input, never silently
compute a passed=True result from it.

Root cause pinned by these tests: Python's comparison operators (>=, <,
>) all evaluate to False when either operand is NaN — so before this
fix, every hard-gate check in evaluate_risk() (drawdown stop, RR floor,
correlation cap, exposure cap) would silently be BYPASSED (not failed)
by a NaN input, and a negative/zero account_balance produced a negative
position size instead of being refused outright.
"""
from __future__ import annotations

import math

import pytest

from risk.risk_engine import RiskInputs, evaluate_risk

CONFIG = {
    "risk": {
        "min_risk_reward": 2.0,
        "max_exposure": 0.05,
        "max_drawdown_reduce": 0.10,
        "max_drawdown_stop": 0.15,
        "risk_per_trade_min": 0.0025,
        "risk_per_trade_max": 0.01,
    }
}


def _good_inputs(**overrides) -> RiskInputs:
    base = dict(
        account_balance=10_000.0,
        entry_price=1.1000,
        stop_loss_price=1.0950,
        take_profit_price=1.1150,
    )
    base.update(overrides)
    return RiskInputs(**base)


def test_sanity_a_normal_valid_trade_passes():
    r = evaluate_risk(_good_inputs(), CONFIG)
    assert r.passed is True
    assert r.position_size_units is not None
    assert r.position_size_units > 0


# ── NaN inputs — must fail CLOSED, not silently bypass every hard gate ──

@pytest.mark.parametrize("field", [
    "account_balance", "entry_price", "stop_loss_price", "take_profit_price",
    "current_open_risk_pct", "current_drawdown_pct", "correlated_exposure_pct",
])
def test_nan_in_any_numeric_field_fails_closed(field):
    inputs = _good_inputs(**{field: float("nan")})
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert field in r.reasons[0]
    assert r.position_size_units is None


def test_nan_drawdown_does_not_bypass_the_hard_stop():
    """The specific reproduction that exposed BUG-006: a NaN drawdown
    must never silently sail through the system-level drawdown halt."""
    inputs = _good_inputs(current_drawdown_pct=float("nan"))
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False


def test_nan_correlated_exposure_does_not_bypass_the_correlation_cap():
    inputs = _good_inputs(correlated_exposure_pct=float("nan"))
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False


# ── Infinite inputs — same fail-closed contract as NaN ──────────────────

@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["entry_price", "stop_loss_price", "take_profit_price", "account_balance"])
def test_infinite_in_any_numeric_field_fails_closed(field, value):
    inputs = _good_inputs(**{field: value})
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False


# ── Account balance edge cases ──────────────────────────────────────────

def test_negative_account_balance_is_refused_not_negatively_sized():
    """Pre-fix this returned passed=True with position_size_units=-1000.0
    (a negative position size) — a blown/insolvent account must be
    refused outright, never sized."""
    inputs = _good_inputs(account_balance=-500.0)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert r.position_size_units is None
    assert "not positive" in r.reasons[0]


def test_zero_account_balance_is_refused():
    inputs = _good_inputs(account_balance=0.0)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert r.position_size_units is None


# ── Degenerate SL/TP geometry ────────────────────────────────────────────

def test_sl_equal_to_entry_is_rejected_via_zero_rr():
    """ATR=0 in practice manifests as stop_loss == entry_price — the
    existing _risk_reward_ratio() already returns rr=0.0 for this,
    which the RR floor correctly rejects."""
    inputs = _good_inputs(stop_loss_price=1.1000)  # == entry_price
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert "Risk/reward" in r.reasons[0]


def test_tp_equal_to_entry_is_rejected_via_zero_reward():
    inputs = _good_inputs(take_profit_price=1.1000)  # == entry_price
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False


def test_backwards_stop_on_long_setup_is_rejected():
    """BUG-006: target above entry implies a long, so stop must be
    below entry. A stop on the SAME side as the target (a backwards
    stop) previously computed a technically-passing RR ratio via
    abs()-only magnitude math and slipped through."""
    inputs = _good_inputs(entry_price=1.10, stop_loss_price=1.20, take_profit_price=1.30)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert "backwards stop" in r.reasons[0]


def test_backwards_stop_on_short_setup_is_rejected():
    inputs = _good_inputs(entry_price=1.10, stop_loss_price=1.00, take_profit_price=0.90)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert "backwards stop" in r.reasons[0]


def test_correct_long_geometry_still_passes():
    """Regression pin: the directional-sanity check must not reject
    correctly-constructed trades."""
    inputs = _good_inputs(entry_price=1.10, stop_loss_price=1.05, take_profit_price=1.20)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is True


def test_correct_short_geometry_still_passes():
    inputs = _good_inputs(entry_price=1.10, stop_loss_price=1.15, take_profit_price=1.00)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is True


# ── Drawdown / exposure boundary values ──────────────────────────────────

def test_drawdown_over_100_percent_still_halts_correctly():
    """A drawdown > 100% (equity below zero, an extreme but finite
    value) must still trip the hard stop — not just NaN."""
    inputs = _good_inputs(current_drawdown_pct=1.50)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert "drawdown" in r.reasons[0].lower()


def test_max_exposure_boundary_exact_limit_passes():
    """Exactly at the limit is allowed — the check is a strict '>',
    matching evaluate_risk()'s own comment convention elsewhere."""
    inputs = _good_inputs(current_open_risk_pct=0.04)  # + risk_max(0.01) = 0.05 == max_exposure
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is True


def test_max_exposure_just_over_boundary_blocks():
    inputs = _good_inputs(current_open_risk_pct=0.0401)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert "exposure" in r.reasons[0].lower()


def test_correlation_limit_exact_boundary_blocks():
    inputs = _good_inputs(correlated_exposure_pct=0.10, correlation_limit_pct=0.10)
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert "Correlated exposure" in r.reasons[0]


# ── Combined / adversarial cases ─────────────────────────────────────────

def test_all_bad_at_once_still_fails_closed_on_first_invalid_field():
    """Multiple simultaneous invalid inputs must still refuse the trade
    (not crash, not partially validate)."""
    inputs = RiskInputs(
        account_balance=float("nan"), entry_price=float("inf"),
        stop_loss_price=-500.0, take_profit_price=float("nan"),
        current_drawdown_pct=2.0, correlated_exposure_pct=float("nan"),
    )
    r = evaluate_risk(inputs, CONFIG)
    assert r.passed is False
    assert r.position_size_units is None


def test_does_not_raise_on_any_fuzzed_input():
    """No fuzzed combination should ever raise — a risk gate crashing
    is exactly as dangerous as one that silently passes."""
    values = [0.0, -1.0, 1.0, float("nan"), float("inf"), float("-inf"), 1e300, -1e300]
    for bal in values:
        for entry in values:
            for stop in values:
                for tp in values:
                    inputs = RiskInputs(
                        account_balance=bal, entry_price=entry,
                        stop_loss_price=stop, take_profit_price=tp,
                    )
                    r = evaluate_risk(inputs, CONFIG)
                    assert isinstance(r.passed, bool)
