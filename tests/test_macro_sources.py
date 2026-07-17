"""
tests/test_macro_sources.py
----------------------------
Official macro sources (CBOE VIX CSV, FRED series) + per-series failover
and the snapshot TTL cache. No network — responses are fixtures.
"""

from types import SimpleNamespace

import pandas as pd
import pytest

import core.alt_data_loader as adl


CBOE_CSV = (
    "DATE,OPEN,HIGH,LOW,CLOSE\n"
    "2026-07-07,17.10,18.40,16.90,18.02\n"
    "2026-07-08,18.00,18.20,16.50,16.77\n"
    "2026-07-09,16.80,17.90,16.60,17.55\n"
)

FRED_JSON = {
    "observations": [
        {"date": "2026-07-07", "value": "101.42"},
        {"date": "2026-07-08", "value": "."},          # FRED's missing marker
        {"date": "2026-07-09", "value": "101.88"},
    ]
}


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    adl._SNAPSHOT_CACHE.update(at=0.0, key=None, data=None)
    yield
    adl._SNAPSHOT_CACHE.update(at=0.0, key=None, data=None)


def _fake_get(payload_text=None, payload_json=None):
    def fake(url, params=None, timeout=None):
        return SimpleNamespace(
            text=payload_text,
            json=lambda: payload_json,
            raise_for_status=lambda: None,
        )
    return fake


def test_cboe_vix_parses_full_ohlc(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", _fake_get(payload_text=CBOE_CSV))
    df = adl.load_vix_from_cboe()
    assert len(df) == 3
    assert df["high"].iloc[0] == pytest.approx(18.40)
    assert df["close"].iloc[-1] == pytest.approx(17.55)
    assert str(df.index.tz) == "UTC"


def test_fred_api_path_skips_missing_values(monkeypatch):
    import requests
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")
    monkeypatch.setattr(requests, "get", _fake_get(payload_json=FRED_JSON))
    df = adl.load_from_fred("DTWEXBGS")
    assert len(df) == 2                       # the "." observation dropped
    assert df["close"].iloc[-1] == pytest.approx(101.88)
    assert (df["open"] == df["close"]).all()  # close-only contract


def test_vix_failover_cboe_then_fred_then_yahoo(monkeypatch):
    calls = []
    monkeypatch.setattr(adl, "load_vix_from_cboe",
                        lambda: (_ for _ in ()).throw(ValueError("cboe down")))

    def fake_fred(series_id, months=6):
        calls.append(("fred", series_id))
        return adl._close_only_frame(["2026-07-09"], [17.5])

    monkeypatch.setattr(adl, "load_from_fred", fake_fred)
    monkeypatch.setattr(adl, "load_from_yfinance",
                        lambda *a, **k: pytest.fail("yahoo must not be reached"))

    snap = adl.load_macro_snapshot(["VIX"])
    assert calls == [("fred", "VIXCLS")]
    assert snap["VIX"].attrs["provider"] == "fred"


def test_dxy_is_fred_only_no_yahoo_fallback(monkeypatch):
    # Yahoo was removed as an untrusted feed (2026-07-17): DXY is FRED-only,
    # so when FRED is down the series is simply absent — it must NOT silently
    # fall back to Yahoo.
    monkeypatch.setattr(adl, "load_from_fred",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("fred down")))
    monkeypatch.setattr(adl, "load_from_yfinance",
                        lambda *a, **k: pytest.fail("yahoo must not be reached"))
    snap = adl.load_macro_snapshot(["DXY"])
    assert "DXY" not in snap


def test_gld_uses_fred_gold_not_yahoo(monkeypatch):
    # GLD (gold) now resolves to the FRED LBMA gold-fixing series, not Yahoo.
    seen = []
    monkeypatch.setattr(adl, "load_from_fred",
                        lambda series_id, months=6: seen.append(series_id) or adl._close_only_frame(["2026-07-09"], [2350.0]))
    monkeypatch.setattr(adl, "load_from_yfinance",
                        lambda *a, **k: pytest.fail("yahoo must not be reached"))
    snap = adl.load_macro_snapshot(["GLD"])
    assert seen == ["GOLDAMGBD228NLBM"]
    assert snap["GLD"].attrs["provider"] == "fred"


def test_snapshot_cache_prevents_refetch_within_ttl(monkeypatch):
    counter = {"n": 0}

    def fake_fred(series_id, months=6):
        counter["n"] += 1
        return adl._close_only_frame(["2026-07-09"], [101.0])

    monkeypatch.setattr(adl, "load_from_fred", fake_fred)
    adl.load_macro_snapshot(["DXY"])
    adl.load_macro_snapshot(["DXY"])          # served from cache
    assert counter["n"] == 1
