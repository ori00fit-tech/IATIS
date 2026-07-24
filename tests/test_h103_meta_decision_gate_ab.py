"""H103 unit tests — the pre-registered SYMMETRIC verdict logic, pinned
to the registry/hypothesis-doc text. Only evaluate_decision (pure) is
tested here; run_arm/backtest_symbol_ab need the real pipeline and only
run on the VPS, same discipline as H024/H037's tests."""
from __future__ import annotations

from research.experiments.H103_meta_decision_gate_ab import (
    CARRIERS,
    MIN_POOLED_A_TEST_TRADES,
    evaluate_decision,
)


def _trades(n: int, win_pnl: float, loss_pnl: float, win_frac: float) -> list[dict]:
    """n trades with a fixed win fraction and per-trade pnl magnitudes —
    lets tests target an exact profit factor."""
    n_wins = round(n * win_frac)
    n_losses = n - n_wins
    return ([{"pnl": win_pnl} for _ in range(n_wins)]
            + [{"pnl": -abs(loss_pnl)} for _ in range(n_losses)])


def _row(symbol: str, test_a: list[dict], test_b: list[dict]) -> dict:
    return {
        "symbol": symbol,
        "test_trades_A": len(test_a),
        "test_trades_B": len(test_b),
        "_test_a": test_a,
        "_test_b": test_b,
    }


# A baseline pool of 350 arm-A trades at PF~1.30 (clears the n>=300 floor),
# split across enough symbols including 2 carriers, reused across scenarios.
def _baseline_rows(b_factory) -> list[dict]:
    rows = []
    # 3 non-carrier symbols, 100 arm-A trades each (300 total, at the floor)
    for i, sym in enumerate(["EURUSD", "GBPUSD", "USDJPY"]):
        a = _trades(100, win_pnl=130.0, loss_pnl=100.0, win_frac=0.5)  # PF=1.30
        rows.append(_row(sym, a, b_factory(a)))
    # 2 carrier symbols, 50 trades each — included in both pooled and carrier stats
    for sym in ["XAUUSD", "BTCUSD"]:
        a = _trades(50, win_pnl=130.0, loss_pnl=100.0, win_frac=0.5)
        rows.append(_row(sym, a, b_factory(a)))
    return rows


def test_adopt_when_all_three_conditions_hold():
    # arm B: same PF, MORE trades (gate was blocking some, removal adds them)
    rows = _baseline_rows(lambda a: a + _trades(20, 130.0, 100.0, 0.5))
    d = evaluate_decision(rows)
    assert d["verdict"].startswith("ADOPT")
    assert all(d["checks"].values())


def test_failed_keep_when_pooled_pf_drops_more_than_0_03():
    # arm B: fewer wins -> PF drops well past the 0.03 tolerance
    rows = _baseline_rows(lambda a: _trades(len(a) + 20, 130.0, 100.0, 0.35))
    d = evaluate_decision(rows)
    assert d["verdict"].startswith("FAILED")
    assert "keep" in d["verdict"].lower()
    assert d["checks"]["1_pooled_PF_not_worse_than_0.03"] is False


def test_failed_keep_when_carriers_drop_even_if_pooled_holds():
    def b_factory(a):
        return a  # non-carriers: identical arm B
    rows = _baseline_rows(b_factory)
    # override just the carrier rows with a badly degraded arm B
    for r in rows:
        if r["symbol"] in CARRIERS:
            bad = _trades(len(r["_test_a"]) + 5, 130.0, 100.0, 0.20)  # PF collapses
            r["_test_b"] = bad
    d = evaluate_decision(rows)
    assert d["verdict"].startswith("FAILED")
    assert d["checks"]["2_carriers_PF_not_worse_than_0.03"] is False


def test_null_when_dpf_immaterial_and_no_trade_count_increase():
    # arm B: same trades, same PF, SAME count (gate was vacuous here)
    rows = _baseline_rows(lambda a: list(a))
    d = evaluate_decision(rows)
    assert d["verdict"].startswith("NULL")
    assert d["checks"]["3_trade_count_increased"] is False


def test_failed_default_when_pf_improves_but_trade_count_does_not_increase():
    # PF(B) meaningfully BETTER (not degraded) but condition 3 (n increased)
    # fails and dPF isn't small enough to call NULL -> falls to the
    # conservative FAILED/keep default (ADOPT requires ALL three).
    rows = _baseline_rows(lambda a: _trades(len(a), 150.0, 100.0, 0.6))
    d = evaluate_decision(rows)
    assert not d["verdict"].startswith("ADOPT")
    assert not d["verdict"].startswith("NULL")
    assert d["checks"]["3_trade_count_increased"] is False


def test_insufficient_data_below_the_300_floor():
    a = _trades(50, 130.0, 100.0, 0.5)  # well under MIN_POOLED_A_TEST_TRADES
    rows = [_row("EURUSD", a, a)]
    d = evaluate_decision(rows)
    assert d["verdict"] == "INSUFFICIENT_DATA"
    assert d["pooled_test_trades_A"] < MIN_POOLED_A_TEST_TRADES


def test_boundary_exactly_at_300_is_evaluated_not_insufficient():
    a = _trades(MIN_POOLED_A_TEST_TRADES, 130.0, 100.0, 0.5)
    rows = [_row("EURUSD", a, list(a))]
    d = evaluate_decision(rows)
    assert d["verdict"] != "INSUFFICIENT_DATA"


def test_gate_default_wiring_matches_live_behavior():
    """Sanity check that the module's own constants match the registered
    tolerances — a drift here would silently loosen/tighten the rule."""
    from research.experiments.H103_meta_decision_gate_ab import DECISION
    assert DECISION["max_pf_degradation"] == 0.03
    assert DECISION["max_carrier_degradation"] == 0.03
