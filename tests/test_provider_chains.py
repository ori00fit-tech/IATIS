"""
tests/test_provider_chains.py
------------------------------
Asset-class-aware data layer (philosophy-audit data proposal):
  - class routing picks the right chain (crypto→ccxt first, fx→ctrader first)
  - native-timeframe awareness: a provider is only asked for timeframes it
    serves natively (Yahoo never gets an H4 request; Twelve Data free never
    gets H4/D1); resampling is the last resort, not the default
  - the cTrader guard fails fast without credentials and the chain falls
    through with no side effects
"""

import pandas as pd
import pytest

import core.data_providers as dp


def _df(n=100, freq="h", mark=1000.0):
    idx = pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC")
    px = pd.Series(100.0, index=idx)
    return pd.DataFrame({"open": px, "high": px + 1, "low": px - 1,
                         "close": px, "volume": mark}, index=idx)


# ── Routing ──────────────────────────────────────────────────────────────

def test_symbol_class_routing():
    assert dp.symbol_class("BTC/USD") == "crypto"
    assert dp.symbol_class("ETHUSD") == "crypto"
    assert dp.symbol_class("XAU/USD") == "metals"
    assert dp.symbol_class("WTI/USD") == "energy"     # fetch-name special
    assert dp.symbol_class("DJI") == "indices"
    assert dp.symbol_class("EUR/USD") == "fx"


def test_provider_chain_defaults_and_overrides():
    assert dp.provider_chain_for("BTC/USD")[0] == "ccxt"
    assert dp.provider_chain_for("EUR/USD")[0] == "ctrader"
    assert dp.provider_chain_for("EUR/USD", {"fx": ["twelve_data"]}) == ["twelve_data"]


def test_fcs_api_placement():
    """fcs_api sits right after twelve_data (fx/metals) or right after
    ctrader where there's no twelve_data entry (indices) — no crypto/energy
    route was requested."""
    fx_chain = dp.provider_chain_for("EUR/USD")
    assert fx_chain[fx_chain.index("twelve_data") + 1] == "fcs_api"

    metals_chain = dp.provider_chain_for("XAU/USD")
    assert metals_chain[metals_chain.index("twelve_data") + 1] == "fcs_api"

    indices_chain = dp.provider_chain_for("DJI")
    assert indices_chain[indices_chain.index("ctrader") + 1] == "fcs_api"

    assert "fcs_api" not in dp.provider_chain_for("BTC/USD")
    assert "fcs_api" not in dp.provider_chain_for("WTI/USD")


def test_yahoo_finance_is_last_in_every_chain():
    """Operator request (2026-07-14): yahoo is the least reliable source
    here (no rate-limit contract, throttles under heavy use, H4 is a
    resample not a native candle) — demoted to last resort everywhere,
    not removed."""
    for symbol in ("BTC/USD", "XAU/USD", "WTI/USD", "DJI", "EUR/USD"):
        chain = dp.provider_chain_for(symbol)
        assert chain[-1] == "yahoo_finance", f"{symbol}: {chain}"


# ── Native-timeframe awareness ───────────────────────────────────────────

def test_crypto_h4_comes_native_from_ccxt(monkeypatch):
    calls = []

    def fake_ccxt(symbol, interval, outputsize):
        calls.append(("ccxt", interval))
        return _df(n=min(outputsize, 300), freq="4h" if interval == "H4" else "h",
                   mark=42.0)

    monkeypatch.setattr(dp, "_fetch_ccxt_provider", fake_ccxt)
    monkeypatch.setattr(dp, "_fetch_twelve_data",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError(
                            "twelve_data must not be called when ccxt serves natively")))
    views = dp.fetch_multi_timeframe_with_failover(
        "BTC/USD", ["H4", "D1", "H1"], outputsize=300,
        providers=["ccxt", "twelve_data"],
    )
    assert ("ccxt", "H4") in calls and ("ccxt", "D1") in calls
    assert float(views["H4"]["volume"].iloc[0]) == 42.0     # native, not resampled


def test_yahoo_is_never_asked_for_h4_in_multi_tf(monkeypatch):
    def fake_yahoo(symbol, interval, outputsize):
        assert interval != "H4", "H4 is not native on Yahoo"
        return _df(n=outputsize, freq="h" if interval == "H1" else "D")

    monkeypatch.setattr(dp, "_fetch_yahoo_finance", fake_yahoo)
    views = dp.fetch_multi_timeframe_with_failover(
        "EUR/USD", ["H4", "D1", "H1"], outputsize=240,
        providers=["yahoo_finance"],
    )
    # H4 exists anyway — resampled from the fetched H1 base.
    assert "H4" in views
    assert len(views["H4"]) == pytest.approx(60, abs=2)     # 240 H1 → ~60 H4


def test_h4_starvation_class_fixed_by_native_provider(monkeypatch):
    # The July incident: only H1 native → thin resampled H4. With a native
    # H4 provider in the chain the decision TF gets full depth directly.
    monkeypatch.setattr(dp, "_fetch_ccxt_provider",
                        lambda s, i, o: _df(n=o, freq="4h" if i == "H4" else "h"))
    views = dp.fetch_multi_timeframe_with_failover(
        "ETH/USD", ["H4"], outputsize=750, providers=["ccxt"])
    assert len(views["H4"]) == 750                          # not ~187 resampled


# ── cTrader guard ────────────────────────────────────────────────────────

def test_ctrader_falls_through_cleanly_without_credentials(monkeypatch):
    monkeypatch.delenv("CTRADER_CLIENT_ID", raising=False)
    monkeypatch.delenv("CTRADER_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(dp, "_fetch_twelve_data",
                        lambda s, i, o, c: _df(n=o, mark=7.0))
    views = dp.fetch_multi_timeframe_with_failover(
        "EUR/USD", ["H1"], outputsize=120,
        providers=["ctrader", "twelve_data"],
    )
    assert float(views["H1"]["volume"].iloc[0]) == 7.0      # next in chain won
    assert dp._ctrader_feed_client is None                   # no connection attempted


def test_ctrader_guard_raises_datafetcherror(monkeypatch):
    monkeypatch.delenv("CTRADER_CLIENT_ID", raising=False)
    with pytest.raises(dp.DataFetchError, match="not configured"):
        dp._fetch_ctrader("EUR/USD", "H4", 100)
