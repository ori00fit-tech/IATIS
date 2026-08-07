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


def test_symbol_class_routes_registered_equities_and_etfs():
    """2026-07-24: an unregistered ticker must NOT silently fall through
    to 'fx' — only symbols explicitly listed in _STOCKS/_ETF route there;
    everything else still defaults to fx (the pre-existing, documented
    fallback), which then fails loudly (wrong chain, wrong endpoint)
    rather than silently misrouting to a chain that can't serve it."""
    assert dp.symbol_class("AAPL") == "stocks"
    assert dp.symbol_class("NVDA") == "stocks"
    assert dp.symbol_class("SPY") == "etf"
    assert dp.symbol_class("QQQ") == "etf"
    assert dp.symbol_class("TSLA") == "fx"  # not yet registered — falls through


def test_provider_chain_for_stocks_and_etf():
    stocks_chain = dp.provider_chain_for("AAPL")
    etf_chain = dp.provider_chain_for("SPY")
    assert stocks_chain[0] == "alpaca"
    assert etf_chain[0] == "alpaca"
    assert "twelve_data" in stocks_chain
    assert "alpha_vantage" in stocks_chain and "finnhub" in stocks_chain


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


def test_yahoo_finance_is_excluded_from_every_chain():
    """Operator request (2026-07-16, after the 2026-07-14 demotion):
    yahoo is REMOVED from all live price chains — measured wrong
    instruments (^IXIC composite ≠ NDX, futures-basis metals, cash
    sessions = 28% H1 coverage, H4 as a 1h resample). The fetcher stays
    in the codebase for deliberate offline comparisons only."""
    for symbol in ("BTC/USD", "XAU/USD", "WTI/USD", "DJI", "EUR/USD"):
        chain = dp.provider_chain_for(symbol)
        assert "yahoo_finance" not in chain, f"{symbol}: {chain}"


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


# ── _deepen_with_history (BUG, 2026-08-04): FX/metals symbols with no
# native H4/D1 provider (Twelve Data Free 403s on H4/D1) resample from a
# short fetched H1 base and starve NNFX (needs 210 H4 bars for EMA200) and
# the MTF D1 gate (needs 50 D1 bars) — exactly the "665 NNFX Insufficient
# data" class the operator reported live. _deepen_with_history() exists
# specifically to left-extend that thin base from a local archive file,
# but looked for data/{SYM}_{TF}_2y.csv while scripts/download_deep_
# history.py — the only tool that ever writes this archive — writes
# data/{SYM}_{TF}_deep.csv. The mismatch meant this deepener silently
# no-op'd on every real deployment. Zero test coverage existed for this
# function before this fix.

def test_deepen_with_history_noop_when_no_archive_file(tmp_path, monkeypatch):
    monkeypatch.setattr("core.data_manager.DATA_DIR", tmp_path)
    short = _df(n=50, freq="4h")
    result = dp._deepen_with_history("EURUSD", "H4", short)
    assert len(result) == 50  # unchanged — no file to deepen from


def test_deepen_with_history_reads_the_deep_csv_archive(tmp_path, monkeypatch):
    monkeypatch.setattr("core.data_manager.DATA_DIR", tmp_path)
    archive = _df(n=900, freq="4h")
    archive.index.name = "datetime"
    archive.to_csv(tmp_path / "EURUSD_H4_deep.csv")
    short = _df(n=50, freq="4h")
    result = dp._deepen_with_history("EURUSD", "H4", short)
    assert len(result) > 50  # deepened from the archive


def test_deepen_with_history_ignores_a_stray_2y_csv_file(tmp_path, monkeypatch):
    """Regression pin for the exact bug: a file under the OLD, wrong
    filename must never be picked up — proves the fix actually changed
    which path is read, not just that *a* file works."""
    monkeypatch.setattr("core.data_manager.DATA_DIR", tmp_path)
    archive = _df(n=900, freq="4h")
    archive.index.name = "datetime"
    archive.to_csv(tmp_path / "EURUSD_H4_2y.csv")  # old, wrong name
    short = _df(n=50, freq="4h")
    result = dp._deepen_with_history("EURUSD", "H4", short)
    assert len(result) == 50  # NOT deepened — old filename is ignored


