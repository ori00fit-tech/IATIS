"""
tests/test_scheduler.py
--------------------------
Tests for scheduler.py — all using synthetic data and mocked Telegram
so no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.helpers import load_config


@pytest.fixture
def synthetic_config():
    config = load_config()
    config["data"]["source"] = "synthetic"
    config["telegram"] = {"enabled": False}   # no Telegram noise in tests
    return config


# ---------------------------------------------------------------------------
# _get_symbols
# ---------------------------------------------------------------------------

def test_get_symbols_reads_enabled_from_config(synthetic_config):
    from scheduler import _get_symbols
    synthetic_config["data"]["twelve_data_symbols"] = [
        {"symbol": "EUR/USD", "enabled": True},
        {"symbol": "XAU/USD", "enabled": True},
        {"symbol": "GBP/USD", "enabled": False},
    ]
    symbols = _get_symbols(synthetic_config)
    assert "EUR/USD" in symbols
    assert "XAU/USD" in symbols
    assert "GBP/USD" not in symbols


def test_get_symbols_falls_back_to_single_symbol(synthetic_config):
    from scheduler import _get_symbols
    synthetic_config["data"].pop("twelve_data_symbols", None)
    synthetic_config["data"]["twelve_data_symbol"] = "EUR/USD"
    symbols = _get_symbols(synthetic_config)
    assert symbols == ["EUR/USD"]


def test_get_symbols_filters_disabled(synthetic_config):
    from scheduler import _get_symbols
    synthetic_config["data"]["twelve_data_symbols"] = [
        {"symbol": "EUR/USD", "enabled": False},
        {"symbol": "XAU/USD", "enabled": False},
    ]
    # all disabled → falls through to fallback
    symbols = _get_symbols(synthetic_config)
    assert isinstance(symbols, list)
    assert len(symbols) >= 1


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------

def test_run_once_returns_one_report_per_symbol(synthetic_config):
    from scheduler import run_once

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    assert len(reports) == 1
    assert reports[0].get("final_verdict") in ("EXECUTE", "NO_TRADE")


def test_run_once_handles_multiple_symbols(synthetic_config):
    from scheduler import run_once

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        reports = run_once(synthetic_config, symbols=["EUR/USD", "XAU/USD"])

    assert len(reports) == 2


def test_run_once_skips_if_already_running(synthetic_config):
    """Overlap protection: if the lock is held, run_once returns empty."""
    import scheduler as sched_module
    from scheduler import run_once

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        acquired = sched_module._lock.acquire(blocking=False)
        assert acquired

        try:
            reports = run_once(synthetic_config, symbols=["EUR/USD"])
        finally:
            sched_module._lock.release()

    assert reports == []


def test_run_once_sends_telegram_on_pipeline_error(synthetic_config):
    """If a symbol's pipeline raises, run_once catches it and sends alert."""
    from scheduler import run_once

    with patch("scheduler.run_pipeline", side_effect=RuntimeError("boom")), \
         patch("scheduler.send_raw") as mock_raw, \
         patch("scheduler.send_signal"):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    # error alert should have been sent
    assert mock_raw.call_count >= 1
    alert_text = mock_raw.call_args[0][0]
    assert "error" in alert_text.lower() or "Error" in alert_text


def test_run_once_continues_after_one_symbol_fails(synthetic_config):
    """A failure on one symbol must not stop other symbols from running."""
    from scheduler import run_once as _run_once
    call_count = {"n": 0}
    original_pipeline = __import__("main").run_pipeline

    def side_effect(cfg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first symbol failed")
        return original_pipeline(cfg)

    with patch("scheduler.run_pipeline", side_effect=side_effect), \
         patch("scheduler.send_raw"), \
         patch("scheduler.send_signal"):
        reports = _run_once(synthetic_config, symbols=["EUR/USD", "XAU/USD"])

    # second symbol still produced a report
    assert len(reports) == 1


# ---------------------------------------------------------------------------
# startup message
# ---------------------------------------------------------------------------

def test_run_loop_sends_startup_message(synthetic_config):
    """run_loop() sends exactly one startup message before the first run."""
    import scheduler as sched_module

    # stop the loop after the first iteration
    sched_module._running.set()

    with patch("scheduler.send_raw") as mock_raw, \
         patch("scheduler.send_signal"), \
         patch("scheduler.run_once", return_value=[]), \
         patch("scheduler.time.sleep", side_effect=lambda _: sched_module._running.clear()):
        sched_module.run_loop(synthetic_config, interval_minutes=60, symbols=["EUR/USD"])

    # first call should be the startup message
    first_call_text = mock_raw.call_args_list[0][0][0]
    assert "started" in first_call_text.lower() or "Scheduler" in first_call_text
