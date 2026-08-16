"""tests/test_oanda_execution.py — OANDA client + TradeExecutor tests (no API calls)."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch
from execution.oanda_client import IATIS_TO_OANDA, OANDA_TO_IATIS, OandaClient
from execution.trade_executor import TradeExecutor, ExecutionResult


# ─── Symbol mapping ───────────────────────────────────────────────────────────

def test_all_iatis_forex_mapped():
    forex = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD",
             "USDCAD", "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY",
             "EURGBP", "EURCHF"]
    for sym in forex:
        assert sym in IATIS_TO_OANDA, f"{sym} not in OANDA mapping"


def test_metals_mapped():
    assert "XAUUSD" in IATIS_TO_OANDA
    assert "XAGUSD" in IATIS_TO_OANDA
    assert IATIS_TO_OANDA["XAUUSD"] == "XAU_USD"


def test_indices_mapped():
    assert "US30" in IATIS_TO_OANDA
    assert "NAS100" in IATIS_TO_OANDA
    assert "SPX500" in IATIS_TO_OANDA


def test_crypto_not_on_oanda():
    # BTC/ETH not supported on OANDA — should be absent from mapping
    assert "BTCUSD" not in IATIS_TO_OANDA
    assert "ETHUSD" not in IATIS_TO_OANDA


def test_reverse_mapping_consistent():
    for iatis, oanda in IATIS_TO_OANDA.items():
        assert OANDA_TO_IATIS[oanda] == iatis


def test_oanda_client_raises_without_key():
    with pytest.raises(ValueError, match="OANDA_API_KEY"):
        OandaClient(api_key="", account_id="123")


def test_oanda_client_raises_without_account():
    with pytest.raises(ValueError, match="OANDA_ACCOUNT_ID"):
        OandaClient(api_key="test_key", account_id="")


# ─── TradeExecutor ────────────────────────────────────────────────────────────

def _make_report(symbol="EURUSD", verdict="EXECUTE", score=75.0,
                 bias="BEARISH", blackout=False):
    return {
        "symbol": symbol,
        "final_verdict": verdict,
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {
            "score": score,
            "vote": {"winning_bias": bias},
        },
        "risk": {"recommended_risk_pct": 0.01},
        "news": {
            "blackout_active": blackout,
            "blackout_reason": "FOMC" if blackout else "",
        },
    }


def test_executor_dry_run_no_trade():
    executor = TradeExecutor(dry_run=True)
    result = executor.execute_from_report(_make_report(verdict="NO_TRADE"))
    assert result.executed is False
    assert "NO_TRADE" in result.skip_reason


def test_executor_dry_run_execute():
    executor = TradeExecutor(dry_run=True)
    result = executor.execute_from_report(_make_report())
    assert result.executed is True
    assert result.dry_run is True
    assert result.trade_id == "DRY_RUN"
    assert result.direction == "SELL"  # BEARISH → SELL


def test_executor_dry_run_bullish():
    executor = TradeExecutor(dry_run=True)
    result = executor.execute_from_report(_make_report(bias="BULLISH"))
    assert result.direction == "BUY"


def test_executor_blocks_on_news_blackout():
    executor = TradeExecutor(dry_run=True)
    result = executor.execute_from_report(_make_report(blackout=True))
    assert result.executed is False
    assert "blackout" in result.skip_reason.lower()


def test_executor_blocks_low_score():
    executor = TradeExecutor(dry_run=True, min_score=65.0)
    result = executor.execute_from_report(_make_report(score=55.0))
    assert result.executed is False
    assert "threshold" in result.skip_reason


def test_executor_blocks_missing_prices():
    executor = TradeExecutor(dry_run=True)
    report = _make_report()
    report["entry_price"] = None
    result = executor.execute_from_report(report)
    assert result.executed is False
    assert "entry/SL/TP" in result.skip_reason


def test_executor_to_dict():
    executor = TradeExecutor(dry_run=True)
    result = executor.execute_from_report(_make_report())
    d = result.to_dict()
    assert "executed" in d
    assert "symbol" in d
    assert "timestamp" in d


def test_executor_crypto_dry_run():
    """Crypto should execute in dry_run even though OANDA doesn't support it."""
    executor = TradeExecutor(dry_run=True)
    result = executor.execute_from_report(_make_report(symbol="BTCUSD"))
    # In dry_run mode, we still log it (no actual OANDA call)
    assert result.executed is True
    assert result.dry_run is True