def test_deepen_with_history_fresh_bars_win_on_overlap(tmp_path, monkeypatch):
    monkeypatch.setattr("core.data_manager.DATA_DIR", tmp_path)
    archive = _df(n=900, freq="4h", mark=1.0)  # 2026-01-01 -> ~2026-06-19
    archive.index.name = "datetime"
    archive.to_csv(tmp_path / "EURUSD_H4_deep.csv")
    # fresh starts 20 bars before the archive's last timestamp — a real
    # overlap at the boundary, matching the actual live shape (archive =
    # deep past, fresh fetch = most recent window extending to "now").
    overlap_start = archive.index[-20]
    fresh_idx = pd.date_range(overlap_start, periods=50, freq="4h", tz="UTC")
    px = pd.Series(100.0, index=fresh_idx)
    fresh = pd.DataFrame({"open": px, "high": px + 1, "low": px - 1,
                          "close": px, "volume": 2.0}, index=fresh_idx)
    result = dp._deepen_with_history("EURUSD", "H4", fresh)
    # the overlapping timestamps must carry the FRESH value, not the archive's
    assert float(result.loc[overlap_start, "volume"]) == 2.0
    # the archive still supplies the older, non-overlapping bars
    assert float(result["volume"].iloc[0]) == 1.0
    assert len(result) > len(fresh)


def test_deepen_with_history_noop_when_already_deep_enough():
    already_deep = _df(n=dp._MIN_RESAMPLE_BASE_BARS, freq="4h")
    result = dp._deepen_with_history("EURUSD", "H4", already_deep)
    assert result is already_deep  # short-circuits before touching disk at all


def test_multi_tf_failover_deepens_starved_h4_resample_from_archive(tmp_path, monkeypatch):
    """End-to-end: the exact live class this bug caused — no native H4
    provider (Twelve Data Free-style), a thin H1 base resamples to well
    under NNFX's 210-bar floor, and the archive must lift it back above
    that floor."""
    monkeypatch.setattr("core.data_manager.DATA_DIR", tmp_path)
    archive = _df(n=900, freq="4h")
    archive.index.name = "datetime"
    archive.to_csv(tmp_path / "EURUSD_H4_deep.csv")
    monkeypatch.setattr(dp, "_fetch_twelve_data",
                        lambda s, i, o, use_cache=True: _df(n=200, freq="h"))
    # H4 isn't native on twelve_data (_NATIVE_TF), so H1 must also be
    # requested — it's what actually gets fetched and resampled into H4.
    views = dp.fetch_multi_timeframe_with_failover(
        "EURUSD", ["H4", "H1"], outputsize=200, providers=["twelve_data"])
    assert len(views["H4"]) >= 210  # cleared NNFX's EMA200 floor


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


def test_fetch_ctrader_timestamp_unit_is_seconds_not_milliseconds(monkeypatch):
    """Regression (2026-07-27): execution/ctrader_client.py's
    _on_trendbars_res stores "timestamp" as utcTimestampInMinutes*60 —
    SECONDS since epoch (confirmed by scripts/download_ctrader_fx_history.py
    and scripts/backtest_ic_symbols.py, both of which parse get_trendbars()'
    output with unit="s"/time.gmtime()). _fetch_ctrader used to parse it
    with unit="ms", dividing every cTrader bar's real elapsed time by 1000 —
    collapsing a 2026 timestamp to ~20 days after the Unix epoch (Jan 1970)
    and compressing real 1-hour bar spacing to ~3.6 seconds, silently
    corrupting every cTrader-sourced DataFrame's index while leaving the
    OHLC values untouched. Root-caused live via the Cross-Provider Data
    Confidence panel: every ctrader-vs-twelve_data check on FX symbols
    returned NO_OVERLAP (0 common bars) — impossible unless one side's
    timestamps were off by orders of magnitude, which they were."""
    monkeypatch.setenv("CTRADER_CLIENT_ID", "x")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "y")

    real_dt = pd.Timestamp("2026-07-27 12:00:00", tz="UTC")
    ts_seconds = int(real_dt.timestamp())  # what get_trendbars() actually returns

    class _FakeClient:
        def get_trendbars(self, symbol, period="H1", count=10):
            return [{"timestamp": ts_seconds, "open": 1.1, "high": 1.11,
                      "low": 1.09, "close": 1.1, "volume": 100}]

    monkeypatch.setattr(dp, "get_shared_ctrader_client", lambda: _FakeClient())

    df = dp._fetch_ctrader("EUR/USD", "H1", 10)
    assert df.index[0] == real_dt
    assert df.index[0].year == 2026  # not 1970


