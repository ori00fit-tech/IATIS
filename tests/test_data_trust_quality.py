"""tests/test_data_trust_quality.py -- tests for data_trust/quality.py
(Hypothesis Discovery Engine, Phase 11 — Data Trust & Provenance Layer)."""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from data_trust import manifest as dtm
from data_trust import quality as dtq


def _df(n: int = 10, freq: str = "h", tz: str | None = "UTC") -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq=freq, tz=tz)
    return pd.DataFrame({
        "open": [1.10 + i * 0.001 for i in range(n)],
        "high": [1.11 + i * 0.001 for i in range(n)],
        "low": [1.09 + i * 0.001 for i in range(n)],
        "close": [1.105 + i * 0.001 for i in range(n)],
        "volume": [100.0] * n,
    }, index=idx)


def _df_with_gap(n: int = 10, drop_index: int = 5) -> pd.DataFrame:
    df = _df(n=n)
    return df.drop(df.index[drop_index])


# --- assess_timezone ---------------------------------------------------


def test_assess_timezone_tz_naive_is_invalid():
    df = _df(tz=None)
    result = dtq.assess_timezone(df)
    assert result["ok"] is False
    assert "tz-naive" in result["reason"]


def test_assess_timezone_non_utc_aware_is_invalid():
    df = _df(tz="America/New_York")
    result = dtq.assess_timezone(df)
    assert result["ok"] is False
    assert "not UTC" in result["reason"]


def test_assess_timezone_utc_is_valid():
    df = _df(tz="UTC")
    result = dtq.assess_timezone(df)
    assert result["ok"] is True
    assert result["timezone"] == "UTC"


# --- assess_missing_bars (the locked table) ---------------------------------


def test_assess_missing_bars_no_gaps_is_valid():
    df = _df(n=20)
    result = dtq.assess_missing_bars(df, "1h")
    assert result == {"gaps_total": 0, "gaps_in_critical_window": 0, "classification": dtq.VALID}


def test_assess_missing_bars_gap_outside_critical_window_is_warning():
    df = _df_with_gap(n=20, drop_index=1)  # gap near the start
    window_start = df.index[-3]
    window_end = df.index[-1]
    result = dtq.assess_missing_bars(df, "1h", critical_window=(window_start, window_end))
    assert result["gaps_total"] == 1
    assert result["gaps_in_critical_window"] == 0
    assert result["classification"] == dtq.VALID_WITH_WARNINGS


def test_assess_missing_bars_gap_inside_critical_window_is_invalid():
    df = _df(n=20)
    gap_ts = df.index[10]
    df = df.drop(gap_ts)
    window_start = df.index[5]
    window_end = df.index[-1]
    result = dtq.assess_missing_bars(df, "1h", critical_window=(window_start, window_end))
    assert result["gaps_total"] == 1
    assert result["gaps_in_critical_window"] == 1
    assert result["classification"] == dtq.INVALID


def test_assess_missing_bars_no_critical_window_any_gap_is_invalid():
    df = _df_with_gap(n=20, drop_index=1)
    result = dtq.assess_missing_bars(df, "1h", critical_window=None)
    assert result["classification"] == dtq.INVALID
    assert result["gaps_in_critical_window"] == result["gaps_total"]


def test_assess_missing_bars_no_critical_window_no_gaps_is_valid():
    df = _df(n=20)
    result = dtq.assess_missing_bars(df, "1h", critical_window=None)
    assert result["classification"] == dtq.VALID


def test_assess_missing_bars_reuses_find_gaps_never_reimplements_it():
    source = inspect.getsource(dtq)
    assert "find_gaps(" in source


# --- assess_data_trust (the orchestrator) -----------------------------------


def test_assess_data_trust_rejects_invalid_provenance():
    with pytest.raises(dtm.DataTrustError, match="provenance"):
        dtq.assess_data_trust(_df(), symbol="EURUSD", timeframe="H1", provider="twelve_data",
                               provenance="BOGUS")