# ---------------------------------------------------------------------------
# Layer-2 cTrader demo execution: money-safety guard (live account blocked)
# ---------------------------------------------------------------------------

def test_ctrader_refuses_live_account_without_allow_flag():
    """A real order must NEVER hit a non-demo cTrader account unless
    allow_live_trading is explicitly True — even when dry_run is off."""
    executor = TradeExecutor(dry_run=False, broker="ctrader", allow_live_trading=False)

    fake_client = MagicMock()
    fake_client.environment = "live"
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is False
    assert "Live trading blocked" in result.skip_reason
    fake_client.place_market_order.assert_not_called()


def test_ctrader_places_on_demo_account():
    """On a demo account, a real order IS placed (this is layer-2 evidence)."""
    from execution.trade_executor import ExecutionResult as _ER  # noqa

    executor = TradeExecutor(dry_run=False, broker="ctrader", allow_live_trading=False)

    fake_client = MagicMock()
    fake_client.environment = "demo"
    fake_client.has_open_position.return_value = False
    fake_client.get_account_info.return_value = MagicMock(balance=200.0)
    fake_client.calculate_volume.return_value = 1000
    fake_client.place_market_order.return_value = MagicMock(
        success=True, position_id="pos123", entry_price=1.0850, error="",
    )
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is True
    assert result.dry_run is False
    assert result.trade_id == "pos123"
    fake_client.place_market_order.assert_called_once()


def test_ctrader_live_allowed_when_flag_set():
    """With allow_live_trading=True, a live account is permitted (the
    explicit real-money path)."""
    executor = TradeExecutor(dry_run=False, broker="ctrader", allow_live_trading=True)

    fake_client = MagicMock()
    fake_client.environment = "live"
    fake_client.has_open_position.return_value = False
    fake_client.get_account_info.return_value = MagicMock(balance=200.0)
    fake_client.calculate_volume.return_value = 1000
    fake_client.place_market_order.return_value = MagicMock(
        success=True, position_id="live1", entry_price=1.0850, error="",
    )
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is True
    fake_client.place_market_order.assert_called_once()


# ─── EXEC-DUP (2026-08-15 red-team audit): duplicate-position guard on the
# cTrader branch — previously present on OANDA/Dukascopy JForex but missing
# here, meaning a broker-truth already-open position never blocked a
# second real order via this path. ─────────────────────────────────────

def test_ctrader_refuses_duplicate_position():
    """client.has_open_position(symbol)=True must block a second cTrader
    order — this is the exact backstop for a previous order whose
    confirmation timed out but was actually filled (the late execution
    event still updates client._positions, so a later has_open_position()
    check correctly catches it)."""
    executor = TradeExecutor(dry_run=False, broker="ctrader", allow_live_trading=False)

    fake_client = MagicMock()
    fake_client.environment = "demo"
    fake_client.has_open_position.return_value = True
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is False
    assert "Already have open position" in result.skip_reason
    fake_client.get_account_info.assert_not_called()
    fake_client.place_market_order.assert_not_called()


def test_ctrader_duplicate_check_runs_before_account_lookup():
    """The duplicate check must short-circuit before any account/sizing
    call — no wasted API calls once a duplicate is already known."""
    executor = TradeExecutor(dry_run=False, broker="ctrader", allow_live_trading=False)

    fake_client = MagicMock()
    fake_client.environment = "demo"
    fake_client.has_open_position.return_value = True
    with patch.object(executor, "_get_client", return_value=fake_client):
        executor.execute_from_report(_make_report())

    fake_client.has_open_position.assert_called_once_with("EURUSD")
    fake_client.calculate_volume.assert_not_called()


