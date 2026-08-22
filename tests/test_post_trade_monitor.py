"""tests/test_post_trade_monitor.py
------------------------------------
execution/post_trade_monitor.py — the orchestration layer that turns
already-computed evidence from reconciliation / execution_attempts /
execution_quality / kill_switch / forward_review into durable incidents
via storage/post_trade_incidents.py. Every scan is exercised against
hand-built evidence (never a live broker/D1-backed subsystem call) so
these tests never depend on real reconciliation/broker state.
"""
from __future__ import annotations

from types import SimpleNamespace

from execution.post_trade_monitor import (
    LATENCY_ANOMALY_P95_SECONDS,
    REJECTED_STREAK_THRESHOLD,
    post_trade_summary,
    run_all_scans,
    scan_execution_attempts,
    scan_execution_quality,
    scan_forward_review,
    scan_kill_switch,
    scan_reconciliation,
)
from storage.post_trade_incidents import (
    CRITICAL,
    HIGH,
    KILL_SWITCH_ACTIVATED,
    KILL_SWITCH_CORRUPTION,
    KILL_SWITCH_STATE_CHANGE,
    LOW,
    MEDIUM,
    OPEN,
    OUTCOME_RECONCILIATION_GAP,
    RECONCILIATION_CONTROL_UNAVAILABLE,
    RECONCILIATION_MISMATCH,
    RESOLVED,
    get_incident,
    list_incidents,
)

# ── scan_reconciliation ──────────────────────────────────────────────────


def test_scan_reconciliation_no_stored_result_is_a_noop(monkeypatch):
    import execution.reconciliation as reconciliation

    monkeypatch.setattr(reconciliation, "last_result", lambda: None)
    assert scan_reconciliation() == []


def test_scan_reconciliation_match_produces_no_incidents():
    report = {"status": "match", "checked_at": "t0", "broker_only": [], "internal_only": []}
    assert scan_reconciliation(report) == []


def test_scan_reconciliation_broker_only_creates_critical_mismatch():
    report = {
        "status": "mismatch", "checked_at": "t0",
        "broker_only": ["EURUSD"], "internal_only": [],
    }
    touched = scan_reconciliation(report)
    assert len(touched) == 1
    row = get_incident(touched[0])
    assert row["control_type"] == RECONCILIATION_MISMATCH
    assert row["severity"] == CRITICAL
    assert row["symbol"] == "EURUSD"
    assert row["status"] == OPEN


def test_scan_reconciliation_internal_only_without_repair_stays_open():
    report = {
        "status": "mismatch", "checked_at": "t0",
        "broker_only": [], "internal_only": ["XAUUSD"],
    }
    touched = scan_reconciliation(report)
    row = get_incident(touched[0])
    assert row["control_type"] == OUTCOME_RECONCILIATION_GAP
    assert row["severity"] == HIGH
    assert row["status"] == OPEN


def test_scan_reconciliation_internal_only_auto_resolves_when_repaired():
    report = {
        "status": "mismatch", "checked_at": "t0",
        "broker_only": [], "internal_only": ["XAUUSD"],
    }
    repair = {"repaired": ["sig123"], "skipped_no_open_signal": []}
    touched = scan_reconciliation(report, repair)
    row = get_incident(touched[0])
    assert row["status"] == RESOLVED
    assert row["resolution"]["evidence"]["repaired_signal_ids"] == ["sig123"]


def test_scan_reconciliation_internal_only_stays_open_when_skipped_no_open_signal():
    report = {
        "status": "mismatch", "checked_at": "t0",
        "broker_only": [], "internal_only": ["XAUUSD"],
    }
    repair = {"repaired": [], "skipped_no_open_signal": ["XAUUSD"]}
    touched = scan_reconciliation(report, repair)
    assert get_incident(touched[0])["status"] == OPEN


