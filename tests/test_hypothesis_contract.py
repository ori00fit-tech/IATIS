"""
tests/test_hypothesis_contract.py
------------------------------------
Slice 4 (Hypothesis Execution Contract, 2026-08-19) — mirrors
tests/test_research_manifest.py's own fixture conventions. Proves the
four properties the operator explicitly required: identical inputs
produce identical execution identity; changing the dataset fingerprint
changes it; changing the hypothesis definition changes it; a contract
missing a required field cannot pass validate_for_new_run() (fail
closed); and existing historical *_manifest.json records stay readable
without ever going through the new validator.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research import hypothesis_contract as hc


def _hyp_md(tmp_path: Path, hypothesis_id: str = "H999", content: str = "# H999\n\nStatement A.\n") -> Path:
    d = tmp_path / "hypotheses"
    d.mkdir(exist_ok=True)
    p = d / f"{hypothesis_id}_test_case.md"
    p.write_text(content)
    return p, d


def _price_csv(tmp_path: Path, symbol: str = "EURUSD", content: str = "datetime,open,high,low,close\n2024-01-01,1,2,0.5,1.5\n") -> Path:
    p = tmp_path / f"{symbol}_H1_2y.csv"
    p.write_text(content)
    return p


def _base_kwargs(tmp_path: Path, hyp_path: Path, dataset_path: Path) -> dict:
    return dict(
        hypothesis_id="H999",
        hypothesis_definition_path=hyp_path,
        symbol="EURUSD",
        timeframe="H1",
        dataset_paths=[dataset_path],
        train_period={"start": "2022-01-01", "end": "2023-06-30"},
        oos_period={"start": "2023-07-01", "end": "2024-01-01"},
        engine_config={"enabled": {"smc": True, "price_action": True}},
        risk_config={"min_rr": 2.0, "sl_atr_multiplier": 2.0},
        trial_family_size=1,
    )


# ── resolve_hypothesis_definition_path ───────────────────────────────────

def test_resolve_hypothesis_definition_path_finds_the_real_file(tmp_path):
    hyp_path, hyp_dir = _hyp_md(tmp_path)
    resolved = hc.resolve_hypothesis_definition_path("H999", hypotheses_dir=hyp_dir)
    assert resolved == hyp_path


def test_resolve_hypothesis_definition_path_refuses_to_invent_a_missing_one(tmp_path):
    hyp_dir = tmp_path / "hypotheses"
    hyp_dir.mkdir()
    with pytest.raises(hc.HypothesisContractError, match="No hypothesis definition file found"):
        hc.resolve_hypothesis_definition_path("H999", hypotheses_dir=hyp_dir)


def test_resolve_hypothesis_definition_path_refuses_an_ambiguous_match(tmp_path):
    hyp_dir = tmp_path / "hypotheses"
    hyp_dir.mkdir()
    (hyp_dir / "H999_case_a.md").write_text("a")
    (hyp_dir / "H999_case_b.md").write_text("b")
    with pytest.raises(hc.HypothesisContractError, match="Ambiguous"):
        hc.resolve_hypothesis_definition_path("H999", hypotheses_dir=hyp_dir)


# ── build_hypothesis_contract / compute_execution_identity ───────────────

def test_identical_inputs_produce_identical_execution_identity(tmp_path):
    """Operator's mandatory property #1."""
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path)

    c1 = hc.build_hypothesis_contract(**kwargs)
    c2 = hc.build_hypothesis_contract(**kwargs)

    # generated_at/git differ in principle across two calls (git is real,
    # generated_at is wall-clock) — the IDENTITY must ignore both.
    assert hc.compute_execution_identity(c1) == hc.compute_execution_identity(c2)


def test_changing_dataset_fingerprint_changes_execution_identity(tmp_path):
    """Operator's mandatory property #2."""
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path)
    c_before = hc.build_hypothesis_contract(**kwargs)

    ds_path.write_text(ds_path.read_text() + "2024-01-02,1.5,2.5,1.0,2.0\n")
    c_after = hc.build_hypothesis_contract(**kwargs)

    assert c_before["dataset_fingerprints"][0]["sha256"] != c_after["dataset_fingerprints"][0]["sha256"]
    assert hc.compute_execution_identity(c_before) != hc.compute_execution_identity(c_after)


def test_changing_hypothesis_definition_changes_execution_identity(tmp_path):
    """Operator's mandatory property #3 — editing the .md file (a new
    version of the pre-registered claim) must change identity even
    though nothing else about the run's inputs changed."""
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path)
    c_before = hc.build_hypothesis_contract(**kwargs)

    hyp_path.write_text(hyp_path.read_text() + "\nRevised falsification criteria.\n")
    c_after = hc.build_hypothesis_contract(**kwargs)

    assert c_before["hypothesis_definition"]["sha256"] != c_after["hypothesis_definition"]["sha256"]
    assert hc.compute_execution_identity(c_before) != hc.compute_execution_identity(c_after)


def test_dataset_frames_closes_the_bar_count_gap(tmp_path):
    """Slice 5 fix: when the caller has a loaded DataFrame available,
    dataset_fingerprints must carry bar count/date range -- not just the
    bare file hash Slice 4 shipped by default."""
    import pandas as pd

    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path)

    without_frames = hc.build_hypothesis_contract(**kwargs)
    assert "bars" not in without_frames["dataset_fingerprints"][0]

    df = pd.DataFrame({"close": [1.5]}, index=pd.to_datetime(["2024-01-01"]))
    kwargs["dataset_frames"] = {str(ds_path): df}
    with_frames = hc.build_hypothesis_contract(**kwargs)
    assert with_frames["dataset_fingerprints"][0]["bars"] == 1
    assert "2024-01-01" in with_frames["dataset_fingerprints"][0]["first"]
    # sha256 is unaffected -- it's still purely a hash of the CSV's file bytes.
    assert with_frames["dataset_fingerprints"][0]["sha256"] == without_frames["dataset_fingerprints"][0]["sha256"]


