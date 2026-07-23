"""H023 unit tests — the arm-A/arm-B volume-zeroing helper and the
pre-registered verdict logic, pinned to the registry text. These are the
parts of research/experiments/H023_wyckoff_volume_gating.py that don't
need real market data or a live pipeline run, mirroring the H037 test
discipline (tests/test_h037_decision_delay.py)."""
from __future__ import annotations

import pandas as pd
import pytest

from research.experiments.H023_wyckoff_volume_gating import (
    CONTROL_SYMBOLS,
    DECISION,
    FX_SYMBOLS,
    _prepare_df,
    wyckoff_gate_verdict,
)


# ---------------------------------------------------------- _prepare_df

def _df_with_volume(vol=1000) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0],
        "close": [1.05, 1.15], "volume": [vol, vol],
    })


def test_prepare_df_zeroes_fx_volume_in_arm_b():
    df = _df_with_volume()
    out = _prepare_df(df, "EURUSD", zero_fx_volume=True)
    assert (out["volume"] == 0).all()


def test_prepare_df_leaves_fx_volume_untouched_in_arm_a():
    df = _df_with_volume()
    out = _prepare_df(df, "EURUSD", zero_fx_volume=False)
    assert (out["volume"] == 1000).all()


def test_prepare_df_never_touches_controls_even_in_arm_b():
    df = _df_with_volume()
    for sym in CONTROL_SYMBOLS:
        out = _prepare_df(df, sym, zero_fx_volume=True)
        assert (out["volume"] == 1000).all(), f"{sym} volume was zeroed — controls must be untouched"


def test_prepare_df_does_not_mutate_the_input_frame():
    df = _df_with_volume()
    _prepare_df(df, "EURUSD", zero_fx_volume=True)
    assert (df["volume"] == 1000).all()  # original untouched — caller's df is not aliased


def test_prepare_df_is_a_noop_without_a_volume_column():
    df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05]})
    out = _prepare_df(df, "EURUSD", zero_fx_volume=True)
    assert "volume" not in out.columns  # no crash, nothing fabricated


def test_all_seven_fx_symbols_are_covered():
    assert set(FX_SYMBOLS) == {
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURJPY", "GBPJPY", "AUDJPY",
    }


# -------------------------------------------------------- verdict logic

def _trades(pnls: list[float], start_i: int = 0) -> list[dict]:
    return [{"i": start_i + k, "outcome": "win" if p > 0 else "loss", "pnl": p}
            for k, p in enumerate(pnls)]


def test_insufficient_data_below_min_n():
    a = _trades([10.0] * 50)   # n=50 < 100 floor
    b = _trades([12.0] * 50)
    v, checks, reasons = wyckoff_gate_verdict(a, b, {"EURUSD": 0.1}, [], [])
    assert v == "INSUFFICIENT_DATA"
    assert "n=50" in reasons[0]
    assert checks == {}


def test_adopt_when_all_four_conditions_hold():
    # arm A: PF = 100/100 = 1.0 (n=100); arm B: PF = 120/100 = 1.2 -> dPF=0.20
    a = _trades([1.0] * 100 + [-1.0] * 100)
    b = _trades([1.2] * 100 + [-1.0] * 100)
    per_symbol = {s: 0.15 for s in FX_SYMBOLS}  # all 7 positive -> sign_frac=1.0
    ctrl_a = _trades([1.0] * 20 + [-1.0] * 20)
    ctrl_b = _trades([1.0] * 20 + [-1.0] * 20)  # controls unchanged
    v, checks, reasons = wyckoff_gate_verdict(a, b, per_symbol, ctrl_a, ctrl_b)
    assert v.startswith("ADOPT")
    assert all(checks.values())


def test_reject_when_symbol_sign_fraction_fails():
    a = _trades([1.0] * 100 + [-1.0] * 100)
    b = _trades([1.2] * 100 + [-1.0] * 100)  # pooled dPF still >= 0.10
    # only 3 of 7 FX symbols improve -> sign_frac = 3/7 < 5/7
    per_symbol = {**{s: 0.05 for s in FX_SYMBOLS[:3]}, **{s: -0.05 for s in FX_SYMBOLS[3:]}}
    ctrl = _trades([1.0] * 10)
    v, checks, _ = wyckoff_gate_verdict(a, b, per_symbol, ctrl, ctrl)
    assert not v.startswith("ADOPT")
    assert checks["3_symbol_sign_frac>=5/7"] is False


def test_reject_when_controls_degrade():
    a = _trades([1.0] * 100 + [-1.0] * 100)
    b = _trades([1.2] * 100 + [-1.0] * 100)
    per_symbol = {s: 0.15 for s in FX_SYMBOLS}
    ctrl_a = _trades([1.0] * 50 + [-1.0] * 50)     # PF = 1.0
    ctrl_b = _trades([1.0] * 30 + [-1.0] * 70)     # PF = 0.43 -- degraded > 0.05
    v, checks, _ = wyckoff_gate_verdict(a, b, per_symbol, ctrl_a, ctrl_b)
    assert not v.startswith("ADOPT")
    assert checks["4_controls_not_degraded"] is False


def test_null_when_pooled_delta_immaterial_and_controls_hold():
    a = _trades([1.0] * 100 + [-1.0] * 100)   # PF = 1.0
    b = _trades([1.03] * 100 + [-1.0] * 100)  # PF ~= 1.03, dPF ~= 0.03 < 0.10
    per_symbol = {s: 0.02 for s in FX_SYMBOLS}
    ctrl = _trades([1.0] * 10)
    v, _, _ = wyckoff_gate_verdict(a, b, per_symbol, ctrl, ctrl)
    assert v.startswith("NULL")


def test_failed_when_pooled_delta_material_but_below_full_adopt_bar():
    # dPF material and positive but sign_frac fails -> FAILED, not NULL
    a = _trades([1.0] * 100 + [-1.0] * 100)
    b = _trades([1.2] * 100 + [-1.0] * 100)
    per_symbol = {**{s: 0.05 for s in FX_SYMBOLS[:2]}, **{s: -0.05 for s in FX_SYMBOLS[2:]}}
    ctrl = _trades([1.0] * 10)
    v, _, reasons = wyckoff_gate_verdict(a, b, per_symbol, ctrl, ctrl)
    assert v.startswith("FAILED")
    assert reasons  # explains which check(s) failed


def test_decision_constants_match_registry_text():
    assert DECISION["min_pooled_fx_dPF"] == 0.10
    assert DECISION["min_pooled_fx_test_n"] == 100
    assert DECISION["min_symbol_sign_frac"] == pytest.approx(5 / 7)
    assert DECISION["max_control_degradation"] == 0.05