def test_scan_reconciliation_control_failure_skip_creates_medium_incident():
    report = {
        "status": "skipped", "checked_at": "t0",
        "reason": "broker client unavailable", "skip_reason_kind": "control_failure",
    }
    touched = scan_reconciliation(report)
    assert len(touched) == 1
    row = get_incident(touched[0])
    assert row["control_type"] == RECONCILIATION_CONTROL_UNAVAILABLE
    assert row["severity"] == MEDIUM


def test_scan_reconciliation_not_live_skip_is_healthy_not_an_incident():
    report = {
        "status": "skipped", "checked_at": "t0",
        "reason": "broker execution not live", "skip_reason_kind": "not_live",
    }
    assert scan_reconciliation(report) == []


def test_scan_reconciliation_dedups_across_repeated_calls():
    report = {
        "status": "mismatch", "checked_at": "t0",
        "broker_only": ["EURUSD"], "internal_only": [],
    }
    first = scan_reconciliation(report)
    second = scan_reconciliation({**report, "checked_at": "t1"})
    assert first == second
    assert len(list_incidents(control_type=RECONCILIATION_MISMATCH)) == 1


# ── scan_execution_attempts ─────────────────────────────────────────────


def test_scan_execution_attempts_timeout_unknown_creates_critical_incident():
    from storage.execution_attempts import TIMEOUT_UNKNOWN, record_execution_attempt

    record_execution_attempt(
        symbol="EURUSD", broker="ctrader", direction="BUY", status=TIMEOUT_UNKNOWN,
        broker_error_message="Order timed out after 15.0s",
    )
    touched = scan_execution_attempts()
    assert len(touched) == 1
    row = get_incident(touched[0])
    assert row["control_type"] == "EXECUTION_TIMEOUT_UNKNOWN"
    assert row["severity"] == CRITICAL
    assert row["symbol"] == "EURUSD"


def test_scan_execution_attempts_does_not_reopen_a_resolved_timeout():
    from storage.execution_attempts import TIMEOUT_UNKNOWN, record_execution_attempt
    from storage.post_trade_incidents import resolve_incident

    record_execution_attempt(
        symbol="EURUSD", broker="ctrader", direction="BUY", status=TIMEOUT_UNKNOWN,
    )
    touched = scan_execution_attempts()
    resolve_incident(touched[0], actor="tester", reason="confirmed never filled at the broker")
    again = scan_execution_attempts()
    assert again == []


def test_scan_execution_attempts_rejected_streak_below_threshold_is_a_noop():
    from storage.execution_attempts import REJECTED, record_execution_attempt

    assert REJECTED_STREAK_THRESHOLD >= 2
    for _ in range(REJECTED_STREAK_THRESHOLD - 1):
        record_execution_attempt(symbol="GBPUSD", broker="ctrader", direction="SELL", status=REJECTED)
    assert scan_execution_attempts() == []


def test_scan_execution_attempts_rejected_streak_at_threshold_creates_incident():
    from storage.execution_attempts import REJECTED, record_execution_attempt

    for _ in range(REJECTED_STREAK_THRESHOLD):
        record_execution_attempt(symbol="GBPUSD", broker="ctrader", direction="SELL", status=REJECTED)
    touched = scan_execution_attempts()
    assert len(touched) == 1
    row = get_incident(touched[0])
    assert row["control_type"] == "EXECUTION_REJECTED"
    assert row["severity"] == MEDIUM
    assert row["symbol"] == "GBPUSD"


def test_scan_execution_attempts_streak_broken_by_accepted_is_not_counted():
    from storage.execution_attempts import ACCEPTED, REJECTED, record_execution_attempt

    record_execution_attempt(symbol="AUDUSD", broker="ctrader", direction="BUY", status=REJECTED)
    record_execution_attempt(symbol="AUDUSD", broker="ctrader", direction="BUY", status=ACCEPTED)
    for _ in range(REJECTED_STREAK_THRESHOLD):
        record_execution_attempt(symbol="AUDUSD", broker="ctrader", direction="BUY", status=REJECTED)
    # Most-recent-first contiguous run only includes the last REJECTED_STREAK_THRESHOLD.
    touched = scan_execution_attempts()
    assert len(touched) == 1


