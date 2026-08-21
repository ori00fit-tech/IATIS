"""tests/test_algorithm_inventory.py

Regression coverage for research/algorithm_inventory.py — the RTS 6
Art.5 / PRA SS5/18-style Algorithm & Control Inventory. Two kinds of
tests: (1) against a hand-built, isolated config dict, so assertions
don't drift every time config/engines.yaml's real numbers change; (2)
a handful of sanity checks against the REAL, on-disk config, matching
tests/test_api_contract.py::test_research_engines_reports_frozen_prod4_set's
own established pattern of pinning known, currently-true real facts
(CLAUDE.md's frozen prod4 set).
"""
from __future__ import annotations

import inspect

import pytest

from research.algorithm_inventory import PROD4_ENGINES, build_algorithm_inventory


def _synthetic_config():
    return {
        "engines": {
            "smc_full_spec": False,
            "crypto_positioning_modulator": False,
            "enabled": {
                "smc": True, "price_action": True, "nnfx": True, "wyckoff": True,
                "ict": False, "quant": False, "macro": False,
                "divergence": False, "market_structure": False, "sentiment": False,
            },
            "versions": {"smc": "1.0", "price_action": "1.0", "price_action_v2": "2.0", "wyckoff_v2": "2.0"},
            "thresholds": {
                "smc": {"swing_window": 3, "base_score_max": 65.0},
                "price_action": {"rsi_bull": 55},
            },
        },
        "confluence": {
            "min_engines_agreeing": 2,
            "min_score_to_trade": 58,
            "min_informative_weight_share": 0.6,
            "weights": {
                "smc": 0.202, "price_action": 0.1869, "nnfx": 0.2273, "wyckoff": 0.0707,
                "ict": 0.0657, "quant": 0.0707, "macro": 0.0, "divergence": 0.0606,
                "market_structure": 0.0859, "sentiment": 0.0303,
            },
        },
    }


def test_module_has_no_write_path():
    """Hard-block, matching this codebase's own established convention
    for every read-only research/mission module: no write-marker
    anywhere near a config/registry path."""
    import research.algorithm_inventory as m

    src = inspect.getsource(m)
    banned = ("write_text(", "yaml.safe_dump", "yaml.dump", "json.dump(", 'open(', '"w")', "'w')")
    for marker in banned:
        assert marker not in src, f"unexpected write-like call {marker!r} in research/algorithm_inventory.py"


def test_returns_ten_base_plus_two_variant_entries():
    inv = build_algorithm_inventory(_synthetic_config())
    assert inv["counts"]["total"] == 12
    assert inv["counts"]["base_algorithms"] == 10
    assert inv["counts"]["research_variants"] == 2
    keys = {e["key"] for e in inv["algorithms"]}
    assert keys == {
        "smc", "ict", "nnfx", "price_action", "quant", "wyckoff",
        "divergence", "market_structure", "sentiment", "macro",
        "price_action_v2", "wyckoff_v2",
    }


def test_prod4_set_matches_claudemd_frozen_state():
    assert PROD4_ENGINES == {"smc", "price_action", "nnfx", "wyckoff"}
    inv = build_algorithm_inventory(_synthetic_config())
    by_key = {e["key"]: e for e in inv["algorithms"]}
    for k in PROD4_ENGINES:
        assert by_key[k]["prod4"] is True
        assert by_key[k]["enabled"] is True
    for k in ("ict", "quant", "macro", "divergence", "market_structure", "sentiment"):
        assert by_key[k]["prod4"] is False
        assert by_key[k]["enabled"] is False


def test_variants_are_never_live_eligible_or_enabled():
    inv = build_algorithm_inventory(_synthetic_config())
    by_key = {e["key"]: e for e in inv["algorithms"]}
    for k in ("price_action_v2", "wyckoff_v2"):
        entry = by_key[k]
        assert entry["is_live_eligible"] is False
        assert entry["enabled"] is False
        assert entry["prod4"] is False
        assert entry["live_capital_eligible"] is False
        assert entry["hypothesis_id"] is None
        assert "Mission Center" in entry["reachable_via"]
    assert by_key["price_action_v2"]["variant_of"] == "price_action"
    assert by_key["wyckoff_v2"]["variant_of"] == "wyckoff"


