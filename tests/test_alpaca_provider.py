"""tests/test_alpaca_provider.py — Alpaca crypto data provider.

Scope guard (crypto only), auth fallthrough, response parsing, chain
placement, and native-timeframe registration.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core import data_providers as dp
from core.data_providers import DataFetchError, _fetch_alpaca


def _alpaca_response(symbol="BTC/USD", n=5):
    bars = [
        {
            "t": f"2026-07-{10 + i:02d}T00:00:00Z",
            "o": 100000.0 + i, "h": 100010.0 + i,
            "l": 99990.0 + i, "c": 100005.0 + i, "v": 12.5,
        }
        for i in range(n)
    ]
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"bars": {symbol: bars}, "next_page_token": None}
    return resp


@pytest.fixture
def alpaca_creds(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def test_missing_credentials_fall_through():
    with pytest.raises(DataFetchError, match="ALPACA_API_KEY"):
        _fetch_alpaca("BTC/USD", "H4", 100)


def test_non_crypto_symbol_is_refused(alpaca_creds):
    """Alpaca serves no FX/metals/index CFDs — must refuse loudly, never
    return look-alike data for the wrong instrument."""
    for sym in ("EUR/USD", "XAUUSD", "SPX500"):
        with pytest.raises(DataFetchError, match="crypto only"):
            _fetch_alpaca(sym, "H4", 100)


def test_fetch_parses_bars_and_sorts_ascending(alpaca_creds):
    with patch("requests.get", return_value=_alpaca_response()) as mock_get:
        df = _fetch_alpaca("BTC/USD", "H4", 100)

    assert len(df) == 5
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing
    assert df["close"].iloc[-1] == pytest.approx(100009.0)

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["symbols"] == "BTC/USD"
    assert kwargs["params"]["timeframe"] == "4Hour"
    assert kwargs["params"]["sort"] == "desc"
    assert kwargs["headers"]["APCA-API-KEY-ID"] == "test-key"
    # Data host, never the trading (paper-api) host.
    url = mock_get.call_args[0][0]
    assert url.startswith("https://data.alpaca.markets")
    assert "v1beta3/crypto" in url


def test_empty_response_raises(alpaca_creds):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"bars": {}}
    with patch("requests.get", return_value=resp):
        with pytest.raises(DataFetchError, match="empty"):
            _fetch_alpaca("ETH/USD", "H1", 50)


def test_unsupported_interval_raises(alpaca_creds):
    with pytest.raises(DataFetchError, match="unsupported interval"):
        _fetch_alpaca("BTC/USD", "W1", 50)


def test_chain_placement_crypto_only():
    """First fallback after ccxt in crypto; absent everywhere else."""
    assert dp.DEFAULT_CHAINS["crypto"][:2] == ["ccxt", "alpaca"]
    for cls in ("fx", "metals", "energy", "indices"):
        assert "alpaca" not in dp.DEFAULT_CHAINS[cls]


def test_native_timeframes_registered():
    assert {"H1", "H4", "D1"} <= dp._NATIVE_TF["alpaca"]


def test_failover_dispatch_reaches_alpaca(alpaca_creds):
    """ccxt fails → alpaca serves, and the returned provider name is
    'alpaca' (what lands in the decision's provenance)."""
    with patch("core.data_providers._fetch_ccxt_provider",
               side_effect=DataFetchError("binance down")), \
         patch("requests.get", return_value=_alpaca_response()):
        df, provider = dp.fetch_with_failover(
            "BTC/USD", "H4", outputsize=5,
            providers=["ccxt", "alpaca"],
        )
    assert provider == "alpaca"
    assert len(df) == 5
