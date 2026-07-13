"""tests/test_min_info_share_fx_ab.py

Unit coverage for H020's pre-registered decision rule
(scripts/min_info_share_fx_ab.py's compute_verdict), locked in BEFORE any
real backtest result exists — see research/results/registry.json's H020
entry for the rule as written.

Rule: VERDICT "FX-NEGATIVE - WORTH ASSET-CLASS SCOPING" only if ALL of
(1) FX mean dPF >= 0.03, (2) FX losing pairs <= 1, (3) carrier mean |dPF|
<= 0.02. Otherwise "NO ACTION".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from min_info_share_fx_ab import compute_verdict, FX_SYMBOLS, CARRIER_SYMBOLS


def _row(delta_pf: float) -> dict:
    return {"delta_pf": delta_pf}


def test_symbol_lists_match_pre_registration():
    assert FX_SYMBOLS == ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURJPY", "GBPJPY", "AUDJPY"]
    assert CARRIER_SYMBOLS == ["XAUUSD", "BTCUSD", "ETHUSD"]


def test_verdict_fx_negative_when_all_three_conditions_hold():
    fx_rows = [_row(0.05)] * 6 + [_row(-0.01)]  # mean=0.036..., 1 loser
    carrier_rows = [_row(0.01), _row(-0.015), _row(0.005)]  # mean|.| <= 0.02
    v = compute_verdict(fx_rows, carrier_rows)
    assert v["decision"] == "FX-NEGATIVE - WORTH ASSET-CLASS SCOPING"
    assert v["fx_losing_pairs"] == 1


def test_verdict_no_action_when_fx_improvement_too_small():
    fx_rows = [_row(0.02)] * 7  # mean below the 0.03 bar
    carrier_rows = [_row(0.0)] * 3
    v = compute_verdict(fx_rows, carrier_rows)
    assert v["decision"] == "NO ACTION"


def test_verdict_no_action_when_too_many_fx_pairs_lose():
    fx_rows = [_row(0.08)] * 5 + [_row(-0.02)] * 2  # mean high but 2 losers
    carrier_rows = [_row(0.0)] * 3
    v = compute_verdict(fx_rows, carrier_rows)
    assert v["fx_losing_pairs"] == 2
    assert v["decision"] == "NO ACTION"


def test_verdict_no_action_when_carriers_also_move():
    fx_rows = [_row(0.05)] * 7  # clean FX signal
    carrier_rows = [_row(0.05), _row(0.04), _row(0.03)]  # gate matters for carriers too
    v = compute_verdict(fx_rows, carrier_rows)
    assert v["decision"] == "NO ACTION"


def test_verdict_boundary_values_are_inclusive():
    # Exactly at the bar on all three conditions (>=0.03, <=1 loser, <=0.02)
    # must still pass — the rule is inclusive, not strict.
    fx_rows = [_row(0.03)] * 7  # mean == 0.03 exactly, 0 losers
    carrier_rows = [_row(0.02)] * 3  # mean|.| == 0.02 exactly
    v = compute_verdict(fx_rows, carrier_rows)
    assert v["fx_test_mean_delta_pf"] == 0.03
    assert v["carrier_test_mean_abs_delta_pf"] == 0.02
    assert v["decision"] == "FX-NEGATIVE - WORTH ASSET-CLASS SCOPING"
