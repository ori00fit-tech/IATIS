"""
tests/test_mission_validator.py
------------------------------------
AI Research Lab / Mission Center Phase 3 (2026-07-30) — the safety-
critical test suite for backtest/mission_validator.py, mirroring
tests/test_mission_runner.py's own two non-negotiable properties: no
code path here ever writes research/results/registry.json, config.yaml,
or config/engines.yaml, and a validation's per-symbol results are always
recorded (pass or fail) — nothing suppressed.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import mission_runner, mission_validator
from backtest.mission_validator import (
    CROSS_SYMBOL,
    MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD,
    NO_EDGE,
    SAME_SYMBOL,
    SAME_SYMBOL_CONFIRMED,
    SAME_SYMBOL_NOT_CONFIRMED,
    STRONG_LEAD,
    WEAK_LEAD,
    ValidationConfig,
    _compute_candidate_lock,
    _compute_date_overlap,
    _default_same_symbol_start,
    run_validation,
)
from backtest.optimizer import MissionSearchSpace, _ENGINES_IDX_KEY, _INDICATORS_IDX_KEY, _TF_IDX_KEY
from storage import research_mission_validations, research_missions

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "research" / "results" / "registry.json"
CONFIG_YAML_PATH = REPO_ROOT / "config.yaml"
ENGINES_YAML_PATH = REPO_ROOT / "config" / "engines.yaml"


def _ohlcv(n: int, seed: int = 7, trend: float = 0.10, freq: str = "h") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


def _write_dataset(tmp_path: Path, symbol: str = "EURUSD", n: int = 2400) -> None:
    _ohlcv(n, seed=hash(symbol) % 1000).to_csv(tmp_path / f"{symbol}_H1_2y.csv")


def _write_partial_dataset(tmp_path: Path, symbol: str = "EURUSD", n: int = 2400) -> None:
    """Data Integrity Core (2026-08-19) — a structurally-valid CSV with a
    real, large gap carved out of the middle (validate_ohlcv() alone
    cannot see this; only dataset_completeness_pct() can)."""
    df = _ohlcv(n, seed=hash(symbol) % 1000)
    keep = df.index[(df.index < df.index[400]) | (df.index >= df.index[1200])]
    df.loc[keep].to_csv(tmp_path / f"{symbol}_H1_2y.csv")


def _space() -> MissionSearchSpace:
    return MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )


def _seed_mission_only(mission_id: str, trial_symbol: str = "EURUSD") -> None:
    space = _space()
    research_missions.upsert_mission(
        mission_id=mission_id, name="test-mission", sampler="random", objective_metric="profit_factor",
        symbols=[trial_symbol], n_trials_per_symbol=1, min_trades=1, seed=42,
        search_space=mission_runner._search_space_dict(space), config={}, status="finished",
    )


def _record_trial(
    mission_id: str, trial_number: int, symbol: str = "EURUSD", state: str = "COMPLETE",
    avg_rr: float = 0.5, std_rr: float = 1.0, trades: int = 50,
) -> None:
    """avg_rr/std_rr default to values whose trial_p_value() is ~0.0004 —
    comfortably < bonferroni_alpha(n) for any family size up to ~100
    trials (Slice 3, 2026-08-19), so a lone COMPLETE trial's family
    (n_trials_in_family == 1, bonferroni_alpha == 0.05) always reaches
    SURVIVES_CORRECTION unless a test explicitly overrides these to prove
    the opposite."""
    raw_params = {_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0, "sl_atr_multiplier": 2.0}
    research_missions.record_trial(
        mission_id=mission_id, trial_number=trial_number, symbol=symbol, state=state,
        objective_value=1.2 if state == "COMPLETE" else None,
        params=raw_params,
        metrics={"profit_factor": 1.2, "avg_rr": avg_rr, "std_rr": std_rr} if state == "COMPLETE" else None,
        trades=trades if state == "COMPLETE" else 0,
        error=None, started_at="t", finished_at="t",
    )


def _seed_mission_and_trial(mission_id: str, trial_symbol: str = "EURUSD", state: str = "COMPLETE") -> None:
    _seed_mission_only(mission_id, trial_symbol)
    _record_trial(mission_id, 0, trial_symbol, state=state)


def _small_vc(tmp_path: Path, validation_id: str, mission_id: str,
              validation_symbols=("EURUSD", "GBPUSD"), validation_mode: str = CROSS_SYMBOL) -> ValidationConfig:
    # Default CROSS_SYMBOL — preserves this file's pre-existing test intent
    # (multi-symbol generalization checks) now that SAME_SYMBOL is the
    # dataclass's own default (Forensic Audit Phase 1, item D, 2026-08-02).
    return ValidationConfig(
        validation_id=validation_id, mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=tuple(validation_symbols), data_dir=tmp_path, start=None, end=None,
        validation_mode=validation_mode,
        wf_windows=2, wf_min_trades_per_window=1, wf_warmup_bars=50,
        rb_multipliers=(0.8, 1.0, 1.2), rb_params=("sl_atr_multiplier",), rb_min_trades=1,
        mc_n_simulations=50, mc_seed=1, output_dir=tmp_path / "reports",
    )


# ── Hard-block safety tests ──────────────────────────────────────────────

def test_no_write_call_near_registry_mention_in_mission_validator():
    write_markers = ("write_text", "json.dump", "yaml.dump", "yaml.safe_dump", '"w")', "'w')")
    source = inspect.getsource(mission_validator)
    for line in source.splitlines():
        if "registry" in line.lower():
            for marker in write_markers:
                assert marker not in line, f"possible write near a registry reference: {line!r}"


def test_registry_json_byte_identical_before_and_after_validation_run(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-registry-check")

    before = REGISTRY_PATH.read_bytes()
    before_mtime = REGISTRY_PATH.stat().st_mtime

    vc = _small_vc(tmp_path, "v-registry-check", "val-registry-check")
    run_validation(vc)

    assert REGISTRY_PATH.read_bytes() == before
    assert REGISTRY_PATH.stat().st_mtime == before_mtime


def test_never_touches_config_yaml_or_engines_yaml(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-config-check")

    before_config = CONFIG_YAML_PATH.read_bytes()
    before_engines = ENGINES_YAML_PATH.read_bytes()

    vc = _small_vc(tmp_path, "v-config-check", "val-config-check")
    run_validation(vc)

    assert CONFIG_YAML_PATH.read_bytes() == before_config
    assert ENGINES_YAML_PATH.read_bytes() == before_engines


# ── Happy path / recording ────────────────────────────────────────────────

def test_run_validation_records_every_symbol_and_writes_report(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-happy-path")

    vc = _small_vc(tmp_path, "v-happy-path", "val-happy-path")
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-happy-path")
    assert validation is not None
    assert validation["status"] == "finished"
    assert validation["total_symbols"] == 2
    assert validation["overall_verdict"] in (NO_EDGE, WEAK_LEAD, STRONG_LEAD)

    results = research_mission_validations.validation_results("v-happy-path")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        breakdown = json.loads(r["criteria_breakdown_json"])
        # Every criterion is always present, pass or fail — nothing suppressed.
        assert set(breakdown) == {
            "profit_factor", "trades", "max_drawdown_pct", "expectancy", "sharpe_ratio",
            "walk_forward", "monte_carlo_risk_of_ruin", "monte_carlo_probability_profit",
            "robustness_all_stable", "dataset_completeness",
        }
        for c in breakdown.values():
            assert set(c) == {"actual", "threshold", "passed"}

    reports = list((tmp_path / "reports").glob("mission_validation_v-happy-path_*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["overall_verdict"] == validation["overall_verdict"]
    assert len(report["results"]) == 2


def test_run_validation_reconstructs_hypothesis_bundle_mode_trial_correctly(tmp_path):
    # Hypothesis Bundles (2026-07-30): the whole design bet was that
    # run_validation()'s existing search_space_from_dict() + resolve_point()
    # two-liner needs ZERO changes to correctly re-resolve a bundle-mode
    # trial's exact point. Prove it end-to-end, not just in isolation.
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")

    space = MissionSearchSpace(
        timeframes_choices=(("H1",),), engine_set_choices=(("nnfx",),),
        indicator_set_choices=((),),
        hypothesis_bundle_choices=(
            {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []},
            {"name": "NNFX + Price Action", "timeframes": ["H1"], "engines": ["nnfx", "price_action"], "indicators": [], "context_filters": []},
        ),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    research_missions.upsert_mission(
        mission_id="val-hypothesis-bundle", name="test-hypothesis-mission", sampler="random",
        objective_metric="profit_factor", symbols=["EURUSD"], n_trials_per_symbol=1, min_trades=1,
        seed=42, search_space=mission_runner._search_space_dict(space), config={}, status="finished",
    )
    research_missions.record_trial(
        mission_id="val-hypothesis-bundle", trial_number=0, symbol="EURUSD", state="COMPLETE",
        objective_value=1.2, params={"__hypothesis_idx": 1, "sl_atr_multiplier": 2.0},
        metrics={"profit_factor": 1.2}, trades=50, error=None, started_at="t", finished_at="t",
    )

    vc = _small_vc(tmp_path, "v-hypothesis-bundle", "val-hypothesis-bundle")
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-hypothesis-bundle")
    assert validation is not None
    assert validation["status"] == "finished"
    results = research_mission_validations.validation_results("v-hypothesis-bundle")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    # No crash reconstructing the bundle-mode point, and every symbol still
    # got a real evaluated result (not silently skipped/errored).
    for r in results:
        assert r["error"] is None


# ── Data-download M15 support (2026-08-11) ────────────────────────────────

def test_run_validation_m15_base_loads_the_m15_physical_file(tmp_path):
    """A trial whose resolved point.timeframes[0] == 'M15' must genuinely
    load the M15 physical file in _evaluate_symbol, never silently fall
    back to H1 — proven end-to-end via run_validation, not just via
    physical_load_timeframe in isolation."""
    space_m15 = MissionSearchSpace(
        timeframes_choices=(("M15", "H1", "H4", "D1"),),
        engine_set_choices=(("nnfx", "price_action"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    raw_params = {_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0, "sl_atr_multiplier": 2.0}

    def _seed(mission_id: str) -> None:
        research_missions.upsert_mission(
            mission_id=mission_id, name="test-m15-mission", sampler="random", objective_metric="profit_factor",
            symbols=["EURUSD"], n_trials_per_symbol=1, min_trades=1, seed=42,
            search_space=mission_runner._search_space_dict(space_m15), config={}, status="finished",
        )
        research_missions.record_trial(
            mission_id=mission_id, trial_number=0, symbol="EURUSD", state="COMPLETE",
            objective_value=1.2, params=raw_params, metrics={"profit_factor": 1.2}, trades=50,
            error=None, started_at="t", finished_at="t",
        )

    def _vc(validation_id: str, mission_id: str) -> ValidationConfig:
        return ValidationConfig(
            validation_id=validation_id, mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
            validation_symbols=("EURUSD",), data_dir=tmp_path, start=None, end=None,
            validation_mode=SAME_SYMBOL,
            wf_windows=2, wf_min_trades_per_window=1, wf_warmup_bars=50,
            rb_multipliers=(0.8, 1.0, 1.2), rb_params=("sl_atr_multiplier",), rb_min_trades=1,
            mc_n_simulations=50, mc_seed=1, output_dir=tmp_path / "reports",
        )

    # No dataset at all yet -> the M15 request must fail loudly and be
    # recorded as a per-symbol error, never silently substitute H1.
    _seed("val-m15-missing")
    run_validation(_vc("v-m15-missing", "val-m15-missing"))
    results = research_mission_validations.validation_results("v-m15-missing")
    assert len(results) == 1
    assert results[0]["error"] is not None
    assert "M15" in results[0]["error"]

    # Now write ONLY the M15 file (no H1 file at all) -> must succeed.
    _ohlcv(2400, seed=1, freq="15min").to_csv(tmp_path / "EURUSD_M15_2y.csv")
    _seed("val-m15-present")
    run_validation(_vc("v-m15-present", "val-m15-present"))
    results2 = research_mission_validations.validation_results("v-m15-present")
    assert len(results2) == 1
    assert results2[0]["error"] is None


# ── confluence_overrides end-to-end audit (2026-08-XX) ──────────────────────
# Confirms the full chain a validated candidate's confluence_overrides must
# survive: MissionSearchSpace -> search_space_json -> search_space_from_dict
# (resume) -> resolve_point() -> _evaluate_symbol()'s WalkForwardConfig/
# RobustnessConfig construction (NOT just evaluate_point()'s own direct
# re-evaluation, which already threaded this through since Phase 1). This
# pins the regression: a single-engine mission (confluence_overrides=
# {"min_engines_agreeing": 1}) must validate at the SAME lowered quorum, not
# silently fall back to the production default of 2, which one engine can
# never satisfy.

def test_resolve_point_always_carries_confluence_overrides(tmp_path):
    from backtest.optimizer import resolve_point

    space = MissionSearchSpace(
        timeframes_choices=(("H1",),), engine_set_choices=(("smc",),),
        indicator_set_choices=((),), confluence_overrides={"min_engines_agreeing": 1},
    )
    point = resolve_point(space, {_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0})
    assert point["confluence_overrides"] == {"min_engines_agreeing": 1}


def test_run_validation_threads_confluence_overrides_into_walk_forward_and_robustness_configs(tmp_path, monkeypatch):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")

    space = MissionSearchSpace(
        timeframes_choices=(("H1",),), engine_set_choices=(("smc",),),
        indicator_set_choices=((),), confluence_overrides={"min_engines_agreeing": 1},
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    research_missions.upsert_mission(
        mission_id="val-confluence", name="single-engine-mission", sampler="random",
        objective_metric="profit_factor", symbols=["EURUSD"], n_trials_per_symbol=1, min_trades=1,
        seed=42, search_space=mission_runner._search_space_dict(space), config={}, status="finished",
    )
    research_missions.record_trial(
        mission_id="val-confluence", trial_number=0, symbol="EURUSD", state="COMPLETE",
        objective_value=1.2, params={_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0, "sl_atr_multiplier": 2.0},
        metrics={"profit_factor": 1.2}, trades=50, error=None, started_at="t", finished_at="t",
    )

    seen_wf_configs = []
    seen_rb_configs = []
    real_run_walk_forward = mission_validator.run_walk_forward
    real_run_robustness = mission_validator.run_robustness

    def _spy_wf(symbol, df, config):
        seen_wf_configs.append(config)
        return real_run_walk_forward(symbol, df, config)

    def _spy_rb(symbol, df, config):
        seen_rb_configs.append(config)
        return real_run_robustness(symbol, df, config)

    monkeypatch.setattr(mission_validator, "run_walk_forward", _spy_wf)
    monkeypatch.setattr(mission_validator, "run_robustness", _spy_rb)

    vc = _small_vc(tmp_path, "v-confluence", "val-confluence", validation_symbols=("EURUSD",))
    run_validation(vc)

    assert len(seen_wf_configs) == 1
    assert seen_wf_configs[0].confluence_overrides == {"min_engines_agreeing": 1}
    assert len(seen_rb_configs) == 1
    assert seen_rb_configs[0].confluence_overrides == {"min_engines_agreeing": 1}


def test_run_validation_omits_confluence_overrides_when_mission_never_set_any(tmp_path, monkeypatch):
    _write_dataset(tmp_path, "EURUSD")
    _seed_mission_and_trial("val-no-confluence-override")

    seen_wf_configs = []
    real_run_walk_forward = mission_validator.run_walk_forward

    def _spy_wf(symbol, df, config):
        seen_wf_configs.append(config)
        return real_run_walk_forward(symbol, df, config)

    monkeypatch.setattr(mission_validator, "run_walk_forward", _spy_wf)

    vc = _small_vc(tmp_path, "v-no-confluence-override", "val-no-confluence-override", validation_symbols=("EURUSD",))
    run_validation(vc)

    assert len(seen_wf_configs) == 1
    assert seen_wf_configs[0].confluence_overrides is None


# ── Effective sample size / significance diagnostic ────────────────────────
# (Mission Center Research Rigor Phase 2, 2026-08-XX) — informational only,
# never a VALIDATION_CRITERIA entry, never blocking.

def test_run_validation_records_significance_diagnostic_per_symbol(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-significance")

    vc = _small_vc(tmp_path, "v-significance", "val-significance")
    run_validation(vc)

    results = research_mission_validations.validation_results("v-significance")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        assert r["significance_json"] is not None
        sig = json.loads(r["significance_json"])
        assert set(sig) == {
            "n_trades", "effective_sample_size", "autocorrelation_ratio",
            "nominal_p_value", "ess_adjusted_p_value", "note",
        }
        if sig["effective_sample_size"] is not None:
            # ESS is always <= n_trades, so the ESS-adjusted p-value can
            # only ever be >= the nominal one (more conservative).
            assert sig["effective_sample_size"] <= sig["n_trades"]
            if sig["nominal_p_value"] is not None and sig["ess_adjusted_p_value"] is not None:
                assert sig["ess_adjusted_p_value"] >= sig["nominal_p_value"]


def test_run_validation_records_regime_robustness_diagnostic_per_symbol(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-regime-robustness")

    vc = _small_vc(tmp_path, "v-regime-robustness", "val-regime-robustness")
    run_validation(vc)

    results = research_mission_validations.validation_results("v-regime-robustness")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        assert r["regime_robustness_json"] is not None
        rr = json.loads(r["regime_robustness_json"])
        assert set(rr) == {
            "regimes_traded", "regimes_material", "regimes_profitable",
            "regime_robustness_score", "dominant_regime", "dominant_regime_share", "note",
        }


def test_validation_criteria_dataset_completeness_bar_matches_warehouse_ready_bar():
    """Data Integrity Core (2026-08-19) — pins that the CSV-path bar and
    the D1-warehouse READY bar can never silently drift apart."""
    from storage.market_bars import MIN_COVERAGE_PCT_FOR_READY

    assert mission_validator.VALIDATION_CRITERIA["min_dataset_completeness_pct"] == MIN_COVERAGE_PCT_FOR_READY


def test_run_validation_records_dataset_completeness_criterion_per_symbol(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-completeness")

    vc = _small_vc(tmp_path, "v-completeness", "val-completeness")
    run_validation(vc)

    results = research_mission_validations.validation_results("v-completeness")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        breakdown = json.loads(r["criteria_breakdown_json"])
        dc = breakdown["dataset_completeness"]
        assert set(dc) == {"actual", "threshold", "passed"}
        assert dc["threshold"] == 95.0
        assert dc["passed"] is True  # both datasets are complete, continuous CSVs
        assert dc["actual"] == 100.0


def test_run_validation_fails_dataset_completeness_for_a_partial_symbol(tmp_path):
    """The authoritative proof this fix exists to deliver: a genuinely
    PARTIAL dataset must fail its own criterion (and therefore the whole
    symbol's `passed`) — it can never silently produce a CONFIRMED/
    STRONG_LEAD verdict alongside a complete symbol's real result."""
    _write_dataset(tmp_path, "EURUSD")
    _write_partial_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-partial")

    vc = _small_vc(tmp_path, "v-partial", "val-partial")
    run_validation(vc)

    results = {r["symbol"]: r for r in research_mission_validations.validation_results("v-partial")}
    assert set(results) == {"EURUSD", "GBPUSD"}

    good = json.loads(results["EURUSD"]["criteria_breakdown_json"])
    assert good["dataset_completeness"]["passed"] is True

    bad = json.loads(results["GBPUSD"]["criteria_breakdown_json"])
    assert bad["dataset_completeness"]["passed"] is False
    assert bad["dataset_completeness"]["actual"] < 95.0
    assert results["GBPUSD"]["passed"] == 0  # sqlite boolean — the whole symbol result fails, not just the one criterion


def test_compute_effective_sample_size_diagnostic_too_few_trades():
    from backtest.metrics import TradeRecord
    from backtest.mission_validator import _compute_effective_sample_size_diagnostic

    trades = [TradeRecord(trade_id="t1", symbol="EURUSD", direction="BUY", entry_time="t", exit_time="t",
                           entry_price=1.0, exit_price=1.01, stop_loss=0.99, take_profit=1.02,
                           position_size=1.0, rr_actual=1.0, is_win=True, pnl_usd=10.0)] * 3
    result = _compute_effective_sample_size_diagnostic(trades)
    assert result["n_trades"] == 3
    assert result["effective_sample_size"] is None
    assert result["nominal_p_value"] is None
    assert "Too few" in result["note"]


def test_compute_effective_sample_size_diagnostic_never_reaches_a_criterion():
    # Structural pin: _compute_effective_sample_size_diagnostic() output is
    # never consumed by VALIDATION_CRITERIA/criteria_breakdown — a source
    # scan proving the two never share a call chain in _evaluate_symbol.
    import inspect

    source = inspect.getsource(mission_validator._evaluate_symbol)
    # The significance result is assigned once and only ever placed into
    # the returned dict's own "significance" key — never fed into
    # `breakdown`.
    assert "significance_result = _compute_effective_sample_size_diagnostic" in source
    assert '"significance": json_safe(significance_result)' in source
    breakdown_block = source[source.index("breakdown = {"):source.index("passed = all")]
    assert "significance_result" not in breakdown_block


# ── Regime robustness diagnostic ────────────────────────────────────────────
# (Mission Center Research Rigor Phase 3, 2026-08-XX) — informational only,
# never a VALIDATION_CRITERIA entry, never blocking.

def test_regime_robustness_no_trades():
    from backtest.mission_validator import _compute_regime_robustness_diagnostic

    result = _compute_regime_robustness_diagnostic({})
    assert result["regimes_traded"] == 0
    assert result["regime_robustness_score"] is None
    assert "No closed trades" in result["note"]


def test_regime_robustness_only_unknown_regime_is_treated_as_no_trades():
    from backtest.mission_validator import _compute_regime_robustness_diagnostic

    by_regime = {"Unknown": {"trades": 40, "wins": 20, "profit_factor": 1.5}}
    result = _compute_regime_robustness_diagnostic(by_regime)
    assert result["regimes_traded"] == 0
    assert result["regime_robustness_score"] is None


def test_regime_robustness_no_regime_reaches_material_floor():
    from backtest.mission_validator import _compute_regime_robustness_diagnostic

    by_regime = {
        "TRENDING": {"trades": 3, "wins": 2, "profit_factor": 2.0},
        "RANGING": {"trades": 2, "wins": 1, "profit_factor": 0.5},
    }
    result = _compute_regime_robustness_diagnostic(by_regime, min_trades_per_regime=10)
    assert result["regimes_traded"] == 2
    assert result["regimes_material"] == 0
    assert result["regime_robustness_score"] is None
    assert result["dominant_regime"] == "TRENDING"
    assert "too few trades" in result["note"]


def test_regime_robustness_concentrated_in_one_regime():
    by_regime = {
        "TRENDING": {"trades": 95, "wins": 60, "profit_factor": 1.8},
        "RANGING": {"trades": 5, "wins": 2, "profit_factor": 0.6},
    }
    from backtest.mission_validator import _compute_regime_robustness_diagnostic
    result = _compute_regime_robustness_diagnostic(by_regime, min_trades_per_regime=10)
    assert result["regimes_material"] == 1  # only TRENDING clears the floor
    assert result["regimes_profitable"] == 1
    assert result["regime_robustness_score"] == pytest.approx(1.0)
    assert result["dominant_regime"] == "TRENDING"
    assert result["dominant_regime_share"] == pytest.approx(0.95)


def test_regime_robustness_profitable_in_both_regimes():
    from backtest.mission_validator import _compute_regime_robustness_diagnostic

    by_regime = {
        "TRENDING": {"trades": 60, "wins": 35, "profit_factor": 1.4},
        "RANGING": {"trades": 40, "wins": 22, "profit_factor": 1.1},
    }
    result = _compute_regime_robustness_diagnostic(by_regime, min_trades_per_regime=10)
    assert result["regimes_material"] == 2
    assert result["regimes_profitable"] == 2
    assert result["regime_robustness_score"] == pytest.approx(1.0)
    assert result["dominant_regime_share"] < 0.7  # neither regime dominates


def test_regime_robustness_profitable_in_only_one_of_two_material_regimes():
    from backtest.mission_validator import _compute_regime_robustness_diagnostic

    by_regime = {
        "TRENDING": {"trades": 50, "wins": 35, "profit_factor": 1.6},
        "RANGING": {"trades": 50, "wins": 15, "profit_factor": 0.4},
    }
    result = _compute_regime_robustness_diagnostic(by_regime, min_trades_per_regime=10)
    assert result["regimes_material"] == 2
    assert result["regimes_profitable"] == 1
    assert result["regime_robustness_score"] == pytest.approx(0.5)


def test_regime_robustness_infinite_profit_factor_counts_as_profitable():
    # A regime with zero losing trades gets profit_factor=inf (metrics.py's
    # own convention) — must be treated as profitable, not crash the >= 1.0
    # comparison.
    from backtest.mission_validator import _compute_regime_robustness_diagnostic

    by_regime = {"TRENDING": {"trades": 20, "wins": 20, "profit_factor": float("inf")}}
    result = _compute_regime_robustness_diagnostic(by_regime, min_trades_per_regime=10)
    assert result["regimes_profitable"] == 1
    assert result["regime_robustness_score"] == pytest.approx(1.0)


def test_regime_robustness_never_reaches_a_criterion():
    source = inspect.getsource(mission_validator._evaluate_symbol)
    assert "regime_robustness_result = _compute_regime_robustness_diagnostic" in source
    assert '"regime_robustness": json_safe(regime_robustness_result)' in source
    breakdown_block = source[source.index("breakdown = {"):source.index("passed = all")]
    assert "regime_robustness_result" not in breakdown_block


# ── Stability score diagnostic ──────────────────────────────────────────────
# (Mission Center Research Rigor Phase 4, 2026-08-XX) — informational only,
# never a VALIDATION_CRITERIA entry, never blocking.

def _sweep(param: str, verdict: str):
    from backtest.robustness import ParamSweepResult
    return ParamSweepResult(param=param, baseline_value=2.0, baseline_pf=1.5, points=[], verdict=verdict)


def test_stability_score_no_sweeps():
    from backtest.mission_validator import _compute_stability_score_diagnostic

    result = _compute_stability_score_diagnostic([])
    assert result["params_swept"] == 0
    assert result["stability_score"] is None
    assert "No swept parameter" in result["note"]


def test_stability_score_all_insufficient():
    from backtest.mission_validator import _compute_stability_score_diagnostic

    sweeps = [_sweep("sl_atr_multiplier", "INSUFFICIENT"), _sweep("min_rr", "INSUFFICIENT")]
    result = _compute_stability_score_diagnostic(sweeps)
    assert result["params_swept"] == 2
    assert result["params_measurable"] == 0
    assert result["params_insufficient"] == 2
    assert result["stability_score"] is None


def test_stability_score_all_stable():
    from backtest.mission_validator import _compute_stability_score_diagnostic

    sweeps = [_sweep("sl_atr_multiplier", "STABLE"), _sweep("min_rr", "STABLE")]
    result = _compute_stability_score_diagnostic(sweeps)
    assert result["params_measurable"] == 2
    assert result["params_stable"] == 2
    assert result["stability_score"] == pytest.approx(1.0)


def test_stability_score_mixed_verdicts_excludes_insufficient_from_denominator():
    from backtest.mission_validator import _compute_stability_score_diagnostic

    sweeps = [
        _sweep("sl_atr_multiplier", "STABLE"),
        _sweep("min_rr", "SENSITIVE"),
        _sweep("risk_per_trade", "INSUFFICIENT"),
    ]
    result = _compute_stability_score_diagnostic(sweeps)
    assert result["params_swept"] == 3
    assert result["params_measurable"] == 2  # INSUFFICIENT excluded
    assert result["params_stable"] == 1
    assert result["params_sensitive"] == 1
    assert result["params_insufficient"] == 1
    assert result["stability_score"] == pytest.approx(0.5)


def test_stability_score_all_sensitive():
    from backtest.mission_validator import _compute_stability_score_diagnostic

    sweeps = [_sweep("sl_atr_multiplier", "SENSITIVE")]
    result = _compute_stability_score_diagnostic(sweeps)
    assert result["params_measurable"] == 1
    assert result["params_stable"] == 0
    assert result["stability_score"] == pytest.approx(0.0)


def test_stability_score_never_reaches_a_criterion():
    source = inspect.getsource(mission_validator._evaluate_symbol)
    assert "stability_score_result = _compute_stability_score_diagnostic" in source
    assert '"stability": json_safe(stability_score_result)' in source
    breakdown_block = source[source.index("breakdown = {"):source.index("passed = all")]
    assert "stability_score_result" not in breakdown_block


def test_run_validation_records_stability_diagnostic_per_symbol(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-stability")

    vc = _small_vc(tmp_path, "v-stability", "val-stability")
    run_validation(vc)

    results = research_mission_validations.validation_results("v-stability")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        assert r["stability_json"] is not None
        st = json.loads(r["stability_json"])
        assert set(st) == {
            "params_swept", "params_measurable", "params_stable",
            "params_sensitive", "params_insufficient", "stability_score", "note",
        }


# ── Cost stress diagnostic ──────────────────────────────────────────────────
# (Mission Center Research Rigor Phase 5, 2026-08-XX) — informational only,
# never a VALIDATION_CRITERIA entry, never blocking.

def _cost_stress_point(risk_overrides: dict | None = None) -> dict:
    return {
        "timeframes": ["H1"], "engines": ["nnfx", "price_action"],
        "indicators": [], "context_filters": [], "engine_variants": {},
        "risk_overrides": dict(risk_overrides or {}),
    }


def _fake_eval_result(pf: float, trades: int):
    from backtest.metrics import BacktestMetrics
    from backtest.optimizer import EvalResult

    return EvalResult(
        metrics=BacktestMetrics(profit_factor=pf), objective_value=pf,
        insufficient=trades < 1, trades=trades,
    )


def test_cost_stress_diagnostic_resolves_real_baseline_and_scales_each_level(monkeypatch):
    from backtest.mission_validator import COST_STRESS_MULTIPLIERS, _compute_cost_stress_diagnostic

    calls: list[dict] = []

    def _fake_evaluate_point(symbol, df, point, min_trades, objective_metric):
        calls.append(point)
        return _fake_eval_result(pf=1.5, trades=100)

    monkeypatch.setattr(mission_validator, "evaluate_point", _fake_evaluate_point)

    result = _compute_cost_stress_diagnostic("EURUSD", None, _cost_stress_point())

    assert len(calls) == len(COST_STRESS_MULTIPLIERS) == 3
    assert result["baseline_commission_pips"] > 0  # a real, measured per-symbol default, not fabricated
    for multiplier, call, level in zip(COST_STRESS_MULTIPLIERS, calls, result["levels"]):
        ro = call["risk_overrides"]
        assert ro["commission_pips"] == pytest.approx(result["baseline_commission_pips"] * multiplier)
        assert ro["slippage_pips"] == pytest.approx(result["baseline_slippage_pips"] * multiplier)
        assert level["multiplier"] == multiplier
        assert level["commission_pips"] == pytest.approx(ro["commission_pips"], abs=1e-3)
        assert level["trades"] == 100
        assert level["profit_factor"] == 1.5
        assert level["edge_survives"] is True
    assert result["survives_all_stress_levels"] is True
    assert "1.5x" in result["note"] and "VALIDATION_CRITERIA" in result["note"]


def test_cost_stress_diagnostic_preserves_existing_risk_overrides(monkeypatch):
    from backtest.mission_validator import _compute_cost_stress_diagnostic

    calls: list[dict] = []

    def _fake_evaluate_point(symbol, df, point, min_trades, objective_metric):
        calls.append(point)
        return _fake_eval_result(pf=1.2, trades=50)

    monkeypatch.setattr(mission_validator, "evaluate_point", _fake_evaluate_point)
    _compute_cost_stress_diagnostic("EURUSD", None, _cost_stress_point({"sl_atr_multiplier": 2.0}))

    for call in calls:
        assert call["risk_overrides"]["sl_atr_multiplier"] == 2.0
        assert call["timeframes"] == ["H1"]
        assert call["engines"] == ["nnfx", "price_action"]


def test_cost_stress_diagnostic_edge_fails_when_pf_below_one(monkeypatch):
    from backtest.mission_validator import _compute_cost_stress_diagnostic

    monkeypatch.setattr(
        mission_validator, "evaluate_point",
        lambda symbol, df, point, min_trades, objective_metric: _fake_eval_result(pf=0.8, trades=40),
    )
    result = _compute_cost_stress_diagnostic("EURUSD", None, _cost_stress_point())

    assert all(lv["edge_survives"] is False for lv in result["levels"])
    assert result["survives_all_stress_levels"] is False


def test_cost_stress_diagnostic_edge_survives_none_when_no_trades(monkeypatch):
    from backtest.mission_validator import _compute_cost_stress_diagnostic

    monkeypatch.setattr(
        mission_validator, "evaluate_point",
        lambda symbol, df, point, min_trades, objective_metric: _fake_eval_result(pf=0.0, trades=0),
    )
    result = _compute_cost_stress_diagnostic("EURUSD", None, _cost_stress_point())

    assert all(lv["edge_survives"] is None for lv in result["levels"])
    assert result["survives_all_stress_levels"] is None  # no measurable levels -> None, never fabricated


def test_cost_stress_diagnostic_mixed_measurability_survives_all_ignores_unmeasurable(monkeypatch):
    from backtest.mission_validator import _compute_cost_stress_diagnostic

    responses = [_fake_eval_result(pf=1.4, trades=30), _fake_eval_result(pf=0.0, trades=0), _fake_eval_result(pf=1.1, trades=20)]
    calls = iter(responses)
    monkeypatch.setattr(
        mission_validator, "evaluate_point",
        lambda symbol, df, point, min_trades, objective_metric: next(calls),
    )
    result = _compute_cost_stress_diagnostic("EURUSD", None, _cost_stress_point())

    assert [lv["edge_survives"] for lv in result["levels"]] == [True, None, True]
    assert result["survives_all_stress_levels"] is True


def test_cost_stress_diagnostic_infinite_profit_factor_is_json_safe(monkeypatch):
    from backtest.metrics import json_safe
    from backtest.mission_validator import _compute_cost_stress_diagnostic

    monkeypatch.setattr(
        mission_validator, "evaluate_point",
        lambda symbol, df, point, min_trades, objective_metric: _fake_eval_result(pf=float("inf"), trades=10),
    )
    result = _compute_cost_stress_diagnostic("EURUSD", None, _cost_stress_point())
    assert all(lv["profit_factor"] == float("inf") for lv in result["levels"])

    safe = json_safe(result)
    dumped = json.dumps(safe)  # must not raise — bare inf is invalid JSON
    reloaded = json.loads(dumped)
    assert reloaded["levels"][0]["profit_factor"] == "Infinity"


def test_cost_stress_never_reaches_a_criterion():
    source = inspect.getsource(mission_validator._evaluate_symbol)
    assert "cost_stress_result = _compute_cost_stress_diagnostic" in source
    assert '"cost_stress": json_safe(cost_stress_result)' in source
    breakdown_block = source[source.index("breakdown = {"):source.index("passed = all")]
    assert "cost_stress_result" not in breakdown_block


def test_run_validation_records_cost_stress_diagnostic_per_symbol(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-cost-stress")

    vc = _small_vc(tmp_path, "v-cost-stress", "val-cost-stress")
    run_validation(vc)

    results = research_mission_validations.validation_results("v-cost-stress")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        assert r["cost_stress_json"] is not None
        cs = json.loads(r["cost_stress_json"])
        assert set(cs) == {
            "baseline_commission_pips", "baseline_slippage_pips",
            "levels", "survives_all_stress_levels", "note",
        }
        assert len(cs["levels"]) == 3
        for level in cs["levels"]:
            assert set(level) == {
                "multiplier", "commission_pips", "slippage_pips",
                "trades", "profit_factor", "edge_survives",
            }


# ── Discovery score diagnostic ───────────────────────────────────────────────
# (Mission Center Research Rigor Phase 6, 2026-08-XX) — informational only,
# never a VALIDATION_CRITERIA entry, never blocking, never used to rank or
# auto-select a candidate. A transparent average of the four diagnostics
# already computed above — no new backtest evaluation.

def _cost_stress_dict(*edge_survives: bool | None) -> dict:
    return {
        "baseline_commission_pips": 1.0, "baseline_slippage_pips": 0.5,
        "levels": [
            {"multiplier": m, "commission_pips": 1.0 * m, "slippage_pips": 0.5 * m,
             "trades": 10, "profit_factor": 1.2, "edge_survives": es}
            for m, es in zip((1.5, 2.0, 3.0), edge_survives)
        ],
        "survives_all_stress_levels": None, "note": "x",
    }


def test_discovery_score_all_four_components_available():
    from backtest.mission_validator import _compute_discovery_score_diagnostic

    result = _compute_discovery_score_diagnostic(
        significance={"ess_adjusted_p_value": 0.01},
        regime_robustness={"regime_robustness_score": 1.0},
        stability={"stability_score": 0.5},
        cost_stress=_cost_stress_dict(True, True, False),
    )
    assert result["significance_component"] == 1.0
    assert result["regime_robustness_component"] == 1.0
    assert result["stability_component"] == 0.5
    assert result["cost_stress_component"] == pytest.approx(2 / 3)
    assert result["components_used"] == 4
    assert result["components_total"] == 4
    assert result["discovery_score"] == pytest.approx((1.0 + 1.0 + 0.5 + 2 / 3) / 4, abs=1e-3)


def test_discovery_score_significance_component_buckets():
    from backtest.mission_validator import _significance_component

    assert _significance_component({"ess_adjusted_p_value": 0.01}) == 1.0
    assert _significance_component({"ess_adjusted_p_value": 0.07}) == 0.5
    assert _significance_component({"ess_adjusted_p_value": 0.5}) == 0.0
    assert _significance_component({"ess_adjusted_p_value": None}) is None


def test_discovery_score_cost_stress_component_none_when_unmeasurable():
    from backtest.mission_validator import _cost_stress_component

    assert _cost_stress_component(_cost_stress_dict(None, None, None)) is None
    assert _cost_stress_component(_cost_stress_dict(True, False, None)) == pytest.approx(0.5)
    assert _cost_stress_component(_cost_stress_dict(True, True, True)) == 1.0


def test_discovery_score_excludes_missing_components_from_average_not_as_zero():
    from backtest.mission_validator import _compute_discovery_score_diagnostic

    result = _compute_discovery_score_diagnostic(
        significance={"ess_adjusted_p_value": None},
        regime_robustness={"regime_robustness_score": None},
        stability={"stability_score": 1.0},
        cost_stress=_cost_stress_dict(None, None, None),
    )
    assert result["components_used"] == 1
    assert result["components_total"] == 4
    assert result["discovery_score"] == 1.0  # average of just the one available component, not diluted by zeros


def test_discovery_score_none_when_no_components_available():
    from backtest.mission_validator import _compute_discovery_score_diagnostic

    result = _compute_discovery_score_diagnostic(
        significance={"ess_adjusted_p_value": None},
        regime_robustness={"regime_robustness_score": None},
        stability={"stability_score": None},
        cost_stress=_cost_stress_dict(None, None, None),
    )
    assert result["components_used"] == 0
    assert result["discovery_score"] is None


def test_discovery_score_never_reaches_a_criterion():
    source = inspect.getsource(mission_validator._evaluate_symbol)
    assert "discovery_score_result = _compute_discovery_score_diagnostic" in source
    assert '"discovery_score": json_safe(discovery_score_result)' in source
    breakdown_block = source[source.index("breakdown = {"):source.index("passed = all")]
    assert "discovery_score_result" not in breakdown_block


def test_run_validation_records_discovery_score_diagnostic_per_symbol(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-discovery-score")

    vc = _small_vc(tmp_path, "v-discovery-score", "val-discovery-score")
    run_validation(vc)

    results = research_mission_validations.validation_results("v-discovery-score")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        assert r["discovery_score_json"] is not None
        ds = json.loads(r["discovery_score_json"])
        assert set(ds) == {
            "significance_component", "regime_robustness_component",
            "stability_component", "cost_stress_component",
            "components_used", "components_total", "discovery_score", "note",
        }


# ── Reproducibility fingerprint / candidate lock + date overlap ────────────
# (Diagnostic Infrastructure Phase 1, 2026-08-02) — both informational
# only, never a VALIDATION_CRITERIA entry, never blocking.

def test_compute_candidate_lock_missing_fingerprint_is_unavailable():
    trial = {"fingerprint_json": None}
    result = _compute_candidate_lock(trial, "EURUSD", Path("data"))
    assert result == {"available": False, "note": "Trial predates fingerprint tracking."}


def test_compute_candidate_lock_matches_when_dataset_unchanged(tmp_path, monkeypatch):
    # Pinned, clean git state — this repo's own working tree may genuinely
    # be dirty during development, which would make "current working tree
    # is dirty" a real, correct diff even with an unchanged dataset. That
    # git-dirty behavior is covered separately; this test isolates the
    # dataset-comparison logic on its own.
    monkeypatch.setattr(mission_validator, "git_state", lambda: {"commit": "abc123", "dirty": False})
    _write_dataset(tmp_path, "EURUSD")
    from research.manifest import dataset_fingerprint
    from backtest.runner import find_symbol_csv

    fingerprint = {"git": {"commit": "abc123", "dirty": False},
                   "dataset": dataset_fingerprint(find_symbol_csv("EURUSD", tmp_path))}
    trial = {"fingerprint_json": json.dumps(fingerprint)}
    result = _compute_candidate_lock(trial, "EURUSD", tmp_path)
    assert result["available"] is True
    assert result["matches"] is True
    assert result["diffs"] == []


def test_compute_candidate_lock_detects_dataset_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(mission_validator, "git_state", lambda: {"commit": "abc123", "dirty": False})
    _write_dataset(tmp_path, "EURUSD")
    from research.manifest import dataset_fingerprint
    from backtest.runner import find_symbol_csv

    csv_path = find_symbol_csv("EURUSD", tmp_path)
    fingerprint = {"git": {"commit": "abc123", "dirty": False}, "dataset": dataset_fingerprint(csv_path)}
    trial = {"fingerprint_json": json.dumps(fingerprint)}

    # Simulate a legitimately-grown dataset since the trial ran.
    csv_path.write_text(csv_path.read_text() + "\n")
    result = _compute_candidate_lock(trial, "EURUSD", tmp_path)
    assert result["available"] is True
    assert result["matches"] is False
    assert "dataset file content changed (different SHA256)" in result["diffs"]


def test_compute_candidate_lock_flags_dirty_working_tree_even_with_matching_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(mission_validator, "git_state", lambda: {"commit": "abc123", "dirty": True})
    _write_dataset(tmp_path, "EURUSD")
    from research.manifest import dataset_fingerprint
    from backtest.runner import find_symbol_csv

    fingerprint = {"git": {"commit": "abc123", "dirty": False},
                   "dataset": dataset_fingerprint(find_symbol_csv("EURUSD", tmp_path))}
    trial = {"fingerprint_json": json.dumps(fingerprint)}
    result = _compute_candidate_lock(trial, "EURUSD", tmp_path)
    assert result["matches"] is False
    assert "current working tree is dirty (uncommitted changes)" in result["diffs"]


def test_compute_candidate_lock_survives_unparseable_json():
    trial = {"fingerprint_json": "not json"}
    result = _compute_candidate_lock(trial, "EURUSD", Path("data"))
    assert result["available"] is False


# ── MC-4 (2026-08-15 red-team audit): warehouse-origin candidate lock ──────
# A warehouse-origin trial's fingerprint has no CSV to re-derive a sha256
# from — the old code always fell through to find_symbol_csv/dataset_
# fingerprint, which produced a spurious "dataset changed" mismatch on
# EVERY warehouse-origin trial regardless of whether anything really
# changed. These pin the fix: a warehouse-origin fingerprint is compared
# against a FRESH warehouse manifest checksum, never a CSV re-derivation.

def test_compute_candidate_lock_warehouse_origin_matches_when_checksum_unchanged(monkeypatch):
    monkeypatch.setattr(mission_validator, "git_state", lambda: {"commit": "abc123", "dirty": False})
    fingerprint = {
        "git": {"commit": "abc123", "dirty": False},
        "dataset": {"origin": "warehouse", "warehouse_source": "dukascopy", "checksum": "deadbeef",
                    "row_count": 500, "coverage_pct": 99.5, "status": "READY", "timeframe": "H4"},
    }
    trial = {"fingerprint_json": json.dumps(fingerprint)}
    monkeypatch.setattr(
        "storage.market_bars.get_manifest",
        lambda symbol, timeframe: {"source": "dukascopy", "checksum": "deadbeef",
                                    "row_count": 500, "coverage_pct": 99.5, "status": "READY"},
    )
    result = _compute_candidate_lock(trial, "EURUSD", Path("data"))
    assert result["available"] is True
    assert result["matches"] is True
    assert result["diffs"] == []
    assert result["current"]["dataset"]["origin"] == "warehouse"


def test_compute_candidate_lock_warehouse_origin_detects_checksum_drift(monkeypatch):
    monkeypatch.setattr(mission_validator, "git_state", lambda: {"commit": "abc123", "dirty": False})
    fingerprint = {
        "git": {"commit": "abc123", "dirty": False},
        "dataset": {"origin": "warehouse", "checksum": "old-checksum", "timeframe": "H4"},
    }
    trial = {"fingerprint_json": json.dumps(fingerprint)}
    monkeypatch.setattr(
        "storage.market_bars.get_manifest",
        lambda symbol, timeframe: {"source": "dukascopy", "checksum": "new-checksum"},
    )
    result = _compute_candidate_lock(trial, "EURUSD", Path("data"))
    assert result["matches"] is False
    assert "warehouse dataset checksum changed (D1 market_bars manifest)" in result["diffs"]
    # Never the CSV-branch message — the whole point of the fix.
    assert "dataset file content changed (different SHA256)" not in result["diffs"]


def test_compute_candidate_lock_warehouse_origin_never_falls_through_to_csv(monkeypatch, tmp_path):
    """A warehouse-origin trial must never call find_symbol_csv/
    dataset_fingerprint — even when a same-symbol CSV happens to exist on
    disk, the CSV path is irrelevant to a warehouse-origin comparison."""
    monkeypatch.setattr(mission_validator, "git_state", lambda: {"commit": "abc123", "dirty": False})

    def _boom(*a, **kw):
        raise AssertionError("find_symbol_csv must not be called for a warehouse-origin trial")

    monkeypatch.setattr(mission_validator, "find_symbol_csv", _boom)
    fingerprint = {
        "git": {"commit": "abc123", "dirty": False},
        "dataset": {"origin": "warehouse", "checksum": "cs1", "timeframe": "H4"},
    }
    trial = {"fingerprint_json": json.dumps(fingerprint)}
    monkeypatch.setattr("storage.market_bars.get_manifest", lambda symbol, timeframe: {"checksum": "cs1"})
    result = _compute_candidate_lock(trial, "EURUSD", tmp_path)
    assert result["matches"] is True


def test_compute_candidate_lock_warehouse_origin_degrades_gracefully_on_d1_failure(monkeypatch):
    monkeypatch.setattr(mission_validator, "git_state", lambda: {"commit": "abc123", "dirty": False})
    fingerprint = {
        "git": {"commit": "abc123", "dirty": False},
        "dataset": {"origin": "warehouse", "checksum": "cs1", "timeframe": "H4"},
    }
    trial = {"fingerprint_json": json.dumps(fingerprint)}

    def _boom(symbol, timeframe):
        raise RuntimeError("D1 unreachable")

    monkeypatch.setattr("storage.market_bars.get_manifest", _boom)
    result = _compute_candidate_lock(trial, "EURUSD", Path("data"))  # must not raise
    assert result["available"] is True
    assert result["matches"] is False  # cs1 vs. no current checksum -> real drift, not silently "matches"


def test_compute_date_overlap_detects_overlap():
    mission = {"config_json": json.dumps({"start": "2020-01-01", "end": "2022-01-01"})}
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("EURUSD",))
    vc = ValidationConfig(**{**vc.__dict__, "start": "2021-06-01", "end": "2023-01-01"})
    result = _compute_date_overlap(mission, vc)
    assert result["overlaps"] is True
    assert result["original_start"] == "2020-01-01"


def test_compute_date_overlap_detects_disjoint_ranges():
    mission = {"config_json": json.dumps({"start": "2020-01-01", "end": "2020-06-01"})}
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("EURUSD",))
    vc = ValidationConfig(**{**vc.__dict__, "start": "2021-01-01", "end": "2022-01-01"})
    result = _compute_date_overlap(mission, vc)
    assert result["overlaps"] is False


def test_compute_date_overlap_full_history_both_sides_overlaps():
    mission = {"config_json": json.dumps({})}
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("EURUSD",))
    result = _compute_date_overlap(mission, vc)
    assert result["overlaps"] is True


# ── MC-5 (2026-08-15 red-team audit): SAME_SYMBOL default start date ───────
# A SAME_SYMBOL validation that never gets an explicit `start` used to load
# the WHOLE dataset (load_symbol_data reads None as "no lower bound"),
# silently including the mission's own training window — not genuinely
# out-of-sample. These pin the fix: default `start` to the day after the
# mission's own training `end`, for SAME_SYMBOL only, only when the
# operator didn't explicitly set one.

def test_default_same_symbol_start_fills_in_day_after_training_end():
    mission = {"config_json": json.dumps({"start": "2020-01-01", "end": "2022-06-30"})}
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("EURUSD",), validation_mode=SAME_SYMBOL)
    result = _default_same_symbol_start(vc, mission)
    assert result.start == "2022-07-01"
    assert result.end == vc.end  # untouched


