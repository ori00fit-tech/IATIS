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


def test_run_once_constructs_executor_when_dukascopy_jforex_enabled(synthetic_config):
    """dukascopy_jforex_enabled + broker=dukascopy_jforex must reach the
    same broker_live gate ctrader_enabled/oanda_enabled already do — the
    additive config key from Dukascopy JForex Phase 2b."""
    from scheduler import run_once

    synthetic_config["execution"] = {
        "dry_run": False, "broker": "dukascopy_jforex",
        "ctrader_enabled": False, "oanda_enabled": False,
        "dukascopy_jforex_enabled": True, "dukascopy_jforex_fixed_quantity": 0.01,
    }

    with patch("scheduler.run_pipeline", return_value=_fake_execute_report()), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("scheduler.TradeExecutor") as MockExecutor:
        MockExecutor.return_value.execute_from_report.return_value = MagicMock(
            executed=False, dry_run=False, skip_reason="test"
        )
        run_once(synthetic_config, symbols=["EUR/USD"])

    MockExecutor.assert_called_once()
    assert MockExecutor.call_args.kwargs["broker"] == "dukascopy_jforex"
    assert MockExecutor.call_args.kwargs["dukascopy_jforex_fixed_quantity"] == 0.01


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
# Fail-closed no-trade gates (2026-08-15 red-team audit: RE-F1, RE-F3)
#
# Both gates previously swallowed a storage-read exception at debug level
# and proceeded as if nothing was wrong — Symbol Health defaulted to
# "allow the trade" on an unreadable health status, and the correlation
# filter defaulted to "no positions are open" on an unreadable open-
# positions list. Per CLAUDE.md's own rule ("UNKNOWN/INVALID/INCOMPLETE
# -> NO-TRADE unless explicit and justified exception"), both must now
# block instead.
# ---------------------------------------------------------------------------

def test_run_once_blocks_when_symbol_health_check_raises(synthetic_config):
    from scheduler import run_once

    with patch("storage.symbol_health.get_symbol_health",
               side_effect=RuntimeError("D1 unreachable")), \
         patch("scheduler.run_pipeline") as mock_pipeline, \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    mock_pipeline.assert_not_called()  # blocked before the pipeline even runs
    assert reports[0].get("final_verdict") == "NO_TRADE"
    assert reports[0].get("health_check_failed") is True


def test_run_once_correlation_seed_failure_blocks_every_new_execute(synthetic_config):
    from scheduler import run_once

    with patch("storage.outcome_tracker.get_open_signals",
               side_effect=RuntimeError("D1 unreachable")), \
         patch("scheduler.run_pipeline") as mock_pipeline, \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    mock_pipeline.assert_not_called()  # blocked before the pipeline even runs
    assert reports[0].get("correlation_blocked") is True


def test_run_once_correlation_seed_failure_does_not_block_when_filter_disabled(synthetic_config):
    """The fail-closed block only applies when the correlation filter is
    actually enabled — a disabled filter stays fully inert, matching
    every other 'operator explicitly opted out' precedent in this file."""
    from scheduler import run_once

    synthetic_config["features"] = {**synthetic_config.get("features", {}), "correlation_filter": False}

    with patch("storage.outcome_tracker.get_open_signals",
               side_effect=RuntimeError("D1 unreachable")), \
         patch("scheduler.run_pipeline") as mock_pipeline, \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        run_once(synthetic_config, symbols=["EUR/USD"])

    mock_pipeline.assert_called_once()  # not blocked — filter is off


# ---------------------------------------------------------------------------
# Reconciliation auto-repair (2026-07-30)
# ---------------------------------------------------------------------------

