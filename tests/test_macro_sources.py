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


# ---------------------------------------------------------------------------
# Confluence Engine Overhaul Phase 3c (2026-08-01) — 5 new FRED series for
# the Macro engine rebuild, plus the per-series lookback-months override
# needed for the two non-daily series (COPPER monthly, FED_BALANCE_SHEET
# weekly). All purely additive — every existing series above must keep
# resolving/behaving exactly as already pinned by the tests above.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol,expected_series_id", [
    ("OIL_WTI", "DCOILWTICO"),
    ("NATGAS", "DHHNGSP"),
    ("CREDIT_SPREAD", "BAA10Y"),
    ("FED_BALANCE_SHEET", "WALCL"),
    ("COPPER", "PCOPPUSDM"),
])
def test_new_series_resolve_to_correct_fred_ids(monkeypatch, symbol, expected_series_id):
    seen = []

    def fake_fred(series_id, months=6):
        seen.append((series_id, months))
        return adl._close_only_frame(["2026-07-09"], [1.0])

    monkeypatch.setattr(adl, "load_from_fred", fake_fred)
    snap = adl.load_macro_snapshot([symbol])
    assert seen[0][0] == expected_series_id
    assert snap[symbol].attrs["provider"] == "fred"


def test_lookback_months_override_applies_only_to_non_daily_series(monkeypatch):
    seen = {}

    def fake_fred(series_id, months=6):
        seen[series_id] = months
        return adl._close_only_frame(["2026-07-09"], [1.0])

    monkeypatch.setattr(adl, "load_from_fred", fake_fred)
    adl.load_macro_snapshot(["DXY", "US10Y", "OIL_WTI", "COPPER", "FED_BALANCE_SHEET"])

    assert seen["DTWEXBGS"] == 6      # DXY — unchanged default
    assert seen["DGS10"] == 6         # US10Y — unchanged default
    assert seen["DCOILWTICO"] == 6    # OIL_WTI (daily) — unchanged default
    assert seen["PCOPPUSDM"] == 24    # COPPER (monthly) — extended override
    assert seen["WALCL"] == 12        # FED_BALANCE_SHEET (weekly) — extended override


def test_load_macro_snapshot_default_symbols_unchanged(monkeypatch):
    # Existing callers relying on the default symbol list must be unaffected
    # by this phase's additions — the 5 new series are opt-in only.
    seen = []
    monkeypatch.setattr(adl, "load_from_fred", lambda series_id, months=6: (seen.append(series_id), adl._close_only_frame(["2026-07-09"], [1.0]))[1])
    monkeypatch.setattr(adl, "load_vix_from_cboe", lambda: (_ for _ in ()).throw(ValueError("cboe down")))
    adl.load_macro_snapshot()  # no explicit symbols -> default list
    assert set(seen) == {"DTWEXBGS", "DGS10", "DGS2", "VIXCLS", "GOLDAMGBD228NLBM", "SP500"}


# ---------------------------------------------------------------------------
# Provider Benchmark Phase 3 (Macro Benchmark) — Alpha Vantage economic
# indicators. Used only for benchmarking; load_macro_snapshot() (the Macro
# engine's live-decision-path source, tested above) must stay completely
# unaffected — pinned explicitly at the bottom of this block.
# ---------------------------------------------------------------------------

AV_ECONOMIC_JSON = {
    "name": "10-Year Treasury Constant Maturity Rate",
    "interval": "daily",
    "unit": "percent",
    "data": [
        {"date": "2026-07-09", "value": "4.25"},
        {"date": "2026-07-08", "value": "."},   # Alpha Vantage's own missing-observation marker
        {"date": "2026-07-07", "value": "4.30"},
    ],
}


