"""H037 unit tests — the delayed-replay geometry and the pre-registered
verdict logic, pinned to the registry text. The replay tests use the REAL
simulate_trade/calc_pnl on synthetic bars, so the delay-0 identity is
exercised with the actual house machinery."""
import numpy as np
import pandas as pd
import pytest

from research.experiments.H037_decision_delay import (
    DECISION,
    MIN_POOLED_A_TEST_TRADES,
    delay_verdict,
    replay_with_delay,
)


# ------------------------------------------------------------ replay fns

def _df_from_close(close: np.ndarray, spread: float = 0.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(close), freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": close, "high": close + spread, "low": close - spread,
         "close": close},
        index=idx,
    )


def _signal(i=5, entry=100.0, sl_dist=1.0, tp_dist=2.0, direction=1, outcome="win"):
    return {"i": i, "entry": entry, "sl_dist": sl_dist, "tp_dist": tp_dist,
            "direction": direction, "outcome_immediate": outcome}


def test_delay0_reproduces_immediate_geometry():
    # ramp: long from 100 hits TP=102 before SL=99
    close = np.concatenate([np.full(6, 100.0), np.linspace(100.5, 104, 10)])
    df = _df_from_close(close, spread=0.1)
    trades = replay_with_delay([_signal()], df, delay=0, symbol="XAUUSD",
                               asset_class="metals", pip=0.1, dpp=1.0)
    assert len(trades) == 1
    assert trades[0]["outcome"] == "win"
    assert trades[0]["entry_bar"] == 5


def test_delayed_entry_reanchors_to_later_close():
    # price ramps: delayed long enters higher, same distances
    close = np.concatenate([np.full(6, 100.0), np.linspace(101, 110, 12)])
    df = _df_from_close(close, spread=0.05)
    t0 = replay_with_delay([_signal()], df, 0, "XAUUSD", "metals", 0.1, 1.0)
    t2 = replay_with_delay([_signal()], df, 2, "XAUUSD", "metals", 0.1, 1.0)
    assert t0[0]["entry_bar"] == 5 and t2[0]["entry_bar"] == 7
    # both win on a monotone ramp (TP re-anchored 2.0 above the new entry)
    assert t0[0]["outcome"] == "win" and t2[0]["outcome"] == "win"


def test_delay_can_flip_an_outcome():
    # spike up then collapse: immediate long wins (TP 102 hit at bar 6),
    # a 2-bar delayed long enters at the top and rides the collapse to SL
    close = np.array([100.0] * 6 + [102.5, 104.0, 101.0, 96.0, 95.0, 94.0])
    df = _df_from_close(close, spread=0.05)
    t0 = replay_with_delay([_signal()], df, 0, "XAUUSD", "metals", 0.1, 1.0)
    t2 = replay_with_delay([_signal()], df, 2, "XAUUSD", "metals", 0.1, 1.0)
    assert t0[0]["outcome"] == "win"
    assert t2[0]["outcome"] == "loss"
    assert t2[0]["pnl"] < 0 < t0[0]["pnl"]


def test_delayed_signal_inside_occupancy_is_dropped():
    # two signals; the second one's DELAYED entry bar lands inside the
    # first trade's occupancy and must be dropped (retention guard's job)
    close = np.array([100.0] * 6 + [100.2, 100.4, 102.5,  # 1st TP hit at bar 8
                      100.0, 100.0, 100.0, 100.0, 100.0])
    df = _df_from_close(close, spread=0.05)
    sigs = [_signal(i=5), _signal(i=7)]  # capture allows i=7 only if free —
    # here we test the replay's own rule: with delay 0 the 2nd signal (bar 7)
    # is inside occupancy (trade open bars 6-8) and drops; same with delay 1
    t0 = replay_with_delay(sigs, df, 0, "XAUUSD", "metals", 0.1, 1.0)
    assert len(t0) == 1


def test_signal_too_close_to_series_end_is_dropped():
    close = np.full(10, 100.0)
    df = _df_from_close(close)
    t3 = replay_with_delay([_signal(i=7)], df, 3, "XAUUSD", "metals", 0.1, 1.0)
    assert t3 == []  # i+N = 10 > n-2


# --------------------------------------------------------- verdict logic

def _d(dpf=0.20, retention=0.95, swf=0.7, ca=1.3, cb=1.3):
    return {"dpf": dpf, "retention": retention, "symbol_win_frac": swf,
            "car_pf_a": ca, "car_pf_b": cb}


def test_adopt_smallest_passing_delay_with_family_consistency():
    per = {1: _d(dpf=0.20), 2: _d(dpf=0.05), 3: _d(dpf=0.18)}
    v, checks, reasons = delay_verdict(per, 500)
    assert v == "ADOPT (delay 1)"
    assert checks["5_family_dPF>0_in>=2_of_3"] is True


def test_single_passing_delay_with_negative_neighbors_is_noise():
    per = {1: _d(dpf=-0.10), 2: _d(dpf=0.30), 3: _d(dpf=-0.08)}
    v, checks, reasons = delay_verdict(per, 500)
    assert not v.startswith("ADOPT")
    assert checks["5_family_dPF>0_in>=2_of_3"] is False
    assert any("family-consistency" in r for r in reasons)


def test_null_when_all_deltas_immaterial():
    per = {1: _d(dpf=0.02), 2: _d(dpf=-0.03), 3: _d(dpf=0.01)}
    v, _, _ = delay_verdict(per, 500)
    assert v.startswith("NULL")


def test_failed_when_retention_broken():
    per = {1: _d(dpf=0.02, retention=0.60), 2: _d(dpf=-0.03), 3: _d(dpf=0.01)}
    v, checks, _ = delay_verdict(per, 500)
    assert v == "FAILED / NO CHANGE"  # NULL requires retention held on ALL
    assert checks["delay_1"]["2_volume_retention>=0.80"] is False


def test_carrier_degradation_blocks_adopt():
    per = {1: _d(dpf=0.20, cb=1.20), 2: _d(dpf=0.16, cb=1.29), 3: _d(dpf=0.01)}
    v, checks, _ = delay_verdict(per, 500)
    assert v == "ADOPT (delay 2)"  # delay 1 fails carriers; 2 passes; family ok
    assert checks["delay_1"]["4_carriers_not_degraded"] is False


def test_insufficient_data_short_circuits():
    per = {1: _d(dpf=9.9), 2: _d(dpf=9.9), 3: _d(dpf=9.9)}
    v, _, reasons = delay_verdict(per, MIN_POOLED_A_TEST_TRADES - 1)
    assert v == "INSUFFICIENT_DATA"
    assert "n=299" in reasons[0]