def test_run_once_auto_repairs_internal_only_reconciliation_mismatch(synthetic_config, monkeypatch):
    """A signal outcome_tracker still thinks is open, but the broker no
    longer reports, must be closed automatically — this is what stops the
    open-risk/exposure gate from being permanently inflated by stale rows
    (the root cause of the live 'internal open=5, broker open=1' report)."""
    from scheduler import run_once
    from storage.outcome_tracker import get_open_signals, log_signal

    synthetic_config["execution"] = {
        **synthetic_config.get("execution", {}),
        "ctrader_enabled": True, "dry_run": False,
    }

    log_signal(_fake_execute_report("EURUSD"))
    assert len(get_open_signals()) == 1

    class _FakeClient:
        def get_open_positions(self):
            return []  # broker reports nothing open -> EURUSD is internal_only

    monkeypatch.setattr("core.data_providers.get_shared_ctrader_client", lambda: _FakeClient())

    with patch("scheduler.run_pipeline", return_value={"symbol": "EUR/USD", "final_verdict": "NO_TRADE"}), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        run_once(synthetic_config, symbols=["EUR/USD"])

    assert get_open_signals() == []


def test_run_once_does_not_auto_repair_when_flag_disabled(synthetic_config, monkeypatch):
    from scheduler import run_once
    from storage.outcome_tracker import get_open_signals, log_signal

    synthetic_config["execution"] = {
        **synthetic_config.get("execution", {}),
        "ctrader_enabled": True, "dry_run": False,
    }
    synthetic_config["features"] = {
        **synthetic_config.get("features", {}),
        "reconciliation_auto_repair": False,
    }

    log_signal(_fake_execute_report("EURUSD"))

    class _FakeClient:
        def get_open_positions(self):
            return []

    monkeypatch.setattr("core.data_providers.get_shared_ctrader_client", lambda: _FakeClient())

    with patch("scheduler.run_pipeline", return_value={"symbol": "EUR/USD", "final_verdict": "NO_TRADE"}), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        run_once(synthetic_config, symbols=["EUR/USD"])

    assert len(get_open_signals()) == 1  # left alone — flag was off


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


# ---------------------------------------------------------------------------
# cTrader OAuth: proactive token refresh (run_once's per-tick check)
# ---------------------------------------------------------------------------

def test_run_once_calls_proactive_token_refresh_check(synthetic_config):
    from scheduler import run_once

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"), patch(
        "integrations.ctrader.token_manager.get_valid_access_token"
    ) as mock_refresh:
        run_once(synthetic_config, symbols=["EUR/USD"])

    mock_refresh.assert_called_once_with(margin_seconds=86400)


def test_run_once_survives_a_proactive_refresh_failure(synthetic_config):
    """A token-refresh check failure (network down, missing credentials,
    etc.) must never abort the whole scheduler tick — it's logged and the
    run continues exactly as if cTrader weren't configured at all."""
    from scheduler import run_once

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"), patch(
        "integrations.ctrader.token_manager.get_valid_access_token",
        side_effect=RuntimeError("token endpoint unreachable"),
    ):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    assert len(reports) == 1


# ---------------------------------------------------------------------------
# TCA async-fill resolution pass (2026-08-17 fix) — the per-tick pass that
# completes any fill queued as PENDING by record_or_queue_fill() once
# execution/ctrader_client.py's async event stream reports the real broker
# price. Gated identically to reconciliation: only when ctrader_enabled and
# not dry_run.
# ---------------------------------------------------------------------------

def _live_ctrader_config(synthetic_config):
    synthetic_config["execution"] = {
        **synthetic_config.get("execution", {}),
        "ctrader_enabled": True, "dry_run": False,
    }
    # reconcile() itself also reaches for get_shared_ctrader_client() under
    # this same gate — keep it a harmless no-op match so the reconciliation
    # pass doesn't fire an alert/auto-repair unrelated to this test.
    return synthetic_config


class _FakeCtraderClientWithFillUpdate:
    def __init__(self, fill_updates: dict[str, dict] | None = None):
        self._fill_updates = dict(fill_updates or {})
        self.take_fill_update_calls: list[str] = []

    def get_open_positions(self):
        return []

    def take_fill_update(self, position_id: str):
        self.take_fill_update_calls.append(position_id)
        return self._fill_updates.pop(position_id, None)


