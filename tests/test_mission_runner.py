"""
tests/test_mission_runner.py
--------------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — the safety-
critical test suite for backtest/mission_runner.py. Two properties here
are non-negotiable (per the user's explicit condition for building this
feature at all): registry.json is NEVER written by any code path in
this module/optimizer.py/multiple_testing.py, and a killed-and-resumed
mission continues rather than restarting from zero.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import mission_runner, multiple_testing, optimizer
from backtest.mission_runner import MissionConfig, run_mission
from backtest.optimizer import MissionSearchSpace
from storage import research_missions

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "research" / "results" / "registry.json"
CONFIG_YAML_PATH = REPO_ROOT / "config.yaml"
ENGINES_YAML_PATH = REPO_ROOT / "config" / "engines.yaml"


def _ohlcv(n: int, seed: int = 7, trend: float = 0.10) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


def _write_dataset(tmp_path: Path, symbol: str = "EURUSD", n: int = 2400) -> None:
    _ohlcv(n).to_csv(tmp_path / f"{symbol}_H1_2y.csv")


def _small_config(tmp_path: Path, mission_id: str, n_trials: int = 2, symbols=("EURUSD",)) -> MissionConfig:
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action", "smc", "wyckoff"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    return MissionConfig(
        mission_id=mission_id, name="test-mission", symbols=tuple(symbols),
        data_dir=tmp_path, start=None, end=None, sampler="random",
        n_trials_per_symbol=n_trials, objective_metric="profit_factor",
        min_trades=1, seed=42, search_space=space, oos_holdout_fraction=None,
        max_wall_clock_seconds=None, output_dir=tmp_path / "reports",
    )


# ── Hard-block safety tests ──────────────────────────────────────────────

def test_no_module_contains_a_write_call_near_registry_json():
    """Source-level guard: none of the three modules powering a mission
    should contain a file-write call anywhere near a "registry" mention —
    docstrings/comments legitimately explain the safety guarantee by
    NAME (so a bare substring-absence check would false-positive on this
    module's own documentation), but no write-shaped call
    (write_text/json.dump/yaml.*dump/open(...,"w")) may ever appear on a
    line mentioning "registry" in any of these three files."""
    write_markers = ("write_text", "json.dump", "yaml.dump", "yaml.safe_dump", '"w")', "'w')")
    for module in (mission_runner, optimizer, multiple_testing):
        source = inspect.getsource(module)
        for line in source.splitlines():
            if "registry" in line.lower():
                for marker in write_markers:
                    assert marker not in line, (
                        f"{module.__name__}: possible write near a registry "
                        f"reference: {line!r}"
                    )


def test_registry_json_byte_identical_before_and_after_mission_run(tmp_path):
    _write_dataset(tmp_path)
    before = REGISTRY_PATH.read_bytes()
    before_mtime = REGISTRY_PATH.stat().st_mtime

    mc = _small_config(tmp_path, "mission-registry-check")
    run_mission(mc)

    after = REGISTRY_PATH.read_bytes()
    after_mtime = REGISTRY_PATH.stat().st_mtime
    assert after == before
    assert after_mtime == before_mtime


def test_never_touches_config_yaml_or_engines_yaml(tmp_path):
    _write_dataset(tmp_path)
    before_config = CONFIG_YAML_PATH.read_bytes()
    before_engines = ENGINES_YAML_PATH.read_bytes()

    mc = _small_config(tmp_path, "mission-config-check")
    run_mission(mc)

    assert CONFIG_YAML_PATH.read_bytes() == before_config
    assert ENGINES_YAML_PATH.read_bytes() == before_engines


# ── Reproducibility fingerprint (Diagnostic Infrastructure Phase 1) ────────

def test_trials_carry_a_real_fingerprint_computed_once_per_symbol(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    from research import manifest as research_manifest

    call_count = {"n": 0}
    real_dataset_fingerprint = research_manifest.dataset_fingerprint

    def _counting_fingerprint(*args, **kwargs):
        call_count["n"] += 1
        return real_dataset_fingerprint(*args, **kwargs)

    monkeypatch.setattr(mission_runner, "dataset_fingerprint", _counting_fingerprint)

    mc = _small_config(tmp_path, "mission-fingerprint-check", n_trials=3)
    run_mission(mc)

    recorded = research_missions.existing_trials("mission-fingerprint-check", "EURUSD")
    assert len(recorded) == 3
    # dataset_fingerprint() (the SHA256 read) is only called ONCE per
    # symbol — not once per trial — even though every trial's row carries
    # a non-null, real fingerprint.
    assert call_count["n"] == 1
    fingerprints = [json.loads(r["fingerprint_json"]) for r in recorded]
    for fp in fingerprints:
        assert fp["git"]["commit"]
        assert fp["dataset"]["sha256"]
    # Identical dict content reused across every trial of the same symbol.
    assert fingerprints[0] == fingerprints[1] == fingerprints[2]


def test_recorded_trials_carry_gate_rejections_breakdown(tmp_path):
    """Prune Forensic Audit (2026-08-04, reports/forensic/21_...) regression:
    every recorded trial's metrics_json must carry gate_rejections/
    context_rejections/indicator_rejections — before this fix, Mission
    Center discarded these entirely, so a low-trade-count trial (pruned or
    not) could never be diagnosed as "quorum failure" vs "neutral bias"
    vs "score" vs a filter, only its final trade count."""
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-gate-rejections-check", n_trials=2)
    run_mission(mc)

    recorded = research_missions.existing_trials("mission-gate-rejections-check", "EURUSD")
    assert len(recorded) == 2
    for row in recorded:
        metrics = json.loads(row["metrics_json"])
        assert "gate_rejections" in metrics
        assert "context_rejections" in metrics
        assert "indicator_rejections" in metrics
        assert isinstance(metrics["gate_rejections"], dict)


# ── Resume ────────────────────────────────────────────────────────────────

def test_resume_skips_already_completed_trials(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    mission_id = "mission-resume-check"

    real_evaluate_point = mission_runner.evaluate_point
    call_count = {"n": 0}

    def _kill_after_two(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] > 2:
            raise KeyboardInterrupt("simulated process kill")
        return real_evaluate_point(*args, **kwargs)

    monkeypatch.setattr(mission_runner, "evaluate_point", _kill_after_two)

    mc = _small_config(tmp_path, mission_id, n_trials=5)
    with pytest.raises(KeyboardInterrupt):
        run_mission(mc)

    recorded = research_missions.existing_trials(mission_id, "EURUSD")
    assert len(recorded) == 2
    assert [r["trial_number"] for r in recorded] == [0, 1]

    # Restore the real function and resume with the identical mission_id —
    # must continue from trial 2, not restart from 0.
    monkeypatch.setattr(mission_runner, "evaluate_point", real_evaluate_point)
    call_count["n"] = 0
    mc_resumed = _small_config(tmp_path, mission_id, n_trials=5)
    run_mission(mc_resumed)

    final = research_missions.existing_trials(mission_id, "EURUSD")
    assert len(final) == 5
    assert [r["trial_number"] for r in final] == [0, 1, 2, 3, 4]

    mission = research_missions.get_mission(mission_id)
    assert mission["status"] == "finished"


def test_end_to_end_mission_run_with_engine_variant_choices(tmp_path):
    """Track C (Phase 4) — a real mission run with a non-trivial
    engine_variant_choices dimension must complete successfully and
    record trials whose stored params_json actually varies
    __engine_variants_idx (not just always index 0)."""
    _write_dataset(tmp_path)
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action", "wyckoff"),),
        indicator_set_choices=((),),
        engine_variant_choices=({}, {"price_action": "v2"}, {"wyckoff": "v2"}),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    mc = MissionConfig(
        mission_id="mission-engine-variant-e2e", name="test-mission", symbols=("EURUSD",),
        data_dir=tmp_path, start=None, end=None, sampler="random",
        n_trials_per_symbol=6, objective_metric="profit_factor",
        min_trades=1, seed=42, search_space=space, oos_holdout_fraction=None,
        max_wall_clock_seconds=None, output_dir=tmp_path / "reports",
    )
    run_mission(mc)

    trials = research_missions.existing_trials("mission-engine-variant-e2e", "EURUSD")
    assert len(trials) == 6
    seen_variant_idx = {json.loads(t["params_json"]).get("__engine_variants_idx") for t in trials}
    assert len(seen_variant_idx) > 1, "engine_variant_choices was never actually varied across trials"

    mission = research_missions.get_mission("mission-engine-variant-e2e")
    assert mission["status"] == "finished"


def test_confluence_overrides_end_to_end_unblocks_a_single_engine_mission(tmp_path):
    """Mission Center Research Rigor Phase 1 — the operator-reported bug:
    a real mission with only ONE engine enabled produces real, non-PRUNED
    trades when confluence_overrides lowers the quorum, wired all the way
    through MissionConfig -> MissionSearchSpace -> resolve_point ->
    evaluate_point -> build_engine_config_override."""
    _write_dataset(tmp_path)
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx",),),
        indicator_set_choices=((),),
        confluence_overrides={"min_engines_agreeing": 1},
    )
    mc = MissionConfig(
        mission_id="mission-confluence-override-e2e", name="test-mission", symbols=("EURUSD",),
        data_dir=tmp_path, start=None, end=None, sampler="random",
        n_trials_per_symbol=1, objective_metric="profit_factor",
        min_trades=1, seed=42, search_space=space, oos_holdout_fraction=None,
        max_wall_clock_seconds=None, output_dir=tmp_path / "reports",
    )
    run_mission(mc)

    trials = research_missions.existing_trials("mission-confluence-override-e2e", "EURUSD")
    assert len(trials) == 1
    assert trials[0]["state"] == "COMPLETE"
    assert trials[0]["trades"] > 0


def test_duplicate_configuration_detection_skips_reevaluation(tmp_path, monkeypatch):
    """Every trial in this space resolves to the IDENTICAL effective
    configuration (a single timeframe/engine choice, and 3 indicator_set_
    choices that are all the same empty tuple, so which index gets
    sampled makes no difference) — the 2nd and 3rd trials must be
    detected as duplicates and never call evaluate_point() again."""
    _write_dataset(tmp_path)
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action", "smc", "wyckoff"),),
        indicator_set_choices=((), (), ()),
    )
    mc = MissionConfig(
        mission_id="mission-duplicate-check", name="test-mission", symbols=("EURUSD",),
        data_dir=tmp_path, start=None, end=None, sampler="random",
        n_trials_per_symbol=3, objective_metric="profit_factor",
        min_trades=1, seed=42, search_space=space, oos_holdout_fraction=None,
        max_wall_clock_seconds=None, output_dir=tmp_path / "reports",
    )

    real_evaluate_point = mission_runner.evaluate_point
    call_count = {"n": 0}

    def _counting_evaluate_point(*args, **kwargs):
        call_count["n"] += 1
        return real_evaluate_point(*args, **kwargs)

    monkeypatch.setattr(mission_runner, "evaluate_point", _counting_evaluate_point)
    run_mission(mc)

    assert call_count["n"] == 1  # the 2nd and 3rd trials' configs were duplicates — never re-evaluated

    trials = research_missions.existing_trials("mission-duplicate-check", "EURUSD")
    assert len(trials) == 3
    states = [t["state"] for t in trials]
    assert states.count("DUPLICATE") == 2
    original = next(t for t in trials if t["state"] != "DUPLICATE")
    for dup in (t for t in trials if t["state"] == "DUPLICATE"):
        assert dup["objective_value"] == original["objective_value"]
        assert dup["trades"] == original["trades"]


def test_duplicate_replay_on_resume_never_keyerrors(tmp_path):
    """A DUPLICATE row has no Optuna TrialState of its own — replaying it
    on resume must map to the underlying COMPLETE/PRUNED outcome its
    cached value implies, not KeyError on optuna.trial.TrialState['DUPLICATE']."""
    _write_dataset(tmp_path)
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action", "smc", "wyckoff"),),
        indicator_set_choices=((), (), ()),
    )
    mc = MissionConfig(
        mission_id="mission-duplicate-resume", name="test-mission", symbols=("EURUSD",),
        data_dir=tmp_path, start=None, end=None, sampler="random",
        n_trials_per_symbol=3, objective_metric="profit_factor",
        min_trades=1, seed=42, search_space=space, oos_holdout_fraction=None,
        max_wall_clock_seconds=None, output_dir=tmp_path / "reports",
    )
    run_mission(mc)
    assert any(t["state"] == "DUPLICATE" for t in research_missions.existing_trials("mission-duplicate-resume", "EURUSD"))

    # Resume with a higher target — must not raise, must reach the new target.
    mc_resumed = MissionConfig(**{**mc.__dict__, "n_trials_per_symbol": 4})
    run_mission(mc_resumed)  # would raise KeyError inside create_trial() before the fix
    assert len(research_missions.existing_trials("mission-duplicate-resume", "EURUSD")) == 4


def test_abandoned_trial_attempt_is_recorded_and_counted(tmp_path, monkeypatch):
    """A trial that starts evaluating but crashes before record_trial()
    leaves an orphaned attempt marker — count_orphaned_attempts() must
    see it, mirroring test_transient_record_trial_write_failure_does_not_
    abort_the_mission's own crash-simulation shape."""
    _write_dataset(tmp_path)
    mission_id = "mission-abandoned-attempt-check"
    mc = _small_config(tmp_path, mission_id, n_trials=1)

    real_record_trial = research_missions.record_trial
    monkeypatch.setattr(
        research_missions, "record_trial",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated crash before D1 write")),
    )
    run_mission(mc)  # non-fatal — the mission must still finish
    monkeypatch.setattr(research_missions, "record_trial", real_record_trial)

    assert research_missions.count_orphaned_attempts(mission_id, "EURUSD") == 1
    assert research_missions.existing_trials(mission_id, "EURUSD") == []


# ── Graceful stop / isolation ─────────────────────────────────────────────

def test_max_wall_clock_seconds_stops_gracefully_and_marks_finished(tmp_path):
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-budget-check", n_trials=1000)
    mc = MissionConfig(**{**mc.__dict__, "max_wall_clock_seconds": 0.0})
    run_mission(mc)

    mission = research_missions.get_mission("mission-budget-check")
    assert mission["status"] == "finished"
    recorded = research_missions.existing_trials("mission-budget-check", "EURUSD")
    assert len(recorded) < 1000


def test_one_symbol_failure_does_not_abort_other_symbols(tmp_path):
    # Only EURUSD gets a dataset written — GBPUSD is missing.
    _write_dataset(tmp_path, symbol="EURUSD")
    mc = _small_config(tmp_path, "mission-isolation-check", symbols=("EURUSD", "GBPUSD"))
    run_mission(mc)

    eurusd_trials = research_missions.existing_trials("mission-isolation-check", "EURUSD")
    gbpusd_trials = research_missions.existing_trials("mission-isolation-check", "GBPUSD")
    assert len(eurusd_trials) == 2
    assert len(gbpusd_trials) == 0

    mission = research_missions.get_mission("mission-isolation-check")
    assert mission["status"] == "finished"


def test_transient_record_trial_write_failure_does_not_abort_the_mission(tmp_path, monkeypatch):
    """Regression (found 2026-08-04 while investigating an operator-reported
    150-trial mission that did not complete): before this fix, an
    unguarded research_missions.record_trial() call meant a single
    transient D1 write hiccup on ANY trial propagated all the way up
    through run_mission()'s outer try/except, marking the ENTIRE mission
    'failed' and abandoning every trial after the one that hit the
    hiccup — even though most trials up to that point had succeeded.
    record_trial() must be guarded the same way evaluate_point() already
    is: one trial's write failure is logged and skipped, never fatal to
    the mission."""
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-record-trial-hiccup-check", n_trials=5)

    from backtest import mission_runner as m
    real_record_trial = research_missions.record_trial
    call_count = {"n": 0}

    def flaky_record_trial(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("D1 proxy timeout (transient)")
        return real_record_trial(*args, **kwargs)

    monkeypatch.setattr(m.research_missions, "record_trial", flaky_record_trial)

    run_mission(mc)

    mission = research_missions.get_mission("mission-record-trial-hiccup-check")
    assert mission["status"] == "finished"  # not "failed" — the whole run must not abort
    recorded = research_missions.existing_trials("mission-record-trial-hiccup-check", "EURUSD")
    # 4 of the 5 trials landed — the 2nd one's write failed and was
    # skipped (not retried, matching the exact same "log and move on"
    # semantics evaluate_point()'s own FAIL path already has).
    assert len(recorded) == 4
    assert call_count["n"] == 5  # every trial still attempted its write


def test_transient_cancellation_check_failure_does_not_mark_a_successful_mission_failed(tmp_path, monkeypatch):
    """Regression (found live, 2026-07-30): a mission whose every trial
    completed successfully was shown as status='failed' in the dashboard.
    Root cause: the cancellation-check read (get_mission, a real D1
    round-trip) was unguarded — a transient read error there propagated
    all the way up and overwrote the final status, even though nothing
    about the actual trials had gone wrong. _is_cancelled() must fail
    OPEN (treat a read error as "not cancelled"), never poison the run."""
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-transient-check-fail")

    from backtest import mission_runner as m
    real_get_mission = research_missions.get_mission

    def flaky_get_mission(mission_id):
        raise RuntimeError("D1 proxy timeout (transient)")

    monkeypatch.setattr(m.research_missions, "get_mission", flaky_get_mission)
    try:
        run_mission(mc)
    finally:
        monkeypatch.setattr(m.research_missions, "get_mission", real_get_mission)

    trials = research_missions.existing_trials("mission-transient-check-fail", "EURUSD")
    assert len(trials) == 2
    assert all(t["state"] == "COMPLETE" for t in trials)

    mission = research_missions.get_mission("mission-transient-check-fail")
    assert mission["status"] == "finished"
    assert mission["error"] is None


# ── Report generation ──────────────────────────────────────────────────────

def test_mission_report_written_with_exploratory_banner(tmp_path):
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-report-check")
    run_mission(mc)

    reports = list((tmp_path / "reports").glob("mission_mission-report-check_*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text())
    assert "EXPLORATORY" in payload["note"]
    assert "EXPLORATORY" in payload["mission_wide_significance"]["banner"]
    assert "EURUSD" in payload["per_symbol"]
    assert len(payload["per_symbol"]["EURUSD"]["trials"]) == 2


# ── CLI wiring ──────────────────────────────────────────────────────────────

def test_cli_wires_all_json_flags_into_mission_search_space(monkeypatch, tmp_path):
    import sys

    captured: dict = {}

    def fake_run_mission(mc: MissionConfig) -> None:
        captured["mc"] = mc

    monkeypatch.setattr(mission_runner, "run_mission", fake_run_mission)
    monkeypatch.setattr(sys, "argv", [
        "mission_runner.py",
        "--mission-id", "cli-test-mission",
        "--symbols", "eurusd", "gbpusd",
        "--data-dir", str(tmp_path),
        "--sampler", "random",
        "--n-trials-per-symbol", "3",
        "--objective-metric", "sharpe_ratio",
        "--min-trades", "5",
        "--seed", "99",
        "--timeframes-choices", '[["H1"],["H4","D1","H1"]]',
        "--engine-set-choices", '[["nnfx","price_action"]]',
        "--indicator-set-choices", "[[]]",
        "--context-filter-set-choices",
        '[[],[{"name":"direction","mode":"entry_filter","params":{"allowed":["BULLISH"]},"weight":0}]]',
        "--risk-param-ranges", '{"sl_atr_multiplier":[1.0,3.0]}',
        "--output-dir", str(tmp_path / "reports"),
    ])
    mission_runner.main()

    mc = captured["mc"]
    assert mc.mission_id == "cli-test-mission"
    assert mc.symbols == ("EURUSD", "GBPUSD")
    assert mc.sampler == "random"
    assert mc.n_trials_per_symbol == 3
    assert mc.objective_metric == "sharpe_ratio"
    assert mc.min_trades == 5
    assert mc.seed == 99
    assert mc.search_space.timeframes_choices == (("H1",), ("H4", "D1", "H1"))
    assert mc.search_space.engine_set_choices == (("nnfx", "price_action"),)
    assert mc.search_space.risk_param_ranges == {"sl_atr_multiplier": (1.0, 3.0)}
    assert mc.search_space.context_filter_set_choices == (
        (),
        ({"name": "direction", "mode": "entry_filter", "params": {"allowed": ["BULLISH"]}, "weight": 0},),
    )
    assert mc.search_space.hypothesis_bundle_choices is None
    # Track C (Phase 4) — --engine-variant-choices omitted -> default (all-v1)
    assert mc.search_space.engine_variant_choices == ({},)


def test_cli_wires_engine_variant_choices(monkeypatch, tmp_path):
    import sys

    captured: dict = {}

    def fake_run_mission(mc: MissionConfig) -> None:
        captured["mc"] = mc

    monkeypatch.setattr(mission_runner, "run_mission", fake_run_mission)
    monkeypatch.setattr(sys, "argv", [
        "mission_runner.py",
        "--mission-id", "cli-engine-variant-test",
        "--symbols", "eurusd",
        "--data-dir", str(tmp_path),
        "--sampler", "random",
        "--n-trials-per-symbol", "3",
        "--timeframes-choices", '[["H1"]]',
        "--engine-set-choices", '[["nnfx","price_action","wyckoff"]]',
        "--engine-variant-choices", '[{},{"price_action":"v2"},{"wyckoff":"v2"}]',
        "--risk-param-ranges", '{"sl_atr_multiplier":[1.0,3.0]}',
        "--output-dir", str(tmp_path / "reports"),
    ])
    mission_runner.main()

    mc = captured["mc"]
    assert mc.search_space.engine_variant_choices == ({}, {"price_action": "v2"}, {"wyckoff": "v2"})


def test_cli_wires_hypothesis_bundle_choices(monkeypatch, tmp_path):
    import json
    import sys

    captured: dict = {}

    def fake_run_mission(mc: MissionConfig) -> None:
        captured["mc"] = mc

    monkeypatch.setattr(mission_runner, "run_mission", fake_run_mission)
    bundles = [
        {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []},
        {"name": "NNFX + Wyckoff", "timeframes": ["H4"], "engines": ["nnfx", "wyckoff"], "indicators": [], "context_filters": []},
    ]
    monkeypatch.setattr(sys, "argv", [
        "mission_runner.py",
        "--mission-id", "cli-hypothesis-test",
        "--symbols", "eurusd",
        "--data-dir", str(tmp_path),
        "--sampler", "random",
        "--n-trials-per-symbol", "3",
        "--timeframes-choices", '[["H1"]]',
        "--engine-set-choices", '[["nnfx"]]',
        "--hypothesis-bundle-choices", json.dumps(bundles),
        "--risk-param-ranges", '{"sl_atr_multiplier":[1.0,3.0]}',
        "--output-dir", str(tmp_path / "reports"),
    ])
    mission_runner.main()

    mc = captured["mc"]
    assert mc.search_space.hypothesis_bundle_choices == tuple(bundles)


def test_mission_run_with_hypothesis_bundles_uses_both_bundles(tmp_path):
    # The authoritative live-run proof — 2 hypothesis bundles with
    # genuinely different engine sets, run through the real mission
    # orchestrator on synthetic data, must produce trials for BOTH.
    _write_dataset(tmp_path)
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),), engine_set_choices=(("nnfx",),),
        indicator_set_choices=((),),
        hypothesis_bundle_choices=(
            {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []},
            {"name": "NNFX + Wyckoff + Price Action", "timeframes": ["H1"], "engines": ["nnfx", "wyckoff", "price_action"], "indicators": [], "context_filters": []},
        ),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    mc = MissionConfig(
        mission_id="mission-hypothesis-bundle-run", name="test-hypothesis-mission",
        symbols=("EURUSD",), data_dir=tmp_path, start=None, end=None,
        sampler="random", n_trials_per_symbol=12, objective_metric="profit_factor",
        min_trades=1, seed=42, search_space=space, oos_holdout_fraction=None,
        max_wall_clock_seconds=None, output_dir=tmp_path / "reports",
    )
    run_mission(mc)

    trials = research_missions.existing_trials("mission-hypothesis-bundle-run", "EURUSD")
    import json as _json
    seen_bundle_indices = {_json.loads(t["params_json"])["__hypothesis_idx"] for t in trials}
    assert seen_bundle_indices == {0, 1}  # both hypotheses actually got tried
