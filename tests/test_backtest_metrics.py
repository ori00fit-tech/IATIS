"""
tests/test_backtest_metrics.py
--------------------------------
AI Research Lab Phase 1 (2026-07-27) — hand-computed-value tests for the
8 new BacktestMetrics fields (SQN, Recovery Factor, Ulcer Index, Kelly
criterion, VaR-95, CVaR-95, skew, kurtosis) and the 3 previously-dead
fields now honestly populated (mar_ratio, max_dd_duration, exposure_pct),
plus by_engine. No dedicated test file existed for backtest/metrics.py
before this — it was only exercised indirectly via report-generation
tests.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backtest.metrics import TradeRecord, calculate_metrics


def _trade(pnl, rr=0.0, is_win=None, entry_time=None, exit_time=None,
           engine_votes=None) -> TradeRecord:
    if is_win is None:
        is_win = pnl > 0
    return TradeRecord(
        trade_id="t", symbol="EURUSD", direction="BUY",
        entry_time=entry_time or pd_ts(0), exit_time=exit_time or pd_ts(1),
        entry_price=1.1, exit_price=1.1, stop_loss=1.09, take_profit=1.12,
        position_size=1.0, pnl_usd=pnl, rr_actual=rr, is_win=is_win,
        engine_votes=engine_votes or {},
    )


def pd_ts(hours: int):
    import pandas as pd
    return pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=hours)


def test_calculate_metrics_still_returns_empty_default_on_no_trades():
    m = calculate_metrics([])
    assert m.total_trades == 0
    assert m.sqn == 0.0
    assert m.var_95 == 0.0
    assert m.by_engine == {}


def test_sqn_matches_hand_computed_value_on_known_trades():
    rrs = [1.0, -1.0, 2.0, -0.5, 1.5]
    trades = [_trade(pnl=r * 100, rr=r) for r in rrs]
    m = calculate_metrics(trades)
    rr_arr = np.array(rrs)
    expected = float(np.mean(rr_arr) / np.std(rr_arr, ddof=1) * math.sqrt(len(rrs)))
    assert m.sqn == pytest.approx(expected, rel=1e-6)


def test_recovery_factor_zero_when_no_drawdown():
    # Monotonically increasing equity -> zero drawdown -> recovery_factor
    # stays at its 0.0 default (division guarded).
    trades = [_trade(pnl=100, rr=1.0), _trade(pnl=50, rr=0.5)]
    m = calculate_metrics(trades)
    assert m.max_drawdown_usd == 0
    assert m.recovery_factor == 0.0


def test_recovery_factor_positive_with_real_drawdown():
    trades = [_trade(pnl=100, rr=1.0), _trade(pnl=-50, rr=-0.5), _trade(pnl=80, rr=0.8)]
    m = calculate_metrics(trades)
    assert m.max_drawdown_usd > 0
    assert m.recovery_factor == pytest.approx(m.net_profit / m.max_drawdown_usd)


def test_kelly_criterion_clamped_to_valid_range():
    # All-wins: avg_loss == 0 -> guarded, stays 0.0 (no crash).
    all_wins = [_trade(pnl=100, rr=1.0) for _ in range(5)]
    m = calculate_metrics(all_wins)
    assert m.kelly_criterion == 0.0
    assert -1.0 <= m.kelly_criterion <= 1.0

    # All-losses: avg_win == 0 -> guarded, stays 0.0 (no crash).
    all_losses = [_trade(pnl=-100, rr=-1.0) for _ in range(5)]
    m2 = calculate_metrics(all_losses)
    assert m2.kelly_criterion == 0.0

    # Mixed, real case: must be within [-1, 1].
    mixed = [_trade(pnl=100, rr=1.0), _trade(pnl=-50, rr=-0.5),
             _trade(pnl=100, rr=1.0), _trade(pnl=-50, rr=-0.5)]
    m3 = calculate_metrics(mixed)
    assert -1.0 <= m3.kelly_criterion <= 1.0
    assert m3.kelly_criterion != 0.0


def test_var_95_and_cvar_95_ordering():
    pnls = [100, -200, 50, -300, 80, -10, 60, -150, 40, -400]
    trades = [_trade(pnl=p, rr=p / 100) for p in pnls]
    m = calculate_metrics(trades)
    # CVaR is the mean of the tail at/below VaR — always at least as bad.
    assert m.cvar_95 <= m.var_95


def test_skew_kurtosis_zero_on_symmetric_sample():
    # A symmetric distribution around 0 should have skew near 0.
    pnls = [-300, -200, -100, -50, 0, 50, 100, 200, 300, 0]
    trades = [_trade(pnl=p, rr=p / 100) for p in pnls]
    m = calculate_metrics(trades)
    assert abs(m.skew) < 0.3


def test_max_dd_duration_counts_consecutive_underwater_trades_not_bars():
    # equity: 10000 -> 10100 (peak) -> 10050 (UW #1) -> 10000 (UW #2)
    #   -> 10200 (new peak, recovered) -> 10150 (UW #1 again)
    pnls = [100, -50, -50, 200, -50]
    trades = [_trade(pnl=p, rr=p / 100) for p in pnls]
    m = calculate_metrics(trades)
    assert m.max_dd_duration == 2


def test_exposure_pct_uses_entry_exit_timestamps_not_bar_count():
    import pandas as pd
    base = pd.Timestamp("2026-01-01", tz="UTC")
    # Trade 1: 1h long, starting at t=0. Trade 2: 1h long, starting at t=3h.
    # Total span = 4h (0 -> 4h exit of trade 2). In-market = 2h. => 50%.
    t1 = _trade(pnl=10, rr=0.1, entry_time=base, exit_time=base + timedelta(hours=1))
    t2 = _trade(pnl=10, rr=0.1, entry_time=base + timedelta(hours=3), exit_time=base + timedelta(hours=4))
    m = calculate_metrics([t1, t2])
    assert m.exposure_pct == pytest.approx(50.0, abs=0.1)


def test_by_engine_populated_from_trade_engine_votes():
    t1 = _trade(pnl=100, rr=1.0, engine_votes={"nnfx": {"bias": "BULLISH"}, "smc": {"bias": "BULLISH"}})
    t2 = _trade(pnl=-50, rr=-0.5, engine_votes={"nnfx": {"bias": "BULLISH"}})
    m = calculate_metrics([t1, t2])
    assert m.by_engine["nnfx"]["trades"] == 2
    assert m.by_engine["nnfx"]["wins"] == 1
    assert m.by_engine["smc"]["trades"] == 1
    assert m.by_engine["smc"]["wins"] == 1
    assert m.by_engine["nnfx"]["win_rate"] == pytest.approx(50.0)


def test_std_rr_matches_hand_computed_value():
    rrs = [1.0, -1.0, 2.0, -0.5, 1.5]
    trades = [_trade(pnl=r * 100, rr=r) for r in rrs]
    m = calculate_metrics(trades)
    assert m.std_rr == pytest.approx(float(np.std(np.array(rrs), ddof=1)), rel=1e-6)


def test_mar_ratio_equals_calmar_ratio():
    trades = [_trade(pnl=100, rr=1.0), _trade(pnl=-50, rr=-0.5), _trade(pnl=80, rr=0.8)]
    m = calculate_metrics(trades)
    assert m.mar_ratio == m.calmar_ratio


# ── Edge Discovery (2026-07-31) — by_direction_regime_session + PF on
# by_regime/by_direction/by_session/by_direction_regime_session ──────────

def _full_trade(pnl, direction="BUY", regime="", session="", is_win=None, exit_reason="") -> TradeRecord:
    if is_win is None:
        is_win = pnl > 0
    return TradeRecord(
        trade_id="t", symbol="EURUSD", direction=direction,
        entry_time=pd_ts(0), exit_time=pd_ts(1),
        entry_price=1.1, exit_price=1.1, stop_loss=1.09, take_profit=1.12,
        position_size=1.0, pnl_usd=pnl, is_win=is_win, regime=regime, session=session,
        exit_reason=exit_reason,
    )


def test_by_direction_regime_session_key_format_and_pooling():
    trades = [
        _full_trade(100, direction="BUY", regime="TRENDING", session="London"),
        _full_trade(50, direction="BUY", regime="TRENDING", session="London"),
        _full_trade(-30, direction="SELL", regime="RANGING", session="Asia"),
    ]
    m = calculate_metrics(trades)
    assert m.by_direction_regime_session["BUY|TRENDING|London"]["trades"] == 2
    assert m.by_direction_regime_session["BUY|TRENDING|London"]["wins"] == 2
    assert m.by_direction_regime_session["BUY|TRENDING|London"]["pnl"] == pytest.approx(150)
    assert m.by_direction_regime_session["SELL|RANGING|Asia"]["trades"] == 1
    assert m.by_direction_regime_session["SELL|RANGING|Asia"]["wins"] == 0


def test_by_direction_regime_session_unknown_fallback():
    trades = [_full_trade(100, direction="BUY", regime="", session="")]
    m = calculate_metrics(trades)
    assert "BUY|Unknown|Unknown" in m.by_direction_regime_session


def test_profit_factor_added_to_by_direction_by_session_by_regime_but_not_by_engine():
    trades = [
        TradeRecord(
            trade_id="t1", symbol="EURUSD", direction="BUY", entry_time=pd_ts(0), exit_time=pd_ts(1),
            entry_price=1.1, exit_price=1.1, stop_loss=1.09, take_profit=1.12, position_size=1.0,
            pnl_usd=100, is_win=True, regime="TRENDING", session="London",
            engine_votes={"nnfx": {"bias": "BULLISH"}},
        ),
        _full_trade(-40, direction="SELL", regime="RANGING", session="Asia", is_win=False),
    ]
    m = calculate_metrics(trades)
    assert "profit_factor" in m.by_direction["BUY"]
    assert "profit_factor" in m.by_regime["TRENDING"]
    assert "profit_factor" in m.by_session["London"]
    assert "profit_factor" in m.by_direction_regime_session["BUY|TRENDING|London"]
    assert "profit_factor" not in m.by_engine["nnfx"]


def test_bucket_profit_factor_matches_top_level_convention_zero_losses():
    trades = [_full_trade(100, direction="BUY", regime="TRENDING", session="London")]
    m = calculate_metrics(trades)
    assert m.by_direction["BUY"]["profit_factor"] == float("inf")
    assert m.by_direction_regime_session["BUY|TRENDING|London"]["profit_factor"] == float("inf")


def test_bucket_profit_factor_real_ratio():
    trades = [
        _full_trade(100, direction="BUY", regime="TRENDING", session="London"),
        _full_trade(-25, direction="BUY", regime="TRENDING", session="London"),
    ]
    m = calculate_metrics(trades)
    bucket = m.by_direction_regime_session["BUY|TRENDING|London"]
    assert bucket["profit_factor"] == pytest.approx(4.0)


# ── SHADOW Comparable Statistic Design (Design Gate, locked 2026-10) —
# by_exit_reason: raw {trades, wins} counts keyed by the literal
# exit_reason value, no win_rate/profit_factor derivation, no semantics
# change to win_rate/winning_trades/losing_trades. ────────────────────────


def test_by_exit_reason_empty_on_no_trades():
    m = calculate_metrics([])
    assert m.by_exit_reason == {}


def test_by_exit_reason_populated_per_literal_value():
    trades = [
        _full_trade(100, is_win=True, exit_reason="TP"),
        _full_trade(80, is_win=True, exit_reason="TP_GAP"),
        _full_trade(-50, is_win=False, exit_reason="SL"),
        _full_trade(-20, is_win=False, exit_reason="SL_GAP"),
        _full_trade(5, is_win=True, exit_reason="FORCED_CLOSE"),
        _full_trade(-5, is_win=False, exit_reason="FORCED_CLOSE"),
    ]
    m = calculate_metrics(trades)
    assert m.by_exit_reason["TP"] == {"trades": 1, "wins": 1}
    assert m.by_exit_reason["TP_GAP"] == {"trades": 1, "wins": 1}
    assert m.by_exit_reason["SL"] == {"trades": 1, "wins": 0}
    assert m.by_exit_reason["SL_GAP"] == {"trades": 1, "wins": 0}
    # FORCED_CLOSE recorded as its own key, mixed wins/losses counted
    # exactly like any other exit_reason -- no causal/non-causal judgment
    # made here (that is a future consumer's decision, not this field's).
    assert m.by_exit_reason["FORCED_CLOSE"] == {"trades": 2, "wins": 1}


def test_by_exit_reason_absent_key_means_never_occurred_not_zero():
    """An exit_reason that never occurred in this run must be ABSENT from
    the dict entirely -- a consumer checking for a zero count via a
    present key with trades=0 would be reading a fabricated fact. Locked
    distinction: absent means 'no evidence', never 'confirmed zero'."""
    trades = [_full_trade(100, is_win=True, exit_reason="TP")]
    m = calculate_metrics(trades)
    assert "TP" in m.by_exit_reason
    for never_occurred in ("SL", "TP_GAP", "SL_GAP", "FORCED_CLOSE"):
        assert never_occurred not in m.by_exit_reason


def test_by_exit_reason_has_no_win_rate_or_profit_factor_key():
    """Locked shape is {trades, wins} only -- unlike by_direction/
    by_session/by_regime/by_direction_regime_session, this bucket gets no
    derived win_rate/profit_factor. A future consumer computes its own
    TP/TP_GAP-vs-SL/SL_GAP ratio from the raw counts instead."""
    trades = [
        _full_trade(100, is_win=True, exit_reason="TP"),
        _full_trade(-50, is_win=False, exit_reason="SL"),
    ]
    m = calculate_metrics(trades)
    assert "win_rate" not in m.by_exit_reason["TP"]
    assert "profit_factor" not in m.by_exit_reason["TP"]
    assert set(m.by_exit_reason["TP"].keys()) == {"trades", "wins"}


def test_by_exit_reason_does_not_change_existing_win_rate_semantics():
    """Regression guard: adding by_exit_reason must not alter
    win_rate/winning_trades/losing_trades' own existing, pre-existing
    meaning (profitability-based, pooled across every exit_reason)."""
    trades = [
        _full_trade(100, is_win=True, exit_reason="TP"),
        _full_trade(-50, is_win=False, exit_reason="SL"),
        _full_trade(5, is_win=True, exit_reason="FORCED_CLOSE"),
    ]
    m = calculate_metrics(trades)
    assert m.total_trades == 3
    assert m.winning_trades == 2
    assert m.losing_trades == 1
    assert m.win_rate == pytest.approx(200 / 3)
