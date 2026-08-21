"""
tests/test_pretrade_limits.py
---------------------------------
risk/pretrade_limits.py — the P0 pre-trade hard-limits authority. Pure
logic tests, independent of any broker client.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from risk.pretrade_limits import (
    PendingOrder,
    PretradeContext,
    PretradeLimits,
    PretradeNotApproved,
    SymbolSpec,
    approved_context,
    compute_decision_id,
    evaluate_pretrade,
    load_pretrade_limits,
    manual_override_context,
    require_pretrade_approval,
    reset_rate_state,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_rate_state()
    yield
    reset_rate_state()


NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


def _order(**kwargs) -> PendingOrder:
    defaults = dict(
        decision_id="dec_1",
        symbol="EURUSD",
        direction="BUY",
        quantity=10.0,           # 0.10 lot, centi-lots -> ~$11,000 notional, comfortably under every default limit
        reference_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,      # RR = 0.01/0.005 = 2.0
        bar_time=(NOW - timedelta(minutes=5)).isoformat(),
    )
    defaults.update(kwargs)
    return PendingOrder(**defaults)


def _spec(**kwargs) -> SymbolSpec:
    defaults = dict(price_digits=5, quantity_step=1.0, min_quantity=1.0, max_quantity=10_000.0)
    defaults.update(kwargs)
    return SymbolSpec(**defaults)


def _context(**kwargs) -> PretradeContext:
    defaults = dict(
        now_utc=NOW,
        account_equity=10_000.0,
        open_positions_notional={},
        # $20,000 of existing (non-EURUSD) exposure — a fresh, all-zero
        # portfolio would make ANY first order mathematically 100% of
        # portfolio notional, tripping max_symbol_concentration_pct's
        # default 50% cap by construction. 20,000 leaves the baseline
        # $11,000 EURUSD order comfortably under both max_portfolio_
        # notional_usd (50,000) and max_symbol_concentration_pct (50%).
        portfolio_notional=20_000.0,
        symbol_spec=_spec(),
        executable_price=1.1000,
    )
    defaults.update(kwargs)
    return PretradeContext(**defaults)


def _limits(**kwargs) -> PretradeLimits:
    defaults = dict(
        enabled=True,
        max_notional_usd=50_000.0,
        max_quantity=1000.0,
        max_position_per_symbol_usd=20_000.0,
        max_portfolio_notional_usd=50_000.0,
        max_symbol_concentration_pct=0.5,
        price_collar_pct=0.02,
        max_slippage_pct=0.02,
        require_stop_loss=True,
        min_stop_distance_pct=0.0005,
        max_stop_distance_pct=0.10,
        min_risk_reward=2.0,
        max_stale_data_seconds=21_600,
        max_orders_per_window=10,
        order_window_seconds=3600,
        duplicate_window_seconds=30,
    )
    defaults.update(kwargs)
    return PretradeLimits(**defaults)


# ─── 1. Baseline accept ─────────────────────────────────────────────────────

def test_order_below_all_limits_is_approved():
    decision = evaluate_pretrade(_order(), _context(), _limits())
    assert decision.approved is True
    assert decision.violations == ()
    assert all(v != "REJECT" for v in decision.checks.values())


def test_approved_decision_records_pass_for_every_configured_check():
    decision = evaluate_pretrade(_order(), _context(), _limits())
    for name, status in decision.checks.items():
        assert status in ("PASS",), f"{name}={status}, expected PASS on a clean baseline order"


# ─── Disabled layer ─────────────────────────────────────────────────────────

def test_disabled_layer_approves_without_checking_anything():
    decision = evaluate_pretrade(_order(quantity=999_999.0), _context(), _limits(enabled=False))
    assert decision.approved is True
    assert decision.checks["pretrade_limits"].startswith("SKIP")


# ─── 2. Max notional ────────────────────────────────────────────────────────

def test_max_notional_exceeded_rejects():
    # 1.0 lot * 100,000 * 1.10 = $110,000 notional > $50,000 max
    decision = evaluate_pretrade(_order(quantity=1000.0), _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "max_notional" for v in decision.violations)


def test_max_notional_boundary_epsilon():
    # Isolate max_notional: null every other notional-dependent limit so a
    # deliberately large $110k test order doesn't also trip
    # max_position_per_symbol/max_portfolio_notional/max_symbol_concentration.
    limits = _limits(
        max_notional_usd=110_000.0,
        max_position_per_symbol_usd=None, max_portfolio_notional_usd=None,
        max_symbol_concentration_pct=None,
    )
    # exactly at the boundary (quantity=100 centi-lots = 1.0 lot -> $110,000 notional)
    at = evaluate_pretrade(_order(quantity=100.0), _context(), limits)
    assert at.approved is True
    over = evaluate_pretrade(_order(quantity=100.01), _context(), limits)
    assert over.approved is False


def test_max_notional_skip_when_not_configured():
    decision = evaluate_pretrade(_order(quantity=1_000_000.0), _context(), _limits(max_notional_usd=None))
    assert decision.checks["max_notional"].startswith("SKIP")


# ─── 3. Max quantity ────────────────────────────────────────────────────────

def test_max_quantity_exceeded_rejects():
    decision = evaluate_pretrade(_order(quantity=1001.0), _context(), _limits(max_notional_usd=None))
    assert decision.approved is False
    assert any(v.check == "max_quantity" for v in decision.violations)


def test_max_quantity_boundary():
    limits = _limits(
        max_notional_usd=None, max_position_per_symbol_usd=None,
        max_portfolio_notional_usd=None, max_symbol_concentration_pct=None,
        max_quantity=1000.0,
    )
    at = evaluate_pretrade(_order(quantity=1000.0), _context(), limits)
    assert at.approved is True
    over = evaluate_pretrade(_order(quantity=1000.0001), _context(), limits)
    assert over.approved is False


# ─── 4. Max position per symbol ─────────────────────────────────────────────

def test_max_position_per_symbol_exceeded_rejects():
    ctx = _context(open_positions_notional={"EURUSD": 15_000.0}, portfolio_notional=15_000.0)
    # new order adds ~$11,000 notional -> projected $26,000 > $20,000 max
    decision = evaluate_pretrade(_order(), ctx, _limits(max_notional_usd=None, max_portfolio_notional_usd=None, max_symbol_concentration_pct=None))
    assert decision.approved is False
    assert any(v.check == "max_position_per_symbol" for v in decision.violations)


def test_max_position_per_symbol_unknown_existing_position_rejects():
    ctx = _context(open_positions_notional=None)
    decision = evaluate_pretrade(_order(), ctx, _limits(max_notional_usd=None, max_portfolio_notional_usd=None, max_symbol_concentration_pct=None))
    assert decision.approved is False
    assert any(v.check == "max_position_per_symbol" for v in decision.violations)


# ─── 5. Max portfolio exposure ──────────────────────────────────────────────

def test_max_portfolio_exposure_exceeded_rejects():
    ctx = _context(portfolio_notional=45_000.0)
    decision = evaluate_pretrade(_order(), ctx, _limits(max_notional_usd=None, max_position_per_symbol_usd=None, max_symbol_concentration_pct=None))
    assert decision.approved is False
    assert any(v.check == "max_portfolio_notional" for v in decision.violations)


def test_max_portfolio_exposure_unknown_rejects():
    ctx = _context(portfolio_notional=None)
    decision = evaluate_pretrade(_order(), ctx, _limits(max_notional_usd=None, max_position_per_symbol_usd=None, max_symbol_concentration_pct=None))
    assert decision.approved is False
    assert any(v.check == "max_portfolio_notional" for v in decision.violations)


# ─── 6. Concentration ───────────────────────────────────────────────────────

def test_concentration_exceeded_rejects():
    ctx = _context(portfolio_notional=5_000.0, open_positions_notional={"EURUSD": 5_000.0})
    limits = _limits(max_notional_usd=None, max_position_per_symbol_usd=None, max_portfolio_notional_usd=None, max_symbol_concentration_pct=0.3)
    decision = evaluate_pretrade(_order(), ctx, limits)
    assert decision.approved is False
    assert any(v.check == "max_symbol_concentration" for v in decision.violations)


# ─── 7. Price collar / slippage ─────────────────────────────────────────────

def test_price_collar_exceeded_rejects():
    ctx = _context(executable_price=1.1300)  # ~2.7% deviation from 1.1000
    decision = evaluate_pretrade(_order(), ctx, _limits())
    assert decision.approved is False
    assert any(v.check == "price_collar" for v in decision.violations)


def test_price_collar_boundary():
    limits = _limits(price_collar_pct=0.02, max_slippage_pct=0.02)
    at_ctx = _context(executable_price=1.1000 * 1.02)
    at = evaluate_pretrade(_order(), at_ctx, limits)
    assert at.approved is True
    over_ctx = _context(executable_price=1.1000 * 1.0201)
    over = evaluate_pretrade(_order(), over_ctx, limits)
    assert over.approved is False


# ─── 8. Stale price ─────────────────────────────────────────────────────────

def test_stale_data_rejects():
    order = _order(bar_time=(NOW - timedelta(hours=10)).isoformat())
    decision = evaluate_pretrade(order, _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "stale_data" for v in decision.violations)


def test_missing_bar_time_rejects_when_staleness_configured():
    order = _order(bar_time=None)
    decision = evaluate_pretrade(order, _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "stale_data" for v in decision.violations)


def test_future_bar_time_rejects():
    order = _order(bar_time=(NOW + timedelta(hours=1)).isoformat())
    decision = evaluate_pretrade(order, _context(), _limits())
    assert decision.approved is False


# ─── 9. Invalid price ───────────────────────────────────────────────────────

def test_zero_reference_price_rejects():
    decision = evaluate_pretrade(_order(reference_price=0.0), _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "invalid_market_data" for v in decision.violations)


def test_negative_price_rejects():
    decision = evaluate_pretrade(_order(reference_price=-1.1), _context(), _limits())
    assert decision.approved is False


# ─── 10. NaN / inf price ────────────────────────────────────────────────────

def test_nan_price_rejects():
    decision = evaluate_pretrade(_order(reference_price=float("nan")), _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "invalid_market_data" for v in decision.violations)


def test_inf_stop_loss_rejects():
    decision = evaluate_pretrade(_order(stop_loss=float("inf")), _context(), _limits())
    assert decision.approved is False


def test_missing_quantity_rejects():
    decision = evaluate_pretrade(_order(quantity=None), _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "invalid_market_data" for v in decision.violations)


# ─── 11. Missing symbol metadata ────────────────────────────────────────────

def test_missing_symbol_spec_rejects():
    decision = evaluate_pretrade(_order(), _context(symbol_spec=None), _limits())
    assert decision.approved is False
    assert any(v.check == "symbol_metadata" for v in decision.violations)


def test_symbol_spec_missing_digits_rejects():
    spec = _spec(price_digits=None)
    decision = evaluate_pretrade(_order(), _context(symbol_spec=spec), _limits())
    assert decision.approved is False


# ─── 12. Invalid volume step ────────────────────────────────────────────────

def test_quantity_not_multiple_of_step_rejects():
    spec = _spec(quantity_step=10.0)
    decision = evaluate_pretrade(_order(quantity=105.0), _context(symbol_spec=spec), _limits())
    assert decision.approved is False
    assert any(v.check == "symbol_metadata" for v in decision.violations)


def test_quantity_below_broker_minimum_rejects():
    spec = _spec(min_quantity=50.0)
    decision = evaluate_pretrade(_order(quantity=10.0), _context(symbol_spec=spec), _limits(max_notional_usd=None, max_quantity=None))
    assert decision.approved is False


def test_quantity_above_broker_maximum_rejects():
    spec = _spec(max_quantity=500.0)
    decision = evaluate_pretrade(_order(quantity=600.0), _context(symbol_spec=spec), _limits(max_notional_usd=None, max_quantity=None))
    assert decision.approved is False


# ─── 13. Invalid price precision (digits) ───────────────────────────────────

def test_none_digits_treated_as_metadata_failure():
    spec = SymbolSpec(price_digits=None, quantity_step=1.0)
    decision = evaluate_pretrade(_order(), _context(symbol_spec=spec), _limits())
    assert decision.approved is False


# ─── 14. Invalid SL ─────────────────────────────────────────────────────────

def test_missing_stop_loss_rejects_when_required():
    decision = evaluate_pretrade(_order(stop_loss=None), _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "stop_loss_validity" for v in decision.violations)


def test_stop_loss_not_required_skips_when_absent():
    limits = _limits(require_stop_loss=False, min_risk_reward=None)
    decision = evaluate_pretrade(_order(stop_loss=None, take_profit=None), _context(), limits)
    assert decision.checks["stop_loss_validity"].startswith("SKIP")


def test_stop_distance_too_tight_rejects():
    order = _order(stop_loss=1.09999)  # ~0.0009% distance
    decision = evaluate_pretrade(order, _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "stop_loss_validity" for v in decision.violations)


def test_stop_distance_too_wide_rejects():
    order = _order(stop_loss=0.95)  # ~13.6% distance
    decision = evaluate_pretrade(order, _context(), _limits())
    assert decision.approved is False


# ─── 15. Invalid RR ─────────────────────────────────────────────────────────

def test_rr_below_minimum_rejects():
    order = _order(take_profit=1.1010)  # RR = 0.001/0.005 = 0.2
    decision = evaluate_pretrade(order, _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "risk_reward" for v in decision.violations)


def test_rr_boundary_epsilon():
    limits = _limits(min_risk_reward=2.0)
    # entry=1.1000, sl=1.0950 (risk=0.005), tp exactly at RR=2.0 -> 1.1100
    at = evaluate_pretrade(_order(take_profit=1.1100), _context(), limits)
    assert at.approved is True
    under = evaluate_pretrade(_order(take_profit=1.1099), _context(), limits)
    assert under.approved is False


def test_zero_distance_stop_undefined_rr_rejects():
    order = _order(stop_loss=1.1000)   # == reference_price -> zero distance
    decision = evaluate_pretrade(order, _context(), _limits(min_stop_distance_pct=None))
    assert decision.approved is False


# ─── 16. Missing account state ──────────────────────────────────────────────

def test_missing_account_equity_does_not_crash_and_still_evaluates_other_checks():
    ctx = _context(account_equity=None, portfolio_notional=None)
    decision = evaluate_pretrade(_order(), ctx, _limits(max_notional_usd=None, max_position_per_symbol_usd=None))
    assert decision.approved is False
    assert any(v.check == "max_portfolio_notional" for v in decision.violations)


# ─── 17. Missing configuration ──────────────────────────────────────────────

def test_all_limits_none_only_checks_market_data_and_metadata():
    limits = PretradeLimits(enabled=True, require_stop_loss=False)
    decision = evaluate_pretrade(_order(), _context(), limits)
    assert decision.approved is True
    for name in (
        "max_notional", "max_quantity", "max_position_per_symbol",
        "max_portfolio_notional", "max_symbol_concentration", "price_collar",
        "stale_data", "risk_reward", "duplicate_order", "max_orders_per_window",
    ):
        assert decision.checks[name].startswith("SKIP"), f"{name}={decision.checks[name]}"


# ─── 18. Risk calculation exception -> reject, never crash --------------------

def test_evaluate_never_raises_on_malformed_bar_time():
    order = _order(bar_time="not-a-timestamp")
    decision = evaluate_pretrade(order, _context(), _limits())
    assert decision.approved is False
    assert any(v.check == "stale_data" for v in decision.violations)


# ─── 19. Duplicate submission -----------------------------------------------

def test_duplicate_submission_within_window_rejects():
    order = _order(decision_id="dup_1")
    first = evaluate_pretrade(order, _context(), _limits())
    assert first.approved is True
    second = evaluate_pretrade(order, _context(), _limits())
    assert second.approved is False
    assert any(v.check == "duplicate_order" for v in second.violations)


def test_different_decision_ids_never_treated_as_duplicates():
    a = evaluate_pretrade(_order(decision_id="a"), _context(), _limits())
    b = evaluate_pretrade(_order(decision_id="b"), _context(), _limits())
    assert a.approved is True
    assert b.approved is True


def test_rejected_order_does_not_poison_the_duplicate_window():
    order = _order(decision_id="dup_2", quantity=None)  # will reject on invalid_market_data
    first = evaluate_pretrade(order, _context(), _limits())
    assert first.approved is False
    fixed = _order(decision_id="dup_2")
    second = evaluate_pretrade(fixed, _context(), _limits())
    assert second.approved is True  # the earlier REJECT must not count as a "recent submission"


# ─── Rate limiting ───────────────────────────────────────────────────────────

def test_max_orders_per_window_rejects_after_limit_reached():
    limits = _limits(max_orders_per_window=3, order_window_seconds=3600, duplicate_window_seconds=None)
    for i in range(3):
        decision = evaluate_pretrade(_order(decision_id=f"rate_{i}"), _context(), limits)
        assert decision.approved is True
    fourth = evaluate_pretrade(_order(decision_id="rate_3"), _context(), limits)
    assert fourth.approved is False
    assert any(v.check == "max_orders_per_window" for v in fourth.violations)


# ─── decision_id determinism -------------------------------------------------

def test_compute_decision_id_is_deterministic():
    a = compute_decision_id("EURUSD", "BUY", "2026-08-21T10:00:00", 1.1, 1.09, 1.12)
    b = compute_decision_id("EURUSD", "BUY", "2026-08-21T10:00:00", 1.1, 1.09, 1.12)
    assert a == b


def test_compute_decision_id_differs_on_any_field_change():
    base = compute_decision_id("EURUSD", "BUY", "2026-08-21T10:00:00", 1.1, 1.09, 1.12)
    variants = [
        compute_decision_id("GBPUSD", "BUY", "2026-08-21T10:00:00", 1.1, 1.09, 1.12),
        compute_decision_id("EURUSD", "SELL", "2026-08-21T10:00:00", 1.1, 1.09, 1.12),
        compute_decision_id("EURUSD", "BUY", "2026-08-21T11:00:00", 1.1, 1.09, 1.12),
        compute_decision_id("EURUSD", "BUY", "2026-08-21T10:00:00", 1.2, 1.09, 1.12),
    ]
    assert base not in variants
    assert len(set(variants)) == len(variants)


# ─── load_pretrade_limits -----------------------------------------------------

def test_load_pretrade_limits_reads_from_risk_config():
    config = {"risk": {"pretrade_limits": {"enabled": True, "max_notional_usd": 1234.0}}}
    limits = load_pretrade_limits(config)
    assert limits.enabled is True
    assert limits.max_notional_usd == 1234.0
    assert limits.max_quantity is None  # not set -> None, never a guessed default


def test_load_pretrade_limits_defaults_to_enabled_when_missing():
    limits = load_pretrade_limits({})
    assert limits.enabled is True
    assert limits.max_notional_usd is None


# ─── Bypass-prevention guard --------------------------------------------------

def test_require_pretrade_approval_raises_without_context():
    with pytest.raises(PretradeNotApproved):
        require_pretrade_approval("some_decision")


def test_require_pretrade_approval_passes_inside_approved_context():
    with approved_context("dec_x"):
        require_pretrade_approval("dec_x")  # must not raise


def test_require_pretrade_approval_raises_for_mismatched_decision_id():
    with approved_context("dec_x"):
        with pytest.raises(PretradeNotApproved):
            require_pretrade_approval("dec_y")


def test_context_resets_after_exit():
    with approved_context("dec_x"):
        pass
    with pytest.raises(PretradeNotApproved):
        require_pretrade_approval("dec_x")


def test_manual_override_requires_a_reason():
    with pytest.raises(ValueError):
        with manual_override_context("dec_z", ""):
            pass


def test_manual_override_allows_the_call_through():
    with manual_override_context("dec_z", "manual smoke test of broker connectivity"):
        require_pretrade_approval("dec_z")  # must not raise
