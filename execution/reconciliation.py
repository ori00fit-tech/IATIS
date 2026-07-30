"""
execution/reconciliation.py
----------------------------
Scheduled broker-vs-internal position reconciliation (gap analysis M3).

Institutions reconcile positions daily without exception — it is the
control that catches every OTHER control failing (a fill the tracker
missed, an outcome closed internally that is still open at the broker,
a manual intervention on the account). IATIS reconciled only on
(re)connect; this module makes it a per-scheduler-tick diff.

What it compares (symbol-level, the honest granularity — outcome rows
are paper records without broker position ids):

    broker side   : ctrader client's position map — rebuilt from
                    ProtoOAReconcileReq on every (re)connect and kept
                    current by execution events while connected.
    internal side : storage/outcome_tracker.py open outcomes.

When it runs: only when the cTrader execution path is actually live
(execution.ctrader_enabled and not dry_run). In paper mode there is no
broker book to reconcile against — the check reports 'skipped', never a
false mismatch.

Never raises, never gates: a reconciliation failure is reported and the
run continues. The scheduler alerts (with cooldown) on any mismatch.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


def reconcile(config: dict) -> dict[str, Any]:
    """Diff broker open positions against internal open outcomes.

    Returns a report dict; report["status"] is one of:
        "match"    — both sides agree (possibly both empty)
        "mismatch" — symbols open on one side only (details included)
        "skipped"  — broker path not live / client unavailable
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    exec_cfg = config.get("execution", {})

    if not exec_cfg.get("ctrader_enabled", False) or exec_cfg.get("dry_run", True):
        return {"status": "skipped", "checked_at": checked_at,
                "reason": "broker execution not live (ctrader_enabled+dry_run gate)"}

    try:
        from core.data_providers import get_shared_ctrader_client
        client = get_shared_ctrader_client()
        broker_positions = client.get_open_positions()
    except Exception as exc:  # noqa: BLE001 — monitoring must not kill the run
        logger.warning(f"reconciliation: broker side unavailable (non-fatal): {exc}")
        return {"status": "skipped", "checked_at": checked_at,
                "reason": f"broker client unavailable: {type(exc).__name__}: {exc}"}

    try:
        from storage.outcome_tracker import get_open_signals
        internal_open = get_open_signals()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"reconciliation: internal side unavailable (non-fatal): {exc}")
        return {"status": "skipped", "checked_at": checked_at,
                "reason": f"outcome tracker unavailable: {type(exc).__name__}: {exc}"}

    broker_syms = {p.symbol for p in broker_positions}
    internal_syms = {r.get("symbol") for r in internal_open}

    broker_only = sorted(broker_syms - internal_syms)
    internal_only = sorted(internal_syms - broker_syms)

    report = {
        "status": "match" if not broker_only and not internal_only else "mismatch",
        "checked_at": checked_at,
        "broker_open": sorted(broker_syms),
        "internal_open": sorted(internal_syms),
        "broker_only": broker_only,     # broker holds it, tracker doesn't know
        "internal_only": internal_only, # tracker thinks open, broker disagrees
        "n_broker": len(broker_positions),
        "n_internal": len(internal_open),
    }

    if report["status"] == "mismatch":
        logger.warning(
            f"RECONCILIATION MISMATCH: broker_only={broker_only} "
            f"internal_only={internal_only} "
            f"(broker n={report['n_broker']}, internal n={report['n_internal']})"
        )
    else:
        logger.info(
            f"reconciliation: MATCH — {report['n_broker']} broker / "
            f"{report['n_internal']} internal open position(s)"
        )
    return report


# ---------------------------------------------------------------------------
# Persistence — the dashboard reads STORED results only. The API server
# process must never call reconcile() itself: get_shared_ctrader_client()
# would open a SECOND cTrader session and fight the scheduler's over the
# single per-account slot (the documented ALREADY_LOGGED_IN storm,
# diagnosed 2026-07-14). The scheduler owns the session, so the scheduler
# stores; everyone else reads.
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS reconciliation_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT,
    broker_only   TEXT,
    internal_only TEXT,
    n_broker      INTEGER,
    n_internal    INTEGER
)
"""


def store_result(report: dict[str, Any]) -> None:
    """Persist one reconcile() report (scheduler-only caller). Never raises."""
    try:
        import json

        from storage import d1_client
        with d1_client.d1_connection() as con:
            con.execute(_DDL)
            con.execute(
                """INSERT INTO reconciliation_checks
                   (ts, status, reason, broker_only, internal_only, n_broker, n_internal)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    report.get("checked_at"),
                    report.get("status", "unknown"),
                    report.get("reason"),
                    json.dumps(report.get("broker_only", [])),
                    json.dumps(report.get("internal_only", [])),
                    report.get("n_broker"),
                    report.get("n_internal"),
                ),
            )
    except Exception as exc:  # noqa: BLE001 — monitoring must not kill the run
        logger.warning(f"reconciliation: store failed (non-fatal): {exc}")


