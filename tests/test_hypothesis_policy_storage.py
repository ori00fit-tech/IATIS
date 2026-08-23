"""tests/test_hypothesis_policy_storage.py -- D1 round-trip and
fail-closed governance tests for backtest.hypothesis_policy.grant_policy()
/revoke_policy()/get_symbol_policy() + storage/hypothesis_policy.py
(Hypothesis Discovery Engine, Phase 6 — Symbol Policy Registry).

Exercises the FULL real path: Hypothesis -> Mission (Phase 4) -> Matrix
Cell driven to VALIDATED+SAME_SYMBOL_CONFIRMED -> Promotion (Phase 5) ->
Policy grant/revoke (Phase 6), plus every fail-closed rule from the
operator's finalized Phase 6 contract."""
from __future__ import annotations

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from backtest import hypothesis_policy as hpol
from backtest import hypothesis_promotion as hp
from storage import hypothesis_factory as hf_storage
from storage import hypothesis_policy as hpol_storage
from storage import research_matrix as rm_storage


def _persist_hypothesis(**overrides) -> hf.Hypothesis:
    symbol = overrides.get("symbol", "EURUSD")
    engine = overrides.get("engine", "price_action")
    hyps = hf.generate_hypotheses(
        symbols=[symbol], engines=[engine], timeframes=[overrides.get("timeframe", "H1")],
        risk_presets=[overrides.get("risk_preset", "balanced")],
        engine_versions={engine: (overrides.get("engine_version", "v2"),)},
    )
    h = hyps[0]
    hf_storage.record_hypotheses([h])
    return h


def _promoted_promotion_id(**overrides) -> tuple[hf.Hypothesis, str]:
    """A real Hypothesis -> Mission -> VALIDATED+CONFIRMED Cell -> a
    genuinely PROMOTED promotion record, exactly through the real Phase
    4/5 pipeline."""
    h = _persist_hypothesis(**overrides)
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    assert promotion["decision"] == hp.PROMOTED
    return h, promotion["promotion_id"]


def _identity_of(h: hf.Hypothesis) -> dict:
    return {"symbol": h.symbol, "engine": h.engine, "engine_version": h.engine_version, "timeframe": h.timeframe, "risk_preset": h.risk_preset}


# --- deny-by-default ------------------------------------------------------


def test_get_symbol_policy_is_no_policy_for_a_never_granted_identity():
    assert hpol.get_symbol_policy("EURUSD", "price_action", "v2", "H1", "balanced") == hpol.NO_POLICY


# --- basic grant / revoke lifecycle ---------------------------------------


def test_grant_policy_persists_a_granted_event():
    h, promotion_id = _promoted_promotion_id()
    result = hpol.grant_policy(promotion_id, "alice", "confirmed via Stage B, approved for policy")

    assert result["created"] is True
    assert result["event_type"] == hpol.GRANTED
    assert result["event_id"].startswith("POLICY-EVENT-")
    assert result["symbol"] == h.symbol
    assert result["engine"] == h.engine
    assert result["engine_version"] == h.engine_version
    assert result["timeframe"] == h.timeframe
    assert result["risk_preset"] == h.risk_preset
    assert result["promotion_id"] == promotion_id
    assert result["actioned_by"] == "alice"

    assert hpol.get_symbol_policy(**_identity_of(h)) == hpol.GRANTED


def test_revoke_policy_persists_a_revoked_event_and_updates_current_state():
    h, promotion_id = _promoted_promotion_id()
    grant = hpol.grant_policy(promotion_id, "alice", "initial approval")

    revoke = hpol.revoke_policy(grant["event_id"], "bob", "operator judgment call — revoking pending review")
    assert revoke["created"] is True
    assert revoke["event_type"] == hpol.REVOKED
    assert revoke["revokes_event_id"] == grant["event_id"]

    assert hpol.get_symbol_policy(**_identity_of(h)) == hpol.REVOKED


# --- fail-closed: promotion validity ---------------------------------------


