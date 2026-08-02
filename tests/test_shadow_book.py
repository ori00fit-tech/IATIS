"""
tests/test_shadow_book.py
--------------------------
Tier-2 measurement layer: the shadow book (counterfactuals for rejected
signals), gate classification, backtest swap costs, and the pre-registered
forward-review rules. Offline via the fake-D1 fixture.
"""

from __future__ import annotations

import pandas as pd
import pytest

from storage import shadow_book as sb


CONFIG = {
    "data": {"twelve_data_symbols": [
        {"internal": "EURUSD", "rr": 2.0, "enabled": True},
        {"internal": "XAUUSD", "rr": 2.5, "enabled": True},
    ]},
    "risk": {"min_risk_reward": 2.0, "sl_atr_multiplier": 2.5},
}


def _report(symbol="EURUSD", bias="BULLISH", price=1.0850,
            bar_high=1.0862, bar_low=1.0842, fail_reasons=None, **extra):
    r = {
        "final_verdict": "NO_TRADE",
        "symbol": symbol,
        "current_price": price,
        "bar_high": bar_high,
        "bar_low": bar_low,
        "confluence": {
            "vote": {"winning_bias": bias},
            "score": 55.0,
            "fail_reasons": (fail_reasons if fail_reasons is not None
                             else ["Only 1 engine(s) agree, minimum required is 2"]),
            "contradiction": {"blocked": False},
            "reversal_veto": {"vetoed": False},
        },
        "risk": {"passed": None},
        "news": {"blackout_active": False},
        "summary": "NO_TRADE: x",
    }
    r.update(extra)
    return r


# ── Logging & levels ─────────────────────────────────────────────────────

def test_rejected_directional_decision_creates_shadow_with_system_levels():
    sid = sb.log_shadow_signal(_report(), CONFIG)
    assert sid and sid.endswith("_EURUSD")
    s = sb.get_open_shadows()[0]
    # entry = close; SL = entry - bar_range*2.5; TP at RR 2.0
    bar_range = 1.0862 - 1.0842
    assert s["entry_price"] == pytest.approx(1.0850)
    assert s["stop_loss"] == pytest.approx(1.0850 - bar_range * 2.5)
    assert s["take_profit"] == pytest.approx(1.0850 + bar_range * 2.5 * 2.0)
    assert s["primary_gate"] == "quorum"


def test_neutral_or_executed_decisions_are_not_shadowed():
    assert sb.log_shadow_signal(_report(bias="NEUTRAL"), CONFIG) is None
    r = _report()
    r["final_verdict"] = "EXECUTE"
    assert sb.log_shadow_signal(r, CONFIG) is None
    assert sb.get_open_shadows() == []


def test_gate_classification_pipeline_order():
    assert sb.classify_gate(_report(
        fail_reasons=["Confluence score 45.5 below minimum required 58"])) == "score"
    assert sb.classify_gate(_report(
        fail_reasons=["Only 30% of enabled engine weight voted informatively "
                      "(minimum 50%) — panel mostly mute"])) == "info_share"
    r = _report(fail_reasons=[])
    r["risk"] = {"passed": False, "reasons": ["Projected total exposure ..."]}
    assert sb.classify_gate(r) == "risk"
    r2 = _report(fail_reasons=[])
    r2["downgrade_reason"] = "Meta Decision blocked: ..."
    assert sb.classify_gate(r2) == "meta_or_regime"


def test_gate_classification_market_quality():
    r = {
        "final_verdict": "NO_TRADE",
        "summary": "NO_TRADE: Market Quality Score=32/100 (POOR) — dead session",
        "market_quality": {"score": 32, "grade": "POOR"},
        "current_price": 1.0,
        "bar_high": 1.0, "bar_low": 1.0,
    }
    assert sb.classify_gate(r) == "mqs"


def test_gate_classification_data_validation_failure():
    # main.py's pre-pipeline validation early return — no confluence/risk
    # keys at all, distinct from every other NO_TRADE shape.
    r = {"final_verdict": "NO_TRADE", "reason": "Data validation failed: bad bars"}
    assert sb.classify_gate(r) == "data_validation"


def test_gate_labels_cover_every_classify_gate_return_value():
    # A category classify_gate() can return but GATE_LABELS doesn't know
    # about would silently fall back to the raw code (e.g. "meta_or_regime"
    # instead of a readable label) wherever GATE_LABELS is used to render
    # UI/summary text — this pins the two dicts can't drift apart.
    import inspect
    source = inspect.getsource(sb.classify_gate)
    for category in sb.GATE_LABELS:
        assert f'"{category}"' in source, f"GATE_LABELS has {category!r} with no matching classify_gate() branch"


# ── Resolution & ledger ──────────────────────────────────────────────────

def test_shadow_closes_on_intrabar_tp_and_ledger_attributes_gate():
    sb.log_shadow_signal(_report(), CONFIG)
    s = sb.get_open_shadows()[0]
    tp = s["take_profit"]
    closed = sb.auto_close_shadows(
        {"EURUSD": tp - 0.0001},                      # close retraced
        bar_ranges={"EURUSD": (tp + 0.0005, s["entry_price"])},  # high touched TP
    )
    assert closed == 1
    ledger = sb.gate_ledger()
    g = next(x for x in ledger["gates"] if x["primary_gate"] == "quorum")
    assert g["n_closed"] == 1 and g["wins"] == 1
    assert g["avg_r"] == pytest.approx(2.0, abs=0.01)
    assert g["verdict"] == "rejecting profit"


