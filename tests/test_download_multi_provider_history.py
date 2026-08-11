"""tests/test_download_multi_provider_history.py

Regression coverage for scripts/download_multi_provider_history.py — the
Dukascopy -> cTrader -> Twelve Data fallback orchestrator. No live
network call is ever made in these tests; every provider-specific fetch
function is mocked (matching the established convention for this class
of manual, operator-run download script, e.g.
tests/test_download_ctrader_fx_history.py's _FakeClient).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import download_multi_provider_history as m
from download_multi_provider_history import (
    ProviderAttempt,
    SymbolTimeframeResult,
    _asset_class_for_symbol,
    build_provider_fetchers,
    coverage_summary,
    download_symbol_timeframe,
    verify_and_write,
)


# ---------------------------------------------------------------------------
# main(): .env PermissionError -> actionable SystemExit, not a raw traceback
# (2026-08-11 — confirmed live on the VPS this crashed with the raw
# PermissionError traceback instead of the friendly message
# scripts/download_ctrader_fx_history.py already has).
# ---------------------------------------------------------------------------


def test_main_reports_actionable_message_on_env_permission_error(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["download_multi_provider_history.py"])
    with patch("dotenv.load_dotenv", side_effect=PermissionError("denied")):
        with pytest.raises(SystemExit, match="sudo -u iatis"):
            m.main()


def _ohlcv(n: int = 6, freq: str = "15min") -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": [1.10 + 0.001 * i for i in range(n)],
         "high": [1.101 + 0.001 * i for i in range(n)],
         "low": [1.099 + 0.001 * i for i in range(n)],
         "close": [1.100 + 0.001 * i for i in range(n)],
         "volume": [10.0] * n},
        index=idx,
    )


# ---------------------------------------------------------------------------
# download_symbol_timeframe: fallback order / first-success-wins / isolation
# ---------------------------------------------------------------------------


def test_fallback_order_is_dukascopy_then_ctrader_then_twelve_data():
    assert m.PROVIDER_ORDER == ("dukascopy", "ctrader", "twelve_data")


def test_first_provider_success_short_circuits_the_rest():
    call_log: list[str] = []

    def dukascopy_ok():
        call_log.append("dukascopy")
        return _ohlcv()

    def ctrader_should_never_run():
        call_log.append("ctrader")
        return _ohlcv()

    def twelve_data_should_never_run():
        call_log.append("twelve_data")
        return _ohlcv()

    result = download_symbol_timeframe(
        "EURUSD", "M15",
        {"dukascopy": dukascopy_ok, "ctrader": ctrader_should_never_run, "twelve_data": twelve_data_should_never_run},
    )
    assert result.provider_used == "dukascopy"
    assert call_log == ["dukascopy"]
    assert len(result.attempts) == 1
    assert result.attempts[0] == ProviderAttempt("dukascopy", True, None, bars=6)


def test_falls_through_to_second_provider_on_first_failure():
    def dukascopy_fails():
        raise RuntimeError("no instrument mapping")

    def ctrader_ok():
        return _ohlcv()

    result = download_symbol_timeframe("EURUSD", "M15", {"dukascopy": dukascopy_fails, "ctrader": ctrader_ok})
    assert result.provider_used == "ctrader"
    assert len(result.attempts) == 2
    assert result.attempts[0] == ProviderAttempt("dukascopy", False, "no instrument mapping")
    assert result.attempts[1].success is True


def test_falls_through_to_third_provider_on_empty_result():
    def dukascopy_fails():
        raise RuntimeError("boom")

    def ctrader_empty():
        return pd.DataFrame()

    def twelve_data_ok():
        return _ohlcv()

    result = download_symbol_timeframe(
        "XAUUSD", "H1",
        {"dukascopy": dukascopy_fails, "ctrader": ctrader_empty, "twelve_data": twelve_data_ok},
    )
    assert result.provider_used == "twelve_data"
    assert len(result.attempts) == 3
    assert result.attempts[1] == ProviderAttempt("ctrader", False, "empty result")


def test_reports_honest_failure_when_every_available_provider_fails():
    def dukascopy_fails():
        raise RuntimeError("network down")

    def ctrader_fails():
        raise RuntimeError("no session")

    result = download_symbol_timeframe("EURUSD", "M15", {"dukascopy": dukascopy_fails, "ctrader": ctrader_fails})
    assert result.provider_used is None
    assert result.df is None
    assert len(result.attempts) == 2  # every provider that WAS tried is recorded, honestly


def test_a_provider_absent_from_the_dict_is_skipped_not_counted_as_a_failed_attempt():
    # e.g. no cTrader session this run — "ctrader" key simply isn't present.
    def dukascopy_ok():
        return _ohlcv()

    result = download_symbol_timeframe("EURUSD", "M15", {"dukascopy": dukascopy_ok})
    assert result.provider_used == "dukascopy"
    assert len(result.attempts) == 1  # not 3 — the two absent tiers are not "attempts"


def test_one_item_failure_does_not_affect_the_orchestration_of_another_item():
    # per-item isolation: two independent calls, one all-fail, one succeeds —
    # confirms no shared mutable state leaks between download_symbol_timeframe
    # calls (a real risk if attempts/results were accidentally module-level).
    def always_fails():
        raise RuntimeError("down")

    def always_ok():
        return _ohlcv()

    failed = download_symbol_timeframe("EURUSD", "M15", {"dukascopy": always_fails})
    ok = download_symbol_timeframe("XAUUSD", "M15", {"dukascopy": always_ok})

    assert failed.provider_used is None
    assert ok.provider_used == "dukascopy"
    assert len(ok.attempts) == 1  # unaffected by the previous, unrelated failure


# ---------------------------------------------------------------------------
# build_provider_fetchers: real symbol-map gating + correct call wiring
# ---------------------------------------------------------------------------


def test_dukascopy_tier_present_only_when_symbol_is_mapped():
    fetchers = build_provider_fetchers(
        "EURUSD", "M15", dukascopy_years=1.0,
        ctrader_client=None, ctrader_years=1.0,
        td_api_key="", td_symbol_map={},
    )
    assert "dukascopy" in fetchers

    fetchers_unmapped = build_provider_fetchers(
        "NOTASYMBOL", "M15", dukascopy_years=1.0,
        ctrader_client=None, ctrader_years=1.0,
        td_api_key="", td_symbol_map={},
    )
    assert "dukascopy" not in fetchers_unmapped


def test_ctrader_tier_absent_when_no_client():
    fetchers = build_provider_fetchers(
        "EURUSD", "M15", dukascopy_years=1.0,
        ctrader_client=None, ctrader_years=1.0,
        td_api_key="", td_symbol_map={},
    )
    assert "ctrader" not in fetchers


def test_ctrader_tier_present_when_client_given_and_symbol_mapped():
    fake_client = Mock()
    fetchers = build_provider_fetchers(
        "EURUSD", "M15", dukascopy_years=1.0,
        ctrader_client=fake_client, ctrader_years=1.0,
        td_api_key="", td_symbol_map={},
    )
    assert "ctrader" in fetchers


def test_ctrader_tier_absent_when_symbol_not_in_ctrader_map_even_with_client():
    fake_client = Mock()
    fetchers = build_provider_fetchers(
        "QQQ", "M15", dukascopy_years=1.0,   # QQQ (ETF) is not in IATIS_TO_CTRADER
        ctrader_client=fake_client, ctrader_years=1.0,
        td_api_key="", td_symbol_map={},
    )
    assert "ctrader" not in fetchers


def test_twelve_data_tier_absent_without_an_api_key():
    fetchers = build_provider_fetchers(
        "EURUSD", "M15", dukascopy_years=1.0,
        ctrader_client=None, ctrader_years=1.0,
        td_api_key="", td_symbol_map={"EURUSD": "EUR/USD"},
    )
    assert "twelve_data" not in fetchers


def test_twelve_data_tier_present_with_key_and_symbol_mapped():
    fetchers = build_provider_fetchers(
        "EURUSD", "M15", dukascopy_years=1.0,
        ctrader_client=None, ctrader_years=1.0,
        td_api_key="fake-key", td_symbol_map={"EURUSD": "EUR/USD"},
    )
    assert "twelve_data" in fetchers


def test_dukascopy_closure_calls_download_symbol_hours_and_coarsens():
    base_df = _ohlcv(n=8, freq="15min")
    with patch("scripts.download_dukascopy_history.download_symbol_hours", return_value=base_df) as mock_fetch:
        fetchers = build_provider_fetchers(
            "EURUSD", "H1", dukascopy_years=2.0,
            ctrader_client=None, ctrader_years=1.0,
            td_api_key="", td_symbol_map={},
        )
        out = fetchers["dukascopy"]()
    mock_fetch.assert_called_once()
    call_args = mock_fetch.call_args[0]
    assert call_args[0] == "EURUSD"  # Dukascopy instrument for EURUSD is EURUSD itself
    assert call_args[1] == "EURUSD"  # internal symbol
    assert len(out) == 2  # 8 M15 bars coarsened to H1 -> 2 bars


def test_ctrader_closure_calls_download_symbol_deep_with_correct_args():
    fake_client = Mock()
    with patch("scripts.download_ctrader_fx_history.download_symbol_deep", return_value=_ohlcv()) as mock_fetch:
        fetchers = build_provider_fetchers(
            "EURUSD", "H1", dukascopy_years=1.0,
            ctrader_client=fake_client, ctrader_years=3.0,
            td_api_key="", td_symbol_map={},
        )
        fetchers["ctrader"]()
    mock_fetch.assert_called_once_with(fake_client, "EURUSD", years=3.0, timeframe="H1")


def test_twelve_data_closure_calls_fetch_td_history_with_correct_args():
    with patch("scripts.download_twelve_data_history.fetch_td_history", return_value=_ohlcv()) as mock_fetch:
        fetchers = build_provider_fetchers(
            "EURUSD", "M15", dukascopy_years=1.0,
            ctrader_client=None, ctrader_years=1.0,
            td_api_key="secret-key", td_symbol_map={"EURUSD": "EUR/USD"},
        )
        fetchers["twelve_data"]()
    mock_fetch.assert_called_once_with("EUR/USD", "M15", "secret-key")


# ---------------------------------------------------------------------------
# verify_and_write: real correctness checks, real file written
# ---------------------------------------------------------------------------


def test_verify_and_write_reports_valid_ohlc_and_writes_a_readable_file(tmp_path):
    df = _ohlcv(n=10, freq="15min")
    out_path = tmp_path / "EURUSD_M15_multiprovider.csv"
    result = verify_and_write("EURUSD", "M15", df, "fx_major", out_path)

    assert result["ohlc_valid"] is True
    assert result["ohlc_error"] is None
    assert result["bars"] == 10
    assert result["completeness_score"] is not None
    assert out_path.exists()

    reloaded = pd.read_csv(out_path)
    assert list(reloaded.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert len(reloaded) == 10


def test_verify_and_write_reports_ohlc_violation_without_crashing(tmp_path):
    df = _ohlcv(n=5)
    df.loc[df.index[0], "high"] = df.loc[df.index[0], "low"] - 0.01  # break high >= low
    out_path = tmp_path / "EURUSD_M15_multiprovider.csv"
    result = verify_and_write("EURUSD", "M15", df, "fx_major", out_path)

    assert result["ohlc_valid"] is False
    assert result["ohlc_error"]
    assert out_path.exists()  # still written — the point is honest reporting, not withholding data


def test_verify_and_write_uses_the_right_asset_class_for_completeness():
    # A crypto asset class must not penalize weekend gaps; an fx_major
    # asset class must classify a real Saturday gap as expected_closure,
    # not real_gap — this is completeness_score's own job, exercised here
    # through the real call this function makes (no reimplementation).
    idx = pd.to_datetime(["2024-01-05 22:00", "2024-01-07 21:00"], utc=True)  # Fri 22:00 -> Sun 21:00, still closed
    df = pd.DataFrame({"open": [1.1, 1.1], "high": [1.1, 1.1], "low": [1.1, 1.1],
                       "close": [1.1, 1.1], "volume": [1.0, 1.0]}, index=idx)
    result_fx = verify_and_write("EURUSD", "H1", df.copy(), "fx_major", Path("/tmp/_unused_fx.csv"))
    assert result_fx["completeness_detail"]["real_gap"] == 0  # entirely weekend closure
    Path("/tmp/_unused_fx.csv").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# coverage_summary: honest, plain-dict rollup
# ---------------------------------------------------------------------------


def test_coverage_summary_reports_success_and_total_failure_honestly():
    success = SymbolTimeframeResult(
        "EURUSD", "M15", "dukascopy", [ProviderAttempt("dukascopy", True, None, bars=100)],
        verify={"bars": 100, "years": 1.0, "completeness_score": 99.5, "ohlc_valid": True},
    )
    failure = SymbolTimeframeResult(
        "USOIL", "M15", None,
        [ProviderAttempt("dukascopy", False, "no mapping"), ProviderAttempt("ctrader", False, "no session")],
    )
    rows = coverage_summary([success, failure])
    assert rows[0]["provider_used"] == "dukascopy"
    assert rows[0]["bars"] == 100
    assert rows[0]["completeness_score"] == 99.5
    assert rows[1]["provider_used"] is None
    assert rows[1]["bars"] == 0  # never fabricated — no verify block exists for a total failure
    assert len(rows[1]["attempts"]) == 2
    assert rows[1]["attempts"][0]["error"] == "no mapping"


def test_coverage_summary_never_includes_a_dataframe():
    r = SymbolTimeframeResult("EURUSD", "M15", "dukascopy", [ProviderAttempt("dukascopy", True, None, bars=1)],
                              df=_ohlcv(n=1))
    rows = coverage_summary([r])
    assert "df" not in rows[0]


# ---------------------------------------------------------------------------
# small local helpers
# ---------------------------------------------------------------------------


def test_asset_class_for_symbol_looks_up_config_and_falls_back():
    cfg = {"data": {"twelve_data_symbols": [
        {"internal": "BTCUSD", "asset_class": "crypto"},
        {"internal": "EURUSD", "asset_class": "fx_major"},
    ]}}
    assert _asset_class_for_symbol("BTCUSD", cfg) == "crypto"
    assert _asset_class_for_symbol("EURUSD", cfg) == "fx_major"
    assert _asset_class_for_symbol("UNKNOWN", cfg) == "fx_major"  # honest, documented default


def test_hour_range_produces_hourly_steps_covering_the_requested_span():
    hours = m._hour_range(years=1 / 365.25)  # ~1 day
    assert len(hours) == 24
    assert (hours[1] - hours[0]).total_seconds() == 3600
    assert hours == sorted(hours)
