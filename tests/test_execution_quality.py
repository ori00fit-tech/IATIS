"""tests/test_execution_quality.py — TCA ledger (storage/execution_quality.py).

Slippage math, unit conventions (must match backtesting/backtest_engine.py),
persistence through the fake D1, dry-run exclusion, and summary aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from storage.execution_quality import (
    BACKTEST_SLIPPAGE_ASSUMPTION_PIPS,
    compute_slippage,
    log_fill,
    pip_size_for,
    summary,
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