# ─── Dukascopy JForex (2026-08-08) ─────────────────────────────────────────

def test_dukascopy_jforex_refuses_live_account_without_allow_flag():
    executor = TradeExecutor(
        dry_run=False, broker="dukascopy_jforex", allow_live_trading=False,
        dukascopy_jforex_fixed_quantity=0.01,
    )
    fake_client = MagicMock()
    fake_client.environment = "live"
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is False
    assert "Live trading blocked" in result.skip_reason
    fake_client.place_market_order.assert_not_called()


def test_dukascopy_jforex_refuses_when_no_fixed_quantity_configured():
    """0.0 (unset) must refuse to trade, never guess a size."""
    executor = TradeExecutor(dry_run=False, broker="dukascopy_jforex", dukascopy_jforex_fixed_quantity=0.0)
    fake_client = MagicMock()
    fake_client.environment = "demo"
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is False
    assert "not configured" in result.skip_reason
    fake_client.place_market_order.assert_not_called()


def test_dukascopy_jforex_refuses_duplicate_open_position():
    executor = TradeExecutor(dry_run=False, broker="dukascopy_jforex", dukascopy_jforex_fixed_quantity=0.01)
    fake_client = MagicMock()
    fake_client.environment = "demo"
    fake_client.has_open_position.return_value = True
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is False
    assert "Already have open position" in result.skip_reason
    fake_client.place_market_order.assert_not_called()


def test_dukascopy_jforex_places_order_on_demo_account():
    executor = TradeExecutor(dry_run=False, broker="dukascopy_jforex", dukascopy_jforex_fixed_quantity=0.01)
    fake_client = MagicMock()
    fake_client.environment = "demo"
    fake_client.has_open_position.return_value = False
    fake_client.place_market_order.return_value = MagicMock(
        success=True, dukas_order_id="jf123", fill_price=1.0850, error="",
    )
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is True
    assert result.dry_run is False
    assert result.trade_id == "jf123"
    fake_client.place_market_order.assert_called_once()
    sent_order = fake_client.place_market_order.call_args[0][0]
    assert sent_order.quantity == 0.01
    assert sent_order.client_order_id == "IATIS_EURUSD"


def test_dukascopy_jforex_reports_bridge_rejection():
    executor = TradeExecutor(dry_run=False, broker="dukascopy_jforex", dukascopy_jforex_fixed_quantity=0.01)
    fake_client = MagicMock()
    fake_client.environment = "demo"
    fake_client.has_open_position.return_value = False
    fake_client.place_market_order.return_value = MagicMock(success=False, error="insufficient margin")
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is False
    assert "insufficient margin" in result.skip_reason


def test_dukascopy_jforex_live_allowed_when_flag_set():
    executor = TradeExecutor(
        dry_run=False, broker="dukascopy_jforex", allow_live_trading=True,
        dukascopy_jforex_fixed_quantity=0.01,
    )
    fake_client = MagicMock()
    fake_client.environment = "live"
    fake_client.has_open_position.return_value = False
    fake_client.place_market_order.return_value = MagicMock(
        success=True, dukas_order_id="live_jf1", fill_price=1.0850, error="",
    )
    with patch.object(executor, "_get_client", return_value=fake_client):
        result = executor.execute_from_report(_make_report())

    assert result.executed is True
    fake_client.place_market_order.assert_called_once()


def test_get_client_constructs_dukascopy_jforex_client(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    from execution.dukascopy_jforex_client import DukascopyJForexClient

    executor = TradeExecutor(broker="dukascopy_jforex")
    client = executor._get_client()
    assert isinstance(client, DukascopyJForexClient)
