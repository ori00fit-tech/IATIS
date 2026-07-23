"""
tests/test_close_orphaned_trades.py
--------------------------------------
Coverage for scripts/close_orphaned_trades.py's real logic: the
direction-aware win/loss/breakeven classifier (the one thing worth
pinning — the rest of the script is interactive I/O), plus an
end-to-end run against the fake D1 fixture using a scripted `input()`.
"""
from __future__ import annotations

import os

import pytest

from scripts.close_orphaned_trades import _check_d1_env, classify, main
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


# ── _check_d1_env() diagnostics (the VPS load_dotenv() failure mode) ────────

def test_check_d1_env_passes_when_var_is_set(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://fake-d1-test.workers.dev")
    assert _check_d1_env() is None


def test_check_d1_env_reports_missing_file(monkeypatch, tmp_path):
    monkeypatch.delenv("D1_WORKER_URL", raising=False)
    monkeypatch.setattr("scripts.close_orphaned_trades._REPO_ROOT", tmp_path)
    diag = _check_d1_env()
    assert diag is not None
    assert "not set" in diag
    assert str(tmp_path / ".env") in diag
    assert "exists: False" in diag


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses file permission bits, so chmod 0o000 can't be tested as root",
)
def test_check_d1_env_reports_unreadable_file(monkeypatch, tmp_path):
    monkeypatch.delenv("D1_WORKER_URL", raising=False)
    monkeypatch.setattr("scripts.close_orphaned_trades._REPO_ROOT", tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("D1_WORKER_URL=https://example.com\n")
    env_file.chmod(0o000)
    try:
        diag = _check_d1_env()
    finally:
        env_file.chmod(0o600)  # restore so tmp_path cleanup can remove it
    assert diag is not None
    assert "permission denied" in diag.lower()


def test_main_returns_nonzero_and_skips_storage_when_d1_env_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("D1_WORKER_URL", raising=False)
    monkeypatch.setattr("scripts.close_orphaned_trades._REPO_ROOT", tmp_path)
    rc = main()
    assert rc == 1
    assert "D1_WORKER_URL is not set" in capsys.readouterr().out