def test_av_economic_skips_missing_marker_and_sorts(monkeypatch):
    import requests
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-av-key")
    monkeypatch.setattr(requests, "get", _fake_get(payload_json=AV_ECONOMIC_JSON))
    df = adl.load_from_alpha_vantage_economic("US10Y")
    assert len(df) == 2                        # the "." observation dropped
    assert list(df["close"]) == [4.30, 4.25]    # sorted ascending by date
    assert (df["open"] == df["close"]).all()    # close-only contract


def test_av_economic_unknown_series_raises(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-av-key")
    with pytest.raises(ValueError, match="No Alpha Vantage economic series"):
        adl.load_from_alpha_vantage_economic("NOT_A_SERIES")


def test_av_economic_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key not set"):
        adl.load_from_alpha_vantage_economic("US10Y")


def test_av_economic_rate_limit_note_raises(monkeypatch):
    import requests
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-av-key")
    monkeypatch.setattr(requests, "get", _fake_get(payload_json={"Note": "daily limit reached"}))
    with pytest.raises(ValueError, match="rate limit"):
        adl.load_from_alpha_vantage_economic("CPI")


def test_av_economic_error_message_raises(monkeypatch):
    import requests
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-av-key")
    monkeypatch.setattr(requests, "get", _fake_get(payload_json={"Error Message": "bad function"}))
    with pytest.raises(ValueError, match="Alpha Vantage error"):
        adl.load_from_alpha_vantage_economic("REAL_GDP")


def test_av_economic_empty_data_raises(monkeypatch):
    import requests
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-av-key")
    monkeypatch.setattr(requests, "get", _fake_get(payload_json={"data": []}))
    with pytest.raises(ValueError, match="no data|no usable"):
        adl.load_from_alpha_vantage_economic("UNEMPLOYMENT")


def test_av_economic_all_missing_markers_raises(monkeypatch):
    import requests
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-av-key")
    payload = {"data": [{"date": "2026-07-09", "value": "."}]}
    monkeypatch.setattr(requests, "get", _fake_get(payload_json=payload))
    with pytest.raises(ValueError, match="no usable observations"):
        adl.load_from_alpha_vantage_economic("NONFARM_PAYROLL")


@pytest.mark.parametrize("series_key,expected_function,expected_maturity", [
    ("US10Y", "TREASURY_YIELD", "10year"),
    ("US02Y", "TREASURY_YIELD", "2year"),
    ("FED_FUNDS_RATE", "FEDERAL_FUNDS_RATE", None),
    ("CPI", "CPI", None),
    ("REAL_GDP", "REAL_GDP", None),
    ("UNEMPLOYMENT", "UNEMPLOYMENT", None),
    ("NONFARM_PAYROLL", "NONFARM_PAYROLL", None),
])
def test_av_economic_series_map_functions(monkeypatch, series_key, expected_function, expected_maturity):
    seen_params = {}

    def fake_get(url, params=None, timeout=None):
        seen_params.update(params)
        return SimpleNamespace(
            json=lambda: {"data": [{"date": "2026-07-09", "value": "1.0"}]},
            raise_for_status=lambda: None,
        )

    import requests
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-av-key")
    monkeypatch.setattr(requests, "get", fake_get)
    adl.load_from_alpha_vantage_economic(series_key)
    assert seen_params["function"] == expected_function
    if expected_maturity:
        assert seen_params["maturity"] == expected_maturity


def test_av_economic_never_reaches_load_macro_snapshot(monkeypatch):
    """Hard-block: load_macro_snapshot() (the Macro engine's live source)
    must never call the new Alpha Vantage function — this addition is
    benchmark-only, the live decision path is completely unchanged."""
    calls = []
    monkeypatch.setattr(adl, "load_from_alpha_vantage_economic",
                         lambda *a, **k: calls.append(a) or pytest.fail("must not be called"))
    monkeypatch.setattr(adl, "load_from_fred", lambda series_id, months=6: adl._close_only_frame(["2026-07-09"], [1.0]))
    adl.load_macro_snapshot(["DXY", "US10Y", "US02Y", "VIX"])
    assert calls == []
