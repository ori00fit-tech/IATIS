"""
execution/post_trade_monitor.py
----------------------------------
Orchestration layer for the Unified Post-Trade Control / Incident
Register (storage/post_trade_incidents.py). This module does NOT
reconcile positions, compute slippage, evaluate kill-switch state, or
apply forward-review decision rules — every one of those already exists
and stays exactly where it is:

    execution/reconciliation.py    — broker-vs-internal position diff
    storage/execution_attempts.py  — attempted-order log
    storage/execution_quality.py   — TCA (slippage/latency/pending fills)
    storage/kill_switch.py         — the operational kill switch
    scripts/forward_review.py      — pre-registered forward decision rules

Each `scan_*` function below ONLY reads an already-computed result from
one of those subsystems and turns it into a durable incident when the
evidence crosses a named, documented threshold — it never recomputes,
never re-diffs, never invents a threshold that isn't already this
codebase's own convention. This is purely a monitoring/evidence-
aggregation layer: nothing here gates a trading decision, deactivates
the kill switch, closes a position, or mutates the registry/config —
those invariants are load-bearing and are pinned by dedicated tests.

Caller contract: `run_all_scans()` is meant to be called once per
scheduler tick, in its own try/except (which it also applies internally
per-scan), so a monitoring failure can never affect trading.
`post_trade_summary()` is the separate, read-only snapshot safe to call
from the API/route layer on every page load.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage.post_trade_incidents import (
    CRITICAL,
    EXECUTION_REJECTED,
    EXECUTION_TIMEOUT_UNKNOWN,
    FILL_UNAVAILABLE,
    FORWARD_REVIEW_TRIGGERED,
    HIGH,
    KILL_SWITCH_ACTIVATED,
    KILL_SWITCH_CORRUPTION,
    KILL_SWITCH_STATE_CHANGE,
    LATENCY_ANOMALY,
    LOW,
    MEDIUM,
    OUTCOME_RECONCILIATION_GAP,
    RECONCILIATION_CONTROL_UNAVAILABLE,
    RECONCILIATION_MISMATCH,
    SLIPPAGE_ANOMALY,
    has_terminal_incident,
    resolve_incident,
    upsert_incident,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Named, tunable policy defaults (never live risk parameters — see
# risk/pretrade_limits.py for those) ────────────────────────────────────
REJECTED_STREAK_THRESHOLD = 3          # consecutive REJECTED attempts, same symbol
SLIPPAGE_ANOMALY_MULTIPLE = 3.0        # x BACKTEST_SLIPPAGE_ASSUMPTION_PIPS
LATENCY_ANOMALY_P95_SECONDS = 30.0     # async-fill p95 latency


def scan_reconciliation(
    report: dict[str, Any] | None = None,
    repair: dict[str, Any] | None = None,
) -> list[str]:
    """Reconciliation is authoritative for broker-vs-internal mismatches
    (execution/reconciliation.py) — this only turns its ALREADY-COMPUTED
    report into incidents. `report` defaults to `last_result()` (a
    stored read, never `reconcile()` itself — the caller must never open
    a second cTrader session; see execution/routes/outcomes.py's own
    documented constraint). `repair` is the optional same-tick
    `repair_mismatches()` result scheduler.py already computed, used
    only to auto-RESOLVE an OUTCOME_RECONCILIATION_GAP once it has
    genuinely been closed against real broker-confirmed evidence — see
    storage/post_trade_incidents.py's own docstring for why this is the
    one narrow exception to "never auto-transition." A broker_only
    mismatch is never auto-resolved by anything in this module."""
    if report is None:
        from execution.reconciliation import last_result
        report = last_result()
    if not report:
        return []

    touched: list[str] = []
    status = report.get("status")
    checked_at = report.get("checked_at")

    if status == "mismatch":
        for symbol in report.get("broker_only") or []:
            iid = upsert_incident(
                fingerprint=f"RECON_MISMATCH:BROKER_ONLY:{symbol}",
                control_type=RECONCILIATION_MISMATCH,
                severity=CRITICAL,
                source_component="execution.reconciliation",
                symbol=symbol,
                expected_state="internal tracker has an open position for this symbol",
                actual_state="broker reports this symbol open, internal tracker does not",
                evidence_reference={"checked_at": checked_at, "report": report},
            )
            if iid:
                touched.append(iid)

        skipped_no_open = set((repair or {}).get("skipped_no_open_signal", []))
        for symbol in report.get("internal_only") or []:
            iid = upsert_incident(
                fingerprint=f"RECON_GAP:INTERNAL_ONLY:{symbol}",
                control_type=OUTCOME_RECONCILIATION_GAP,
                severity=HIGH,
                source_component="execution.reconciliation",
                symbol=symbol,
                expected_state="broker holds an open position for this symbol",
                actual_state="internal tracker reports this symbol open, broker does not",
                evidence_reference={"checked_at": checked_at, "report": report, "repair": repair},
            )
            if not iid:
                continue
            touched.append(iid)
            if repair and symbol not in skipped_no_open:
                try:
                    resolve_incident(
                        iid,
                        actor="post_trade_monitor",
                        reason=(
                            "reconciliation auto-repair closed the stale internal-only "
                            "signal(s) for this symbol against real broker-confirmed evidence"
                        ),
                        evidence={"repaired_signal_ids": repair.get("repaired", [])},
                    )
                except ValueError:
                    # Already ACKNOWLEDGED/terminal by an operator — leave their state alone.
                    pass
    elif status == "skipped" and report.get("skip_reason_kind") == "control_failure":
        iid = upsert_incident(
            fingerprint="RECON_CONTROL_UNAVAILABLE",
            control_type=RECONCILIATION_CONTROL_UNAVAILABLE,
            severity=MEDIUM,
            source_component="execution.reconciliation",
            expected_state="reconciliation runs every tick while the broker path is live",
            actual_state=report.get("reason"),
            evidence_reference={"checked_at": checked_at, "report": report},
        )
        if iid:
            touched.append(iid)

    return touched


def scan_execution_attempts(limit: int = 50) -> list[str]:
    """Reuses storage.execution_attempts.recent_attempts() verbatim.
    TIMEOUT_UNKNOWN rows are the sharpest signal — broker communication
    failed AFTER the order was sent, so the real order/position state is
    genuinely unconfirmed (never inferred as filled or unfilled) — and
    get one incident per attempt. REJECTED attempts only become an
    incident once REJECTED_STREAK_THRESHOLD consecutive rejections land
    on the SAME symbol: a single rejection is often a normal, expected
    gate (e.g. a momentary price/volume validation miss); a run of them
    on one symbol is what's actually noteworthy.

    Known, accepted limitation: execution_attempts is an append-only log
    with no mutable status, so an already-RESOLVED incident's underlying
    attempt can still appear in a later `limit`-sized window — this scan
    checks has_terminal_incident() first specifically to avoid re-opening
    dispositioned history rather than silently re-flagging it."""
    from storage.execution_attempts import REJECTED, TIMEOUT_UNKNOWN, recent_attempts

    touched: list[str] = []
    try:
        attempts = recent_attempts(limit=limit)
    except Exception as exc:  # noqa: BLE001 — monitoring must never affect trading
        logger.warning(f"post_trade_monitor: execution_attempts read failed (non-fatal): {exc}")
        return touched

    for row in attempts:
        if row.get("status") != TIMEOUT_UNKNOWN:
            continue
        fingerprint = f"EXEC_TIMEOUT_UNKNOWN:{row.get('id')}"
        if has_terminal_incident(fingerprint):
            continue
        iid = upsert_incident(
            fingerprint=fingerprint,
            control_type=EXECUTION_TIMEOUT_UNKNOWN,
            severity=CRITICAL,
            source_component="storage.execution_attempts",
            symbol=row.get("symbol"),
            order_id=row.get("position_id"),
            signal_id=row.get("signal_id"),
            expected_state="broker confirms ACCEPTED or REJECTED",
            actual_state="broker communication timed out — real order state unconfirmed",
            evidence_reference={"attempt": row},
        )
        if iid:
            touched.append(iid)

    # Per-symbol REJECTED streak — most-recent contiguous run only
    # (recent_attempts() is newest-first).
    by_symbol: dict[str, list[dict]] = {}
    for row in attempts:
        symbol = row.get("symbol") or ""
        if symbol:
            by_symbol.setdefault(symbol, []).append(row)

    for symbol, rows in by_symbol.items():
        streak: list[dict] = []
        for row in rows:
            if row.get("status") == REJECTED:
                streak.append(row)
            else:
                break
        if len(streak) < REJECTED_STREAK_THRESHOLD:
            continue
        iid = upsert_incident(
            fingerprint=f"EXEC_REJECTED_STREAK:{symbol}",
            control_type=EXECUTION_REJECTED,
            severity=MEDIUM,
            source_component="storage.execution_attempts",
            symbol=symbol,
            expected_state=f"fewer than {REJECTED_STREAK_THRESHOLD} consecutive REJECTED attempts",
            actual_state=f"{len(streak)} consecutive REJECTED attempts",
            evidence_reference={
                "latest_broker_error": streak[0].get("broker_error_message"),
                "attempts": streak,
            },
        )
        if iid:
            touched.append(iid)

    return touched


def scan_execution_quality() -> list[str]:
    """Reuses storage.execution_quality.summary()/unavailable_fill_count()/
    latency_stats() verbatim — no slippage/latency math happens here."""
    from storage.execution_quality import (
        BACKTEST_SLIPPAGE_ASSUMPTION_PIPS,
        latency_stats,
        summary,
        unavailable_fill_count,
    )

    touched: list[str] = []
    try:
        s = summary()
        unavailable = unavailable_fill_count()
        latency = latency_stats()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"post_trade_monitor: execution_quality read failed (non-fatal): {exc}")
        return touched

    if unavailable > 0:
        iid = upsert_incident(
            fingerprint="TCA_FILL_UNAVAILABLE",
            control_type=FILL_UNAVAILABLE,
            severity=HIGH,
            source_component="storage.execution_quality",
            expected_state="every queued fill resolves to a real broker fill price",
            actual_state=f"{unavailable} fill(s) never resolved a broker price before the bounded wait expired",
            evidence_reference={"unavailable_count": unavailable},
        )
        if iid:
            touched.append(iid)

    overall = s.get("overall", {})
    mean_pips = overall.get("mean_slippage_pips")
    anomaly_threshold = BACKTEST_SLIPPAGE_ASSUMPTION_PIPS * SLIPPAGE_ANOMALY_MULTIPLE
    if mean_pips is not None and mean_pips > anomaly_threshold:
        iid = upsert_incident(
            fingerprint="TCA_SLIPPAGE_ANOMALY",
            control_type=SLIPPAGE_ANOMALY,
            severity=MEDIUM,
            source_component="storage.execution_quality",
            expected_state=f"mean slippage <= {anomaly_threshold:.2f} pips "
                            f"({SLIPPAGE_ANOMALY_MULTIPLE}x the backtest assumption)",
            actual_state=f"mean slippage {mean_pips} pips over {overall.get('n')} fill(s)",
            evidence_reference={"overall": overall, "backtest_assumption_pips": BACKTEST_SLIPPAGE_ASSUMPTION_PIPS},
        )
        if iid:
            touched.append(iid)

    p95 = latency.get("p95_seconds")
    if p95 is not None and p95 > LATENCY_ANOMALY_P95_SECONDS:
        iid = upsert_incident(
            fingerprint="TCA_LATENCY_ANOMALY",
            control_type=LATENCY_ANOMALY,
            severity=MEDIUM,
            source_component="storage.execution_quality",
            expected_state=f"p95 async-fill latency <= {LATENCY_ANOMALY_P95_SECONDS}s",
            actual_state=f"p95 async-fill latency {p95}s over {latency.get('n')} fill(s)",
            evidence_reference={"latency_stats": latency},
        )
        if iid:
            touched.append(iid)

    return touched


def scan_kill_switch(state: dict[str, Any] | None = None) -> list[str]:
    """Reuses storage.kill_switch.get_state() verbatim — never inspects
    or duplicates its fail-closed logic, only reports on the result.
    Detects corruption via the exact reason string that module's own
    get_state() emits on a corrupted/unreadable file, rather than
    re-implementing its own file-read/parse."""
    from storage.kill_switch import get_state

    touched: list[str] = []
    try:
        state = state if state is not None else get_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"post_trade_monitor: kill switch read failed (non-fatal): {exc}")
        return touched

    reason = state.get("reason") or ""
    is_corruption = "unreadable" in reason and "failing closed" in reason

    if state.get("active") and is_corruption:
        iid = upsert_incident(
            fingerprint="KILL_SWITCH_CORRUPTION",
            control_type=KILL_SWITCH_CORRUPTION,
            severity=CRITICAL,
            source_component="storage.kill_switch",
            expected_state="kill_switch.json is a readable JSON object",
            actual_state=reason,
            evidence_reference={"state": state},
        )
        if iid:
            touched.append(iid)
    elif state.get("active"):
        iid = upsert_incident(
            fingerprint=f"KILL_SWITCH_ACTIVE:{state.get('activated_at')}",
            control_type=KILL_SWITCH_ACTIVATED,
            severity=HIGH,
            source_component="storage.kill_switch",
            expected_state="kill switch inactive (new orders permitted)",
            actual_state=f"active since {state.get('activated_at')} by {state.get('activated_by')}: {reason}",
            evidence_reference={"state": state},
        )
        if iid:
            touched.append(iid)
    elif state.get("deactivated_at"):
        # Informational, LOW severity — one durable record per unique
        # deactivation event (fingerprint keyed on the timestamp), never
        # re-flagged as a new incident on later, unrelated ticks.
        iid = upsert_incident(
            fingerprint=f"KILL_SWITCH_DEACTIVATED:{state.get('deactivated_at')}",
            control_type=KILL_SWITCH_STATE_CHANGE,
            severity=LOW,
            source_component="storage.kill_switch",
            expected_state="informational only — no control gate applies",
            actual_state=f"deactivated at {state.get('deactivated_at')} by {state.get('deactivated_by')} "
                         f"(was: {reason})",
            evidence_reference={"state": state},
        )
        if iid:
            touched.append(iid)

    return touched


def scan_forward_review() -> list[str]:
    """Reuses scripts.forward_review.evaluate_rules() verbatim — the
    pre-registered decision rules and their comparison logic are never
    duplicated here. A registry read failure (e.g. no _decision_rules
    block yet) is a normal, expected state, not an incident."""
    import json

    from scripts.forward_review import CARRIERS, FX, REGISTRY, _bucket_stats, _closed_outcomes, evaluate_rules

    touched: list[str] = []
    try:
        rules = json.loads(REGISTRY.read_text()).get("_decision_rules", {})
        if not rules:
            return touched
        rows = _closed_outcomes()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"post_trade_monitor: forward review read failed (non-fatal): {exc}")
        return touched

    buckets = {"fx": _bucket_stats(rows, FX), "carriers": _bucket_stats(rows, CARRIERS)}
    for verdict in evaluate_rules(rules, buckets):
        if not verdict.get("triggered"):
            continue
        iid = upsert_incident(
            fingerprint=f"FORWARD_REVIEW:{verdict['rule_id']}",
            control_type=FORWARD_REVIEW_TRIGGERED,
            severity=MEDIUM,
            source_component="scripts.forward_review",
            expected_state=f"{verdict['metric']} {verdict['op']} {verdict['threshold']} not yet reached",
            actual_state=f"{verdict['metric']}={verdict['value']} (n={verdict['n']}) -> {verdict['action']}",
            evidence_reference={"verdict": verdict},
        )
        if iid:
            touched.append(iid)

    return touched


def run_all_scans(
    reconciliation_report: dict[str, Any] | None = None,
    reconciliation_repair: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Runs every scan, each in its own try/except, so one subsystem's
    monitoring failure can never affect trading or block the others.
    Never raises. Intended caller: scheduler.py, once per tick, in its
    own outer try/except on top of this."""
    results: dict[str, list[str]] = {}
    scans: tuple[tuple[str, Any], ...] = (
        ("reconciliation", lambda: scan_reconciliation(reconciliation_report, reconciliation_repair)),
        ("execution_attempts", scan_execution_attempts),
        ("execution_quality", scan_execution_quality),
        ("kill_switch", scan_kill_switch),
        ("forward_review", scan_forward_review),
    )
    for name, fn in scans:
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 — monitoring must never affect trading
            logger.warning(f"post_trade_monitor: {name} scan failed (non-fatal): {exc}")
            results[name] = []
    return results


def post_trade_summary() -> dict[str, Any]:
    """Read-only snapshot for the dashboard/API layer — never scans,
    never writes, safe to call from a request handler on every page
    load (unlike the scan_* functions above, which are meant to run
    once per scheduler tick)."""
    from execution.reconciliation import last_result as reconciliation_last_result
    from storage.execution_quality import latency_stats, summary as tca_summary, unavailable_fill_count
    from storage.kill_switch import get_state as kill_switch_state
    from storage.post_trade_incidents import counts_by_severity, counts_by_status

    def _safe(fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"post_trade_monitor: summary read failed (non-fatal): {exc}")
            return default

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "incident_counts_by_status": _safe(counts_by_status, {}),
        "incident_counts_by_severity": _safe(counts_by_severity, {}),
        "reconciliation": _safe(reconciliation_last_result, None),
        "kill_switch": _safe(kill_switch_state, {}),
        "execution_quality": {
            "unavailable_fill_count": _safe(unavailable_fill_count, 0),
            "latency": _safe(latency_stats, {"n": 0}),
            "slippage": _safe(lambda: tca_summary().get("overall", {}), {"n": 0}),
        },
    }
