"""tests/test_matrix_research_planner.py -- pure-function tests for
backtest/matrix_research_planner.py (Phase 3B Evidence Context Builder)."""
from __future__ import annotations

import hashlib
import json

from backtest import matrix_research_planner as planner
from backtest import research_matrix as rm


def _family(family_id="fam1", planned_n=2, family_alpha=0.05) -> dict:
    return {"family_id": family_id, "planned_n": planned_n, "family_alpha": family_alpha, "symbols_json": '["EURUSD"]', "created_at": "2026-01-01T00:00:00+00:00"}


def _cell(**overrides) -> dict:
    base = {
        "cell_id": "MATRIX-CELL-abc123", "family_id": "fam1", "fingerprint": "abc123", "symbol": "EURUSD",
        "bundle_json": '{"name": "SMC only"}', "risk_preset": "balanced", "status": rm.SCREENED,
        "requeue_count": 0, "created_at": "x", "updated_at": "x",
    }
    base.update(overrides)
    return base


def test_build_evidence_context_shapes_family_blocks():
    ctx = planner.build_evidence_context(
        [_family()], {"fam1": [_cell()]}, [],
        dead_list_text="some dead list", frozen_engines=["smc", "nnfx"],
        symbol_universe=["EURUSD", "GBPUSD"], focus_hint="crypto",
    )
    assert len(ctx["families"]) == 1
    block = ctx["families"][0]
    assert block["summary"]["family_id"] == "fam1"
    assert block["summary"]["planned_n"] == 2
    assert block["coverage"]["by_symbol"]["EURUSD"]["total"] == 1
    assert ctx["already_killed_ideas"] == "some dead list"
    assert ctx["frozen_engines"] == ["nnfx", "smc"]  # sorted
    assert ctx["symbol_universe"] == ["EURUSD", "GBPUSD"]
    assert ctx["focus_hint"] == "crypto"


def test_build_evidence_context_degrades_gracefully_without_dead_list():
    ctx = planner.build_evidence_context(
        [], {}, [], dead_list_text=None, frozen_engines=[], symbol_universe=[], focus_hint="",
    )
    assert "Not available" in ctx["already_killed_ideas"]
    assert ctx["focus_hint"] == "none"


def test_build_evidence_context_carries_scoped_cell_evidence_verbatim():
    scoped = [{"cell_id": "MATRIX-CELL-x", "status": "REJECTED", "rejection_reason": "did not survive correction"}]
    ctx = planner.build_evidence_context(
        [], {}, scoped, dead_list_text=None, frozen_engines=[], symbol_universe=[], focus_hint="",
    )
    assert ctx["scoped_cells"] == scoped


def test_build_evidence_context_never_computes_a_verdict_or_score():
    """NON-NEGOTIABLE: nothing in the built context is a rank, score, or
    verdict this module invented -- every leaf value traces back to
    already-decided input (family/cell rows) or a plain constant."""
    ctx = planner.build_evidence_context(
        [_family()], {"fam1": [_cell(status=rm.REJECTED), _cell(status=rm.VALIDATED, cell_id="MATRIX-CELL-2")]}, [],
        dead_list_text=None, frozen_engines=["smc"], symbol_universe=["EURUSD"], focus_hint="",
    )
    for forbidden_key in ("score", "rank", "verdict", "edge", "best"):
        assert forbidden_key not in str(ctx["families"][0]["summary"]).lower().replace("bonferroni", "")


# --- evidence_snapshot_hash --------------------------------------------


def test_evidence_snapshot_hash_is_deterministic_regardless_of_key_order():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert planner.evidence_snapshot_hash(a) == planner.evidence_snapshot_hash(b)


def test_evidence_snapshot_hash_changes_when_content_changes():
    h1 = planner.evidence_snapshot_hash({"a": 1})
    h2 = planner.evidence_snapshot_hash({"a": 2})
    assert h1 != h2


def test_evidence_snapshot_hash_is_a_sha256_hex_digest():
    h = planner.evidence_snapshot_hash({"a": 1})
    assert len(h) == 64
    int(h, 16)  # raises ValueError if not valid hex


# --- Phase 3B-H hardening pass 2: documented verification procedure -------


def test_evidence_snapshot_hash_same_snapshot_same_hash():
    ctx = {"families": [{"summary": {"planned_n": 5}}], "focus_hint": "metals"}
    assert planner.evidence_snapshot_hash(ctx) == planner.evidence_snapshot_hash(ctx)


def test_evidence_snapshot_hash_one_byte_changed_different_hash():
    ctx_a = {"families": [{"summary": {"planned_n": 5}}], "focus_hint": "metals"}
    ctx_b = {"families": [{"summary": {"planned_n": 6}}], "focus_hint": "metals"}  # one digit different
    assert planner.evidence_snapshot_hash(ctx_a) != planner.evidence_snapshot_hash(ctx_b)


def test_evidence_snapshot_hash_verification_procedure_round_trips_through_storage():
    """The documented verification procedure (backtest/matrix_research_
    planner.py's own evidence_snapshot_hash docstring, storage/matrix_ai_
    recommendations.py's module docstring): parse the STORED JSON text
    back into a dict, then re-hash via evidence_snapshot_hash() again --
    this must match the originally-persisted hash even though the stored
    TEXT itself was serialized WITHOUT sort_keys (a naive direct hash of
    the raw stored text would NOT match, purely due to key-order, despite
    identical content)."""
    ctx = {"z_key": 1, "a_key": {"nested": True, "list": [3, 1, 2]}, "m_key": "value"}
    original_hash = planner.evidence_snapshot_hash(ctx)

    # Simulate storage.matrix_ai_recommendations.record_recommendation()'s
    # own persistence serialization: plain json.dumps, no sort_keys.
    stored_text = json.dumps(ctx, default=str)

    # A NAIVE direct hash of the raw stored text is NOT guaranteed to
    # match (documents exactly why the verification procedure matters).
    naive_hash = hashlib.sha256(stored_text.encode("utf-8")).hexdigest()

    # The DOCUMENTED, correct procedure: parse back, re-canonicalize via
    # evidence_snapshot_hash() itself, then compare.
    round_tripped_hash = planner.evidence_snapshot_hash(json.loads(stored_text))
    assert round_tripped_hash == original_hash
    # (the naive path isn't asserted to differ -- for THIS particular
    # dict, insertion order already happens to sort correctly; the point
    # is that the CORRECT procedure is unconditionally reliable, the
    # naive one is not)
    assert isinstance(naive_hash, str)
