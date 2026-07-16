"""
execution/metrics.py
---------------------
Prometheus-text-format metrics (institutional gap analysis S5).

Gives the watchdog and any scraper NUMBERS instead of vibes: how stale
is the last decision, is D1 reachable and how slow, how many decisions/
EXECUTEs/fills/open outcomes exist. MiFID II RTS 6 Art. 16-style
real-time monitoring needs a quantitative surface — this is it.

Design constraints honored:
  - Read-only. No metric write path touches the pipeline.
  - Every gauge is individually guarded: a D1 outage yields
    iatis_d1_up 0 plus whatever can still be computed, never a 500 —
    the metrics endpoint must be at its most reliable exactly when the
    system is at its least.
  - Plain text/plain exposition (no client library dependency): the
    format is stable and trivially parseable (one 'name value' per
    line, '# HELP'/'# TYPE' comments).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from utils.logger import get_logger

logger = get_logger(__name__)


def _line(out: list[str], name: str, value, help_: str, type_: str = "gauge") -> None:
    out.append(f"# HELP {name} {help_}")
    out.append(f"# TYPE {name} {type_}")
    out.append(f"{name} {value}")


def render_metrics() -> str:
    """Build the exposition text. Never raises."""
    out: list[str] = []

    # --- D1 reachability + latency (one cheap probe) --------------------
    d1_up = 0
    con = None
    try:
        from storage import d1_client
        t0 = time.monotonic()
        with d1_client.d1_connection() as probe:
            probe.execute("SELECT 1")
            latency = time.monotonic() - t0
            d1_up = 1
            _line(out, "iatis_d1_latency_seconds", f"{latency:.4f}",
                  "Round-trip time of a SELECT 1 through the D1 proxy Worker.")
            con = probe

            # --- decision store gauges (same connection, best effort) ---
            try:
                row = con.execute(
                    "SELECT COUNT(*) AS n, "
                    "SUM(CASE WHEN verdict='EXECUTE' THEN 1 ELSE 0 END) AS ex, "
                    "MAX(ts) AS last_ts FROM decisions").fetchone()
                if row:
                    _line(out, "iatis_decisions_total", int(row["n"] or 0),
                          "Total decisions recorded.", "counter")
                    _line(out, "iatis_execute_decisions_total", int(row["ex"] or 0),
                          "Total EXECUTE decisions recorded.", "counter")
                    if row["last_ts"]:
                        age = (datetime.now(timezone.utc)
                               - datetime.fromisoformat(str(row["last_ts"]))
                               .replace(tzinfo=timezone.utc)).total_seconds()
                        _line(out, "iatis_last_decision_age_seconds", f"{max(age, 0):.0f}",
                              "Seconds since the newest decision row — the scheduler "
                              "heartbeat proxy. Alert when it exceeds ~2 scheduler intervals.")
            except Exception as exc:
                logger.debug(f"metrics: decisions gauges skipped: {exc}")

            try:
                row = con.execute(
                    "SELECT COUNT(*) AS n FROM outcomes WHERE outcome='open'").fetchone()
                if row is not None:
                    _line(out, "iatis_open_outcomes", int(row["n"] or 0),
                          "Currently open (unresolved) trade outcomes.")
            except Exception as exc:
                logger.debug(f"metrics: outcomes gauge skipped: {exc}")

            try:
                row = con.execute("SELECT COUNT(*) AS n FROM fills").fetchone()
                if row is not None:
                    _line(out, "iatis_fills_total", int(row["n"] or 0),
                          "Real broker fills recorded by the TCA ledger.", "counter")
            except Exception as exc:
                logger.debug(f"metrics: fills gauge skipped: {exc}")

            try:
                from storage.migrations import current_version, LATEST_VERSION
                _line(out, "iatis_schema_version", current_version(con),
                      "Applied D1 schema version (storage/migrations.py).")
                _line(out, "iatis_schema_version_latest", LATEST_VERSION,
                      "Latest schema version shipped in this code tree.")
            except Exception as exc:
                logger.debug(f"metrics: schema gauge skipped: {exc}")
    except Exception as exc:
        logger.warning(f"metrics: D1 probe failed: {exc}")

    _line(out, "iatis_d1_up", d1_up,
          "1 when the D1 proxy Worker answers a probe query, else 0.")
    return "\n".join(out) + "\n"
