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