def test_grant_policy_rejects_unknown_promotion_id():
    with pytest.raises(he.HypothesisExecutionError, match="unknown promotion_id"):
        hpol.grant_policy("PROMOTION-ghost", "alice", "no such promotion")


def test_grant_policy_rejects_a_not_promoted_decision():
    h = _persist_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="REJECTED", rejection_reason="did not survive correction")
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    assert promotion["decision"] == hp.NOT_PROMOTED

    with pytest.raises(he.HypothesisExecutionError, match="not PROMOTED"):
        hpol.grant_policy(promotion["promotion_id"], "alice", "attempted grant on a rejected promotion")

    assert hpol_storage.get_latest_policy_event(**_identity_of(h)) is None


def test_grant_policy_rejects_a_blocked_decision():
    h = _persist_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    assert promotion["decision"] == hp.BLOCKED

    with pytest.raises(he.HypothesisExecutionError, match="not PROMOTED"):
        hpol.grant_policy(promotion["promotion_id"], "alice", "attempted grant on a blocked promotion")


# --- fail-closed: never trusts a stale/tampered promotion row -------------


def test_grant_policy_rejects_a_tampered_decision_column(fake_d1):
    """The stored `decision` column says PROMOTED, but the underlying
    cell never actually reached VALIDATED+CONFIRMED -- grant_policy()
    must re-derive the real, current decision (via evaluate_promotion())
    and refuse, never trust the stored column alone."""
    h = _persist_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="CANDIDATE", stage_a_p_value=0.0001)
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    assert promotion["decision"] == hp.BLOCKED

    fake_d1.execute("UPDATE research_hypothesis_promotions SET decision=? WHERE promotion_id=?", (hp.PROMOTED, promotion["promotion_id"]))
    fake_d1.commit()

    with pytest.raises(he.HypothesisExecutionError, match="no longer resolves to PROMOTED"):
        hpol.grant_policy(promotion["promotion_id"], "alice", "attempted grant on a tampered decision")

    assert hpol_storage.get_latest_policy_event(**_identity_of(h)) is None


def test_grant_policy_rejects_a_tampered_promotion_symbol(fake_d1):
    """A real, genuinely PROMOTED promotion whose own stored `symbol`
    column has been tampered to disagree with what the identity chain
    actually re-derives -- must refuse, never silently grant under the
    wrong symbol."""
    h, promotion_id = _promoted_promotion_id()
    fake_d1.execute("UPDATE research_hypothesis_promotions SET symbol=? WHERE promotion_id=?", ("GBPUSD", promotion_id))
    fake_d1.commit()

    with pytest.raises(he.HypothesisExecutionError, match="disagrees with"):
        hpol.grant_policy(promotion_id, "alice", "attempted grant on a tampered symbol")

    assert hpol_storage.get_latest_policy_event(symbol="GBPUSD", engine=h.engine, engine_version=h.engine_version, timeframe=h.timeframe, risk_preset=h.risk_preset) is None
    assert hpol_storage.get_latest_policy_event(**_identity_of(h)) is None


def test_grant_policy_rejects_a_tampered_hypothesis_fingerprint(fake_d1):
    h, promotion_id = _promoted_promotion_id()
    fake_d1.execute("UPDATE research_hypothesis_promotions SET hypothesis_fingerprint=? WHERE promotion_id=?", ("not-the-real-fingerprint", promotion_id))
    fake_d1.commit()

    with pytest.raises(he.HypothesisExecutionError, match="hypothesis_fingerprint"):
        hpol.grant_policy(promotion_id, "alice", "attempted grant on a tampered fingerprint")

    assert hpol_storage.get_latest_policy_event(**_identity_of(h)) is None


# --- no partial write on any refused grant ---------------------------------


def test_no_policy_event_survives_a_refused_grant():
    h = _persist_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)  # BLOCKED

    with pytest.raises(he.HypothesisExecutionError):
        hpol.grant_policy(promotion["promotion_id"], "alice", "should not be written")

    assert hpol_storage.list_policy_events_for_promotion(promotion["promotion_id"]) == []


