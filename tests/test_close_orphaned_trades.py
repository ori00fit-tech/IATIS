"""
tests/test_close_orphaned_trades.py
--------------------------------------
Coverage for scripts/close_orphaned_trades.py's real logic: the
direction-aware win/loss/breakeven classifier (the one thing worth
pinning — the rest of the script is interactive I/O), plus an
end-to-end run against the fake D1 fixture using a scripted `input()`.
"""
from __future__ import annotations

import pytest

from scripts.close_orphaned_trades import classify, main
from storage.outcome_tracker import get_open_signals, log_signal


def _report(symbol="EURUSD", direction="BULLISH", entry=1.0850, sl=1.0800, tp=1.0950):
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


# ── classify() ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("direction,entry,exit_price,expected", [
    ("BULLISH", 1.0850, 1.0900, "win"),
    ("BUY", 1.0850, 1.0900, "win"),
    ("BULLISH", 1.0850, 1.0800, "loss"),
    ("BEARISH", 1.0850, 1.0800, "win"),
    ("SELL", 1.0850, 1.0800, "win"),
    ("BEARISH", 1.0850, 1.0900, "loss"),
    ("BULLISH", 1.0850, 1.0850, "breakeven"),
    ("BEARISH", 1.0850, 1.0850, "breakeven"),
])
def test_classify(direction, entry, exit_price, expected):
    assert classify(direction, entry, exit_price) == expected


def test_classify_breakeven_tolerance_is_tight():
    # Just outside the default tolerance must NOT be called breakeven.
    assert classify("BULLISH", 1.0850, 1.0850 + 1e-4) == "win"


# ── main() end-to-end (fake D1, scripted input) ─────────────────────────

def test_main_closes_signals_from_scripted_input(monkeypatch, capsys):
    log_signal(_report(symbol="EURUSD", direction="BEARISH", entry=1.0850, sl=1.0920))
    log_signal(_report(symbol="USDCHF", direction="BULLISH", entry=0.8000, sl=0.7950))

    # get_open_signals() orders newest-first, so USDCHF (logged second) is
    # prompted first: exit 1.0900 above its 0.8000 entry on a long -> win.
    # EURUSD (prompted second) gets a blank input -> skipped.
    answers = iter(["1.0900", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))

    rc = main()
    assert rc == 0

    remaining = get_open_signals()
    assert len(remaining) == 1
    assert remaining[0]["symbol"] == "EURUSD"

    out = capsys.readouterr().out
    assert "closed as 'win'" in out
    assert "Closed 1 of 2" in out


def test_main_invalid_price_input_skips_without_crashing(monkeypatch):
    log_signal(_report(symbol="EURUSD", direction="BULLISH", entry=1.0850, sl=1.0800))
    monkeypatch.setattr("builtins.input", lambda *_: "not-a-number")

    rc = main()
    assert rc == 0
    assert len(get_open_signals()) == 1  # untouched


def test_main_no_open_signals_is_a_noop(capsys):
    rc = main()
    assert rc == 0
    assert "nothing to do" in capsys.readouterr().out.lower()
