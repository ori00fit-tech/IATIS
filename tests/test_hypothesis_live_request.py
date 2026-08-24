"""tests/test_hypothesis_live_request.py -- pure / structural tests for
backtest/hypothesis_live_request.py (Hypothesis Discovery Engine, Phase
8C — Live Identity Request adapter)."""
from __future__ import annotations

import inspect

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_factory as hf
from backtest import hypothesis_live_request as hlr
from storage import hypothesis_factory as hf_storage


def _bundle(**overrides) -> dict:
    base = {
        "name": "Prod4 Confluence Panel",
        "timeframes": ["H4"],
        "engines": ["smc", "price_action", "nnfx", "wyckoff"],
        "indicators": [],
        "context_filters": [],
    }
    base.update(overrides)
    return base


def _persist_confluence_hypothesis(**overrides) -> hf.Hypothesis:
    h = hf.generate_confluence_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], decision_version=overrides.get("decision_version", "v1"),
        bundle=overrides.get("bundle", _bundle()), risk_presets=[overrides.get("risk_preset", "balanced")],
    )[0]
    hf_storage.record_hypotheses([h])
    return h


def _persist_single_engine_hypothesis(**overrides) -> hf.Hypothesis:
    h = hf.generate_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], engines=[overrides.get("engine", "price_action")],
        timeframes=[overrides.get("timeframe", "H1")], risk_presets=[overrides.get("risk_preset", "balanced")],
    )[0]
    hf_storage.record_hypotheses([h])
    return h


# --- resolve_governed_identity ---------------------------------------------


def test_resolve_governed_identity_rejects_unknown_hypothesis_id():
    with pytest.raises(he.HypothesisExecutionError, match="unknown hypothesis_id"):
        hlr.resolve_governed_identity("CONFLUENCE-HYPOTHESIS-ghost")


def test_resolve_governed_identity_supports_single_engine_hypothesis():
    """Phase 8C's own required scenario (operator's explicit Point 1):
    SINGLE_ENGINE / Wyckoff / v2 -> engines_for_computation is exactly
    ['wyckoff'] -- direct, un-guessed attribution, never inferred from a
    bundle and never rejected outright."""
    h = _persist_single_engine_hypothesis(engine="wyckoff", timeframe="H4")
    identity = hlr.resolve_governed_identity(h.hypothesis_id)
    assert identity["decision_type"] == hf.SINGLE_ENGINE
    assert identity["engine"] == "wyckoff"
    assert identity["engine_version"] == h.engine_version
    assert identity["timeframe"] == "H4"
    assert identity["bundle"] is None
    assert identity["bundle_id"] is None
    assert identity["engines_for_computation"] == ["wyckoff"]
    assert identity["timeframes_for_computation"] == ["H4"]


def test_resolve_governed_identity_rejects_unrecognized_decision_type(fake_d1):
    h = _persist_single_engine_hypothesis()
    fake_d1.execute("UPDATE research_hypotheses SET decision_type=? WHERE hypothesis_id=?", ("BOGUS", h.hypothesis_id))
    fake_d1.commit()
    with pytest.raises(he.HypothesisExecutionError, match="unrecognized decision_type"):
        hlr.resolve_governed_identity(h.hypothesis_id)


def test_resolve_governed_identity_returns_the_full_identity():
    h = _persist_confluence_hypothesis()
    identity = hlr.resolve_governed_identity(h.hypothesis_id)
    assert identity["symbol"] == h.symbol
    assert identity["engine"] == hf.CONFLUENCE
    assert identity["engine_version"] == "v1"
    assert identity["timeframe"] == "H4"
    assert identity["risk_preset"] == "balanced"
    assert identity["decision_type"] == hf.CONFLUENCE
    assert identity["bundle_id"] == "Prod4 Confluence Panel"
    assert identity["bundle"] == _bundle()