def test_default_same_symbol_start_never_overrides_an_explicit_start():
    mission = {"config_json": json.dumps({"start": "2020-01-01", "end": "2022-06-30"})}
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("EURUSD",), validation_mode=SAME_SYMBOL)
    vc = ValidationConfig(**{**vc.__dict__, "start": "2019-01-01"})
    result = _default_same_symbol_start(vc, mission)
    assert result.start == "2019-01-01"  # operator's explicit choice wins, even if it overlaps


def test_default_same_symbol_start_leaves_cross_symbol_mode_untouched():
    mission = {"config_json": json.dumps({"start": "2020-01-01", "end": "2022-06-30"})}
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("GBPUSD", "XAUUSD"), validation_mode=CROSS_SYMBOL)
    result = _default_same_symbol_start(vc, mission)
    assert result.start is None  # CROSS_SYMBOL's own out-of-sample safeguard is a different symbol, not a date


def test_default_same_symbol_start_degrades_gracefully_when_mission_has_no_end():
    mission = {"config_json": json.dumps({"start": "2020-01-01"})}  # no "end" key
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("EURUSD",), validation_mode=SAME_SYMBOL)
    result = _default_same_symbol_start(vc, mission)
    assert result.start is None  # nothing to default from — no crash, no fabricated date


def test_default_same_symbol_start_degrades_gracefully_on_unparseable_config():
    mission = {"config_json": "not json"}
    vc = _small_vc(Path("."), "v-x", "m-x", validation_symbols=("EURUSD",), validation_mode=SAME_SYMBOL)
    result = _default_same_symbol_start(vc, mission)
    assert result.start is None


