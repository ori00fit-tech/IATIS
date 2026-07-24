"""tests/test_h019_crypto_positioning_ab.py — the pre-registered verdict
logic, pinned to the registry text. Mirrors the H023/H037 test discipline:
only the pure decision function is unit-tested here (no market data
needed); the actual A/B backtest loop needs real price + funding-rate
data and only runs on the VPS."""
from __future__ import annotations

from research.experiments.H019_crypto_positioning_ab import (
    DECISION,
    load_from_csv_positioning,
    positioning_verdict,
)


def test_adopt_when_both_symbols_improve_enough():
    v, checks, reasons = positioning_verdict({"BTCUSD": 0.10, "ETHUSD": 0.06})
    assert v.startswith("ADOPT")
    assert all(checks.values())


def test_reject_when_mean_delta_too_small():
    v, checks, _ = positioning_verdict({"BTCUSD": 0.02, "ETHUSD": 0.01})
    assert v.startswith("FAILED")
    assert checks["1_mean_dPF>=0.05"] is False


def test_reject_when_one_symbol_regresses_even_if_mean_passes():
    """n=2 is too small for the '>=1 losing symbol tolerated' pattern
    used elsewhere in this registry — a single loser fails outright."""
    v, checks, reasons = positioning_verdict({"BTCUSD": 0.30, "ETHUSD": -0.05})
    assert v.startswith("FAILED")
    assert checks["2_zero_losing_symbols"] is False
    assert any("ETHUSD" in r for r in reasons)


def test_boundary_exactly_at_threshold_passes():
    v, checks, _ = positioning_verdict({"BTCUSD": 0.05, "ETHUSD": 0.05})
    assert v.startswith("ADOPT")


def test_both_symbols_flat_fails():
    v, _, _ = positioning_verdict({"BTCUSD": 0.0, "ETHUSD": 0.0})
    assert v.startswith("FAILED")


def test_decision_constants_match_registry_text():
    assert DECISION["min_mean_dPF"] == 0.05
    assert DECISION["max_losing_symbols"] == 0


# ── load_from_csv_positioning (real-data regression, 2026-07-24 VPS run) ──

def test_load_from_csv_positioning_handles_mixed_subsecond_precision(tmp_path):
    """Real Binance funding-rate timestamps mix whole-second and
    sub-second precision row to row — pandas' single-format
    auto-detection locks onto the first rows' shape and then raises the
    moment a later row doesn't match it. This is the exact failure
    observed on the VPS; format='ISO8601' must parse each row on its own
    terms instead."""
    path = tmp_path / "funding.csv"
    path.write_text(
        "datetime,funding_rate,settlement_ts_ms\n"
        "2020-07-24 00:00:00+00:00,0.0001,1595548800000\n"
        "2020-07-25 08:00:00.001000+00:00,0.0002,1595664000001\n"
        "2020-07-26 00:00:00+00:00,0.0003,1595721600000\n"
    )
    df = load_from_csv_positioning(path)
    assert len(df) == 3
    assert str(df.index.tz) == "UTC"
    assert df["funding_rate"].tolist() == [0.0001, 0.0002, 0.0003]


def test_load_from_csv_positioning_uniform_precision_still_works(tmp_path):
    path = tmp_path / "fear_greed.csv"
    path.write_text(
        "datetime,value,published_ts_s\n"
        "2018-02-01 00:00:00+00:00,30,1517443200\n"
        "2018-02-02 00:00:00+00:00,15,1517529600\n"
    )
    df = load_from_csv_positioning(path)
    assert len(df) == 2
    assert df["value"].tolist() == [30, 15]