# ── scan_execution_quality ──────────────────────────────────────────────


def _log_real_fill(symbol="EURUSD", intended=1.1000, fill=1.1000, direction="BUY", trade_id="t1"):
    from storage.execution_quality import log_fill

    exec_result = SimpleNamespace(
        executed=True, dry_run=False, symbol=symbol, direction=direction,
        entry_price=fill, trade_id=trade_id, units=1000.0,
    )
    report = {"symbol": symbol, "entry_price": intended, "stop_loss": intended - 0.0050 if direction == "BUY" else intended + 0.0050}
    assert log_fill(report, exec_result, broker="ctrader")


def test_scan_execution_quality_healthy_is_a_noop():
    _log_real_fill()
    assert scan_execution_quality() == []


def test_scan_execution_quality_unavailable_fill_creates_high_incident():
    from storage.execution_quality import mark_pending_fill_unavailable, queue_pending_fill

    exec_result = SimpleNamespace(
        executed=True, dry_run=False, symbol="EURUSD", direction="BUY",
        entry_price=0.0, trade_id="pending1", units=1000.0,
    )
    report = {"symbol": "EURUSD", "entry_price": 1.1000, "stop_loss": 1.0950}
    assert queue_pending_fill(report, exec_result, broker="ctrader")
    assert mark_pending_fill_unavailable("pending1", reason="broker never confirmed")

    touched = scan_execution_quality()
    control_types = {get_incident(i)["control_type"] for i in touched}
    assert "FILL_UNAVAILABLE" in control_types


def test_scan_execution_quality_slippage_anomaly():
    # Adverse BUY slippage: fill well above intended, several pips beyond
    # the 3x-assumption threshold (0.5 pips assumption -> 1.5 pip trigger).
    _log_real_fill(symbol="EURUSD", intended=1.1000, fill=1.1010, direction="BUY", trade_id="slip1")
    touched = scan_execution_quality()
    control_types = {get_incident(i)["control_type"] for i in touched}
    assert "SLIPPAGE_ANOMALY" in control_types


