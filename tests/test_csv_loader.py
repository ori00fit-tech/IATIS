"""
tests/test_csv_loader.py
----------------------------
Tests for core.data_loader.load_from_csv() — the first real (non-synthetic)
data source. These tests use small, hand-authored CSV fixtures
(tests/fixtures/*.csv), not generated data, because the loader's job is
to correctly parse real-world export formats, including ones that don't
match cleanly (e.g. split Date/Time columns) — exactly the kind of edge
case synthetic data generation would never surface.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.data_loader import load_from_csv
from core.data_validator import validate_ohlcv

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_loads_generic_csv_format():
    df = load_from_csv(FIXTURES / "sample_generic.csv")
    assert len(df) == 5
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "datetime"
    assert validate_ohlcv(df) is True


def test_generic_csv_values_are_correct():
    df = load_from_csv(FIXTURES / "sample_generic.csv")
    first = df.iloc[0]
    assert first["open"] == pytest.approx(1.0850)
    assert first["high"] == pytest.approx(1.0862)
    assert first["low"] == pytest.approx(1.0845)
    assert first["close"] == pytest.approx(1.0858)
    assert first["volume"] == 1200


def test_loads_mt5_style_split_date_time_without_losing_rows():
    # Regression test: an earlier version of load_from_csv used only the
    # "Date" column when a separate "Time" column existed, collapsing
    # every bar on the same calendar day into one duplicate timestamp
    # and silently dropping data. This must not happen.
    df = load_from_csv(FIXTURES / "sample_mt5_style.csv")
    assert len(df) == 2
    assert df.index[0] != df.index[1]


def test_mt5_style_timestamps_combine_date_and_time():
    df = load_from_csv(FIXTURES / "sample_mt5_style.csv")
    assert str(df.index[0]) == "2026-01-01 00:00:00+00:00"
    assert str(df.index[1]) == "2026-01-01 01:00:00+00:00"


def test_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        load_from_csv(FIXTURES / "does_not_exist.csv")


def test_raises_on_unrecognized_columns(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    pd.DataFrame({"A": [1, 2], "B": [3, 4]}).to_csv(bad_csv, index=False)

    with pytest.raises(ValueError, match="Could not identify required columns"):
        load_from_csv(bad_csv)


def test_raises_on_empty_file(tmp_path):
    empty_csv = tmp_path / "empty.csv"
    pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"]).to_csv(empty_csv, index=False)

    with pytest.raises(ValueError):
        load_from_csv(empty_csv)


def test_explicit_column_map_overrides_autodetect(tmp_path):
    custom_csv = tmp_path / "custom.csv"
    pd.DataFrame(
        {
            "ts": ["2026-01-01 00:00:00"],
            "o": [1.10],
            "h": [1.11],
            "l": [1.09],
            "c": [1.105],
            "vol": [500],
        }
    ).to_csv(custom_csv, index=False)

    df = load_from_csv(
        custom_csv,
        column_map={"datetime": "ts", "open": "o", "high": "h", "low": "l", "close": "c", "volume": "vol"},
    )
    assert len(df) == 1
    assert df.iloc[0]["close"] == pytest.approx(1.105)


def test_drops_unparseable_rows_with_warning(tmp_path, caplog):
    messy_csv = tmp_path / "messy.csv"
    pd.DataFrame(
        {
            "Date": ["2026-01-01 00:00:00", "not-a-date", "2026-01-01 02:00:00"],
            "Open": [1.08, 1.09, "not-a-number"],
            "High": [1.09, 1.10, 1.10],
            "Low": [1.07, 1.08, 1.08],
            "Close": [1.085, 1.095, 1.095],
        }
    ).to_csv(messy_csv, index=False)

    df = load_from_csv(messy_csv)
    # only the first row is fully valid; row 2 has a bad date, row 3 has a bad open
    assert len(df) == 1


def test_loaded_csv_data_feeds_full_pipeline_validation():
    """End-to-end sanity check: real CSV data must satisfy the same
    OHLCV contract the synthetic loader produces, so nothing downstream
    (timeframe sync, regime detector, engines) needs special-casing.
    """
    from core.timeframe_sync import build_multi_timeframe_view

    df = load_from_csv(FIXTURES / "sample_generic.csv")
    assert validate_ohlcv(df) is True
    # only H1-equivalent data available in the fixture; just confirm the
    # base view builds without error
    views = build_multi_timeframe_view(df, ["H1"])
    assert "H1" in views
    assert len(views["H1"]) == len(df)


def test_loads_headerless_tab_separated_format(tmp_path):
    # Common real-world broker export format: no header row at all,
    # tab-separated, column order datetime/O/H/L/C/volume. Discovered
    # when a real user-supplied EURUSD M1 dataset (100k rows) used
    # exactly this format.
    headerless_csv = tmp_path / "headerless.tsv"
    headerless_csv.write_text(
        "2026-03-16 02:58\t1.14492\t1.14493\t1.14486\t1.14489\t31\n"
        "2026-03-16 02:59\t1.14491\t1.14492\t1.14479\t1.14479\t30\n"
        "2026-03-16 03:00\t1.14480\t1.14495\t1.14478\t1.14484\t76\n"
    )

    df = load_from_csv(headerless_csv, has_header=False, sep="\t")
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.iloc[0]["close"] == pytest.approx(1.14489)
    assert validate_ohlcv(df) is True


def test_headerless_format_respects_custom_column_order(tmp_path):
    custom_csv = tmp_path / "custom_order.tsv"
    # volume first, then OHLC, then datetime last — unusual but should
    # still work given an explicit no_header_columns order
    custom_csv.write_text(
        "31\t1.14492\t1.14493\t1.14486\t1.14489\t2026-03-16 02:58\n"
    )
    df = load_from_csv(
        custom_csv,
        has_header=False,
        sep="\t",
        no_header_columns=["volume", "open", "high", "low", "close", "datetime"],
    )
    assert len(df) == 1
    assert df.iloc[0]["open"] == pytest.approx(1.14492)
    assert df.iloc[0]["volume"] == 31


def test_load_data_dispatcher_passes_headerless_csv_options(tmp_path):
    """The config.yaml -> load_data() dispatch path must forward
    csv_has_header / csv_separator / csv_columns to load_from_csv(),
    not just the direct Python API. This is the path config.yaml-driven
    runs (e.g. main.py) actually use.
    """
    from core.data_loader import load_data

    headerless_csv = tmp_path / "dispatch_test.tsv"
    headerless_csv.write_text("2026-03-16 02:58\t1.10\t1.11\t1.09\t1.105\t50\n")

    config = {
        "data": {
            "source": "csv",
            "csv_path": str(headerless_csv),
            "csv_has_header": False,
            "csv_separator": "\t",
            "timeframes": ["M15", "H1"],
        }
    }
    df = load_data(config)
    assert len(df) == 1
    assert df.iloc[0]["close"] == pytest.approx(1.105)
