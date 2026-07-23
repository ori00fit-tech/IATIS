"""
tests/test_trade_math.py
--------------------------
Unit tests for utils/trade_math.py — the consolidated P&L math shared by
storage/journal.py, storage/outcome_tracker.py, and
scripts/repair_outcome_pips.py (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-9). These are the tests
that must never let a regression back into any of the three call sites.
"""
from __future__ import annotations

import pytest

from utils.trade_math import is_buy_direction, price_diff, profit_factor, realized_r


# ── is_buy_direction ─────────────────────────────────────────────────────

@pytest.mark.parametrize("direction", ["BUY", "BULLISH"])
def test_is_buy_direction_true_cases(direction):
    assert is_buy_direction(direction) is True


@pytest.mark.parametrize("direction", ["SELL", "BEARISH", "", None, "buy", "long"])
def test_is_buy_direction_false_cases(direction):
    assert is_buy_direction(direction) is False


# ── price_diff ────────────────────────────────────────────────────────────

def test_price_diff_long_profit():
    assert price_diff(1.0850, 1.0950, "BULLISH") == pytest.approx(0.0100)


def test_price_diff_long_loss():
    assert price_diff(1.0850, 1.0800, "BULLISH") == pytest.approx(-0.0050)


def test_price_diff_short_profit():
    assert price_diff(1.0850, 1.0640, "BEARISH") == pytest.approx(0.0210)


def test_price_diff_short_loss():
    assert price_diff(1.0850, 1.0950, "SELL") == pytest.approx(-0.0100)


def test_price_diff_unrecognized_direction_treated_as_short():
    # Matches every existing call site's behavior: anything not in
    # BUY_DIRECTIONS falls through to the short-side formula.
    assert price_diff(100.0, 90.0, "UNKNOWN") == pytest.approx(10.0)


# ── realized_r ────────────────────────────────────────────────────────────

def test_realized_r_short_two_r_win():
    # 1.0850 short, SL 1.0920 (risk 0.0070), exit 1.0640 -> +3.0R
    # (the exact fixture used throughout tests/test_journal.py)
    r = realized_r(entry=1.0850, stop_loss=1.0920, exit_price=1.0640, direction="BEARISH")
    assert r == pytest.approx(3.0, abs=0.01)


def test_realized_r_long_full_sl_loss_is_minus_one():
    r = realized_r(entry=1.0850, stop_loss=1.0800, exit_price=1.0800, direction="BULLISH")
    assert r == pytest.approx(-1.0)


@pytest.mark.parametrize("entry,sl,exit_px", [
    (None, 1.08, 1.09),
    (1.08, None, 1.09),
    (1.08, 1.07, None),
])
def test_realized_r_missing_inputs_returns_none(entry, sl, exit_px):
    assert realized_r(entry, sl, exit_px, "BULLISH") is None


def test_realized_r_zero_stop_distance_returns_none():
    assert realized_r(entry=1.0850, stop_loss=1.0850, exit_price=1.0900, direction="BULLISH") is None


def test_realized_r_breakeven_is_zero():
    assert realized_r(entry=1.0850, stop_loss=1.0800, exit_price=1.0850, direction="BULLISH") == 0.0


# ── profit_factor ─────────────────────────────────────────────────────────

def test_profit_factor_normal_ratio():
    assert profit_factor([2.0, -1.0, 1.5, -0.5]) == pytest.approx(3.5 / 1.5)


def test_profit_factor_wins_with_zero_losses_is_infinity_sentinel():
    assert profit_factor([1.0, 2.0, 3.0]) == "Infinity"


def test_profit_factor_all_breakeven_is_none_not_infinity():
    """The exact bug this module's consolidation fixed (P3-2/P2-9): zero
    wins AND zero losses is 0/0 (undefined), not infinite."""
    assert profit_factor([0.0, 0.0]) is None


def test_profit_factor_empty_list_is_none():
    assert profit_factor([]) is None


def test_profit_factor_only_losses_is_zero():
    assert profit_factor([-1.0, -2.0]) == 0.0
