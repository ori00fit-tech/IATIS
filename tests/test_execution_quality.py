"""tests/test_execution_quality.py — TCA ledger (storage/execution_quality.py).

Slippage math, unit conventions (must match backtesting/backtest_engine.py),
persistence through the fake D1, dry-run exclusion, and summary aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from storage.execution_quality import (
    BACKTEST_SLIPPAGE_ASSUMPTION_PIPS,
    PENDING,
    RESOLVED,
    UNAVAILABLE,
    compute_slippage,
    log_fill,
    mark_pending_fill_unavailable,
    pending_fill_position_ids,
    pip_size_for,
    queue_pending_fill,
    record_or_queue_fill,
    resolve_pending_fill,
    summary,
    sweep_stale_pending_fills,
)


@dataclass
class FakeExecResult:
    executed: bool = True
    dry_run: bool = False
    symbol: str = "EURUSD"
    direction: str = "BUY"
    entry_price: float = 0.0     # fill price, as in ExecutionResult
    units: int = 1000
    trade_id: str = "P123"
    skip_reason: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    timestamp: str = field(default="2026-07-16T12:00:00+00:00")


def _report(symbol="EURUSD", entry=1.10000, sl=1.09500, **extra) -> dict:
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": entry + 2 * abs(entry - sl),
        "bar_time": "2026-07-16 08:00:00",
        **extra,
    }


# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------

def test_buy_adverse_slippage_is_positive():
    # BUY intended 1.1000, filled higher → paid more → adverse (+)
    assert compute_slippage("BUY", 1.1000, 1.1002) == pytest.approx(0.0002)


def test_buy_price_improvement_is_negative():
    assert compute_slippage("BUY", 1.1000, 1.0999) == pytest.approx(-0.0001)


def test_sell_adverse_slippage_is_positive():
    # SELL intended 1.1000, filled lower → received less → adverse (+)
    assert compute_slippage("SELL", 1.1000, 1.0998) == pytest.approx(0.0002)


def test_sell_price_improvement_is_negative():
    assert compute_slippage("SELL", 1.1000, 1.1001) == pytest.approx(-0.0001)


def test_pip_units_match_backtest_convention():
    """MUST stay identical to backtest_engine.config_for_symbol — the whole
    point of the ledger is comparability with slippage_pips=0.5."""
    assert pip_size_for("EURUSD") == 0.0001
    assert pip_size_for("USDJPY") == 0.01
    assert pip_size_for("XAUUSD") == 0.01
    assert pip_size_for("BTCUSD") == 0.01
    assert pip_size_for("ETHUSD") == 0.01


def test_unknown_symbol_falls_back_to_fx_rule():
    assert pip_size_for("ZZZJPY") == 0.01
    assert pip_size_for("ZZZUSD") == 0.0001


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_real_fill_is_recorded_with_signed_pips(fake_d1):
    res = FakeExecResult(entry_price=1.10020)  # BUY, +2.0 pips adverse
    assert log_fill(_report(), res, broker="ctrader") is True

    row = fake_d1.execute("SELECT * FROM fills").fetchone()
    assert row["symbol"] == "EURUSD"
    assert row["direction"] == "BUY"
    assert row["broker"] == "ctrader"
    assert row["pip_size"] == pytest.approx(0.0001)
    assert row["slippage_pips"] == pytest.approx(2.0)
    # SL distance 50 pips → 2 pips = 0.04 R
    assert row["slippage_r"] == pytest.approx(0.04)
    assert row["decision_bar_time"] == "2026-07-16 08:00:00"


def test_dry_run_fill_is_excluded(fake_d1):
    res = FakeExecResult(dry_run=True, entry_price=1.10000)
    assert log_fill(_report(), res) is False
    assert fake_d1.execute(
        "SELECT COUNT(*) c FROM fills" if _table_exists(fake_d1) else "SELECT 0 c"
    ).fetchone()["c"] == 0


def test_unexecuted_result_is_excluded(fake_d1):
    res = FakeExecResult(executed=False, entry_price=1.10000)
    assert log_fill(_report(), res) is False


def test_missing_fill_price_is_excluded():
    res = FakeExecResult(entry_price=0.0)
    assert log_fill(_report(), res) is False


def test_log_fill_never_raises_on_storage_failure(monkeypatch):
    """A D1 outage must not disturb the trade that just executed."""
    import storage.execution_quality as eq

    def boom():
        raise RuntimeError("worker down")

    monkeypatch.setattr(eq.d1_client, "d1_connection", boom)
    res = FakeExecResult(entry_price=1.10020)
    assert eq.log_fill(_report(), res) is False  # swallowed, not raised


def test_provenance_git_commit_carried_when_present(fake_d1):
    res = FakeExecResult(entry_price=1.10020)
    rep = _report(provenance={"git_commit": "abc123def456"})
    assert log_fill(rep, res) is True
    row = fake_d1.execute("SELECT git_commit FROM fills").fetchone()
    assert row["git_commit"] == "abc123def456"


def _table_exists(con) -> bool:
    return bool(con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fills'"
    ).fetchone())


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def test_summary_aggregates_by_symbol_and_session(fake_d1):
    # Two EURUSD BUY fills: +2.0 and +1.0 pips; one XAUUSD SELL: +5.0 "pips"
    log_fill(_report(), FakeExecResult(entry_price=1.10020))
    log_fill(_report(), FakeExecResult(entry_price=1.10010))
    xau = FakeExecResult(symbol="XAUUSD", direction="SELL", entry_price=2399.95)
    log_fill(_report(symbol="XAUUSD", entry=2400.00, sl=2412.00), xau)

    s = summary()
    assert s["backtest_assumption_pips"] == BACKTEST_SLIPPAGE_ASSUMPTION_PIPS
    assert s["overall"]["n"] == 3
    assert s["by_symbol"]["EURUSD"]["n"] == 2
    assert s["by_symbol"]["EURUSD"]["mean_slippage_pips"] == pytest.approx(1.5)
    # XAUUSD: SELL intended 2400.00 filled 2399.95 → 0.05 adverse / 0.01 = 5 pips
    assert s["by_symbol"]["XAUUSD"]["mean_slippage_pips"] == pytest.approx(5.0)
    # Session tagging: every fill got some session bucket
    assert sum(b["n"] for b in s["by_session"].values()) == 3


def test_summary_empty_ledger(fake_d1):
    s = summary()
    assert s["overall"] == {"n": 0}
    assert s["by_symbol"] == {}


def test_summary_includes_recent_fills(fake_d1):
    log_fill(_report(), FakeExecResult(entry_price=1.10020, trade_id="P9"))
    s = summary()
    assert len(s["recent"]) == 1
    r = s["recent"][0]
    assert r["symbol"] == "EURUSD" and r["trade_id"] == "P9"
    assert r["slippage_pips"] == pytest.approx(2.0)


def test_synchronous_fill_leaves_latency_null(fake_d1):
    """log_fill()'s own path is synchronous — the accept->fill gap it
    would measure doesn't apply the same way there, so this column stays
    NULL for it (only resolve_pending_fill()'s async path populates it)."""
    log_fill(_report(), FakeExecResult(entry_price=1.10020))
    row = fake_d1.execute("SELECT fill_latency_seconds FROM fills").fetchone()
    assert row["fill_latency_seconds"] is None


# ---------------------------------------------------------------------------
# Pending fills — the 2026-08-17 TCA async-fill fix (root cause: cTrader's
# synchronous ORDER_ACCEPTED response frequently carries NO real fill
# price; the real one arrives moments later on an async ORDER_FILLED
# event, or — if the process restarted in between — on the next
# ProtoOAReconcileRes. This is the durable, D1-backed queue that lets a
# fill be completed correctly once that later event actually arrives,
# instead of the pre-fix "missing intended/fill price — not recorded"
# silent, permanent drop).
# ---------------------------------------------------------------------------

def test_queue_pending_fill_persists_a_pending_row(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="POS1")  # no price yet, real position_id
    assert queue_pending_fill(_report(), res, broker="ctrader") is True

    row = fake_d1.execute("SELECT * FROM pending_fills WHERE position_id='POS1'").fetchone()
    assert row["status"] == PENDING
    assert row["symbol"] == "EURUSD"
    assert row["direction"] == "BUY"
    assert row["intended_price"] == pytest.approx(1.10000)
    assert row["broker"] == "ctrader"
    # Never writes a `fills` row — nothing to fabricate yet.
    assert fake_d1.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0


def test_queue_pending_fill_requires_a_position_id():
    """No fill price AND no position_id: nothing durable to queue — this
    is the one case that still just logs and drops, matching the
    pre-fix behavior for a genuinely un-trackable event."""
    res = FakeExecResult(entry_price=0.0, trade_id="")
    assert queue_pending_fill(_report(), res) is False


def test_queue_pending_fill_never_raises_on_storage_failure(monkeypatch):
    import storage.execution_quality as eq

    def boom():
        raise RuntimeError("worker down")

    monkeypatch.setattr(eq.d1_client, "d1_connection", boom)
    res = FakeExecResult(entry_price=0.0, trade_id="POS1")
    assert eq.queue_pending_fill(_report(), res) is False


def test_record_or_queue_fill_records_immediately_when_price_known(fake_d1):
    res = FakeExecResult(entry_price=1.10020, trade_id="POS2")
    assert record_or_queue_fill(_report(), res, broker="ctrader") == "RECORDED"
    assert fake_d1.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 1
    assert fake_d1.execute("SELECT COUNT(*) c FROM pending_fills").fetchone()["c"] == 0


def test_record_or_queue_fill_queues_when_price_unknown(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="POS3")
    assert record_or_queue_fill(_report(), res, broker="ctrader") == "QUEUED"
    assert fake_d1.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0
    row = fake_d1.execute("SELECT status FROM pending_fills WHERE position_id='POS3'").fetchone()
    assert row["status"] == PENDING


def test_record_or_queue_fill_drops_dry_run(fake_d1):
    res = FakeExecResult(dry_run=True, entry_price=0.0, trade_id="POS4")
    # dry_run is excluded before any D1 call at all — the pending_fills
    # table is never even created for this call, matching log_fill()'s
    # own existing "dry-run never touches storage" behavior.
    assert record_or_queue_fill(_report(), res) == "DROPPED"


def test_record_or_queue_fill_drops_when_nothing_to_queue(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="")
    assert record_or_queue_fill(_report(), res) == "DROPPED"


def test_resolve_pending_fill_completes_a_queued_row(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="POS5")
    queue_pending_fill(_report(), res, broker="ctrader")

    assert resolve_pending_fill("POS5", 1.10020) is True

    fill_row = fake_d1.execute("SELECT * FROM fills WHERE trade_id='POS5'").fetchone()
    assert fill_row is not None
    assert fill_row["fill_price"] == pytest.approx(1.10020)
    assert fill_row["intended_price"] == pytest.approx(1.10000)
    assert fill_row["slippage_pips"] == pytest.approx(2.0)
    assert fill_row["fill_latency_seconds"] is not None  # the async path DOES measure this

    pending_row = fake_d1.execute("SELECT status FROM pending_fills WHERE position_id='POS5'").fetchone()
    assert pending_row["status"] == RESOLVED


def test_resolve_pending_fill_is_idempotent(fake_d1):
    """The core correctness property: a duplicate resolution attempt
    (e.g. two execution events reporting the same fill) must never write
    a second `fills` row for the same position."""
    res = FakeExecResult(entry_price=0.0, trade_id="POS6")
    queue_pending_fill(_report(), res)

    assert resolve_pending_fill("POS6", 1.10020) is True
    assert resolve_pending_fill("POS6", 1.10099) is False  # already RESOLVED — no-op, even with a different price

    assert fake_d1.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 1
    row = fake_d1.execute("SELECT fill_price FROM fills").fetchone()
    assert row["fill_price"] == pytest.approx(1.10020)  # the FIRST resolution wins, never overwritten


def test_resolve_pending_fill_returns_false_for_unknown_position(fake_d1):
    assert resolve_pending_fill("NEVER_QUEUED", 1.10020) is False
    assert fake_d1.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0


def test_resolve_pending_fill_never_accepts_a_non_positive_price(fake_d1):
    """Defense in depth: even if a caller somehow passes 0/negative, this
    must never write a fabricated fill row."""
    res = FakeExecResult(entry_price=0.0, trade_id="POS7")
    queue_pending_fill(_report(), res)
    assert resolve_pending_fill("POS7", 0.0) is False
    assert resolve_pending_fill("POS7", -1.5) is False
    assert fake_d1.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0


def test_resolve_pending_fill_never_raises_on_storage_failure(monkeypatch):
    import storage.execution_quality as eq

    def boom():
        raise RuntimeError("worker down")

    monkeypatch.setattr(eq.d1_client, "d1_connection", boom)
    assert eq.resolve_pending_fill("POS_X", 1.1) is False


def test_mark_pending_fill_unavailable_transitions_from_pending(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="POS8")
    queue_pending_fill(_report(), res)

    assert mark_pending_fill_unavailable("POS8", reason="test") is True
    row = fake_d1.execute("SELECT status FROM pending_fills WHERE position_id='POS8'").fetchone()
    assert row["status"] == UNAVAILABLE


def test_mark_pending_fill_unavailable_is_idempotent(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="POS9")
    queue_pending_fill(_report(), res)
    mark_pending_fill_unavailable("POS9")
    assert mark_pending_fill_unavailable("POS9") is False  # already UNAVAILABLE — no-op


def test_mark_pending_fill_unavailable_never_writes_a_fabricated_fill(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="POS10")
    queue_pending_fill(_report(), res)
    mark_pending_fill_unavailable("POS10")
    assert fake_d1.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0


def test_sweep_stale_pending_fills_marks_old_rows_unavailable(fake_d1, monkeypatch):
    from datetime import datetime, timedelta, timezone

    res = FakeExecResult(entry_price=0.0, trade_id="POS11")
    queue_pending_fill(_report(), res)

    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=2000)).isoformat()
    fake_d1.execute("UPDATE pending_fills SET ts_queued=? WHERE position_id='POS11'", (old_ts,))

    swept = sweep_stale_pending_fills(max_age_seconds=900.0)
    assert swept == ["POS11"]
    row = fake_d1.execute("SELECT status FROM pending_fills WHERE position_id='POS11'").fetchone()
    assert row["status"] == UNAVAILABLE


def test_sweep_stale_pending_fills_leaves_recent_rows_pending(fake_d1):
    res = FakeExecResult(entry_price=0.0, trade_id="POS12")
    queue_pending_fill(_report(), res)

    swept = sweep_stale_pending_fills(max_age_seconds=900.0)
    assert swept == []
    row = fake_d1.execute("SELECT status FROM pending_fills WHERE position_id='POS12'").fetchone()
    assert row["status"] == PENDING


def test_pending_fill_position_ids_only_lists_pending(fake_d1):
    queue_pending_fill(_report(), FakeExecResult(entry_price=0.0, trade_id="POS13"))
    queue_pending_fill(_report(), FakeExecResult(entry_price=0.0, trade_id="POS14"))
    resolve_pending_fill("POS14", 1.10020)

    ids = pending_fill_position_ids()
    assert ids == ["POS13"]
