"""tests/test_ctrader_client.py — cTrader client unit tests (no API connection)."""
from __future__ import annotations
import pytest
from execution.ctrader_client import (
    IATIS_TO_CTRADER, CTRADER_TO_IATIS, ASSET_CLASS,
    RECOMMENDED_LEVERAGE, CTraderClient, CTraderOrder, CTraderResult
)
from execution.trade_executor import TradeExecutor


# ─── Symbol mapping ───────────────────────────────────────────────────────────

def test_all_19_iatis_symbols_mapped():
    symbols = [
        "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
        "EURJPY","GBPJPY","AUDJPY","EURGBP","EURCHF",
        "XAUUSD","XAGUSD","USOIL",
        "US30","NAS100","SPX500",
        "BTCUSD","ETHUSD",
    ]
    for sym in symbols:
        assert sym in IATIS_TO_CTRADER, f"{sym} missing from cTrader mapping"


def test_reverse_mapping_consistent():
    for iatis, ct in IATIS_TO_CTRADER.items():
        assert CTRADER_TO_IATIS[ct] == iatis


def test_usoil_maps_to_xtiusd():
    assert IATIS_TO_CTRADER["USOIL"] == "XTIUSD"


def test_crypto_supported():
    assert "BTCUSD" in IATIS_TO_CTRADER
    assert "ETHUSD" in IATIS_TO_CTRADER


def test_leverage_recommendations():
    assert RECOMMENDED_LEVERAGE["forex"] <= 50
    assert RECOMMENDED_LEVERAGE["metal"] <= 20
    assert RECOMMENDED_LEVERAGE["crypto"] <= 10


def test_asset_classes_complete():
    for sym in IATIS_TO_CTRADER:
        assert sym in ASSET_CLASS, f"{sym} missing from ASSET_CLASS"


# ─── Credentials validation ───────────────────────────────────────────────────

def test_client_raises_without_credentials():
    with pytest.raises(ValueError, match="CTRADER_CLIENT_ID"):
        CTraderClient(client_id="", client_secret="", account_id=0, access_token="")


def test_client_raises_without_account():
    with pytest.raises(ValueError, match="CTRADER_ACCOUNT_ID"):
        CTraderClient(
            client_id="test", client_secret="test",
            account_id=0, access_token="test"
        )


# ─── Volume calculation ───────────────────────────────────────────────────────

def _make_client():
    """Make client skipping validation (for unit tests)."""
    c = object.__new__(CTraderClient)
    c.client_id = "test"
    c.client_secret = "test"
    c.account_id = 12345
    c.access_token = "test"
    c.host = CTraderClient.DEMO_HOST
    c.environment = "demo"
    c._client = None
    c._connected = False
    c._symbol_list = {}
    return c


def test_volume_eurusd_basic():
    client = _make_client()
    # $10,000 balance, 1% risk, 30 pip SL
    vol = client.calculate_volume("EURUSD", 10_000, 0.01, 0.0030)
    assert vol > 0
    assert vol <= 10000  # max 100 lots


def test_volume_xauusd():
    client = _make_client()
    vol = client.calculate_volume("XAUUSD", 10_000, 0.01, 5.0)
    assert vol > 0


def test_volume_zero_sl_returns_zero():
    client = _make_client()
    vol = client.calculate_volume("EURUSD", 10_000, 0.01, 0.0)
    assert vol == 0


def test_volume_jpy_pair():
    client = _make_client()
    # USDJPY: SL distance = 0.50 (50 pips at pip_size=0.01)
    vol = client.calculate_volume("USDJPY", 10_000, 0.01, 0.50)
    assert vol > 0


# ─── TradeExecutor with cTrader ───────────────────────────────────────────────

def _make_execute_report(symbol="EURUSD", score=75.0, bias="BEARISH"):
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {"score": score, "vote": {"winning_bias": bias}},
        "risk": {"recommended_risk_pct": 0.01},
        "news": {"blackout_active": False, "blackout_reason": ""},
    }


def test_executor_ctrader_dry_run():
    executor = TradeExecutor(dry_run=True, broker="ctrader")
    result = executor.execute_from_report(_make_execute_report())
    assert result.executed is True
    assert result.dry_run is True
    assert result.trade_id == "DRY_RUN"


def test_executor_ctrader_dry_run_btc():
    executor = TradeExecutor(dry_run=True, broker="ctrader")
    result = executor.execute_from_report(_make_execute_report("BTCUSD"))
    assert result.executed is True  # dry_run doesn't hit broker
    assert result.dry_run is True


def test_executor_ctrader_blocks_news():
    executor = TradeExecutor(dry_run=True, broker="ctrader")
    report = _make_execute_report()
    report["news"]["blackout_active"] = True
    report["news"]["blackout_reason"] = "NFP in 15 min"
    result = executor.execute_from_report(report)
    assert result.executed is False
    assert "blackout" in result.skip_reason.lower()
