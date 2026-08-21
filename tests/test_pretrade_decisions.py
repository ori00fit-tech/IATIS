"""tests/test_pretrade_decisions.py
------------------------------------
storage/pretrade_decisions.py — the persisted audit trail for every
risk/pretrade_limits.py::evaluate_pretrade() verdict (approved AND
rejected), separate from storage/execution_attempts.py's broker-response
log (see that module's own docstring for why a decision-audit row must
never be mistaken for an open-position/broker-response record by any
exposure calculation).
"""
from __future__ import annotations

from datetime import datetime, timezone

from risk.pretrade_limits import (
    PendingOrder,
    PretradeContext,
    SymbolSpec,
    evaluate_pretrade,
)
from risk.pretrade_limits import PretradeLimits
from storage.pretrade_decisions import record_pretrade_decision, recent_decisions

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


def _order(**kwargs) -> PendingOrder:
    defaults = dict(
        decision_id="dec_test_1",
        symbol="EURUSD",
        direction="BUY",
        quantity=10.0,
        reference_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        bar_time=NOW.isoformat(),
    )
    defaults.update(kwargs)
    return PendingOrder(**defaults)


def _context(**kwargs) -> PretradeContext:
    defaults = dict(
        now_utc=NOW,
        account_equity=10_000.0,
        open_positions_notional={},
        portfolio_notional=20_000.0,
        symbol_spec=SymbolSpec(price_digits=5, quantity_step=1.0, min_quantity=1.0, max_quantity=10_000.0),
        executable_price=1.1000,
    )
    defaults.update(kwargs)
    return PretradeContext(**defaults)


def _limits(**kwargs) -> PretradeLimits:
    defaults = dict(enabled=True, max_notional_usd=50_000.0, max_symbol_concentration_pct=0.5)
    defaults.update(kwargs)
    return PretradeLimits(**defaults)


def test_record_approved_decision_round_trips():
    order = _order()
    context = _context()
    decision = evaluate_pretrade(order, context, _limits())
    assert decision.approved is True

    record_pretrade_decision(decision, order, context, kill_switch_active=False, strategy_engine="nnfx")

    rows = recent_decisions(symbol="EURUSD")
    assert len(rows) == 1
    row = rows[0]
    assert row["approved"] == 1
    assert row["symbol"] == "EURUSD"
    assert row["direction"] == "BUY"
    assert row["decision_id"] == "dec_test_1"
    assert row["strategy_engine"] == "nnfx"
    assert row["kill_switch_active"] == 0
    assert row["limits_violated"] == ""
    assert row["estimated_notional_usd"] == 11_000.0


def test_record_rejected_decision_captures_violated_limits():
    order = _order(quantity=1000.0)  # huge notional -> exceeds max_notional
    context = _context()
    decision = evaluate_pretrade(order, context, _limits())
    assert decision.approved is False

    record_pretrade_decision(decision, order, context, kill_switch_active=False)

    rows = recent_decisions(symbol="EURUSD")
    assert rows[0]["approved"] == 0
    assert "max_notional" in rows[0]["limits_violated"]
    assert "max_notional" in rows[0]["violations_json"]


def test_record_decision_captures_kill_switch_state():
    order = _order()
    context = _context()
    decision = evaluate_pretrade(order, context, _limits())

    record_pretrade_decision(decision, order, context, kill_switch_active=True)

    rows = recent_decisions(symbol="EURUSD")
    assert rows[0]["kill_switch_active"] == 1


def test_current_and_projected_position_notional_computed_from_context():
    order = _order()
    context = _context(open_positions_notional={"EURUSD": 5_000.0})
    decision = evaluate_pretrade(order, context, _limits())

    record_pretrade_decision(decision, order, context, kill_switch_active=False)

    row = recent_decisions(symbol="EURUSD")[0]
    assert row["current_position_notional"] == 5_000.0
    assert row["projected_position_notional"] == 5_000.0 + 11_000.0


def test_recent_decisions_scoped_by_symbol_does_not_leak_other_symbols():
    for sym in ("AAA", "BBB"):
        order = _order(symbol=sym, decision_id=f"dec_{sym}")
        context = _context()
        decision = evaluate_pretrade(order, context, _limits())
        record_pretrade_decision(decision, order, context, kill_switch_active=False)

    rows = recent_decisions(symbol="AAA")
    assert all(r["symbol"] == "AAA" for r in rows)


def test_recent_decisions_respects_limit():
    for i in range(5):
        order = _order(decision_id=f"dec_lim_{i}")
        context = _context()
        decision = evaluate_pretrade(order, context, _limits())
        record_pretrade_decision(decision, order, context, kill_switch_active=False)

    rows = recent_decisions(symbol="EURUSD", limit=2)
    assert len(rows) == 2


def test_record_never_raises_on_storage_failure(monkeypatch):
    import storage.pretrade_decisions as m

    def _boom(*a, **kw):
        raise RuntimeError("D1 unreachable")

    monkeypatch.setattr(m, "_init_db", _boom)

    order = _order()
    context = _context()
    decision = evaluate_pretrade(order, context, _limits())
    row_id = record_pretrade_decision(decision, order, context, kill_switch_active=False)
    assert row_id  # still returns a generated id even though persistence failed


def test_recent_decisions_never_raises_on_storage_failure(monkeypatch):
    import storage.pretrade_decisions as m

    def _boom(*a, **kw):
        raise RuntimeError("D1 unreachable")

    monkeypatch.setattr(m, "_init_db", _boom)
    assert recent_decisions() == []
