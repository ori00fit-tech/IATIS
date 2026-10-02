"""tests/test_data_trust_manifest.py -- tests for data_trust/manifest.py
(Hypothesis Discovery Engine, Phase 11 — Data Trust & Provenance Layer)."""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from data_trust import manifest as dtm


def _df(n: int = 5, seed_offset: float = 0.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "open": [1.10 + seed_offset + i * 0.001 for i in range(n)],
        "high": [1.11 + seed_offset + i * 0.001 for i in range(n)],
        "low": [1.09 + seed_offset + i * 0.001 for i in range(n)],
        "close": [1.105 + seed_offset + i * 0.001 for i in range(n)],
        "volume": [100.0 + i for i in range(n)],
    }, index=idx)


def _quality_result(**overrides) -> dict:
    base = {"validation_status": "VALID", "reasons": [], "timezone": "UTC", "missing_bars": None}
    base.update(overrides)
    return base


# --- compute_dataset_hash ----------------------------------------------


def test_compute_dataset_hash_is_deterministic():
    df = _df()
    a = dtm.compute_dataset_hash(df, "EURUSD", "H1")
    b = dtm.compute_dataset_hash(df, "EURUSD", "H1")
    assert a == b
    assert isinstance(a, str) and len(a) == 16


def test_compute_dataset_hash_is_independent_of_row_order():
    df = _df()
    shuffled = df.sample(frac=1.0, random_state=42)  # same rows, different order
    assert dtm.compute_dataset_hash(df, "EURUSD", "H1") == dtm.compute_dataset_hash(shuffled, "EURUSD", "H1")


def test_compute_dataset_hash_is_independent_of_column_order():
    df = _df()
    reordered = df[["volume", "close", "low", "high", "open"]]
    assert dtm.compute_dataset_hash(df, "EURUSD", "H1") == dtm.compute_dataset_hash(reordered, "EURUSD", "H1")


def test_compute_dataset_hash_changes_when_a_value_changes():
    df = _df()
    mutated = df.copy()
    mutated.iloc[0, mutated.columns.get_loc("close")] += 0.0001
    assert dtm.compute_dataset_hash(df, "EURUSD", "H1") != dtm.compute_dataset_hash(mutated, "EURUSD", "H1")


def test_compute_dataset_hash_differs_by_symbol_and_timeframe():
    df = _df()
    a = dtm.compute_dataset_hash(df, "EURUSD", "H1")
    b = dtm.compute_dataset_hash(df, "GBPUSD", "H1")
    c = dtm.compute_dataset_hash(df, "EURUSD", "H4")
    assert len({a, b, c}) == 3


def test_compute_dataset_hash_defaults_missing_volume_to_zero():
    df = _df().drop(columns=["volume"])
    with_zero_volume = _df()
    with_zero_volume["volume"] = 0.0
    assert dtm.compute_dataset_hash(df, "EURUSD", "H1") == dtm.compute_dataset_hash(with_zero_volume, "EURUSD", "H1")


def test_compute_dataset_hash_rejects_missing_ohlc_columns():
    df = _df().drop(columns=["close"])
    with pytest.raises(dtm.DataTrustError, match="missing required column"):
        dtm.compute_dataset_hash(df, "EURUSD", "H1")


# --- build_manifest ------------------------------------------------------


def test_build_manifest_rejects_invalid_provenance():
    with pytest.raises(dtm.DataTrustError, match="provenance"):
        dtm.build_manifest(_df(), symbol="EURUSD", timeframe="H1", provider="twelve_data",
                            provenance="SOMETHING_ELSE", quality_result=_quality_result())


def test_build_manifest_populates_fields_from_the_dataframe_and_quality_result():
    df = _df(n=10)
    manifest = dtm.build_manifest(
        df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
        provenance=dtm.LIVE_PROVIDER_DATA,
        quality_result=_quality_result(validation_status="VALID_WITH_WARNINGS", timezone="UTC",
                                        missing_bars={"gaps_total": 2, "gaps_in_critical_window": 0,
                                                      "classification": "VALID_WITH_WARNINGS"}),
    )
    assert manifest["symbol"] == "EURUSD"
    assert manifest["timeframe"] == "H1"
    assert manifest["provider"] == "twelve_data"
    assert manifest["provenance"] == dtm.LIVE_PROVIDER_DATA
    assert manifest["row_count"] == 10
    assert manifest["timezone"] == "UTC"
    assert manifest["validation_status"] == "VALID_WITH_WARNINGS"
    assert manifest["missing_bar_count"] == 2
    assert manifest["schema_version"] == dtm.SCHEMA_VERSION
    assert manifest["dataset_hash"] == dtm.compute_dataset_hash(df, "EURUSD", "H1")
    assert manifest["generated_at"] is not None


def test_build_manifest_has_no_separate_dataset_id_field():
    """Operator's own accepted simplification: dataset_hash IS the
    manifest's identity -- no redundant random dataset_id alongside it."""
    manifest = dtm.build_manifest(_df(), symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                   provenance=dtm.LIVE_PROVIDER_DATA, quality_result=_quality_result())
    assert "dataset_id" not in manifest


def test_build_manifest_dataset_hash_is_none_when_columns_are_missing_never_a_crash():
    df = _df().drop(columns=["close"])
    manifest = dtm.build_manifest(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                   provenance=dtm.LIVE_PROVIDER_DATA,
                                   quality_result=_quality_result(validation_status="INVALID",
                                                                   reasons=["OHLC validation failed: ..."]))
    assert manifest["dataset_hash"] is None
    assert manifest["validation_status"] == "INVALID"


def test_build_manifest_missing_bar_count_is_none_when_not_assessed():
    manifest = dtm.build_manifest(_df(), symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                   provenance=dtm.LIVE_PROVIDER_DATA, quality_result=_quality_result())
    assert manifest["missing_bar_count"] is None


# --- structural: no hidden re-evaluation, no side effects -------------------


def _source_without_module_docstring() -> str:
    source = inspect.getsource(dtm)
    return source.split('"""', 2)[-1]


def test_build_manifest_never_calls_assess_data_trust():
    body = _source_without_module_docstring()
    assert "assess_data_trust" not in body


def test_manifest_module_has_no_io_or_storage_imports():
    body = _source_without_module_docstring()
    forbidden = ("import requests", "d1_client", "from storage", "import storage", "open(", "with open")
    for pattern in forbidden:
        assert pattern not in body, f"data_trust.manifest unexpectedly references {pattern!r}"


def test_build_manifest_does_not_mutate_the_input_dataframe():
    df = _df()
    before = df.copy()
    dtm.build_manifest(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                        provenance=dtm.LIVE_PROVIDER_DATA, quality_result=_quality_result())
    pd.testing.assert_frame_equal(df, before)
