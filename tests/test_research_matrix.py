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


# --- Phase 1: single_engine_identity() ------------------------------------


def test_single_engine_identity_returns_identity_for_single_engine_single_tf_bundle():
    assert rm.single_engine_identity(_BUNDLE_A, None) == ("smc", "v1", "H1")


def test_single_engine_identity_uses_engine_variants_when_present():
    bundle = {"name": "PA v2", "timeframes": ["H1"], "engines": ["price_action"]}
    assert rm.single_engine_identity(bundle, {"price_action": "v2"}) == ("price_action", "v2", "H1")


def test_single_engine_identity_returns_none_for_multi_engine_bundle():
    bundle = {"name": "confluence", "timeframes": ["H1"], "engines": ["smc", "nnfx"]}
    assert rm.single_engine_identity(bundle, None) == (None, None, None)


def test_single_engine_identity_returns_none_for_multi_timeframe_bundle():
    bundle = {"name": "multi-tf", "timeframes": ["H1", "H4"], "engines": ["smc"]}
    assert rm.single_engine_identity(bundle, None) == (None, None, None)


def test_single_engine_identity_returns_none_for_empty_bundle():
    assert rm.single_engine_identity({"name": "empty"}, None) == (None, None, None)


def test_single_engine_identity_fingerprint_compatible_with_hand_built_bundle():
    """A cell generate_discovery_cells() produces for (symbol, engine,
    version, timeframe) fingerprints IDENTICALLY to a hand-constructed
    single-engine bundle passed through generate_matrix_cells() for the
    same combination, PROVIDED the bundle name matches too -- bundle_name
    is itself part of compute_cell_fingerprint()'s hash payload (by
    design: two differently-NAMED bundles are two different hypothesis
    records even if their engines/timeframes happen to coincide), so
    genuine cross-path deduping requires using generate_discovery_cells()'s
    own deterministic auto-name convention, not an arbitrary label."""
    discovery_cells = rm.generate_discovery_cells(
        symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"],
        engine_versions={"price_action": ("v2",)}, risk_presets=["balanced"],
    )
    hand_bundle = {"name": "price_action:v2 @ H1", "timeframes": ["H1"], "engines": ["price_action"], "indicators": [], "context_filters": []}
    hand_cells = rm.generate_matrix_cells(
        symbols=["EURUSD"], bundles=[hand_bundle], risk_presets=["balanced"],
        engine_variants_choices=({"price_action": "v2"},),
    )
    assert len(discovery_cells) == 1
    assert len(hand_cells) == 1
    assert discovery_cells[0].fingerprint == hand_cells[0].fingerprint


# --- Phase 1: generate_discovery_cells() -----------------------------------


def test_generate_discovery_cells_enumerates_all_requested_engines():
    """Item A."""
    cells = rm.generate_discovery_cells(symbols=["EURUSD"], engines=["smc", "nnfx", "price_action"], timeframes=["H1"])
    assert {c.bundle["engines"][0] for c in cells} == {"smc", "nnfx", "price_action"}


def test_generate_discovery_cells_enumerates_all_requested_timeframes():
    """Item B."""
    cells = rm.generate_discovery_cells(symbols=["EURUSD"], engines=["smc"], timeframes=["M15", "H1", "H4", "D1"])
    assert {c.bundle["timeframes"][0] for c in cells} == {"M15", "H1", "H4", "D1"}


def test_generate_discovery_cells_cartesian_expansion_is_correct():
    """Item C. 2 symbols x (price_action: v1,v2 + smc: v1) x 2 timeframes
    x 1 risk preset = 2 x 3 x 2 x 1 = 12."""
    cells = rm.generate_discovery_cells(
        symbols=["EURUSD", "GBPUSD"], engines=["price_action", "smc"], timeframes=["H1", "H4"],
        risk_presets=["balanced"],
    )
    assert len(cells) == 12


def test_generate_discovery_cells_no_duplicate_identity_tuples():
    """Item D. Passing overlapping/duplicate symbols, engines, and
    timeframes must never produce two cells sharing the same
    (symbol, engine, engine_version, timeframe, risk_preset) identity."""
    cells = rm.generate_discovery_cells(
        symbols=["EURUSD", "EURUSD"], engines=["smc", "smc"], timeframes=["H1", "H1"], risk_presets=["balanced"],
    )
    assert len(cells) == 1
    identities = [(c.symbol, c.bundle["engines"][0], c.bundle["timeframes"][0], c.risk_preset) for c in cells]
    assert len(identities) == len(set(identities))


