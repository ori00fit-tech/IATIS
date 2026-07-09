"""
tests/test_outcome_hygiene.py
------------------------------
Open-outcome hygiene (philosophy audit, priority 4):

1. Intrabar TP/SL detection via the decision bar's (high, low) — a level
   touched inside the bar and retraced must close the signal; the old
   close-only check missed it and the trade lingered, saturating the
   exposure cap.
2. Backtest parity: when BOTH levels are touched within one bar, SL is
   assumed first (conservative loss) — same convention as
   backtesting/backtest_engine.check_exit().
3. Time stop: a signal that never reaches TP/SL force-closes at market
   after max_open_hours, labeled by realized R.
"""

from __future__ import annotations

import pytest

from storage import d1_client
from storage.outcome_tracker import (
    auto_close_outcomes,
    get_open_signals,
    log_signal,
)


def _report(symbol: str = "EURUSD", direction: str = "BULLISH",
            entry: float = 1.0850, sl: float = 1.0800,
            tp: float = 1.0950) -> dict:
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "confluence": {"vote": {"winning_bias": direction}, "score": 70},
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "regime": {"regime": "TRENDING"},
        "news": {"news_risk_score": 0},
    }


def _age_signal(hours: float) -> None:
    """Backdate every open signal's entry_time by `hours`."""
    with d1_client.d1_connection() as con:
        con.execute(
            "UPDATE outcomes SET entry_time = datetime('now', ?) || '+00:00' "
            "WHERE outcome='open'",
            (f"-{hours} hours",),
        )


# ── Intrabar detection ───────────────────────────────────────────────────

def test_intrabar_tp_touch_closes_despite_retraced_close():
    log_signal(_report(entry=1.0850, sl=1.0800, tp=1.0950))
    # Close is back between the levels, but the bar's high touched TP.
    closed = auto_close_outcomes(
        {"EURUSD": 1.0900},
        bar_ranges={"EURUSD": (1.0955, 1.0880)},
    )
    assert len(closed) == 1
    assert closed[0]["outcome"] == "win"
    assert closed[0]["exit_price"] == pytest.approx(1.0950)
    assert get_open_signals() == []


def test_intrabar_sl_touch_closes_bearish_signal():
    log_signal(_report(direction="BEARISH", entry=1.0850, sl=1.0900, tp=1.0750))
    closed = auto_close_outcomes(
        {"EURUSD": 1.0860},
        bar_ranges={"EURUSD": (1.0905, 1.0840)},  # high pierced the SL
    )
    assert len(closed) == 1
    assert closed[0]["outcome"] == "loss"
    assert closed[0]["exit_price"] == pytest.approx(1.0900)


def test_both_levels_in_one_bar_is_a_loss_backtest_parity():
    log_signal(_report(entry=1.0850, sl=1.0800, tp=1.0950))
    # The bar swept both levels: SL-first convention → loss.
    closed = auto_close_outcomes(
        {"EURUSD": 1.0870},
        bar_ranges={"EURUSD": (1.0960, 1.0790)},
    )
    assert len(closed) == 1
    assert closed[0]["outcome"] == "loss"
    assert closed[0]["exit_price"] == pytest.approx(1.0800)


def test_no_ranges_falls_back_to_close_only_behavior():
    log_signal(_report(entry=1.0850, sl=1.0800, tp=1.0950))
    closed = auto_close_outcomes({"EURUSD": 1.0870})  # between levels
    assert closed == []
    assert len(get_open_signals()) == 1


# ── Time stop ────────────────────────────────────────────────────────────

def test_time_stop_closes_stale_signal_at_market():
    log_signal(_report(entry=1.0850, sl=1.0800, tp=1.0950))
    _age_signal(hours=200)
    # Price between the levels, mildly favorable: +0.04R → breakeven label.
    closed = auto_close_outcomes({"EURUSD": 1.0852}, max_open_hours=168)
    assert len(closed) == 1
    assert closed[0]["outcome"] == "breakeven"
    assert closed[0]["exit_price"] == pytest.approx(1.0852)
    assert get_open_signals() == []


def test_time_stop_labels_by_realized_r():
    log_signal(_report(entry=1.0850, sl=1.0800, tp=1.0950))
    _age_signal(hours=200)
    # +30 pips on a 50-pip stop = +0.6R → win.
    closed = auto_close_outcomes({"EURUSD": 1.0880}, max_open_hours=168)
    assert closed[0]["outcome"] == "win"


def test_fresh_signal_is_not_time_stopped():
    log_signal(_report(entry=1.0850, sl=1.0800, tp=1.0950))
    closed = auto_close_outcomes({"EURUSD": 1.0870}, max_open_hours=168)
    assert closed == []
    assert len(get_open_signals()) == 1


def test_time_stop_disabled_by_default():
    log_signal(_report(entry=1.0850, sl=1.0800, tp=1.0950))
    _age_signal(hours=10000)
    closed = auto_close_outcomes({"EURUSD": 1.0870})  # no max_open_hours
    assert closed == []
    assert len(get_open_signals()) == 1
