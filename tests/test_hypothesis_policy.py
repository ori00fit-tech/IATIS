"""tests/test_hypothesis_policy.py -- pure / structural tests for
backtest/hypothesis_policy.py (Hypothesis Discovery Engine, Phase 6 —
Symbol Policy Registry)."""
from __future__ import annotations

import inspect

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_policy as hpol

_IDENTITY = {"symbol": "EURUSD", "engine": "price_action", "engine_version": "v2", "timeframe": "H1", "risk_preset": "balanced"}


# --- compute_policy_event_id ------------------------------------------


def test_compute_policy_event_id_is_deterministic():
    a = hpol.compute_policy_event_id(_IDENTITY, hpol.GRANTED, promotion_id="P1", actioned_by="alice", reason="ok", chain_marker=None)
    b = hpol.compute_policy_event_id(_IDENTITY, hpol.GRANTED, promotion_id="P1", actioned_by="alice", reason="ok", chain_marker=None)
    assert a == b
    assert a.startswith("POLICY-EVENT-")


def test_compute_policy_event_id_changes_with_chain_marker():
    """This is the mechanism that guarantees a re-grant after a revoke is
    always a NEW event, even with an identical actor/reason."""
    a = hpol.compute_policy_event_id(_IDENTITY, hpol.GRANTED, promotion_id="P1", actioned_by="alice", reason="ok", chain_marker=None)
    b = hpol.compute_policy_event_id(_IDENTITY, hpol.GRANTED, promotion_id="P1", actioned_by="alice", reason="ok", chain_marker="POLICY-EVENT-somerevoke")
    assert a != b


def test_compute_policy_event_id_changes_with_each_meaningful_input():
    base = dict(identity=_IDENTITY, event_type=hpol.GRANTED, promotion_id="P1", actioned_by="alice", reason="ok", chain_marker=None)
    reference = hpol.compute_policy_event_id(base["identity"], base["event_type"], promotion_id=base["promotion_id"], actioned_by=base["actioned_by"], reason=base["reason"], chain_marker=base["chain_marker"])

    variants = [
        dict(base, event_type=hpol.REVOKED),
        dict(base, promotion_id="P2"),
        dict(base, actioned_by="bob"),
        dict(base, reason="a different reason"),
    ]
    for variant in variants:
        other = hpol.compute_policy_event_id(
            variant["identity"], variant["event_type"], promotion_id=variant["promotion_id"],
            actioned_by=variant["actioned_by"], reason=variant["reason"], chain_marker=variant["chain_marker"],
        )
        assert other != reference, f"changing {variant} did not change the computed event_id"


def test_compute_policy_event_id_changes_with_identity():
    a = hpol.compute_policy_event_id(_IDENTITY, hpol.GRANTED, promotion_id="P1", actioned_by="alice", reason="ok", chain_marker=None)
    other_identity = dict(_IDENTITY, symbol="GBPUSD")
    b = hpol.compute_policy_event_id(other_identity, hpol.GRANTED, promotion_id="P1", actioned_by="alice", reason="ok", chain_marker=None)
    assert a != b


# --- input validation (pure -- raises before ever touching storage) ----


def test_grant_policy_rejects_empty_granted_by():
    with pytest.raises(he.HypothesisExecutionError, match="non-empty human identity"):
        hpol.grant_policy("PROMOTION-x", "", "some reason")


def test_grant_policy_rejects_whitespace_only_granted_by():
    with pytest.raises(he.HypothesisExecutionError, match="non-empty human identity"):
        hpol.grant_policy("PROMOTION-x", "   ", "some reason")


@pytest.mark.parametrize("bad_actor", ["system", "scheduler", "auto", "automatic", "bot", "SYSTEM", " Bot ", "unknown", "anonymous"])
def test_grant_policy_rejects_forbidden_actor_names(bad_actor):
    with pytest.raises(he.HypothesisExecutionError, match="not accepted as a real human identity"):
        hpol.grant_policy("PROMOTION-x", bad_actor, "some reason")


def test_grant_policy_rejects_empty_reason():
    with pytest.raises(he.HypothesisExecutionError, match="reason is required"):
        hpol.grant_policy("PROMOTION-x", "alice", "")


def test_revoke_policy_rejects_empty_revoked_by():
    with pytest.raises(he.HypothesisExecutionError, match="non-empty human identity"):
        hpol.revoke_policy("POLICY-EVENT-x", "", "some reason")


def test_revoke_policy_rejects_forbidden_actor_names():
    with pytest.raises(he.HypothesisExecutionError, match="not accepted as a real human identity"):
        hpol.revoke_policy("POLICY-EVENT-x", "auto", "some reason")


def test_revoke_policy_rejects_empty_reason():
    with pytest.raises(he.HypothesisExecutionError, match="reason is required"):
        hpol.revoke_policy("POLICY-EVENT-x", "alice", "")


# --- structural: identity comes from the governed object, never caller -


def test_grant_policy_signature_accepts_only_promotion_and_authorization_fields():
    params = set(inspect.signature(hpol.grant_policy).parameters)
    assert params == {"promotion_id", "granted_by", "reason"}


def test_revoke_policy_signature_accepts_only_grant_event_and_authorization_fields():
    params = set(inspect.signature(hpol.revoke_policy).parameters)
    assert params == {"grant_event_id", "revoked_by", "reason"}


def test_get_symbol_policy_signature_is_exact_identity_only():
    params = set(inspect.signature(hpol.get_symbol_policy).parameters)
    assert params == {"symbol", "engine", "engine_version", "timeframe", "risk_preset"}


def test_grant_policy_reuses_evaluate_promotion_never_reimplements_it():
    source = inspect.getsource(hpol)
    assert "evaluate_promotion(" in source


def test_reuses_the_existing_hypothesis_execution_error_never_a_new_exception_type():
    import re

    source = inspect.getsource(hpol)
    assert "HypothesisExecutionError" in source
    assert re.search(r"^class \w", source, re.MULTILINE) is None


# --- storage module: leaf-level, append-only ----------------------------


def test_storage_hypothesis_policy_never_imports_backtest():
    from storage import hypothesis_policy as storage_module

    source = inspect.getsource(storage_module)
    assert "import backtest" not in source
    assert "from backtest" not in source


def test_no_update_or_delete_path_exists_for_policy_events():
    from storage import hypothesis_policy as storage_module

    public_names = [n for n in dir(storage_module) if not n.startswith("_")]
    forbidden_substrings = ("update_policy", "delete_policy", "edit_policy", "unrevoke")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"storage.hypothesis_policy unexpectedly exposes {name!r}"


# --- no premature live wiring -------------------------------------------


def test_nothing_outside_tests_imports_hypothesis_policy_yet():
    """Phase 6 is deliberately inert with respect to any live decision
    path -- Phase 7 (a separate future spec) is required before this
    registry can influence anything live. Scanning the actual live-path
    entry points (scheduler.py, main.py, execution/) for a premature
    import catches accidental wiring before it ships."""
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))

    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "hypothesis_policy" not in text, f"{path} unexpectedly references hypothesis_policy — Phase 6 must stay inert until Phase 7"


def test_grant_policy_never_touches_config_or_registry():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    with pytest.raises(he.HypothesisExecutionError):
        hpol.grant_policy("PROMOTION-ghost", "alice", "attempted grant on an unknown promotion")

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after a failed grant_policy() call"