def test_assess_data_trust_all_checks_pass_is_valid():
    result = dtq.assess_data_trust(
        _df(n=20), symbol="EURUSD", timeframe="H1", provider="twelve_data",
        provenance=dtm.LIVE_PROVIDER_DATA, expected_freq="1h",
    )
    assert result["validation_status"] == dtq.VALID
    assert result["reasons"] == []
    assert result["manifest"]["validation_status"] == dtq.VALID
    assert result["manifest"]["dataset_hash"] is not None


def test_assess_data_trust_ohlc_failure_overrides_everything_else():
    df = _df(n=10)
    df.iloc[0, df.columns.get_loc("high")] = df.iloc[0]["low"] - 1.0  # high < low
    result = dtq.assess_data_trust(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                    provenance=dtm.LIVE_PROVIDER_DATA)
    assert result["validation_status"] == dtq.INVALID
    assert any("OHLC validation failed" in r for r in result["reasons"])


def test_assess_data_trust_timezone_failure_is_invalid_even_if_data_otherwise_clean():
    df = _df(n=10, tz=None)
    result = dtq.assess_data_trust(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                    provenance=dtm.LIVE_PROVIDER_DATA)
    assert result["validation_status"] == dtq.INVALID
    assert any("timezone check failed" in r for r in result["reasons"])


def test_assess_data_trust_accumulates_multiple_reasons():
    df = _df(n=10, tz=None)  # tz failure
    df.iloc[0, df.columns.get_loc("high")] = df.iloc[0]["low"] - 1.0  # AND ohlc failure
    result = dtq.assess_data_trust(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                    provenance=dtm.LIVE_PROVIDER_DATA)
    assert len(result["reasons"]) == 2


def test_assess_data_trust_skips_missing_bar_check_when_expected_freq_omitted():
    df = _df_with_gap(n=20, drop_index=5)
    result = dtq.assess_data_trust(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                    provenance=dtm.LIVE_PROVIDER_DATA, expected_freq=None)
    # the gap exists but is never assessed -- status stays VALID
    assert result["validation_status"] == dtq.VALID
    assert result["manifest"]["missing_bar_count"] is None


def test_assess_data_trust_missing_bars_outside_window_downgrades_to_warning_not_invalid():
    df = _df_with_gap(n=20, drop_index=1)
    window = (df.index[-3], df.index[-1])
    result = dtq.assess_data_trust(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                    provenance=dtm.LIVE_PROVIDER_DATA, expected_freq="1h",
                                    critical_window=window)
    assert result["validation_status"] == dtq.VALID_WITH_WARNINGS


# --- structural: no hidden strategy knowledge, no reinvention, no wiring ---


def _source_without_module_docstring() -> str:
    source = inspect.getsource(dtq)
    return source.split('"""', 2)[-1]


def test_critical_window_and_provenance_are_never_derived_from_strategy_context():
    body = _source_without_module_docstring()
    forbidden = ("hypothesis_id", "config[\"engines\"]", "engine_version", "strategy_name", "ENGINE_HYPOTHESIS_MAP")
    for pattern in forbidden:
        assert pattern not in body, f"data_trust.quality unexpectedly references {pattern!r}"


def test_no_scheduler_main_or_trade_executor_import():
    body = _source_without_module_docstring()
    forbidden = ("import scheduler", "from scheduler", "import main", "from main", "trade_executor")
    for pattern in forbidden:
        assert pattern not in body, f"data_trust.quality unexpectedly references {pattern!r}"


def test_no_storage_or_database_import():
    body = _source_without_module_docstring()
    forbidden = ("from storage", "import storage", "d1_client")
    for pattern in forbidden:
        assert pattern not in body, f"data_trust.quality unexpectedly references {pattern!r}"


def test_nothing_outside_tests_imports_data_trust_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "data_trust" not in text, f"{path} unexpectedly references data_trust"
