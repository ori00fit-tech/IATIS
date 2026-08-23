"""tests/test_hypothesis_decision_gate.py -- pure / structural tests for
backtest/hypothesis_decision_gate.py (Hypothesis Discovery Engine, Phase
7 — Live Decision Gate, BUILT/TESTED/NOT WIRED)."""
from __future__ import annotations

import inspect

from backtest import hypothesis_decision_gate as gate


# --- structural: no free-form stage overrides, no manufactured attribution -


def test_evaluate_live_decision_signature_is_exact_identity_only():
    params = inspect.signature(gate.evaluate_live_decision).parameters
    assert set(params) == {"symbol", "engine", "engine_version", "timeframe", "risk_preset"}
    # no defaults -- an omitted engine/engine_version can never silently
    # resolve to some fallback identity.
    for name, param in params.items():
        assert param.default is inspect.Parameter.empty, f"{name} must not have a default value"


def test_evaluate_live_decision_accepts_no_stage_override_parameters():
    """The operator's own strengthened §1: 'the orchestration function
    owns the ordering; callers cannot provide or override stage
    results.' There must be no way to call this as evaluate_live_decision
    (..., policy="GRANTED", kill_switch=False)."""
    params = set(inspect.signature(gate.evaluate_live_decision).parameters)
    forbidden = ("policy", "kill_switch", "risk", "risk_verdict", "decision", "pretrade")
    for name in params:
        assert name not in forbidden


def test_never_manufactures_engine_attribution():
    """The operator's own explicit, non-negotiable finding: this module
    must contain no CODE that selects, ranks, or aliases an engine on
    behalf of the live pipeline. Checked as realistic identifier-style
    tokens (underscored, as any real implementation would spell them) so
    the module's own prose explaining why these are forbidden (which
    necessarily uses the human-readable phrasing) doesn't self-trip this
    test."""
    source = inspect.getsource(gate)
    forbidden_identifiers = (
        "highest_scoring", "dominant_engine", "first_contributing", "majority_engine",
        "best_engine", "confluence_panel", "panel_alias", "select_engine",
        "choose_engine", "rank_engine", "pick_engine",
    )
    lowered = source.lower()
    for forbidden in forbidden_identifiers:
        assert forbidden not in lowered, f"backtest.hypothesis_decision_gate references forbidden vocabulary: {forbidden!r}"


def test_reuses_kill_switch_and_policy_lookup_never_reimplements_them():
    source = inspect.getsource(gate)
    assert "storage_kill_switch.get_state(" in source
    assert "storage_hypothesis_policy.get_latest_policy_event(" in source
    # no local re-derivation of GRANTED/REVOKED/NO_POLICY classification
    # logic beyond reading the imported constants
    assert "from backtest.hypothesis_policy import" in source


# --- no premature live wiring ----------------------------------------------


def test_nothing_outside_tests_imports_hypothesis_decision_gate_yet():
    """This gate is deliberately NOT wired into any live decision path —
    scheduler.py's current verdict is not engine-attributable (no single
    (engine, engine_version) identity exists on its report dict), so this
    module must stay completely inert until a separate, later phase
    solves that gap. Scanning the actual live-path entry points catches
    accidental wiring before it ships."""
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))

    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "hypothesis_decision_gate" not in text, (
            f"{path} unexpectedly references hypothesis_decision_gate — Phase 7 must stay unwired "
            f"until engine attribution is solved by a separate, later phase."
        )


# --- storage module: leaf-level, no dedup/update path -----------------------


def test_storage_hypothesis_decision_gate_never_imports_backtest():
    from storage import hypothesis_decision_gate as storage_module

    source = inspect.getsource(storage_module)
    assert "import backtest" not in source
    assert "from backtest" not in source


def test_no_update_or_delete_path_exists_for_decisions():
    from storage import hypothesis_decision_gate as storage_module

    public_names = [n for n in dir(storage_module) if not n.startswith("_")]
    forbidden_substrings = ("update_decision", "delete_decision", "edit_decision")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"storage.hypothesis_decision_gate unexpectedly exposes {name!r}"


def test_never_touches_config_or_registry_files(monkeypatch, tmp_path):
    from pathlib import Path

    from storage import kill_switch as storage_kill_switch

    monkeypatch.setattr(storage_kill_switch, "STATE_PATH", tmp_path / "kill_switch.json")

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    gate.evaluate_live_decision("EURUSD", "price_action", "v2", "H1", "balanced")

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after evaluate_live_decision()"