def test_resolve_governed_identity_rejects_missing_bundle_json(fake_d1):
    h = _persist_confluence_hypothesis()
    fake_d1.execute("UPDATE research_hypotheses SET bundle_json=NULL WHERE hypothesis_id=?", (h.hypothesis_id,))
    fake_d1.commit()
    with pytest.raises(he.HypothesisExecutionError, match="no bundle_json"):
        hlr.resolve_governed_identity(h.hypothesis_id)


# --- build_governed_risk_config (Contract B, locked) ------------------------


@pytest.mark.parametrize("preset,sl,rr,rpt", [
    ("conservative", 2.5, 2.5, 0.005),
    ("balanced", 2.0, 2.0, 0.01),
    ("aggressive", 1.5, 1.5, 0.02),
])
def test_build_governed_risk_config_matches_the_locked_mapping(preset, sl, rr, rpt):
    result = hlr.build_governed_risk_config(preset)
    assert result == {"sl_atr_multiplier": sl, "min_risk_reward": rr, "risk_per_trade_max": rpt}


def test_build_governed_risk_config_rejects_unknown_preset():
    with pytest.raises(he.HypothesisExecutionError, match="unknown risk_preset"):
        hlr.build_governed_risk_config("super_aggressive")


def test_build_governed_risk_config_never_touches_risk_per_trade_min():
    for preset in ("conservative", "balanced", "aggressive"):
        result = hlr.build_governed_risk_config(preset)
        assert "risk_per_trade_min" not in result
        assert "starting_balance" not in result
        assert "max_drawdown_reduce" not in result
        assert "max_exposure" not in result
        assert "pretrade_limits" not in result


# --- build_governed_config ---------------------------------------------------


def _base_config() -> dict:
    return {
        "engines": {"enabled": {"smc": True, "wyckoff": False}, "versions": {"smc": "1.0"}},
        "data": {
            "symbol": "GBPUSD", "timeframes": ["D1", "H4"],
            "twelve_data_symbols": [
                {"internal": "EURUSD", "rr": 2.0, "min_score": 60, "regime_filter": "TRENDING"},
                {"internal": "GBPUSD", "rr": 2.0},
            ],
        },
        "risk": {
            "starting_balance": 10000.0, "max_drawdown_reduce": 0.1, "max_drawdown_stop": 0.15,
            "max_exposure": 0.05, "min_risk_reward": 3.0, "risk_per_trade_max": 0.01,
            "risk_per_trade_min": 0.0025, "sl_atr_multiplier": 2.5,
            "pretrade_limits": {"enabled": True, "max_notional_usd": 50000.0},
        },
        "confluence": {"min_score_to_trade": 60, "min_engines_agreeing": 2},
    }


def test_build_governed_config_enables_only_the_bundle_engines():
    identity = hlr.resolve_governed_identity(_persist_confluence_hypothesis(symbol="EURUSD").hypothesis_id)
    governed = hlr.build_governed_config(identity, _base_config())
    assert governed["engines"]["enabled"] == {"smc": True, "price_action": True, "nnfx": True, "wyckoff": True}


def test_build_governed_config_sets_symbol_and_timeframe_from_the_bundle():
    identity = hlr.resolve_governed_identity(_persist_confluence_hypothesis(symbol="EURUSD").hypothesis_id)
    governed = hlr.build_governed_config(identity, _base_config())
    assert governed["data"]["symbol"] == "EURUSD"
    assert governed["data"]["timeframes"] == ["H4"]


def test_build_governed_config_neutralizes_the_ambient_symbol_rr_override():
    """The single most important proof in Phase 8C: config/symbols.yaml's
    existing per-symbol `rr` (here simulated as EURUSD's own rr=2.0
    entry) must never be able to override a governed risk_preset's own
    min_rr -- main.py's own `symbol_cfg.get("rr") or risk.min_risk_reward`
    must resolve to the SAME governed value regardless of which branch
    actually executes."""
    identity = hlr.resolve_governed_identity(
        _persist_confluence_hypothesis(symbol="EURUSD", risk_preset="conservative").hypothesis_id
    )
    governed = hlr.build_governed_config(identity, _base_config())

    entries = {e["internal"]: e for e in governed["data"]["twelve_data_symbols"]}
    assert entries["EURUSD"]["rr"] == 2.5  # conservative's min_rr, NOT the ambient 2.0
    assert governed["risk"]["min_risk_reward"] == 2.5
    # every OTHER field on that entry is preserved untouched
    assert entries["EURUSD"]["min_score"] == 60
    assert entries["EURUSD"]["regime_filter"] == "TRENDING"
    # a DIFFERENT symbol's own entry is untouched
    assert entries["GBPUSD"]["rr"] == 2.0