def test_shadow_sl_before_tp_parity_and_saving_losses_verdict():
    sb.log_shadow_signal(_report(), CONFIG)
    s = sb.get_open_shadows()[0]
    closed = sb.auto_close_shadows(
        {"EURUSD": s["entry_price"]},
        bar_ranges={"EURUSD": (s["take_profit"] + 0.001, s["stop_loss"] - 0.001)},
    )
    assert closed == 1
    g = sb.gate_ledger()["gates"][0]
    assert g["avg_r"] == pytest.approx(-1.0, abs=0.01)
    assert g["verdict"] == "saving losses"


# ── Regime persistence + by_symbol/by_regime breakdown (Diagnostic
#    Infrastructure Phase 1, 2026-08-02) ────────────────────────────────

def test_log_shadow_signal_persists_regime_from_report():
    sb.log_shadow_signal(_report(regime={"state": "TRENDING"}), CONFIG)
    s = sb.get_open_shadows()[0]
    assert s["regime"] == "TRENDING"


def test_log_shadow_signal_regime_absent_is_null():
    sb.log_shadow_signal(_report(), CONFIG)
    s = sb.get_open_shadows()[0]
    assert s["regime"] is None


def test_gate_ledger_by_symbol_and_by_regime_partition_same_closed_set():
    sb.log_shadow_signal(_report(symbol="EURUSD", regime={"state": "TRENDING"}), CONFIG)
    sb.log_shadow_signal(_report(symbol="XAUUSD", regime={"state": "RANGING"}), CONFIG)
    for sym in ("EURUSD", "XAUUSD"):
        s = next(x for x in sb.get_open_shadows() if x["symbol"] == sym)
        tp = s["take_profit"]
        sb.auto_close_shadows(
            {sym: tp - 0.0001},
            bar_ranges={sym: (tp + 0.0005, s["entry_price"])},
        )

    ledger = sb.gate_ledger()
    total_from_gates = sum(g["n_closed"] for g in ledger["gates"])
    total_from_symbol = sum(g["n_closed"] for g in ledger["by_symbol"])
    total_from_regime = sum(g["n_closed"] for g in ledger["by_regime"])
    assert total_from_gates == total_from_symbol == total_from_regime == 2

    by_symbol = {g["symbol"]: g for g in ledger["by_symbol"]}
    assert by_symbol["EURUSD"]["n_closed"] == 1
    assert by_symbol["XAUUSD"]["n_closed"] == 1
    by_regime = {g["regime"]: g for g in ledger["by_regime"]}
    assert by_regime["TRENDING"]["n_closed"] == 1
    assert by_regime["RANGING"]["n_closed"] == 1


def test_gate_ledger_by_regime_buckets_missing_regime_as_unknown():
    sb.log_shadow_signal(_report(), CONFIG)  # no regime kwarg -> NULL
    s = sb.get_open_shadows()[0]
    tp = s["take_profit"]
    sb.auto_close_shadows(
        {"EURUSD": tp - 0.0001},
        bar_ranges={"EURUSD": (tp + 0.0005, s["entry_price"])},
    )
    ledger = sb.gate_ledger()
    by_regime = {g["regime"]: g for g in ledger["by_regime"]}
    assert "Unknown" in by_regime
    assert by_regime["Unknown"]["n_closed"] == 1


def test_shadow_time_stop():
    sb.log_shadow_signal(_report(), CONFIG)
    from storage import d1_client
    with d1_client.d1_connection() as con:
        con.execute("UPDATE shadow_signals SET ts = datetime('now','-200 hours') || '+00:00'")
    closed = sb.auto_close_shadows({"EURUSD": 1.0851}, max_open_hours=168)
    assert closed == 1
    assert sb.get_open_shadows() == []


# ── Swap cost mechanism ──────────────────────────────────────────────────

def test_backtest_swap_charges_per_night_held():
    from backtesting.backtest_engine import BacktestConfig
    cfg = BacktestConfig(symbol="EURUSD", swap_pips_per_night=0.5)
    assert cfg.swap_pips_per_night == 0.5
    # from_profile reads data/swap_rates.json — ships all-zero (mechanism
    # off until the operator fills real broker rates).
    prof = BacktestConfig.from_profile("EURUSD")
    assert prof.swap_pips_per_night == 0.0


# ── Pre-registered forward review ────────────────────────────────────────

def test_forward_review_rules_exist_and_evaluator_runs(capsys, monkeypatch):
    import scripts.forward_review as fr
    import json
    rules = json.loads(fr.REGISTRY.read_text())["_decision_rules"]
    assert rules["D001_fx_cut"]["threshold"] == 1.0
    assert rules["D002_carrier_confirmation"]["min_n"] == 100

    monkeypatch.setattr(fr, "_closed_outcomes", lambda: [
        {"symbol": "XAUUSD", "outcome": "win", "pnl_usd": 200.0},
        {"symbol": "EURUSD", "outcome": "loss", "pnl_usd": -100.0},
    ])
    rc = fr.main()
    out = capsys.readouterr().out
    assert rc == 0                        # insufficient n → no verdict fires
    assert "INSUFFICIENT N" in out
