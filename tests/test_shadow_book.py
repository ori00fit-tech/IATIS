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