def test_build_governed_config_adds_an_entry_when_the_symbol_has_none():
    identity = hlr.resolve_governed_identity(
        _persist_confluence_hypothesis(symbol="XAUUSD", risk_preset="aggressive").hypothesis_id
    )
    governed = hlr.build_governed_config(identity, _base_config())
    entries = {e["internal"]: e for e in governed["data"]["twelve_data_symbols"]}
    assert entries["XAUUSD"]["rr"] == 1.5
    # pre-existing entries for OTHER symbols are still present
    assert "EURUSD" in entries and "GBPUSD" in entries


def test_build_governed_config_preserves_global_safety_fields_untouched():
    identity = hlr.resolve_governed_identity(_persist_confluence_hypothesis(symbol="EURUSD").hypothesis_id)
    base = _base_config()
    governed = hlr.build_governed_config(identity, base)
    assert governed["risk"]["starting_balance"] == base["risk"]["starting_balance"]
    assert governed["risk"]["max_drawdown_reduce"] == base["risk"]["max_drawdown_reduce"]
    assert governed["risk"]["max_drawdown_stop"] == base["risk"]["max_drawdown_stop"]
    assert governed["risk"]["max_exposure"] == base["risk"]["max_exposure"]
    assert governed["risk"]["risk_per_trade_min"] == base["risk"]["risk_per_trade_min"]
    assert governed["risk"]["pretrade_limits"] == base["risk"]["pretrade_limits"]


def test_build_governed_config_overrides_the_three_preset_fields():
    identity = hlr.resolve_governed_identity(
        _persist_confluence_hypothesis(symbol="EURUSD", risk_preset="balanced").hypothesis_id
    )
    governed = hlr.build_governed_config(identity, _base_config())
    assert governed["risk"]["sl_atr_multiplier"] == 2.0
    assert governed["risk"]["min_risk_reward"] == 2.0
    assert governed["risk"]["risk_per_trade_max"] == 0.01


def test_build_governed_config_never_mutates_the_base_config():
    identity = hlr.resolve_governed_identity(_persist_confluence_hypothesis(symbol="EURUSD").hypothesis_id)
    base = _base_config()
    import copy

    before = copy.deepcopy(base)
    hlr.build_governed_config(identity, base)
    assert base == before


def test_build_governed_config_rejects_unknown_risk_preset():
    identity = hlr.resolve_governed_identity(_persist_confluence_hypothesis(symbol="EURUSD").hypothesis_id)
    identity = dict(identity, risk_preset="nonexistent")
    with pytest.raises(he.HypothesisExecutionError, match="unknown risk_preset"):
        hlr.build_governed_config(identity, _base_config())


def test_build_governed_config_single_engine_enables_only_that_one_engine():
    identity = hlr.resolve_governed_identity(
        _persist_single_engine_hypothesis(symbol="EURUSD", engine="wyckoff", timeframe="H4").hypothesis_id
    )
    governed = hlr.build_governed_config(identity, _base_config())
    assert governed["engines"]["enabled"] == {"wyckoff": True}
    assert governed["data"]["timeframes"] == ["H4"]


# --- compute_preset_definition_hash (Contract, Point 3) ---------------------


def test_compute_preset_definition_hash_is_deterministic():
    a = hlr.compute_preset_definition_hash("balanced")
    b = hlr.compute_preset_definition_hash("balanced")
    assert a == b
    assert isinstance(a, str) and len(a) == 16