# ── MT5 bridge (2026-07-27) ─────────────────────────────────────────────

def test_mt5_falls_through_cleanly_without_bridge_url(monkeypatch):
    monkeypatch.delenv("MT5_BRIDGE_URL", raising=False)
    monkeypatch.setattr(dp, "_fetch_twelve_data",
                        lambda s, i, o, c: _df(n=o, mark=9.0))
    views = dp.fetch_multi_timeframe_with_failover(
        "EUR/USD", ["H1"], outputsize=120,
        providers=["mt5", "twelve_data"],
    )
    assert float(views["H1"]["volume"].iloc[0]) == 9.0  # next in chain won


def test_fetch_mt5_raises_when_bridge_url_unset(monkeypatch):
    monkeypatch.delenv("MT5_BRIDGE_URL", raising=False)
    with pytest.raises(dp.DataFetchError, match="not configured"):
        dp._fetch_mt5("EUR/USD", "H1", 10)


def test_fetch_mt5_parses_bridge_response(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://127.0.0.1:18812")

    real_dt = pd.Timestamp("2026-07-27 12:00:00", tz="UTC")
    ts_seconds = int(real_dt.timestamp())

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"rates": [{
                "time": ts_seconds, "open": 1.1, "high": 1.11,
                "low": 1.09, "close": 1.1, "volume": 250.0,
            }]}

    def _fake_get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/rates")
        assert params["symbol"] == "EURUSD"
        return _FakeResp()

    import requests as _requests
    monkeypatch.setattr(_requests, "get", _fake_get)

    df = dp._fetch_mt5("EUR/USD", "H1", 10)
    assert df.index[0] == real_dt
    assert df.index[0].year == 2026
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert float(df["volume"].iloc[0]) == 250.0


def test_fetch_mt5_raises_on_bridge_http_error(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://127.0.0.1:18812")

    import requests as _requests

    def _fake_get(url, params=None, headers=None, timeout=None):
        raise _requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(_requests, "get", _fake_get)

    with pytest.raises(dp.DataFetchError, match="MT5 bridge"):
        dp._fetch_mt5("EUR/USD", "H1", 10)


def test_mt5_not_in_default_chains():
    for cls, chain in dp.DEFAULT_CHAINS.items():
        assert "mt5" not in chain, f"{cls}: {chain}"


def test_provider_chain_override_can_include_mt5():
    chain = dp.provider_chain_for("EUR/USD", {"fx": ["ctrader", "mt5", "twelve_data"]})
    assert chain == ["ctrader", "mt5", "twelve_data"]


# ── Dukascopy JForex bridge (2026-08-08) ────────────────────────────────

def test_dukascopy_jforex_falls_through_cleanly_without_bridge_url(monkeypatch):
    monkeypatch.delenv("DUKASCOPY_JFOREX_BRIDGE_URL", raising=False)
    monkeypatch.setattr(dp, "_fetch_twelve_data",
                        lambda s, i, o, c: _df(n=o, mark=9.0))
    views = dp.fetch_multi_timeframe_with_failover(
        "EUR/USD", ["H1"], outputsize=120,
        providers=["dukascopy_jforex", "twelve_data"],
    )
    assert float(views["H1"]["volume"].iloc[0]) == 9.0  # next in chain won


def test_fetch_dukascopy_jforex_raises_when_bridge_url_unset(monkeypatch):
    monkeypatch.delenv("DUKASCOPY_JFOREX_BRIDGE_URL", raising=False)
    with pytest.raises(dp.DataFetchError, match="not configured"):
        dp._fetch_dukascopy_jforex("EUR/USD", "H1", 10)


def test_fetch_dukascopy_jforex_parses_bridge_response(monkeypatch):
    # Real bridge contract, verified live on the operator's VPS 2026-08-07
    # against the actual HistDataController.java source: instID is
    # slash-separated, there is no `count` param (only a required `from`
    # epoch-ms), and each candle's field is "timestamp" in MILLISECONDS.
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")

    real_dt = pd.Timestamp("2026-08-08 12:00:00", tz="UTC")
    ts_ms = int(real_dt.timestamp() * 1000)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{
                "timestamp": ts_ms, "open": 1.1, "high": 1.11,
                "low": 1.09, "close": 1.1, "volume": 250.0, "spread": 0.5,
            }]

    def _fake_get(url, params=None, timeout=None):
        assert url.endswith("/api/v1/history")
        assert params["instID"] == "EUR/USD"
        assert params["timeFrame"] == "1HOUR"
        assert "from" in params and "count" not in params
        return _FakeResp()

    import requests as _requests
    monkeypatch.setattr(_requests, "get", _fake_get)

    df = dp._fetch_dukascopy_jforex("EUR/USD", "H1", 10)
    assert df.index[0] == real_dt
    assert df.index[0].year == 2026
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert float(df["volume"].iloc[0]) == 250.0