def test_run_once_resolves_pending_fill_when_ctrader_live(synthetic_config, monkeypatch):
    """A PENDING fill whose broker-truth price has since arrived (via
    take_fill_update) must be resolved this tick — the whole point of the
    async-fill fix: TCA doesn't permanently fail just because the order's
    own synchronous response carried price=0.0."""
    from scheduler import run_once

    _live_ctrader_config(synthetic_config)
    fake_client = _FakeCtraderClientWithFillUpdate({"POS123": {"price": 1.0855}})
    monkeypatch.setattr("core.data_providers.get_shared_ctrader_client", lambda: fake_client)

    with patch("storage.execution_quality.pending_fill_position_ids", return_value=["POS123"]), \
         patch("storage.execution_quality.resolve_pending_fill", return_value=True) as mock_resolve, \
         patch("storage.execution_quality.sweep_stale_pending_fills") as mock_sweep, \
         patch("scheduler.run_pipeline", return_value={"symbol": "EUR/USD", "final_verdict": "NO_TRADE"}), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        run_once(synthetic_config, symbols=["EUR/USD"])

    assert fake_client.take_fill_update_calls == ["POS123"]
    mock_resolve.assert_called_once_with("POS123", 1.0855)
    mock_sweep.assert_called_once()


def test_run_once_does_not_resolve_when_no_fill_update_yet(synthetic_config, monkeypatch):
    """A PENDING position with no broker-truth update available yet (the
    fill hasn't been confirmed) must be left PENDING — never fabricated
    from take_fill_update() returning None."""
    from scheduler import run_once

    _live_ctrader_config(synthetic_config)
    fake_client = _FakeCtraderClientWithFillUpdate({})  # no update queued
    monkeypatch.setattr("core.data_providers.get_shared_ctrader_client", lambda: fake_client)

    with patch("storage.execution_quality.pending_fill_position_ids", return_value=["POS999"]), \
         patch("storage.execution_quality.resolve_pending_fill") as mock_resolve, \
         patch("storage.execution_quality.sweep_stale_pending_fills"), \
         patch("scheduler.run_pipeline", return_value={"symbol": "EUR/USD", "final_verdict": "NO_TRADE"}), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        run_once(synthetic_config, symbols=["EUR/USD"])

    assert fake_client.take_fill_update_calls == ["POS999"]
    mock_resolve.assert_not_called()


def test_run_once_skips_pending_fill_resolution_in_dry_run(synthetic_config):
    """dry_run=True has no live broker session to poll — the resolution
    pass must never even attempt to reach for a cTrader client."""
    from scheduler import run_once

    synthetic_config["execution"] = {
        **synthetic_config.get("execution", {}), "ctrader_enabled": True, "dry_run": True,
    }

    with patch("storage.execution_quality.pending_fill_position_ids") as mock_pending, \
         patch("scheduler.run_pipeline", return_value={"symbol": "EUR/USD", "final_verdict": "NO_TRADE"}), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        run_once(synthetic_config, symbols=["EUR/USD"])

    mock_pending.assert_not_called()


def test_run_once_skips_pending_fill_resolution_when_ctrader_disabled(synthetic_config):
    from scheduler import run_once

    synthetic_config["execution"] = {
        **synthetic_config.get("execution", {}), "ctrader_enabled": False, "dry_run": False,
    }

    with patch("storage.execution_quality.pending_fill_position_ids") as mock_pending, \
         patch("scheduler.run_pipeline", return_value={"symbol": "EUR/USD", "final_verdict": "NO_TRADE"}), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        run_once(synthetic_config, symbols=["EUR/USD"])

    mock_pending.assert_not_called()


def test_run_once_survives_pending_fill_resolution_failure(synthetic_config, monkeypatch):
    """A storage/client hiccup in the TCA resolution pass must never abort
    the scheduler tick — same non-fatal contract as reconciliation."""
    from scheduler import run_once

    _live_ctrader_config(synthetic_config)
    monkeypatch.setattr(
        "core.data_providers.get_shared_ctrader_client",
        lambda: (_ for _ in ()).throw(RuntimeError("cTrader session down")),
    )

    with patch("scheduler.run_pipeline", return_value={"symbol": "EUR/USD", "final_verdict": "NO_TRADE"}), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    assert len(reports) == 1


# ---------------------------------------------------------------------------
# Kill switch (storage/kill_switch.py) — scheduler-level integration
# ---------------------------------------------------------------------------