def test_run_validation_same_symbol_defaults_start_and_reports_no_overlap(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    # Build the mission directly (not via _seed_mission_and_trial, whose
    # own upsert_mission() call always uses config={} — and upsert_
    # mission()'s ON CONFLICT clause only ever updates `status`, never
    # `config_json`, so a second call can't retrofit a training end date
    # onto an already-seeded mission) with a real training end date the
    # default has something concrete to key off.
    research_missions.upsert_mission(
        mission_id="val-same-symbol-oos", name="test-mission", sampler="random",
        objective_metric="profit_factor", symbols=["EURUSD"], n_trials_per_symbol=1,
        min_trades=1, seed=42, search_space=mission_runner._search_space_dict(_space()),
        config={"start": "2020-01-01", "end": "2020-01-01"}, status="finished",
    )
    raw_params = {_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0, "sl_atr_multiplier": 2.0}
    research_missions.record_trial(
        mission_id="val-same-symbol-oos", trial_number=0, symbol="EURUSD", state="COMPLETE",
        objective_value=1.2, params=raw_params, metrics={"profit_factor": 1.2}, trades=50,
        error=None, started_at="t", finished_at="t",
    )

    vc = _small_vc(tmp_path, "v-same-symbol-oos", "val-same-symbol-oos",
                    validation_symbols=(), validation_mode=SAME_SYMBOL)
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-same-symbol-oos")
    assert validation is not None
    date_overlap = json.loads(validation["date_overlap_json"])
    assert date_overlap["validation_start"] == "2020-01-02"  # day after training end, not None
    assert date_overlap["overlaps"] is False


def test_run_validation_persists_candidate_lock_and_date_overlap(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-integrity-check")

    vc = _small_vc(tmp_path, "v-integrity-check", "val-integrity-check")
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-integrity-check")
    assert validation is not None
    candidate_lock = json.loads(validation["candidate_lock_json"])
    date_overlap = json.loads(validation["date_overlap_json"])
    # The trial seeded by _seed_mission_and_trial() has no fingerprint_json
    # (record_trial() called without fingerprint=), so this must degrade
    # gracefully rather than crash.
    assert candidate_lock == {"available": False, "note": "Trial predates fingerprint tracking."}
    assert "overlaps" in date_overlap

    reports = list((tmp_path / "reports").glob("mission_validation_v-integrity-check_*.json"))
    report = json.loads(reports[0].read_text())
    assert report["candidate_lock"] == candidate_lock
    assert report["date_overlap"] == date_overlap


def test_rejects_non_complete_trial(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-pruned-check", state="PRUNED")

    vc = _small_vc(tmp_path, "v-pruned-check", "val-pruned-check")
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-pruned-check")
    assert validation["status"] == "failed"
    assert "COMPLETE" in validation["error"]
    assert research_mission_validations.validation_results("v-pruned-check") == []


def test_unknown_mission_fails_cleanly(tmp_path):
    vc = _small_vc(tmp_path, "v-unknown-mission", "does-not-exist")
    run_validation(vc)
    validation = research_mission_validations.get_validation("v-unknown-mission")
    assert validation["status"] == "failed"
    assert "not found" in validation["error"]


# ── Verdict boundaries ─────────────────────────────────────────────────────

def _seed_validation_row(validation_id: str, mission_id: str, symbols: list[str]) -> None:
    research_mission_validations.upsert_validation(
        validation_id=validation_id, mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=symbols, objective_metric="profit_factor", criteria={}, status="running",
    )


def test_verdict_boundaries(monkeypatch):
    """Pins the verdict table directly, independent of real backtests:
    0/1 passing -> NO_EDGE; some-but-not-all (or all but below the
    STRONG_LEAD symbol floor) -> WEAK_LEAD; all passing AND >= the floor
    -> STRONG_LEAD. Two symbols both passing must still cap at WEAK_LEAD
    (pins MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD)."""
    from backtest import mission_validator as mv

    def _fake_eval(symbol, point, vc, *, passing_symbols):
        passed = symbol in passing_symbols
        return {
            "symbol": symbol, "passed": passed,
            "metrics": {}, "monte_carlo": {}, "walk_forward": {}, "robustness": {},
            "criteria_breakdown": {}, "feature_mining": None, "significance": None, "regime_robustness": None, "stability": None, "cost_stress": None, "discovery_score": None, "started_at": "t", "finished_at": "t",
        }

    cases = [
        ([], 3, NO_EDGE),                                             # 0 of 3 passing
        (["EURUSD"], 3, NO_EDGE),                                     # 1 of 3 -> still NO_EDGE (<=1)
        (["EURUSD", "GBPUSD"], 2, WEAK_LEAD),                         # 2 of 2 passing, but below the floor
        (["EURUSD", "GBPUSD", "XAUUSD"], 3, STRONG_LEAD),             # 3 of 3, meets the floor
    ]
    assert MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD == 3

    for passing_symbols, n_total, expected in cases:
        symbols = (["EURUSD", "GBPUSD", "XAUUSD"])[:n_total]
        vid = f"v-verdict-{n_total}-{len(passing_symbols)}"
        mission_id = f"mission-verdict-{vid}"
        _seed_mission_and_trial(mission_id)
        _seed_validation_row(vid, mission_id, symbols)

        vc = ValidationConfig(
            validation_id=vid, mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
            validation_symbols=tuple(symbols), data_dir=Path("."), start=None, end=None,
            validation_mode=CROSS_SYMBOL,
            output_dir=Path("/tmp"),
        )
        monkeypatch.setattr(mv, "_evaluate_symbol",
                             lambda s, p, v, _ps=passing_symbols: _fake_eval(s, p, v, passing_symbols=_ps))
        run_validation(vc)

        validation = research_mission_validations.get_validation(vid)
        assert validation["overall_verdict"] == expected, (symbols, passing_symbols, validation)


# ── Validation Mode Explicitness (Forensic Audit Phase 1, item D, 2026-08-02) ──
# The confirmed gap: nothing tied trial_symbol to validation_symbols — only
# a symbol COUNT was enforced. SAME_SYMBOL is the new default; CROSS_SYMBOL
# preserves today's exact behavior byte-for-byte (test_verdict_boundaries
# above, run with validation_mode=CROSS_SYMBOL, is that regression proof).

def _fake_eval_all(symbol, point, vc, *, passing_symbols):
    passed = symbol in passing_symbols
    return {
        "symbol": symbol, "passed": passed,
        "metrics": {}, "monte_carlo": {}, "walk_forward": {}, "robustness": {},
        "criteria_breakdown": {}, "feature_mining": None, "significance": None, "regime_robustness": None, "stability": None, "cost_stress": None, "discovery_score": None, "started_at": "t", "finished_at": "t",
    }


def test_same_symbol_mode_uses_only_same_symbol_vocabulary(monkeypatch):
    from backtest import mission_validator as mv

    mission_id = "mission-same-symbol-vocab"
    _seed_mission_and_trial(mission_id)
    vc = ValidationConfig(
        validation_id="v-same-vocab", mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=("EURUSD",), data_dir=Path("."), start=None, end=None,
        validation_mode=SAME_SYMBOL, output_dir=Path("/tmp"),
    )
    monkeypatch.setattr(mv, "_evaluate_symbol",
                         lambda s, p, v: _fake_eval_all(s, p, v, passing_symbols=["EURUSD"]))
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-same-vocab")
    assert validation["overall_verdict"] in (SAME_SYMBOL_CONFIRMED, SAME_SYMBOL_NOT_CONFIRMED)
    assert validation["overall_verdict"] not in (NO_EDGE, WEAK_LEAD, STRONG_LEAD)
    assert validation["validation_mode"] == SAME_SYMBOL


def test_same_symbol_confirmed_when_the_one_symbol_passes(monkeypatch):
    from backtest import mission_validator as mv

    mission_id = "mission-same-symbol-confirmed"
    _seed_mission_and_trial(mission_id)
    vc = ValidationConfig(
        validation_id="v-same-confirmed", mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=("EURUSD",), data_dir=Path("."), start=None, end=None,
        validation_mode=SAME_SYMBOL, output_dir=Path("/tmp"),
    )
    monkeypatch.setattr(mv, "_evaluate_symbol",
                         lambda s, p, v: _fake_eval_all(s, p, v, passing_symbols=["EURUSD"]))
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-same-confirmed")
    assert validation["overall_verdict"] == SAME_SYMBOL_CONFIRMED


def test_same_symbol_not_confirmed_when_the_one_symbol_fails(monkeypatch):
    from backtest import mission_validator as mv

    mission_id = "mission-same-symbol-not-confirmed"
    _seed_mission_and_trial(mission_id)
    vc = ValidationConfig(
        validation_id="v-same-not-confirmed", mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=("EURUSD",), data_dir=Path("."), start=None, end=None,
        validation_mode=SAME_SYMBOL, output_dir=Path("/tmp"),
    )
    monkeypatch.setattr(mv, "_evaluate_symbol",
                         lambda s, p, v: _fake_eval_all(s, p, v, passing_symbols=[]))
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-same-not-confirmed")
    assert validation["overall_verdict"] == SAME_SYMBOL_NOT_CONFIRMED


def test_cross_symbol_mode_still_uses_cross_symbol_vocabulary(monkeypatch):
    # The byte-for-byte-unchanged regression proof for CROSS_SYMBOL mode,
    # explicit and standalone (not just implied by test_verdict_boundaries).
    from backtest import mission_validator as mv

    mission_id = "mission-cross-symbol-vocab"
    _seed_mission_and_trial(mission_id)
    vc = ValidationConfig(
        validation_id="v-cross-vocab", mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=("GBPUSD", "XAUUSD"), data_dir=Path("."), start=None, end=None,
        validation_mode=CROSS_SYMBOL, output_dir=Path("/tmp"),
    )
    monkeypatch.setattr(mv, "_evaluate_symbol",
                         lambda s, p, v: _fake_eval_all(s, p, v, passing_symbols=["GBPUSD", "XAUUSD"]))
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-cross-vocab")
    assert validation["overall_verdict"] in (NO_EDGE, WEAK_LEAD, STRONG_LEAD)
    assert validation["overall_verdict"] not in (SAME_SYMBOL_CONFIRMED, SAME_SYMBOL_NOT_CONFIRMED)
    assert validation["validation_mode"] == CROSS_SYMBOL


# ── Evidence Integrity / Multiple Testing (Slice 3, 2026-08-19) ─────────
# _compute_mission_family_significance() binds the pre-existing
# backtest.multiple_testing Bonferroni machinery into the promotion
# decision. Mandatory pre-commit scenarios, verbatim from the operator's
# approval: (1) a candidate drawn from a large (2000-trial) search family
# whose adjusted classification does NOT survive correction must never
# reach STRONG_LEAD/SAME_SYMBOL_CONFIRMED even when every raw criterion
# passes; (2) the mirror case — a small family that DOES survive
# correction — must still be able to reach the top tier; (3) PRUNED/FAIL
# trials must never count toward the family size, only COMPLETE.

from backtest.mission_validator import _compute_mission_family_significance  # noqa: E402


def test_compute_mission_family_significance_counts_only_complete_trials():
    mission_id = "mission-family-count"
    _seed_mission_only(mission_id)
    _record_trial(mission_id, 0, state="COMPLETE")
    _record_trial(mission_id, 1, state="PRUNED")
    _record_trial(mission_id, 2, state="FAIL")
    _record_trial(mission_id, 3, state="COMPLETE")
    _record_trial(mission_id, 4, state="PRUNED")

    trial = research_missions.get_trial(mission_id, 0, "EURUSD")
    result = _compute_mission_family_significance(mission_id, "EURUSD", trial)

    # 5 trials recorded, only 2 are COMPLETE — PRUNED/FAIL must never
    # inflate the family size the Bonferroni correction is computed against.
    assert result["n_trials_in_family"] == 2


def test_compute_mission_family_significance_large_family_does_not_survive_correction():
    """The operator's mandatory scenario #1: 2000 COMPLETE trials in the
    family, the candidate's own raw p-value is nominally significant
    (p < 0.05) but does not survive Bonferroni correction against 2000
    trials (p >= 0.05/2000)."""
    mission_id = "mission-family-2000"
    _seed_mission_only(mission_id)
    # avg_rr=0.3, std_rr=1.0, n=100 -> z=3.0, p ~= 0.0027: nominally
    # significant (< 0.05) but >> bonferroni_alpha(2000) == 0.000025.
    for i in range(2000):
        _record_trial(mission_id, i, state="COMPLETE", avg_rr=0.3, std_rr=1.0, trades=100)

    trial = research_missions.get_trial(mission_id, 0, "EURUSD")
    result = _compute_mission_family_significance(mission_id, "EURUSD", trial)

    assert result["n_trials_in_family"] == 2000
    assert result["raw_p_value"] is not None
    assert result["raw_p_value"] < 0.05  # nominally "significant" by itself
    assert result["bonferroni_alpha"] == pytest.approx(0.05 / 2000)
    assert result["raw_p_value"] >= result["bonferroni_alpha"]  # ...but not after correction
    assert result["classification"] != "SURVIVES_CORRECTION"


def test_compute_mission_family_significance_small_family_survives_correction():
    """Mirror of the above: a small family (n_trials_in_family == 1,
    bonferroni_alpha == 0.05) where the candidate's own p-value clears
    the corrected bar -- SURVIVES_CORRECTION, so the top-tier verdict
    stays reachable."""
    mission_id = "mission-family-small"
    _seed_mission_only(mission_id)
    _record_trial(mission_id, 0, state="COMPLETE", avg_rr=0.5, std_rr=1.0, trades=50)

    trial = research_missions.get_trial(mission_id, 0, "EURUSD")
    result = _compute_mission_family_significance(mission_id, "EURUSD", trial)

    assert result["n_trials_in_family"] == 1
    assert result["bonferroni_alpha"] == pytest.approx(0.05)
    assert result["classification"] == "SURVIVES_CORRECTION"


def test_run_validation_caps_verdict_below_top_tier_when_family_does_not_survive_correction(monkeypatch):
    """Operator's mandatory scenario #1, exercised end-to-end through
    run_validation(): candidate's raw VALIDATION_CRITERIA all PASS on
    every validation symbol (forced via a mocked _evaluate_symbol, same
    technique as test_verdict_boundaries), but the candidate's own
    2000-trial family fails Bonferroni correction. overall_verdict MUST
    NOT be STRONG_LEAD (CROSS_SYMBOL) or SAME_SYMBOL_CONFIRMED
    (SAME_SYMBOL) -- regardless of the raw criteria all passing."""
    from backtest import mission_validator as mv

    mission_id = "mission-family-2000-cross"
    _seed_mission_only(mission_id)
    for i in range(2000):
        _record_trial(mission_id, i, state="COMPLETE", avg_rr=0.3, std_rr=1.0, trades=100)

    symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
    vc = ValidationConfig(
        validation_id="v-family-2000-cross", mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=tuple(symbols), data_dir=Path("."), start=None, end=None,
        validation_mode=CROSS_SYMBOL, output_dir=Path("/tmp"),
    )
    monkeypatch.setattr(mv, "_evaluate_symbol",
                         lambda s, p, v: _fake_eval_all(s, p, v, passing_symbols=symbols))
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-family-2000-cross")
    assert validation["overall_verdict"] == WEAK_LEAD  # capped, never STRONG_LEAD
    family_sig = json.loads(validation["mission_family_significance_json"])
    assert family_sig["n_trials_in_family"] == 2000
    assert family_sig["classification"] != "SURVIVES_CORRECTION"


def test_run_validation_same_symbol_caps_below_confirmed_when_family_does_not_survive_correction(monkeypatch):
    """The SAME_SYMBOL-mode mirror of the CROSS_SYMBOL test above."""
    from backtest import mission_validator as mv

    mission_id = "mission-family-2000-same"
    _seed_mission_only(mission_id)
    for i in range(2000):
        _record_trial(mission_id, i, state="COMPLETE", avg_rr=0.3, std_rr=1.0, trades=100)

    vc = ValidationConfig(
        validation_id="v-family-2000-same", mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=("EURUSD",), data_dir=Path("."), start=None, end=None,
        validation_mode=SAME_SYMBOL, output_dir=Path("/tmp"),
    )
    monkeypatch.setattr(mv, "_evaluate_symbol",
                         lambda s, p, v: _fake_eval_all(s, p, v, passing_symbols=["EURUSD"]))
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-family-2000-same")
    assert validation["overall_verdict"] == SAME_SYMBOL_NOT_CONFIRMED  # capped, never SAME_SYMBOL_CONFIRMED


def test_run_validation_reaches_strong_lead_when_family_survives_correction(monkeypatch):
    """Operator's mandatory scenario #2 (the mirror case): a small family
    that DOES survive correction must still let the top-tier verdict be
    reached when every raw criterion passes -- the gate must not be a
    blanket downgrade."""
    from backtest import mission_validator as mv

    mission_id = "mission-family-survives-cross"
    _seed_mission_and_trial(mission_id)  # default avg_rr/std_rr -> SURVIVES_CORRECTION at n_trials=1

    symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
    vc = ValidationConfig(
        validation_id="v-family-survives-cross", mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=tuple(symbols), data_dir=Path("."), start=None, end=None,
        validation_mode=CROSS_SYMBOL, output_dir=Path("/tmp"),
    )
    monkeypatch.setattr(mv, "_evaluate_symbol",
                         lambda s, p, v: _fake_eval_all(s, p, v, passing_symbols=symbols))
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-family-survives-cross")
    assert validation["overall_verdict"] == STRONG_LEAD
    family_sig = json.loads(validation["mission_family_significance_json"])
    assert family_sig["classification"] == "SURVIVES_CORRECTION"