def test_fetch_dukascopy_jforex_defaults_missing_volume_to_zero(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    ts_ms = int(pd.Timestamp("2026-08-08 12:00:00", tz="UTC").timestamp() * 1000)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"timestamp": ts_ms, "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.1}]

    import requests as _requests
    monkeypatch.setattr(_requests, "get", lambda url, params=None, timeout=None: _FakeResp())

    df = dp._fetch_dukascopy_jforex("EUR/USD", "H1", 10)
    assert float(df["volume"].iloc[0]) == 0.0


def test_dukascopy_jforex_inst_id_converts_to_slash_format():
    assert dp._dukascopy_jforex_inst_id("EURUSD") == "EUR/USD"
    assert dp._dukascopy_jforex_inst_id("XAUUSD") == "XAU/USD"
    assert dp._dukascopy_jforex_inst_id("BTCUSD") == "BTC/USD"


def test_dukascopy_jforex_inst_id_raises_on_non_6_char_symbol():
    with pytest.raises(dp.DataFetchError, match="cannot derive instID"):
        dp._dukascopy_jforex_inst_id("EURUSDX")


def test_fetch_dukascopy_jforex_raises_on_unmapped_timeframe(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    with pytest.raises(dp.DataFetchError, match="no native timeframe mapping"):
        dp._fetch_dukascopy_jforex("EUR/USD", "H4", 10)


def test_fetch_dukascopy_jforex_raises_on_bridge_http_error(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")

    import requests as _requests

    def _fake_get(url, params=None, timeout=None):
        raise _requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(_requests, "get", _fake_get)

    with pytest.raises(dp.DataFetchError, match="Dukascopy JForex bridge"):
        dp._fetch_dukascopy_jforex("EUR/USD", "H1", 10)


def test_fetch_dukascopy_jforex_raises_on_empty_result(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    import requests as _requests
    monkeypatch.setattr(_requests, "get", lambda url, params=None, timeout=None: _FakeResp())

    with pytest.raises(dp.DataFetchError, match="no bars for"):
        dp._fetch_dukascopy_jforex("EUR/USD", "H1", 10)


def test_dukascopy_jforex_not_in_default_chains():
    for cls, chain in dp.DEFAULT_CHAINS.items():
        assert "dukascopy_jforex" not in chain, f"{cls}: {chain}"


def test_provider_chain_override_can_include_dukascopy_jforex():
    chain = dp.provider_chain_for("EUR/USD", {"fx": ["ctrader", "dukascopy_jforex", "twelve_data"]})
    assert chain == ["ctrader", "dukascopy_jforex", "twelve_data"]


def test_dukascopy_jforex_native_tf_has_no_m30_or_h4():
    # Verified against dukas-api's own README enum — no 30-minute or
    # 4-hour candle is natively available.
    native = dp._NATIVE_TF["dukascopy_jforex"]
    assert "M30" not in native
    assert "H4" not in native
    assert native == {"M1", "M5", "M15", "H1", "D1"}


# ── get_shared_ctrader_client() thread-safety (Forensic Audit, 2026-08-04) ──
# P0 cTrader lifecycle re-audit: the user's live logs showed overlapping
# TCP_CONNECTED events ~5s apart. get_shared_ctrader_client()'s own
# docstring already names the exact failure mode this guards against
# ("two independently-connecting CTraderClient instances in the same
# process fight over that slot forever") and wires a threading.Lock
# around the singleton's construction — this proves that guard actually
# holds under real concurrent access, not just by reading the code.

def test_get_shared_ctrader_client_constructs_exactly_once_under_concurrency(monkeypatch):
    import threading as _threading
    import time as _time

    dp._ctrader_feed_client = None
    dp._ctrader_feed_lock = None
    monkeypatch.setenv("CTRADER_CLIENT_ID", "x")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "y")

    construct_count = {"n": 0}
    connect_count = {"n": 0}

    class _FakeCTraderClient:
        def __init__(self):
            construct_count["n"] += 1
            # Widen the race window: a real connect() takes real wall-clock
            # time too, which is exactly what let two threads both observe
            # `_ctrader_feed_client is None` before the fix existed.
            _time.sleep(0.05)

        def connect(self, timeout=30):
            connect_count["n"] += 1
            return True

    monkeypatch.setattr("execution.ctrader_client.CTraderClient", _FakeCTraderClient)

    results: list[object] = []
    errors: list[BaseException] = []

    def _worker():
        try:
            results.append(dp.get_shared_ctrader_client())
        except BaseException as exc:  # noqa: BLE001 — surfaced via `errors`, not swallowed
            errors.append(exc)

    threads = [_threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert errors == []
    assert construct_count["n"] == 1, "exactly one CTraderClient must be constructed"
    assert connect_count["n"] == 1, "exactly one connect() attempt must run"
    assert len(results) == 8
    assert all(r is results[0] for r in results), "every caller must get the SAME shared instance"

    dp._ctrader_feed_client = None
    dp._ctrader_feed_lock = None


# ── Still-forming last bar (Forensic Audit follow-up, 2026-08-04) ──────────
# main.py's own decision-report comment assumes df.index[-1] is "the last
# CLOSED candle of the decision timeframe" — but no provider fetch was
# actually verified/enforced to guarantee that. ccxt/Binance's fetchOHLCV in
# particular is well known to include the current, still-forming candle for
# whatever bucket "now" falls into. This directly affects the crypto carrier
# assets (BTCUSD/ETHUSD), whose H4/D1 come NATIVELY from ccxt (no resample
# step in between to incidentally drop it).

def _df_ending_now(n=100, freq="h"):
    """A synthetic OHLCV frame whose LAST bar's open time is the most
    recent completed bucket boundary before real wall-clock now — i.e.
    the last bar is fully closed. Used to prove the trim is a no-op for
    a well-behaved provider."""
    now = pd.Timestamp.now(tz="UTC").floor(freq)
    idx = pd.date_range(end=now - pd.tseries.frequencies.to_offset(freq), periods=n, freq=freq, tz="UTC")
    px = pd.Series(100.0, index=idx)
    return pd.DataFrame({"open": px, "high": px + 1, "low": px - 1,
                         "close": px, "volume": 1.0}, index=idx)


def _df_with_forming_last_bar(n=100, freq="h"):
    """A synthetic OHLCV frame whose LAST bar's open time is the CURRENT,
    still-in-progress bucket (mirrors what ccxt/Binance can return)."""
    now = pd.Timestamp.now(tz="UTC").floor(freq)
    idx = pd.date_range(end=now, periods=n, freq=freq, tz="UTC")
    px = pd.Series(100.0, index=idx)
    return pd.DataFrame({"open": px, "high": px + 1, "low": px - 1,
                         "close": px, "volume": 1.0}, index=idx)


def test_drop_still_forming_bar_trims_a_bar_still_in_progress():
    df = _df_with_forming_last_bar(n=10, freq="h")
    trimmed = dp._drop_still_forming_bar(df, "H1")
    assert len(trimmed) == len(df) - 1
    assert trimmed.index[-1] == df.index[-2]


def test_drop_still_forming_bar_is_a_noop_for_a_closed_last_bar():
    df = _df_ending_now(n=10, freq="h")
    trimmed = dp._drop_still_forming_bar(df, "H1")
    assert len(trimmed) == len(df)
    assert trimmed.index[-1] == df.index[-1]


def test_drop_still_forming_bar_noop_on_unknown_timeframe():
    df = _df_with_forming_last_bar(n=10, freq="h")
    trimmed = dp._drop_still_forming_bar(df, "UNKNOWN_TF")
    assert len(trimmed) == len(df)


def test_drop_still_forming_bar_noop_on_empty_df():
    df = _df_with_forming_last_bar(n=0, freq="h").iloc[0:0]
    trimmed = dp._drop_still_forming_bar(df, "H1")
    assert trimmed.empty


def test_drop_still_forming_bar_localizes_naive_index():
    df = _df_with_forming_last_bar(n=10, freq="h")
    naive = df.copy()
    naive.index = naive.index.tz_localize(None)
    trimmed = dp._drop_still_forming_bar(naive, "H1")
    assert len(trimmed) == len(naive) - 1


def test_fetch_with_failover_trims_forming_bar_from_a_real_provider(monkeypatch):
    """The authoritative end-to-end proof: fetch_with_failover() — the
    single choke point used by fetch_multi_timeframe_with_failover(),
    execution/routes/analyze.py, and scripts/cross_provider_diff.py —
    strips a provider's still-forming last bar before it ever reaches a
    caller, regardless of which provider served it."""
    def fake_ccxt(symbol, interval, outputsize):
        return _df_with_forming_last_bar(n=min(outputsize, 50), freq="h")

    monkeypatch.setattr(dp, "_fetch_ccxt_provider", fake_ccxt)
    df, provider = dp.fetch_with_failover("BTC/USD", "H1", outputsize=50, providers=["ccxt"])
    assert provider == "ccxt"
    # The forming bar is gone — last remaining bar's own window has fully closed.
    assert (df.index[-1] + pd.Timedelta(hours=1)) <= pd.Timestamp.now(tz="UTC")


def test_fetch_with_failover_does_not_trim_an_already_closed_bar(monkeypatch):
    """Regression pin: a provider that already only returns closed bars
    (the common/expected case) is completely unaffected — same bar count
    in and out."""
    def fake_ccxt(symbol, interval, outputsize):
        return _df_ending_now(n=min(outputsize, 50), freq="h")

    monkeypatch.setattr(dp, "_fetch_ccxt_provider", fake_ccxt)
    df, provider = dp.fetch_with_failover("BTC/USD", "H1", outputsize=50, providers=["ccxt"])
    assert len(df) == 50


def test_fetch_with_failover_falls_through_when_only_bar_is_still_forming(monkeypatch):
    """A provider whose ENTIRE response is just the one still-forming bar
    must be treated as a failure (empty-after-trim), falling through to
    the next provider in the chain — never silently handing a caller a
    single, moving-target bar."""
    def fake_ccxt(symbol, interval, outputsize):
        return _df_with_forming_last_bar(n=1, freq="h")

    def fake_twelve_data(symbol, interval, outputsize, use_cache):
        return _df_ending_now(n=10, freq="h")

    monkeypatch.setattr(dp, "_fetch_ccxt_provider", fake_ccxt)
    monkeypatch.setattr(dp, "_fetch_twelve_data", fake_twelve_data)
    df, provider = dp.fetch_with_failover(
        "BTC/USD", "H1", outputsize=10, providers=["ccxt", "twelve_data"],
    )
    assert provider == "twelve_data"