def test_identity_ignores_dataset_fingerprint_list_order(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_a = _price_csv(tmp_path, "EURUSD")
    ds_b = _price_csv(tmp_path, "GBPUSD", "datetime,open,high,low,close\n2024-01-01,2,3,1.5,2.5\n")
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_a)
    kwargs["dataset_paths"] = [ds_a, ds_b]
    c1 = hc.build_hypothesis_contract(**kwargs)
    kwargs["dataset_paths"] = [ds_b, ds_a]
    c2 = hc.build_hypothesis_contract(**kwargs)
    assert hc.compute_execution_identity(c1) == hc.compute_execution_identity(c2)


def test_identity_ignores_engine_config_key_order(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path)
    kwargs["engine_config"] = {"a": 1, "b": 2}
    c1 = hc.build_hypothesis_contract(**kwargs)
    kwargs["engine_config"] = {"b": 2, "a": 1}
    c2 = hc.build_hypothesis_contract(**kwargs)
    assert hc.compute_execution_identity(c1) == hc.compute_execution_identity(c2)


def test_changing_symbol_changes_identity(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path)
    c_eurusd = hc.build_hypothesis_contract(**kwargs)
    kwargs["symbol"] = "GBPUSD"
    c_gbpusd = hc.build_hypothesis_contract(**kwargs)
    assert hc.compute_execution_identity(c_eurusd) != hc.compute_execution_identity(c_gbpusd)


def test_contract_carries_optional_fields_when_supplied(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path)
    kwargs.update(
        dataset_completeness_pct=99.4, provider="dukascopy", direction="LONG_ONLY",
        regime="TRENDING", sampler="grid", seed=42,
        validation_verdict="SAME_SYMBOL_CONFIRMED",
        multiple_testing_classification="SURVIVES_CORRECTION",
        final_evidence_verdict="PASSED",
    )
    contract = hc.build_hypothesis_contract(**kwargs)
    assert contract["provider"] == "dukascopy"
    assert contract["multiple_testing_classification"] == "SURVIVES_CORRECTION"
    assert contract["final_evidence_verdict"] == "PASSED"
    # Optional fields the caller never supplies stay None -- never fabricated.
    kwargs_minimal = _base_kwargs(tmp_path, hyp_path, ds_path)
    minimal = hc.build_hypothesis_contract(**kwargs_minimal)
    assert minimal["provider"] is None
    assert minimal["multiple_testing_classification"] is None
    assert minimal["final_evidence_verdict"] is None


# ── validate_for_new_run — fail-closed ───────────────────────────────────

def test_validate_for_new_run_accepts_a_complete_contract(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    contract = hc.build_hypothesis_contract(**_base_kwargs(tmp_path, hyp_path, ds_path))
    hc.validate_for_new_run(contract)  # must not raise


@pytest.mark.parametrize("missing_field", [
    "hypothesis_id", "hypothesis_definition", "symbol", "timeframe",
    "dataset_fingerprints", "train_period", "oos_period",
    "engine_config", "risk_config",
])
def test_validate_for_new_run_fails_closed_on_missing_required_field(tmp_path, missing_field):
    """Operator's mandatory property #4: missing required provenance
    cannot silently produce controlled evidence."""
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    contract = hc.build_hypothesis_contract(**_base_kwargs(tmp_path, hyp_path, ds_path))
    contract[missing_field] = None if missing_field != "dataset_fingerprints" else []

    with pytest.raises(hc.HypothesisContractError, match="missing required provenance"):
        hc.validate_for_new_run(contract)


def test_validate_for_new_run_fails_closed_on_zero_trial_family_size(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    contract = hc.build_hypothesis_contract(**_base_kwargs(tmp_path, hyp_path, ds_path))
    contract["trial_family_size"] = 0
    with pytest.raises(hc.HypothesisContractError, match="missing required provenance"):
        hc.validate_for_new_run(contract)


def test_validate_for_new_run_reports_every_missing_field_at_once(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path = _price_csv(tmp_path)
    contract = hc.build_hypothesis_contract(**_base_kwargs(tmp_path, hyp_path, ds_path))
    contract["symbol"] = None
    contract["timeframe"] = None
    try:
        hc.validate_for_new_run(contract)
        pytest.fail("expected HypothesisContractError")
    except hc.HypothesisContractError as exc:
        assert "symbol" in str(exc) and "timeframe" in str(exc)


# ── Requirement 9: historical records stay readable, never fabricated ────

def test_existing_historical_manifests_remain_readable_without_the_new_validator():
    """A real, already-committed manifest from before this slice existed
    must stay plain, unrestricted JSON -- never retroactively required to
    satisfy validate_for_new_run(), never touched by this module at all."""
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "research" / "results" / "h019_crypto_positioning_ab_20260724_manifest.json"
    assert manifest_path.exists(), "fixture manifest missing from the repo — pick another real committed manifest"

    historical = json.loads(manifest_path.read_text())  # plain read, no contract involved
    assert historical["kind"] == "h019_crypto_positioning_ab"
    assert "hypothesis_contract" not in historical  # confirms it predates this slice, unmodified

    # This module never reaches into research/results/ at all — confirmed
    # by construction (no file I/O against RESULTS_DIR anywhere in the
    # module), so nothing here could have silently mutated the fixture.
    assert json.loads(manifest_path.read_text()) == historical
