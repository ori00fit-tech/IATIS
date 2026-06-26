"""tests/test_outcome_tracker.py"""
from __future__ import annotations
import tempfile
from pathlib import Path
import pytest
from storage.outcome_tracker import (
    log_signal, close_signal, get_open_signals,
    performance_summary, recent_signals
)


def _tmp_db():
    t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Path(t.name)


def _make_report(symbol="EURUSD", score=72.0, regime="TRENDING"):
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {
            "score": score,
            "vote": {"winning_bias": "BEARISH"},
        },
        "regime": {"state": regime},
        "news": {"news_risk_score": 5.0},
        "engine_outputs": [
            {"engine": "SMC", "bias": "BEARISH", "score": 52},
            {"engine": "NNFX", "bias": "BEARISH", "score": 65},
        ],
    }


def test_log_signal_creates_record():
    db = _tmp_db()
    signal_id = log_signal(_make_report(), path=db)
    assert signal_id != ""
    signals = get_open_signals(path=db)
    assert len(signals) == 1
    assert signals[0]["symbol"] == "EURUSD"
    assert signals[0]["outcome"] == "open"


def test_close_signal_win():
    db = _tmp_db()
    signal_id = log_signal(_make_report(), path=db)
    success = close_signal(signal_id, exit_price=1.0640, outcome="win", path=db)
    assert success is True
    open_sigs = get_open_signals(path=db)
    assert len(open_sigs) == 0


def test_close_signal_loss():
    db = _tmp_db()
    signal_id = log_signal(_make_report(), path=db)
    success = close_signal(signal_id, exit_price=1.0920, outcome="loss", path=db)
    assert success is True


def test_close_nonexistent_signal():
    db = _tmp_db()
    success = close_signal("NONEXISTENT_ID", 1.0, "win", path=db)
    assert success is False


def test_performance_summary_empty():
    db = _tmp_db()
    summary = performance_summary(path=db)
    assert summary["total_closed"] == 0
    assert summary["win_rate"] == 0


def test_performance_summary_with_data():
    db = _tmp_db()
    for i in range(3):
        sid = log_signal(_make_report(symbol=f"EUR{i}"), path=db)
        close_signal(sid, 1.064, "win", path=db)
    sid = log_signal(_make_report(symbol="LOSS"), path=db)
    close_signal(sid, 1.092, "loss", path=db)

    summary = performance_summary(path=db)
    assert summary["total_closed"] == 4
    assert summary["wins"] == 3
    assert summary["losses"] == 1
    assert summary["win_rate"] == 75.0


def test_pnl_pips_calculated():
    db = _tmp_db()
    signal_id = log_signal(_make_report("EURUSD"), path=db)
    # BEARISH entry=1.0850 exit=1.0640 → (1.0850-1.0640)/0.0001 = 210 pips profit
    close_signal(signal_id, 1.0640, "win", path=db)
    recent = recent_signals(limit=1, path=db)
    assert recent[0]["pnl_pips"] == pytest.approx(210.0, abs=1.0)


def test_regime_breakdown():
    db = _tmp_db()
    for regime in ["TRENDING", "TRENDING", "RANGING"]:
        # Force different signal_ids by varying symbol
        sid = log_signal(_make_report(symbol=f"EUR{regime[:3]}", regime=regime), path=db)
        close_signal(sid, 1.064, "win", path=db)

    summary = performance_summary(path=db)
    regimes = {r["regime"]: r for r in summary["by_regime"]}
    assert "TRENDING" in regimes
    assert regimes["TRENDING"]["n"] >= 1


def test_multiple_symbols():
    db = _tmp_db()
    for sym in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]:
        sid = log_signal(_make_report(symbol=sym), path=db)
    assert len(get_open_signals(path=db)) == 4


def test_duplicate_signal_id_ignored():
    db = _tmp_db()
    r = _make_report()
    id1 = log_signal(r, path=db)
    id2 = log_signal(r, path=db)  # same timestamp+symbol → INSERT OR IGNORE
    assert len(get_open_signals(path=db)) == 1