# --- revoke: fail-closed on inactive/unknown grants ------------------------


def test_revoke_policy_rejects_unknown_grant_event_id():
    with pytest.raises(he.HypothesisExecutionError, match="unknown grant_event_id"):
        hpol.revoke_policy("POLICY-EVENT-ghost", "alice", "no such event")


def test_revoke_policy_rejects_revoking_an_already_revoked_grant_as_a_fresh_action():
    """A DIFFERENT revoked_by/reason than the original revoke targeting an
    already-revoked grant must be refused (not the currently active
    event) -- only a byte-identical retry of the SAME revoke is
    idempotent (see the dedicated retry test below)."""
    h, promotion_id = _promoted_promotion_id()
    grant = hpol.grant_policy(promotion_id, "alice", "initial approval")
    hpol.revoke_policy(grant["event_id"], "bob", "first revoke")

    with pytest.raises(he.HypothesisExecutionError, match="not the currently active grant"):
        hpol.revoke_policy(grant["event_id"], "carol", "a second, different revoke attempt")


def test_revoke_policy_is_idempotent_for_an_exact_retry():
    h, promotion_id = _promoted_promotion_id()
    grant = hpol.grant_policy(promotion_id, "alice", "initial approval")

    first = hpol.revoke_policy(grant["event_id"], "bob", "operator judgment call")
    second = hpol.revoke_policy(grant["event_id"], "bob", "operator judgment call")

    assert first["event_id"] == second["event_id"]
    assert first["created"] is True
    assert second["created"] is False
    assert len(hpol_storage.list_policy_events_for_identity(**_identity_of(h))) == 2  # 1 grant + 1 revoke, never 3


def test_revoke_policy_rejects_revoking_a_non_grant_event():
    h, promotion_id = _promoted_promotion_id()
    grant = hpol.grant_policy(promotion_id, "alice", "initial approval")
    revoke = hpol.revoke_policy(grant["event_id"], "bob", "revoked")

    with pytest.raises(he.HypothesisExecutionError, match="not a GRANTED event"):
        hpol.revoke_policy(revoke["event_id"], "carol", "trying to revoke a revoke")


def test_revoke_policy_rejects_revoking_a_superseded_grant():
    """Grant -> revoke -> re-grant -> attempting to revoke the ORIGINAL
    (now-superseded) grant_event_id must be refused; only the CURRENT
    active grant may be revoked."""
    h, promotion_id = _promoted_promotion_id()
    first_grant = hpol.grant_policy(promotion_id, "alice", "initial approval")
    hpol.revoke_policy(first_grant["event_id"], "bob", "revoked")
    hpol.grant_policy(promotion_id, "carol", "re-approved after review")

    with pytest.raises(he.HypothesisExecutionError, match="not the currently active grant"):
        hpol.revoke_policy(first_grant["event_id"], "dave", "trying to revoke the stale original grant")


# --- re-grant after revoke is always a NEW event, never an update ---------


def test_re_grant_after_revoke_is_a_new_coexisting_event_never_an_update():
    h, promotion_id = _promoted_promotion_id()
    first_grant = hpol.grant_policy(promotion_id, "alice", "initial approval")
    revoke = hpol.revoke_policy(first_grant["event_id"], "bob", "operator judgment call")
    second_grant = hpol.grant_policy(promotion_id, "alice", "initial approval")  # identical actor+reason as the first

    assert second_grant["event_id"] != first_grant["event_id"]
    assert second_grant["created"] is True
    assert hpol.get_symbol_policy(**_identity_of(h)) == hpol.GRANTED

    history = hpol_storage.list_policy_events_for_identity(**_identity_of(h))
    assert {e["event_id"] for e in history} == {first_grant["event_id"], revoke["event_id"], second_grant["event_id"]}
    assert len(history) == 3  # append-only: nothing was ever overwritten