def test_compute_preset_definition_hash_differs_by_preset():
    hashes = {p: hlr.compute_preset_definition_hash(p) for p in ("conservative", "balanced", "aggressive")}
    assert len(set(hashes.values())) == 3


def test_compute_preset_definition_hash_rejects_unknown_preset():
    with pytest.raises(he.HypothesisExecutionError, match="unknown risk_preset"):
        hlr.compute_preset_definition_hash("super_aggressive")


def test_compute_preset_definition_hash_reflects_the_full_canonical_definition():
    """Built from a canonical, sorted-key/fixed-separator json.dumps of
    RISK_PRESETS[name] -- proven by hand-recomputing it the same way and
    comparing, never by trusting the implementation's own internals."""
    import hashlib
    import json

    from backtest.research_matrix import RISK_PRESETS

    preset = RISK_PRESETS["balanced"]
    expected = hashlib.sha256(
        json.dumps(preset, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    assert hlr.compute_preset_definition_hash("balanced") == expected


def test_compute_preset_definition_hash_does_not_reinterpret_a_past_snapshot(monkeypatch):
    """The operator's own required proof: mutating what a preset name
    means NUMERICALLY (simulated here by monkeypatching RISK_PRESETS
    itself) must never change a hash/snapshot already computed and
    persisted for an earlier decision -- computed once, at record time,
    and never re-derived afterward."""
    import copy

    from backtest import research_matrix as rm

    original_hash = hlr.compute_preset_definition_hash("balanced")
    original_params = hlr.build_governed_risk_config("balanced")

    mutated = copy.deepcopy(rm.RISK_PRESETS)
    mutated["balanced"] = dict(mutated["balanced"], min_rr=999.0)
    monkeypatch.setattr(rm, "RISK_PRESETS", mutated)
    monkeypatch.setattr(hlr, "RISK_PRESETS", mutated)

    new_hash = hlr.compute_preset_definition_hash("balanced")
    assert new_hash != original_hash

    # the EARLIER snapshot's own already-computed values are untouched --
    # nothing re-reads them from RISK_PRESETS after the fact.
    assert original_params["min_risk_reward"] == 2.0


# --- structural: no auto-selection / no enumeration / no execution --------


def test_evaluate_live_identity_request_signature_is_exact():
    params = set(inspect.signature(hlr.evaluate_live_identity_request).parameters)
    assert params == {"hypothesis_id", "base_config"}
    forbidden = ("symbol", "engine", "timeframe", "risk_preset", "policy", "kill_switch")
    for name in params:
        assert name.lower() not in forbidden


def _source_without_module_docstring() -> str:
    """Strips the module's own leading docstring -- it legitimately
    explains, in prose, several patterns this module must NOT contain in
    actual code, which would otherwise self-trip a naive substring scan."""
    source = inspect.getsource(hlr)
    return source.split('"""', 2)[-1]


def test_no_policy_registry_enumeration_anywhere_in_this_module():
    body = _source_without_module_docstring()
    forbidden = ("list_policy_events", "list_promotions(", "get_all_granted", "for grant in", "for policy in")
    for pattern in forbidden:
        assert pattern not in body, f"backtest.hypothesis_live_request unexpectedly references {pattern!r}"


def test_never_imports_or_calls_execution_layer():
    body = _source_without_module_docstring()
    assert "TradeExecutor" not in body
    assert "place_market_order" not in body
    assert "from execution" not in body
    assert "import execution" not in body


def test_reuses_run_pipeline_and_evaluate_live_decision_never_reimplements_them():
    source = inspect.getsource(hlr)
    assert "run_pipeline(" in source
    assert "evaluate_live_decision(" in source


def test_nothing_outside_tests_imports_hypothesis_live_request_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "hypothesis_live_request" not in text, f"{path} unexpectedly references hypothesis_live_request"


def test_never_touches_config_or_registry_files():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    with pytest.raises(he.HypothesisExecutionError):
        hlr.resolve_governed_identity("CONFLUENCE-HYPOTHESIS-ghost")

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p]
