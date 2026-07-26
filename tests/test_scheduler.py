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
# outcome-tracker logging — gated on TradeExecutor's real result (reconciliation
# mismatch root-cause fix, 2026-07-25). Previously main.py logged an "open"
# outcome_tracker row unconditionally on the EXECUTE verdict, before knowing
# whether TradeExecutor would actually place an order — any decline/failure
# left a permanently orphaned row. These tests pin the fix at its new home.
# ---------------------------------------------------------------------------

def _fake_execute_report(symbol="EUR/USD"):
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {"score": 72.0, "vote": {"winning_bias": "BEARISH"}},
        "regime": {"state": "TRENDING"},
        "news": {"news_risk_score": 5.0},
        "engine_outputs": [],
    }


def test_run_once_logs_outcome_on_dry_run_execute(synthetic_config):
    """A dry-run 'would execute' result is a legitimate paper trade and
    must still be logged — dry-run is intentional simulation, not a
    decline."""
    from scheduler import run_once
    from execution.trade_executor import ExecutionResult
    from storage.outcome_tracker import get_open_signals

    synthetic_config["execution"] = {"dry_run": True, "ctrader_enabled": False, "oanda_enabled": False}
    fake_result = ExecutionResult(
        executed=True, symbol="EURUSD", direction="SELL", dry_run=True, trade_id="DRY_RUN",
    )

    with patch("scheduler.run_pipeline", return_value=_fake_execute_report()), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("scheduler.TradeExecutor") as MockExecutor:
        MockExecutor.return_value.execute_from_report.return_value = fake_result
        run_once(synthetic_config, symbols=["EUR/USD"])

    open_signals = get_open_signals()
    assert len(open_signals) == 1
    assert open_signals[0]["symbol"] == "EUR/USD"


def test_run_once_does_not_log_outcome_when_execution_declined(synthetic_config):
    """The bug fix: if TradeExecutor declines (broker rejection, max_open_
    trades, missing prices, an exception, ...), no outcome_tracker row may
    be created — previously this is exactly how orphaned 'open' rows were
    created."""
    from scheduler import run_once
    from execution.trade_executor import ExecutionResult
    from storage.outcome_tracker import get_open_signals

    synthetic_config["execution"] = {"dry_run": True, "ctrader_enabled": False, "oanda_enabled": False}
    fake_result = ExecutionResult(
        executed=False, symbol="EURUSD", skip_reason="Max open trades (5/5)", dry_run=True,
    )

    with patch("scheduler.run_pipeline", return_value=_fake_execute_report()), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("scheduler.TradeExecutor") as MockExecutor:
        MockExecutor.return_value.execute_from_report.return_value = fake_result
        run_once(synthetic_config, symbols=["EUR/USD"])

    assert get_open_signals() == []


def test_run_once_does_not_log_outcome_when_broker_path_unconfigured(synthetic_config):
    """dry_run=False and no broker enabled → TradeExecutor is never even
    instantiated (existing behavior) — must also mean no outcome_tracker
    row (the fix; previously main.py logged one regardless)."""
    from scheduler import run_once
    from storage.outcome_tracker import get_open_signals

    synthetic_config["execution"] = {"dry_run": False, "ctrader_enabled": False, "oanda_enabled": False}

    with patch("scheduler.run_pipeline", return_value=_fake_execute_report()), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("scheduler.TradeExecutor") as MockExecutor:
        run_once(synthetic_config, symbols=["EUR/USD"])
        MockExecutor.assert_not_called()

    assert get_open_signals() == []


# ---------------------------------------------------------------------------
# correlation filter — must span across runs, not just within one tick
# (2026-07-25 Risk Engine audit finding: execute_signals started empty
# every run, so a position open for hours/days gave zero correlation
# protection against a new same-group signal on a later tick).
# ---------------------------------------------------------------------------

def test_run_once_correlation_filter_considers_already_open_positions(synthetic_config):
    """Two USD_MAJORS positions already open from a PREVIOUS run must count
    toward the correlation cap on a NEW candidate this run."""
    from scheduler import run_once
    from storage.outcome_tracker import log_signal

    log_signal(_fake_execute_report("EURUSD"))
    log_signal(_fake_execute_report("GBPUSD"))  # both in USD_MAJORS, MAX_PER_GROUP=2

    with patch("scheduler.run_pipeline") as mock_pipeline, \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        reports = run_once(synthetic_config, symbols=["AUDUSD"])  # 3rd USD_MAJORS symbol

    mock_pipeline.assert_not_called()  # blocked before the pipeline even runs
    assert reports[0].get("correlation_blocked") is True


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


# ---------------------------------------------------------------------------
# live-mode + synthetic-source safety guard
# ---------------------------------------------------------------------------

def test_main_refuses_live_mode_on_synthetic_source(monkeypatch):
    """The unattended scheduler entry point must refuse to start if
    system.mode=live and no real data source is available (no API key,
    no --source override) — never silently trade on fabricated bars."""
    import scheduler as sched_module

    unsafe_config = load_config()
    unsafe_config["system"]["mode"] = "live"
    unsafe_config["data"]["source"] = "synthetic"

    monkeypatch.setattr(sched_module, "load_config", lambda: unsafe_config)
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["scheduler.py", "--once"])

    with pytest.raises(SystemExit, match="synthetic"):
        sched_module.main()


def test_main_allows_live_mode_with_real_source(monkeypatch):
    """Sanity check: the guard doesn't block legitimate live runs."""
    import scheduler as sched_module

    safe_config = load_config()
    safe_config["system"]["mode"] = "live"
    safe_config["data"]["source"] = "twelve_data"

    monkeypatch.setattr(sched_module, "load_config", lambda: safe_config)
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "test-key")
    monkeypatch.setattr("sys.argv", ["scheduler.py", "--once"])
    monkeypatch.setattr(sched_module, "run_once", lambda *a, **kw: [])

    sched_module.main()  # should not raise