def test_variant_weight_shares_base_engines_weight_slot():
    """Proves the GAP #3 fix's real mapping is what this inventory
    reports — PriceActionV2/WyckoffV2 vote under their base engine's
    confluence weight, never a separate or missing one."""
    inv = build_algorithm_inventory(_synthetic_config())
    by_key = {e["key"]: e for e in inv["algorithms"]}
    assert by_key["price_action_v2"]["confluence_weight"] == by_key["price_action"]["confluence_weight"]
    assert by_key["wyckoff_v2"]["confluence_weight"] == by_key["wyckoff"]["confluence_weight"]


def test_base_algorithm_without_a_hypothesis_would_be_blocked():
    """No engine in _ALL_ENGINES today is missing from ENGINE_HYPOTHESIS_MAP
    (confirmed by the real-config test below), but the approval_basis
    text must still describe the edge_gate.py behavior correctly for an
    engine key that lacks one — this exercises that branch directly."""
    from research.algorithm_inventory import _approval_basis

    text = _approval_basis(None, None, [])
    assert "EdgeNotProvenError" in text


def test_approval_basis_research_status_states_no_live_capital():
    from research.algorithm_inventory import _approval_basis

    text = _approval_basis("H004", "RESEARCH", [])
    assert "RESEARCH" in text
    assert "cannot receive live capital" in text


def test_approval_basis_passed_without_evidence_is_treated_as_research():
    from research.algorithm_inventory import _approval_basis

    text = _approval_basis("H009", "PASSED", ["oos_trades=12 < 300"])
    assert "fails the codified promotion bar" in text
    assert "oos_trades=12 < 300" in text


def test_approval_basis_passed_with_evidence_is_live_capital_eligible():
    from research.algorithm_inventory import _approval_basis

    text = _approval_basis("H009", "PASSED", [])
    assert "qualifying evidence" in text


def test_num_parameters_and_parameters_reflect_real_thresholds_block():
    inv = build_algorithm_inventory(_synthetic_config())
    by_key = {e["key"]: e for e in inv["algorithms"]}
    assert by_key["smc"]["num_parameters"] == 2
    assert by_key["smc"]["parameters"] == {"swing_window": 3, "base_score_max": 65.0}
    # an engine with no thresholds sub-block reports 0, never crashes
    assert by_key["ict"]["num_parameters"] == 0
    assert by_key["ict"]["parameters"] == {}


def test_purpose_text_present_for_every_algorithm():
    inv = build_algorithm_inventory(_synthetic_config())
    for entry in inv["algorithms"]:
        assert entry["purpose"], entry["key"]
        assert len(entry["purpose"]) > 20, entry["key"]


def test_consensus_rules_and_governance_reflect_config():
    inv = build_algorithm_inventory(_synthetic_config())
    assert inv["consensus_rules"] == {
        "min_engines_agreeing": 2, "min_score_to_trade": 58, "min_informative_weight_share": 0.6,
    }
    assert inv["governance"]["allowed_hypothesis_statuses"] == ["PASSED", "RESEARCH"]
    assert inv["governance"]["promotion_criteria"]["min_trades"] == 300
    assert inv["governance"]["promotion_criteria"]["min_oos_pf"] == 1.2


def test_generated_at_is_a_real_iso_timestamp():
    from datetime import datetime

    inv = build_algorithm_inventory(_synthetic_config())
    parsed = datetime.fromisoformat(inv["generated_at"])
    assert parsed.tzinfo is not None


# ── Real, on-disk config sanity checks (mirrors test_api_contract.py's
#    test_research_engines_reports_frozen_prod4_set) ─────────────────────

def test_real_config_prod4_engines_are_enabled_and_carry_a_hypothesis():
    from utils.helpers import load_config

    inv = build_algorithm_inventory(load_config())
    by_key = {e["key"]: e for e in inv["algorithms"]}
    for k in ("smc", "price_action", "nnfx", "wyckoff"):
        entry = by_key[k]
        assert entry["enabled"] is True, entry
        assert entry["prod4"] is True
        # every currently-enabled engine must carry a registered
        # hypothesis, or main.py's own check_edge_gate() would already
        # be refusing to boot (research/edge_gate.py's own docstring).
        assert entry["hypothesis_id"] is not None, entry


def test_real_config_has_exactly_twelve_algorithms_and_zero_are_prod4_variants():
    from utils.helpers import load_config

    inv = build_algorithm_inventory(load_config())
    assert inv["counts"]["total"] == 12
    for entry in inv["algorithms"]:
        if entry["variant_of"] is not None:
            assert entry["prod4"] is False
            assert entry["enabled"] is False
