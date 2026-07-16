"""tests/test_reconciliation.py — broker-vs-internal reconciliation (M3).

Mismatch injection in both directions, the paper-mode gate, and failure
isolation (a dead broker client must yield 'skipped', never an exception).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from execution.reconciliation import format_alert, reconcile


@dataclass
class FakePosition:
    symbol: str
    position_id: str = "P1"
    direction: str = "BUY"
    volume: int = 1000
    entry_price: float = 1.1
    current_price: float = 1.1
    unrealized_pnl: float = 0.0
    stop_loss: float = 1.09
    take_profit: float = 1.12


class FakeClient:
    def __init__(self, symbols):
        self._symbols = symbols

    def get_open_positions(self):
        return [FakePosition(symbol=s) for s in self._symbols]


LIVE_CFG = {"execution": {"ctrader_enabled": True, "dry_run": False}}


@pytest.fixture
def broker(monkeypatch):
    def _set(symbols):
        monkeypatch.setattr(
            "core.data_providers.get_shared_ctrader_client",
            lambda: FakeClient(symbols),
        )
    return _set


@pytest.fixture
def internal(monkeypatch):
    def _set(symbols):
        monkeypatch.setattr(
            "storage.outcome_tracker.get_open_signals",
            lambda: [{"symbol": s, "signal_id": f"x_{s}"} for s in symbols],
        )
    return _set


def test_match_when_both_sides_agree(broker, internal):
    broker(["EURUSD", "XAUUSD"])
    internal(["XAUUSD", "EURUSD"])
    rec = reconcile(LIVE_CFG)
    assert rec["status"] == "match"
    assert rec["broker_only"] == [] and rec["internal_only"] == []


def test_broker_only_position_is_flagged(broker, internal):
    """A fill the tracker missed — the worst direction (unknown exposure)."""
    broker(["EURUSD", "BTCUSD"])
    internal(["EURUSD"])
    rec = reconcile(LIVE_CFG)
    assert rec["status"] == "mismatch"
    assert rec["broker_only"] == ["BTCUSD"]
    assert "BTCUSD" in format_alert(rec)


def test_internal_only_position_is_flagged(broker, internal):
    """Closed at the broker (manual/SL) while the tracker still counts it open."""
    broker([])
    internal(["ETHUSD"])
    rec = reconcile(LIVE_CFG)
    assert rec["status"] == "mismatch"
    assert rec["internal_only"] == ["ETHUSD"]


def test_paper_mode_is_skipped(broker, internal):
    broker(["EURUSD"])
    internal([])
    for cfg in (
        {"execution": {"ctrader_enabled": True, "dry_run": True}},
        {"execution": {"ctrader_enabled": False, "dry_run": False}},
        {},
    ):
        rec = reconcile(cfg)
        assert rec["status"] == "skipped"


def test_dead_broker_client_yields_skipped_not_exception(monkeypatch, internal):
    def boom():
        raise ConnectionError("socket dead")
    monkeypatch.setattr("core.data_providers.get_shared_ctrader_client", boom)
    internal(["EURUSD"])
    rec = reconcile(LIVE_CFG)
    assert rec["status"] == "skipped"
    assert "broker client unavailable" in rec["reason"]


def test_dead_tracker_yields_skipped_not_exception(broker, monkeypatch):
    broker(["EURUSD"])
    def boom():
        raise RuntimeError("d1 down")
    monkeypatch.setattr("storage.outcome_tracker.get_open_signals", boom)
    rec = reconcile(LIVE_CFG)
    assert rec["status"] == "skipped"


def test_empty_both_sides_is_a_match(broker, internal):
    broker([])
    internal([])
    assert reconcile(LIVE_CFG)["status"] == "match"


def test_store_and_read_roundtrip(fake_d1, broker, internal):
    from execution.reconciliation import last_result, store_result

    broker(["EURUSD", "BTCUSD"])
    internal(["EURUSD"])
    rec = reconcile(LIVE_CFG)
    store_result(rec)

    stored = last_result()
    assert stored is not None
    assert stored["status"] == "mismatch"
    assert stored["broker_only"] == ["BTCUSD"]
    assert stored["n_broker"] == 2 and stored["n_internal"] == 1


def test_last_result_none_when_empty(fake_d1):
    from execution.reconciliation import last_result
    assert last_result() is None


def test_store_never_raises_on_d1_outage(monkeypatch):
    from execution import reconciliation as rc

    def boom():
        raise RuntimeError("worker down")

    monkeypatch.setattr("storage.d1_client.d1_connection", boom)
    rc.store_result({"status": "match", "checked_at": "t"})  # must not raise