def test_scan_execution_quality_latency_anomaly():
    from datetime import datetime, timedelta, timezone

    from storage import d1_client
    from storage.execution_quality import _init_pending

    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=LATENCY_ANOMALY_P95_SECONDS + 10)).isoformat()
    with d1_client.d1_connection() as con:
        _init_pending(con)
        con.execute(
            """INSERT INTO pending_fills
               (position_id, status, ts_queued, symbol, direction, broker, trade_id, intended_price)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("latency1", "PENDING", stale_ts, "EURUSD", "BUY", "ctrader", "latency1", 1.1000),
        )
    from storage.execution_quality import resolve_pending_fill
    assert resolve_pending_fill("latency1", 1.1000)

    touched = scan_execution_quality()
    control_types = {get_incident(i)["control_type"] for i in touched}
    assert "LATENCY_ANOMALY" in control_types


# ── scan_kill_switch ─────────────────────────────────────────────────────


def test_scan_kill_switch_inactive_default_is_a_noop():
    assert scan_kill_switch({"active": False, "reason": None, "activated_at": None,
                              "activated_by": None, "deactivated_at": None, "deactivated_by": None}) == []


def test_scan_kill_switch_active_creates_high_incident():
    state = {"active": True, "reason": "manual halt for maintenance", "activated_at": "t0",
              "activated_by": "operator", "deactivated_at": None, "deactivated_by": None}
    touched = scan_kill_switch(state)
    assert len(touched) == 1
    row = get_incident(touched[0])
    assert row["control_type"] == KILL_SWITCH_ACTIVATED
    assert row["severity"] == HIGH


def test_scan_kill_switch_corruption_creates_critical_incident():
    state = {"active": True, "reason": "kill_switch.json unreadable (bad json) — failing closed",
              "activated_at": None, "activated_by": None, "deactivated_at": None, "deactivated_by": None}
    touched = scan_kill_switch(state)
    row = get_incident(touched[0])
    assert row["control_type"] == KILL_SWITCH_CORRUPTION
    assert row["severity"] == CRITICAL


def test_scan_kill_switch_deactivation_creates_low_informational_incident():
    state = {"active": False, "reason": "resolved", "activated_at": "t0", "activated_by": "operator",
              "deactivated_at": "t1", "deactivated_by": "operator"}
    touched = scan_kill_switch(state)
    row = get_incident(touched[0])
    assert row["control_type"] == KILL_SWITCH_STATE_CHANGE
    assert row["severity"] == LOW


def test_scan_kill_switch_deactivation_dedups_on_timestamp():
    state = {"active": False, "reason": "resolved", "activated_at": "t0", "activated_by": "operator",
              "deactivated_at": "t1", "deactivated_by": "operator"}
    first = scan_kill_switch(state)
    second = scan_kill_switch(state)
    assert first == second


def test_scan_kill_switch_reads_live_state_when_none_passed(monkeypatch):
    import storage.kill_switch as kill_switch

    monkeypatch.setattr(kill_switch, "get_state", lambda: {
        "active": True, "reason": "operator halt", "activated_at": "t0",
        "activated_by": "operator", "deactivated_at": None, "deactivated_by": None,
    })
    touched = scan_kill_switch()
    assert len(touched) == 1


# ── scan_forward_review ─────────────────────────────────────────────────


def test_scan_forward_review_no_rows_is_a_noop(monkeypatch):
    import scripts.forward_review as forward_review

    monkeypatch.setattr(forward_review, "_closed_outcomes", lambda: [])
    assert scan_forward_review() == []


def test_scan_forward_review_triggered_rule_creates_medium_incident(monkeypatch):
    import scripts.forward_review as forward_review

    rows = [{"symbol": "EURUSD", "outcome": "win" if i < 5 else "loss", "pnl_usd": 10.0 if i < 5 else -10.0}
            for i in range(40)]
    monkeypatch.setattr(forward_review, "_closed_outcomes", lambda: rows)
    touched = scan_forward_review()
    assert len(touched) == 1
    row = get_incident(touched[0])
    assert row["control_type"] == "FORWARD_REVIEW_TRIGGERED"
    assert row["severity"] == MEDIUM
    assert "D001" in row["fingerprint"]


def test_scan_forward_review_registry_read_failure_is_non_fatal(monkeypatch):
    import scripts.forward_review as forward_review

    monkeypatch.setattr(forward_review, "REGISTRY", "not-a-real-path-object")
    assert scan_forward_review() == []


# ── run_all_scans / post_trade_summary ──────────────────────────────────


def test_run_all_scans_returns_a_result_per_scan_and_never_raises():
    results = run_all_scans()
    assert set(results.keys()) == {
        "reconciliation", "execution_attempts", "execution_quality",
        "kill_switch", "forward_review",
    }
    for value in results.values():
        assert isinstance(value, list)


def test_run_all_scans_isolates_one_scan_failure_from_the_others(monkeypatch):
    import execution.post_trade_monitor as monitor

    def boom():
        raise RuntimeError("subsystem exploded")

    monkeypatch.setattr(monitor, "scan_kill_switch", boom)
    results = run_all_scans()
    assert results["kill_switch"] == []
    assert "execution_attempts" in results  # other scans still ran


def test_post_trade_summary_shape_and_never_raises():
    summary = post_trade_summary()
    assert "generated_at" in summary
    assert "incident_counts_by_status" in summary
    assert "incident_counts_by_severity" in summary
    assert "reconciliation" in summary
    assert "kill_switch" in summary
    assert "execution_quality" in summary
    assert set(summary["execution_quality"].keys()) == {"unavailable_fill_count", "latency", "slippage"}


def test_post_trade_summary_degrades_gracefully_on_a_read_failure(monkeypatch):
    import storage.post_trade_incidents as post_trade_incidents

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(post_trade_incidents, "counts_by_status", boom)
    summary = post_trade_summary()
    assert summary["incident_counts_by_status"] == {}