def test_grant_policy_is_idempotent_for_a_plain_retry_with_no_intervening_revoke():
    h, promotion_id = _promoted_promotion_id()
    first = hpol.grant_policy(promotion_id, "alice", "initial approval")
    second = hpol.grant_policy(promotion_id, "alice", "initial approval")

    assert first["event_id"] == second["event_id"]
    assert first["created"] is True
    assert second["created"] is False
    assert len(hpol_storage.list_policy_events_for_identity(**_identity_of(h))) == 1


# --- exact identity: no inheritance, no fallback ---------------------------


def test_policy_grant_does_not_leak_across_a_different_engine_version():
    h, promotion_id = _promoted_promotion_id(engine_version="v2")
    hpol.grant_policy(promotion_id, "alice", "approved for v2")

    assert hpol.get_symbol_policy(h.symbol, h.engine, "v2", h.timeframe, h.risk_preset) == hpol.GRANTED
    assert hpol.get_symbol_policy(h.symbol, h.engine, "v1", h.timeframe, h.risk_preset) == hpol.NO_POLICY


def test_policy_grant_does_not_leak_across_a_different_symbol():
    h_eur, promotion_eur = _promoted_promotion_id(symbol="EURUSD")
    hpol.grant_policy(promotion_eur, "alice", "approved for EURUSD")

    assert hpol.get_symbol_policy(**_identity_of(h_eur)) == hpol.GRANTED
    assert hpol.get_symbol_policy("GBPUSD", h_eur.engine, h_eur.engine_version, h_eur.timeframe, h_eur.risk_preset) == hpol.NO_POLICY


def test_policy_grant_does_not_leak_across_a_different_timeframe():
    h, promotion_id = _promoted_promotion_id(timeframe="H1")
    hpol.grant_policy(promotion_id, "alice", "approved for H1")

    assert hpol.get_symbol_policy(h.symbol, h.engine, h.engine_version, "H1", h.risk_preset) == hpol.GRANTED
    assert hpol.get_symbol_policy(h.symbol, h.engine, h.engine_version, "H4", h.risk_preset) == hpol.NO_POLICY


# --- required forensic reason on every event -------------------------------


def test_grant_and_revoke_both_persist_a_required_reason():
    h, promotion_id = _promoted_promotion_id()
    grant = hpol.grant_policy(promotion_id, "alice", "why this was allowed originally")
    revoke = hpol.revoke_policy(grant["event_id"], "bob", "why this was revoked")

    assert grant["reason"] == "why this was allowed originally"
    assert revoke["reason"] == "why this was revoked"


# --- indexes / config isolation --------------------------------------------


def test_policy_events_table_indexes_exist(fake_d1):
    hpol_storage.get_latest_policy_event("X", "y", "v1", "H1", "balanced")  # triggers _init(con)
    idx_names = {r[0] for r in fake_d1.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert {"idx_rspe_identity", "idx_rspe_promotion", "idx_rspe_revokes"} <= idx_names


def test_grant_and_revoke_never_touch_config_or_registry():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    h, promotion_id = _promoted_promotion_id()
    grant = hpol.grant_policy(promotion_id, "alice", "approved")
    hpol.revoke_policy(grant["event_id"], "bob", "revoked")

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after grant_policy()/revoke_policy()"


def test_grant_and_revoke_never_mutate_the_promotion_or_matrix_cell():
    h, promotion_id = _promoted_promotion_id()
    from storage import hypothesis_promotion as hp_storage

    before_promotion = hp_storage.get_promotion(promotion_id)
    before_cell = rm_storage.get_cell(before_promotion["cell_id"])

    grant = hpol.grant_policy(promotion_id, "alice", "approved")
    hpol.revoke_policy(grant["event_id"], "bob", "revoked")

    assert hp_storage.get_promotion(promotion_id) == before_promotion
    assert rm_storage.get_cell(before_promotion["cell_id"]) == before_cell
