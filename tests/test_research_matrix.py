"""tests/test_research_matrix.py -- pure-function tests for backtest/research_matrix.py."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest import research_matrix as rm

_BUNDLE_A = {
    "name": "SMC only", "timeframes": ["H1"], "engines": ["smc"],
    "indicators": [], "context_filters": [],
}
_BUNDLE_B = {
    "name": "NNFX London trend", "timeframes": ["H4"], "engines": ["nnfx"],
    "indicators": [], "context_filters": [{"name": "session", "mode": "entry_filter", "allowed": ["London"]}],
}


def test_risk_preset_to_grid_returns_single_point_per_param():
    grid = rm.risk_preset_to_grid("balanced")
    for values in grid.values():
        assert len(values) == 1


def test_risk_preset_to_grid_rejects_unknown_preset():
    with pytest.raises(rm.ResearchMatrixError):
        rm.risk_preset_to_grid("yolo")


# --- fingerprinting -----------------------------------------------------


def test_fingerprint_is_deterministic():
    a = rm.compute_cell_fingerprint(
        symbol="EURUSD", bundle=_BUNDLE_A, risk_preset="balanced",
        confluence_overrides=None, engine_variants=None, data_provider=None, research_code_commit="abc123",
    )
    b = rm.compute_cell_fingerprint(
        symbol="EURUSD", bundle=dict(_BUNDLE_A), risk_preset="balanced",
        confluence_overrides=None, engine_variants=None, data_provider=None, research_code_commit="abc123",
    )
    assert a == b


def test_fingerprint_ignores_dict_key_order():
    reordered = {"context_filters": [], "indicators": [], "engines": ["smc"], "timeframes": ["H1"], "name": "SMC only"}
    a = rm.compute_cell_fingerprint(symbol="EURUSD", bundle=_BUNDLE_A, risk_preset="balanced", confluence_overrides=None, engine_variants=None, data_provider=None, research_code_commit=None)
    b = rm.compute_cell_fingerprint(symbol="EURUSD", bundle=reordered, risk_preset="balanced", confluence_overrides=None, engine_variants=None, data_provider=None, research_code_commit=None)
    assert a == b


@pytest.mark.parametrize("changed_kwargs", [
    {"symbol": "GBPUSD"},
    {"risk_preset": "aggressive"},
    {"confluence_overrides": {"min_engines_agreeing": 1}},
    {"engine_variants": {"price_action": "v2"}},
    {"data_provider": "dukascopy"},
    {"research_code_commit": "def456"},
])
def test_any_dimension_change_produces_a_different_fingerprint(changed_kwargs):
    base = dict(symbol="EURUSD", bundle=_BUNDLE_A, risk_preset="balanced", confluence_overrides=None, engine_variants=None, data_provider=None, research_code_commit="abc123")
    a = rm.compute_cell_fingerprint(**base)
    changed = dict(base, **changed_kwargs)
    b = rm.compute_cell_fingerprint(**changed)
    assert a != b


def test_bundle_change_produces_a_different_fingerprint():
    a = rm.compute_cell_fingerprint(symbol="EURUSD", bundle=_BUNDLE_A, risk_preset="balanced", confluence_overrides=None, engine_variants=None, data_provider=None, research_code_commit=None)
    b = rm.compute_cell_fingerprint(symbol="EURUSD", bundle=_BUNDLE_B, risk_preset="balanced", confluence_overrides=None, engine_variants=None, data_provider=None, research_code_commit=None)
    assert a != b


def test_cell_id_format():
    spec = rm.MatrixCellSpec(symbol="EURUSD", bundle=_BUNDLE_A, risk_preset="balanced")
    assert spec.cell_id == f"MATRIX-CELL-{spec.fingerprint}"
    assert len(spec.fingerprint) == 16


# --- matrix generation ----------------------------------------------------


def test_generate_matrix_cells_cartesian_product():
    cells = rm.generate_matrix_cells(symbols=["EURUSD", "GBPUSD"], bundles=[_BUNDLE_A, _BUNDLE_B], risk_presets=["balanced", "aggressive"])
    assert len(cells) == 2 * 2 * 2
    assert len({c.cell_id for c in cells}) == len(cells)  # every combination fingerprints uniquely


def test_generate_matrix_cells_rejects_empty_symbols():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_matrix_cells(symbols=[], bundles=[_BUNDLE_A])


def test_generate_matrix_cells_rejects_empty_bundles():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_matrix_cells(symbols=["EURUSD"], bundles=[])


def test_generate_matrix_cells_rejects_bundle_without_name():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_matrix_cells(symbols=["EURUSD"], bundles=[{"timeframes": ["H1"]}])


def test_generate_matrix_cells_rejects_unknown_risk_preset():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_matrix_cells(symbols=["EURUSD"], bundles=[_BUNDLE_A], risk_presets=["yolo"])


def test_generate_matrix_cells_defaults_to_all_three_presets():
    cells = rm.generate_matrix_cells(symbols=["EURUSD"], bundles=[_BUNDLE_A])
    assert {c.risk_preset for c in cells} == set(rm.RISK_PRESET_NAMES)


# --- data quality gate ----------------------------------------------------


def _mtf(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "open": [1.1] * n, "high": [1.2] * n, "low": [1.0] * n, "close": [1.15] * n, "volume": [100] * n,
    }, index=idx)


def test_check_data_quality_ok_on_a_real_sufficient_dataset(tmp_path, monkeypatch):
    import backtest.runner as runner_mod

    df = _mtf()
    csv_path = tmp_path / "EURUSD_H1_test.csv"
    df.to_csv(csv_path)
    monkeypatch.setattr(runner_mod, "find_symbol_csv", lambda *a, **k: csv_path)
    monkeypatch.setattr(runner_mod, "load_symbol_data", lambda *a, **k: df)
    result = rm.check_data_quality("EURUSD", "H1", tmp_path, None, None, min_rows=100, min_completeness_pct=0.0)
    assert result.ok is True
    assert result.dataset_fingerprint is not None


def test_check_data_quality_insufficient_rows(tmp_path, monkeypatch):
    import backtest.runner as runner_mod

    df = _mtf(n=5)
    monkeypatch.setattr(runner_mod, "find_symbol_csv", lambda *a, **k: tmp_path / "EURUSD_H1_test.csv")
    monkeypatch.setattr(runner_mod, "load_symbol_data", lambda *a, **k: df)
    result = rm.check_data_quality("EURUSD", "H1", tmp_path, None, None, min_rows=100)
    assert result.ok is False
    assert "row" in result.reason


def test_check_data_quality_missing_file_never_raises(tmp_path):
    result = rm.check_data_quality("NOPE", "H1", tmp_path, None, None)
    assert result.ok is False
    assert result.reason is not None


def test_check_data_quality_invalid_ohlc_never_raises(tmp_path, monkeypatch):
    import backtest.runner as runner_mod

    bad = _mtf().copy()
    bad["high"] = 0.5  # high < low/close -> structurally invalid
    monkeypatch.setattr(runner_mod, "find_symbol_csv", lambda *a, **k: tmp_path / "EURUSD_H1_test.csv")
    monkeypatch.setattr(runner_mod, "load_symbol_data", lambda *a, **k: bad)
    result = rm.check_data_quality("EURUSD", "H1", tmp_path, None, None, min_rows=1)
    assert result.ok is False


# --- Stage A screening ------------------------------------------------------


def test_screen_stage_a_rejects_too_few_trades():
    r = rm.screen_stage_a({"profit_factor": 2.0}, trades=5)
    assert r.passed is False
    assert "trade" in r.reason


def test_screen_stage_a_rejects_missing_metrics():
    r = rm.screen_stage_a(None, trades=50)
    assert r.passed is False


def test_screen_stage_a_rejects_below_min_pf():
    r = rm.screen_stage_a({"profit_factor": 0.8}, trades=50)
    assert r.passed is False
    assert "profit_factor" in r.reason


def test_screen_stage_a_passes_a_healthy_trial():
    r = rm.screen_stage_a({"profit_factor": 1.5}, trades=50)
    assert r.passed is True
    assert r.reason is None


def test_screen_stage_a_handles_infinity_sentinel():
    r = rm.screen_stage_a({"profit_factor": "Infinity"}, trades=50)
    assert r.passed is True


def test_screen_stage_a_rejects_nan_sentinel():
    r = rm.screen_stage_a({"profit_factor": "NaN"}, trades=50)
    assert r.passed is False
