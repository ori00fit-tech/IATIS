"""tests/test_alpaca_provider.py — Alpaca crypto data provider.

Scope guard (crypto only), auth fallthrough, response parsing, chain
placement, and native-timeframe registration. Also covers the locked
Alpaca Stage 2 Design Gate: the extracted _alpaca_crypto_symbol()
coverage predicate + behavior-equivalence of _fetch_alpaca around it,
and backtest.provider_symbol_resolution._resolve_alpaca()'s reuse of it
(and of _is_equity_symbol()) with no new whitelist of its own.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backtest import provider_symbol_resolution as psr
from backtest.shadow_outcome_resolver import _provider_symbol
from core import data_providers as dp
from core.data_providers import DataFetchError, _alpaca_crypto_symbol, _fetch_alpaca


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


def test_missing_credentials_fall_through(monkeypatch):
    # A deployment's real .env commonly has live ALPACA_API_KEY/SECRET
    # (e.g. the VPS) — must not leak into this "credentials absent" case.
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
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


# ---------- _alpaca_crypto_symbol() (Alpaca Stage 2 Design Gate) -----------
# Extracted from what was previously an inline check-and-reconstruct
# inside _fetch_alpaca's own body -- these tests are the behavior-
# equivalence proof the locked gate requires.


def test_alpaca_crypto_symbol_supported_pairs():
    assert _alpaca_crypto_symbol("BTCUSD") == "BTC/USD"
    assert _alpaca_crypto_symbol("ETHUSD") == "ETH/USD"


def test_alpaca_crypto_symbol_unsupported_returns_none():
    """_CRYPTO remains the sole source of truth -- this function invents
    no second whitelist. Anything outside {BTCUSD, ETHUSD} is None, never
    a guessed/reconstructed value."""
    for internal in ("EURUSD", "XAUUSD", "AAPL", "SPX500"):
        assert _alpaca_crypto_symbol(internal) is None


def test_fetch_alpaca_unsupported_crypto_raises_same_type_and_message(alpaca_creds):
    """Behavior-equivalence (Alpaca Stage 2 Design Gate closing check,
    replacing a zero-diff check on core/data_providers.py, same as the
    Finnhub precedent): _fetch_alpaca's raise for a non-crypto symbol is
    byte-identical in type and message, before and after extracting
    _alpaca_crypto_symbol() out of its body."""
    with pytest.raises(DataFetchError, match=r"Alpaca serves crypto only in this codebase \(got EURUSD\)"):
        _fetch_alpaca("EUR/USD", "H4", 100)


def test_fetch_alpaca_supported_crypto_still_produces_the_same_wire_symbol(alpaca_creds):
    """Behavior-equivalence: BTC/USD and ETH/USD still resolve to the
    exact same alpaca_symbol used in the real request, unchanged by the
    extraction."""
    with patch("requests.get", return_value=_alpaca_response(symbol="BTC/USD")) as mock_get:
        _fetch_alpaca("BTC/USD", "H4", 10)
    assert mock_get.call_args[1]["params"]["symbols"] == "BTC/USD"

    with patch("requests.get", return_value=_alpaca_response(symbol="ETH/USD")) as mock_get:
        _fetch_alpaca("ETH/USD", "H4", 10)
    assert mock_get.call_args[1]["params"]["symbols"] == "ETH/USD"


# ---------- backtest.provider_symbol_resolution._resolve_alpaca() ---------
# Stage 1 (config/symbols.yaml's twelve_data_symbols) needs no changes for
# this gate -- confirmed directly, these entries already exist.


def _alpaca_base_config() -> dict:
    return {"data": {"twelve_data_symbols": [
        {"internal": "BTCUSD", "symbol": "BTC/USD"},
        {"internal": "ETHUSD", "symbol": "ETH/USD"},
        {"internal": "AAPL", "symbol": "AAPL"},
        {"internal": "NVDA", "symbol": "NVDA"},
        {"internal": "SPY", "symbol": "SPY"},
        {"internal": "QQQ", "symbol": "QQQ"},
        {"internal": "EURUSD", "symbol": "EUR/USD"},
    ]}}


@pytest.mark.parametrize("internal_symbol,expected_provider_symbol", [
    ("BTCUSD", "BTC/USD"),
    ("ETHUSD", "ETH/USD"),
    ("AAPL", "AAPL"),
    ("NVDA", "NVDA"),
    ("SPY", "SPY"),
    ("QQQ", "QQQ"),
])
def test_resolve_alpaca_supported_symbols(internal_symbol, expected_provider_symbol):
    result = psr.resolve_provider_symbol(internal_symbol, "alpaca", _alpaca_base_config())
    assert result == {
        "internal_symbol": internal_symbol,
        "provider": "alpaca",
        "shared_symbol": expected_provider_symbol,
        "provider_symbol": expected_provider_symbol,
        "resolution_state": None,
    }


def test_resolve_alpaca_unsupported_symbol_produces_unsupported_state():
    """EUR/USD is a real chain member for other providers but Alpaca
    serves crypto/equity only -- PROVIDER_SYMBOL_UNSUPPORTED must reflect
    Alpaca's actual coverage predicate, never provider-chain membership."""
    result = psr.resolve_provider_symbol("EURUSD", "alpaca", _alpaca_base_config())
    assert result == {
        "internal_symbol": "EURUSD",
        "provider": "alpaca",
        "shared_symbol": "EUR/USD",
        "provider_symbol": None,
        "resolution_state": psr.PROVIDER_SYMBOL_UNSUPPORTED,
    }


def test_resolve_alpaca_provider_symbol_is_always_a_string():
    """Unlike alpha_vantage, alpaca introduces no new type heterogeneity
    -- provider_symbol is always a str (or None), never a tuple."""
    config = _alpaca_base_config()
    for internal_symbol in ("BTCUSD", "AAPL"):
        result = psr.resolve_provider_symbol(internal_symbol, "alpaca", config)
        assert isinstance(result["provider_symbol"], str)


def test_resolve_alpaca_crypto_regression_equals_direct_extracted_call():
    """Regression-equivalence: the crypto value returned here must be
    byte-identical to calling _alpaca_crypto_symbol() directly."""
    config = _alpaca_base_config()
    shared_symbol = _provider_symbol("BTCUSD", config)
    direct = _alpaca_crypto_symbol(dp._internal_symbol(shared_symbol))
    result = psr.resolve_provider_symbol("BTCUSD", "alpaca", config)
    assert result["provider_symbol"] == direct == "BTC/USD"


def test_resolve_alpaca_equity_is_identity_not_capability_proof():
    """Explicit epistemic boundary (locked): a successful equity
    resolution proves only that the symbol passes Alpaca's coverage
    predicate (_STOCKS/_ETF membership) -- it is identity, not a network
    call, and never a claim that Alpaca will actually serve it."""
    result = psr.resolve_provider_symbol("AAPL", "alpaca", _alpaca_base_config())
    assert result["provider_symbol"] == "AAPL" == result["shared_symbol"]
    assert result["resolution_state"] is None


def test_resolve_alpaca_is_second_resolver_to_return_none():
    """Documents the locked finding: alpaca is the second Stage 2
    resolver (after finnhub) that can genuinely return None."""
    assert psr._resolve_finnhub("XAG/USD") is None
    assert psr._resolve_alpaca("EUR/USD") is None
    assert psr._resolve_twelve_data("EUR/USD") is not None
    assert psr._resolve_alpha_vantage("EUR/USD") is not None