def last_result() -> dict[str, Any] | None:
    """Most recent stored reconciliation report (dashboard/endpoint reader)."""
    import json

    from storage import d1_client
    with d1_client.d1_connection() as con:
        con.execute(_DDL)
        row = con.execute(
            "SELECT ts, status, reason, broker_only, internal_only, "
            "n_broker, n_internal FROM reconciliation_checks "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "checked_at": row["ts"],
        "status": row["status"],
        "reason": row["reason"],
        "broker_only": json.loads(row["broker_only"] or "[]"),
        "internal_only": json.loads(row["internal_only"] or "[]"),
        "n_broker": row["n_broker"],
        "n_internal": row["n_internal"],
    }


def repair_mismatches(report: dict[str, Any]) -> dict[str, Any]:
    """Close internal outcome rows for symbols the broker no longer holds.

    ``report["internal_only"]`` means: outcome_tracker thinks a signal is
    still open, but the broker disagrees. Nothing else in the codebase ever
    resolves that drift (auto_close_outcomes() only closes on a price-based
    TP/SL/time-stop match against the position's OWN stored levels — a
    manual close, an off-level stop-out, or a tick where the symbol simply
    wasn't fetched all leave the row open forever). Left unrepaired, this
    directly inflates risk/live_portfolio_state.py's open-risk/exposure
    count (each stale row = one more phantom risk_per_trade_max slot),
    which can block every new EXECUTE decision on exposure that doesn't
    exist at the broker.

    Uses reconcile_close_signal() — never fabricates a win/loss/breakeven,
    since the real exit price/outcome at the broker isn't known here.

    Returns {"repaired": [signal_id, ...], "skipped_no_open_signal": [symbol, ...]}.
    Never raises — a repair failure is reported, not fatal, matching this
    module's existing "never gates" contract.
    """
    from storage.outcome_tracker import get_open_signals, reconcile_close_signal

    repaired: list[str] = []
    skipped: list[str] = []
    internal_only = report.get("internal_only") or []
    if not internal_only:
        return {"repaired": repaired, "skipped_no_open_signal": skipped}

    try:
        open_signals = get_open_signals()
    except Exception as exc:  # noqa: BLE001 — monitoring must not kill the run
        logger.warning(f"reconciliation repair: could not read open signals: {exc}")
        return {"repaired": repaired, "skipped_no_open_signal": list(internal_only)}

    for symbol in internal_only:
        matches = [r for r in open_signals if r.get("symbol") == symbol]
        if not matches:
            skipped.append(symbol)
            continue
        for row in matches:
            signal_id = row.get("signal_id")
            if not signal_id:
                continue
            try:
                if reconcile_close_signal(
                    signal_id,
                    notes="reconciliation: broker no longer reports this position open",
                ):
                    repaired.append(signal_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"reconciliation repair: failed to close {signal_id}: {exc}")

    if repaired:
        logger.warning(f"reconciliation: repaired {len(repaired)} stale internal-only signal(s): {repaired}")
    return {"repaired": repaired, "skipped_no_open_signal": skipped}


def format_alert(report: dict[str, Any]) -> str:
    """Telegram-ready mismatch message."""
    return (
        "🚨 <b>Position reconciliation MISMATCH</b>\n"
        f"Broker-only (tracker missed a fill?): {', '.join(report['broker_only']) or '—'}\n"
        f"Internal-only (closed at broker, open in tracker?): "
        f"{', '.join(report['internal_only']) or '—'}\n"
        f"Broker open: {report['n_broker']} | Internal open: {report['n_internal']}\n"
        f"Checked: {report['checked_at']}"
    )