def test_run_once_blocks_execution_when_kill_switch_active(synthetic_config, tmp_path):
    """The kill switch must suppress order placement entirely — no
    TradeExecutor is even constructed — and the report must be annotated
    so an operator reviewing it can see exactly why nothing happened."""
    from scheduler import run_once
    from storage.kill_switch import activate

    ks_path = tmp_path / "kill_switch.json"
    activate("manual halt for testing", path=ks_path)

    synthetic_config["execution"] = {"dry_run": True, "ctrader_enabled": False, "oanda_enabled": False}

    with patch("scheduler.run_pipeline", return_value=_fake_execute_report()), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("scheduler.TradeExecutor") as MockExecutor, \
         patch("storage.kill_switch.STATE_PATH", ks_path):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    MockExecutor.assert_not_called()
    assert reports[0]["kill_switch_blocked"] is True
    assert "kill switch" in reports[0]["summary"].lower()


def test_run_once_executes_normally_when_kill_switch_inactive(synthetic_config, tmp_path):
    """Regression: the default (never-activated) state must not change
    today's behavior at all."""
    from scheduler import run_once
    from execution.trade_executor import ExecutionResult

    ks_path = tmp_path / "kill_switch.json"  # never created -> inactive
    synthetic_config["execution"] = {"dry_run": True, "ctrader_enabled": False, "oanda_enabled": False}
    fake_result = ExecutionResult(executed=True, symbol="EURUSD", direction="SELL", dry_run=True, trade_id="DRY_RUN")

    with patch("scheduler.run_pipeline", return_value=_fake_execute_report()), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("scheduler.TradeExecutor") as MockExecutor, \
         patch("storage.kill_switch.STATE_PATH", ks_path):
        MockExecutor.return_value.execute_from_report.return_value = fake_result
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    MockExecutor.assert_called_once()
    assert "kill_switch_blocked" not in reports[0]


def test_run_once_fails_closed_when_kill_switch_state_unreadable(synthetic_config, tmp_path):
    """A corrupted/unreadable kill-switch state must block execution,
    never silently allow it — same fail-closed discipline as the
    correlation-filter (RE-F3) and symbol-health (RE-F1) gates."""
    from scheduler import run_once

    ks_path = tmp_path / "kill_switch.json"
    ks_path.write_text("{not valid json")
    synthetic_config["execution"] = {"dry_run": True, "ctrader_enabled": False, "oanda_enabled": False}

    with patch("scheduler.run_pipeline", return_value=_fake_execute_report()), \
         patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("scheduler.TradeExecutor") as MockExecutor, \
         patch("storage.kill_switch.STATE_PATH", ks_path):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    MockExecutor.assert_not_called()
    assert reports[0]["kill_switch_blocked"] is True


# ---------------------------------------------------------------------------
# Post-Trade Control / Incident Register wiring (execution/post_trade_monitor.py)
# ---------------------------------------------------------------------------

def test_run_once_invokes_post_trade_monitor_scans(synthetic_config):
    from scheduler import run_once

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("execution.post_trade_monitor.run_all_scans") as mock_scans:
        run_once(synthetic_config, symbols=["EUR/USD"])

    mock_scans.assert_called_once()
    _, kwargs = mock_scans.call_args
    assert "reconciliation_report" in kwargs
    assert "reconciliation_repair" in kwargs


def test_run_once_post_trade_monitor_failure_does_not_abort_run(synthetic_config):
    from scheduler import run_once

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("execution.post_trade_monitor.run_all_scans", side_effect=RuntimeError("boom")):
        reports = run_once(synthetic_config, symbols=["EUR/USD"])

    assert len(reports) == 1


def test_run_once_respects_post_trade_monitoring_feature_flag(synthetic_config):
    from scheduler import run_once

    synthetic_config.setdefault("features", {})["post_trade_monitoring"] = False

    with patch("scheduler.send_raw"), patch("scheduler.send_signal"), \
         patch("execution.post_trade_monitor.run_all_scans") as mock_scans:
        run_once(synthetic_config, symbols=["EUR/USD"])

    mock_scans.assert_not_called()
