"""
tests/test_purge_unexecuted_outcomes.py
-------------------------------------------
Coverage for scripts/purge_unexecuted_outcomes.py: an end-to-end run
against the fake D1 fixture using scripted `input()`, plus the same
D1-env diagnostics already pinned for scripts/close_orphaned_trades.py
(this script shares that exact helper's logic).
"""
from __future__ import annotations

import os

import pytest

from scripts.purge_unexecuted_outcomes import _check_d1_env, main
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


# ── main() end-to-end (fake D1, scripted input) ─────────────────────────

def test_main_deletes_confirmed_signals_from_scripted_input(monkeypatch, capsys):
    log_signal(_report(symbol="EURJPY", direction="BULLISH"))
    log_signal(_report(symbol="USDJPY", direction="BEARISH"))

    # get_open_signals() orders newest-first, so USDJPY (logged second) is
    # prompted first: confirmed deleted. EURJPY (prompted second): blank
    # input -> defaults to "no" -> skipped.
    answers = iter(["y", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))

    rc = main()
    assert rc == 0

    remaining = get_open_signals()
    assert len(remaining) == 1
    assert remaining[0]["symbol"] == "EURJPY"

    out = capsys.readouterr().out
    assert "deleted: OK" in out
    assert "Deleted 1 of 2" in out


def test_main_accepts_case_insensitive_yes(monkeypatch):
    log_signal(_report(symbol="GBPUSD"))
    monkeypatch.setattr("builtins.input", lambda *_: "YES")

    rc = main()
    assert rc == 0
    assert get_open_signals() == []


def test_main_blank_input_defaults_to_skip(monkeypatch):
    log_signal(_report(symbol="AUDJPY"))
    monkeypatch.setattr("builtins.input", lambda *_: "")

    rc = main()
    assert rc == 0
    assert len(get_open_signals()) == 1  # untouched — default is "no"


def test_main_no_open_signals_is_a_noop(capsys):
    rc = main()
    assert rc == 0
    assert "nothing to do" in capsys.readouterr().out.lower()


# ── _check_d1_env() diagnostics (shared logic with close_orphaned_trades.py) ─

def test_check_d1_env_passes_when_var_is_set(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://fake-d1-test.workers.dev")
    assert _check_d1_env() is None


def test_check_d1_env_reports_missing_file(monkeypatch, tmp_path):
    monkeypatch.delenv("D1_WORKER_URL", raising=False)
    monkeypatch.setattr("scripts.purge_unexecuted_outcomes._REPO_ROOT", tmp_path)
    diag = _check_d1_env()
    assert diag is not None
    assert "not set" in diag
    assert str(tmp_path / ".env") in diag
    assert "exists: False" in diag


def test_main_returns_nonzero_and_skips_storage_when_d1_env_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("D1_WORKER_URL", raising=False)
    monkeypatch.setattr("scripts.purge_unexecuted_outcomes._REPO_ROOT", tmp_path)
    rc = main()
    assert rc == 1
    assert "D1_WORKER_URL is not set" in capsys.readouterr().out