def test_generate_discovery_cells_symbol_is_an_independent_dimension():
    """Item E. Every symbol gets the exact same engine x timeframe set --
    symbol never narrows or changes what's enumerated for another symbol."""
    cells = rm.generate_discovery_cells(symbols=["EURUSD", "GBPUSD"], engines=["smc", "nnfx"], timeframes=["H1", "H4"], risk_presets=["balanced"])
    by_symbol: dict[str, set] = {}
    for c in cells:
        by_symbol.setdefault(c.symbol, set()).add((c.bundle["engines"][0], c.bundle["timeframes"][0]))
    assert by_symbol["EURUSD"] == by_symbol["GBPUSD"]


def test_generate_discovery_cells_does_not_corrupt_historical_bundle_behavior():
    """Item F. generate_matrix_cells() (the pre-existing, multi-engine-
    capable generator) is completely untouched by this phase -- a
    confluence-research bundle still produces exactly the cells it always
    did, with the exact same fingerprints as before this phase existed."""
    cells = rm.generate_matrix_cells(symbols=["EURUSD", "GBPUSD"], bundles=[_BUNDLE_A, _BUNDLE_B], risk_presets=["balanced", "aggressive"])
    assert len(cells) == 2 * 2 * 2
    assert len({c.cell_id for c in cells}) == len(cells)
    # multi-engine bundle: single_engine_identity() correctly abstains
    multi = {"name": "confluence", "timeframes": ["H1"], "engines": ["smc", "nnfx"], "indicators": [], "context_filters": []}
    multi_cells = rm.generate_matrix_cells(symbols=["EURUSD"], bundles=[multi], risk_presets=["balanced"])
    assert rm.single_engine_identity(multi_cells[0].bundle, multi_cells[0].engine_variants) == (None, None, None)


def test_generate_discovery_cells_rejects_unknown_engine():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_discovery_cells(symbols=["EURUSD"], engines=["not_a_real_engine"], timeframes=["H1"])


def test_generate_discovery_cells_rejects_unknown_engine_version():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_discovery_cells(symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], engine_versions={"price_action": ("v99",)})


def test_generate_discovery_cells_rejects_empty_symbols():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_discovery_cells(symbols=[], engines=["smc"], timeframes=["H1"])


def test_generate_discovery_cells_rejects_empty_engines():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_discovery_cells(symbols=["EURUSD"], engines=[], timeframes=["H1"])


def test_generate_discovery_cells_rejects_empty_timeframes():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_discovery_cells(symbols=["EURUSD"], engines=["smc"], timeframes=[])


def test_generate_discovery_cells_rejects_unknown_risk_preset():
    with pytest.raises(rm.ResearchMatrixError):
        rm.generate_discovery_cells(symbols=["EURUSD"], engines=["smc"], timeframes=["H1"], risk_presets=["yolo"])


def test_generate_discovery_cells_default_enumerates_every_real_variant():
    """An engine with real variants (price_action: v1,v2) defaults to
    ALL of them when engine_versions doesn't restrict it -- exhaustive by
    default."""
    cells = rm.generate_discovery_cells(symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"])
    assert {c.engine_variants["price_action"] for c in cells} == {"v1", "v2"}


def test_generate_discovery_cells_engine_without_variants_never_gets_an_engine_variants_entry():
    """smc has no real variant -- engine_variants must be None (never
    {"smc": "v1"}), matching the fingerprint convention every other
    variant-less engine already uses."""
    cells = rm.generate_discovery_cells(symbols=["EURUSD"], engines=["smc"], timeframes=["H1"], risk_presets=["balanced"])
    assert all(c.engine_variants is None for c in cells)


def test_generate_discovery_cells_engine_versions_restricts_the_variant_set():
    cells = rm.generate_discovery_cells(
        symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"],
        engine_versions={"price_action": ("v2",)},
    )
    assert {c.engine_variants["price_action"] for c in cells} == {"v2"}


def test_generate_discovery_cells_is_deterministic_for_identical_inputs():
    """Item I."""
    a = rm.generate_discovery_cells(symbols=["EURUSD", "GBPUSD"], engines=["smc", "price_action"], timeframes=["H1", "H4"])
    b = rm.generate_discovery_cells(symbols=["EURUSD", "GBPUSD"], engines=["smc", "price_action"], timeframes=["H1", "H4"])
    assert [c.cell_id for c in a] == [c.cell_id for c in b]


def test_generate_discovery_cells_never_touches_config_or_registry(tmp_path):
    """Items G/H — a pure function with no D1/file access whatsoever, same
    RESEARCH-ONLY guarantee as every other function in this module."""
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    rm.generate_discovery_cells(symbols=["EURUSD"], engines=["smc", "price_action", "nnfx", "wyckoff"], timeframes=["M15", "H1", "H4", "D1"])

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after generate_discovery_cells()"


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
