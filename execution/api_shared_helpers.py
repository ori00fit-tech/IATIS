"""
execution/api_shared_helpers.py
----------------------------------
Snapshot-computing helpers consumed by MORE THAN ONE router in
execution/routes/ — extracted here (rather than left inside whichever
router "owns" the primary endpoint) so every consumer imports the exact
same implementation instead of drifting copies.

Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1). Each function's
docstring already documented its multi-consumer status before this split
(e.g. "_scheduler_status ... Shared by /health/full and /alerts") — this
module just gives that sharing a real, single source location instead of
same-file proximity.

Consumers:
  _scheduler_status      -> execution/routes/health.py, execution/routes/alerts.py
  _systemd_service_status -> execution/routes/health.py (uses _LOG_UNITS/_UNIT_KIND below)
  _load_manifests         -> execution/routes/research.py, execution/routes/alerts.py
  _data_health_snapshot   -> execution/routes/data.py, execution/routes/research.py (reports), execution/routes/alerts.py
  _forward_rule_progress  -> execution/routes/forward_review.py, (via _forward_rule_alerts) execution/routes/alerts.py
  _forward_rule_alerts    -> execution/routes/alerts.py
  _LOG_UNITS / _UNIT_KIND -> execution/routes/logs.py, execution/routes/health.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from execution.api_core import _get_config, logger

# ---------------------------------------------------------------------------
# Whitelisted systemd units — no arbitrary unit or path is ever accepted
# from the caller. `source` must be a key here or the literal "system";
# journalctl/systemctl always run with a fixed argv (no shell=True, no
# string interpolation of request data into the command line).
# ---------------------------------------------------------------------------
_LOG_UNITS: dict[str, str] = {
    "api": "iatis-api",
    "scheduler": "iatis-scheduler",
    "watchdog": "iatis-watchdog",
    "backup": "iatis-backup",
    "d1_backup": "iatis-d1-backup",
}

# "daemon" units run continuously — inactive means something is actually
# down. "timer" units are triggered by a companion .timer (see the
# iatis-*.timer files at the repo root) and sit inactive between runs by
# design — that's normal, not a fault. Mission Control Audit flagged that
# the dashboard showed all five with identical treatment, making a
# healthy idle timer indistinguishable from a dead daemon.
_UNIT_KIND: dict[str, str] = {
    "api": "daemon",
    "scheduler": "daemon",
    "watchdog": "timer",
    "backup": "timer",
    "d1_backup": "timer",
}


def _scheduler_status() -> dict[str, Any]:
    """Last scheduler run, from the run-marker file, a local log file, or
    journalctl.

    Shared by /health/full and /alerts — extracted so both read the exact
    same signal instead of two slightly-different implementations drifting
    apart over time.

    The marker file (scheduler.py::_write_run_marker) is the primary
    source: the log paths below don't exist when `logging.file` is unset
    (the shipped default) and journalctl needs journal-group permissions
    the API service user may lack — both of which made Mission Control
    report "no run seen" against a healthy, actively-running scheduler.
    """
    import json as _json
    import re as _re
    marker = Path("storage/last_run.json")
    if marker.exists():
        try:
            data = _json.loads(marker.read_text(encoding="utf-8"))
            completed_at = data.get("completed_at")
            if completed_at:
                return {
                    "last_run": completed_at,
                    "last_execute_count": int(data.get("execute_count") or 0),
                    "status": "running",
                }
        except (ValueError, OSError) as exc:
            logger.debug(f"scheduler run marker unreadable: {exc}")
    log_candidates = [
        Path("storage/system.log"),
        Path("/var/log/iatis-scheduler.log"),
    ]
    last_run = None
    last_execute_count = 0
    for sched_log in log_candidates:
        if sched_log.exists():
            lines = sched_log.read_text().splitlines()
            for line in reversed(lines[-500:]):
                if "Run complete" in line:
                    last_run = line.split("|")[0].strip()
                    m = _re.search(r"(\d+) EXECUTE", line)
                    if m: last_execute_count = int(m.group(1))
                    break
            if last_run:
                break
    if not last_run:
        import subprocess
        result = subprocess.run(
            ["journalctl", "-u", "iatis-scheduler", "-n", "100", "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=5
        )
        for line in reversed(result.stdout.splitlines()):
            if "Run complete" in line:
                last_run = line[:30].strip()
                m = _re.search(r"(\d+) EXECUTE", line)
                if m: last_execute_count = int(m.group(1))
                break
    return {
        "last_run": last_run,
        "last_execute_count": last_execute_count,
        "status": "running" if last_run else "unknown",
    }


def _systemd_service_status() -> dict[str, dict[str, Any]]:
    """Real per-service systemd status via `systemctl is-active <unit>` —
    one fixed-argv call per unit (never shell=True), reusing the same
    whitelist /logs already knows about (_LOG_UNITS, above).
    Absent/inert on hosts with no systemd (sandboxes, dev laptops) —
    each unit reports "unavailable" rather than raising.

    Each entry also carries `kind` ("daemon" | "timer") and a `healthy`
    verdict computed for that kind — a timer-triggered oneshot
    (watchdog/backup/d1_backup) is *expected* to read "inactive" between
    scheduled runs, while the same status on a daemon (api/scheduler)
    means it's actually down. Mission Control Audit flagged that showing
    raw systemd state with no such distinction made a healthy idle timer
    indistinguishable from a dead daemon.
    """
    import subprocess

    services: dict[str, dict[str, Any]] = {}
    for key, unit in _LOG_UNITS.items():
        kind = _UNIT_KIND.get(key, "daemon")
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=5,
            )
            status = (result.stdout or "").strip() or "unknown"
        except FileNotFoundError:
            status = "unavailable"
        except subprocess.TimeoutExpired:
            status = "timeout"
        except Exception:
            status = "error"

        healthy = status in ("active", "inactive") if kind == "timer" else status == "active"
        services[key] = {"status": status, "kind": kind, "healthy": healthy}
    return services


def _load_manifests() -> list[dict[str, Any]]:
    """Git-tracked evidence manifests — shared by /research/manifests and
    /alerts (which flags any non-reproducible or newly-generated one).
    """
    import json as _json

    manifests: list[dict[str, Any]] = []
    for f in sorted(Path("research/results").glob("*_manifest.json"), reverse=True):
        try:
            m = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        params = m.get("params") or {}
        git = m.get("git") or {}
        manifests.append({
            "file": f.name,
            "kind": m.get("kind"),
            "generated_at": m.get("generated_at"),
            "reproducible": m.get("reproducible"),
            "git_commit": (git.get("commit") or "")[:8],
            "git_dirty": git.get("dirty"),
            "decision_timeframe": params.get("decision_timeframe"),
            "engines_enabled": params.get("engines_enabled"),
            "note": params.get("note"),
            "datasets_count": len(m.get("datasets") or []),
            "results": m.get("results"),
        })
    return manifests


_DH_V2_RANK = {"OK": 0, "GAPS": 1, "STALE": 2, "STARVED": 3, "MISSING": 4}
# Data-starvation thresholds — the same invariants main.py warns on
# (NNFX needs 210+ decision-TF bars, the MTF gate 50+ D1 bars). Below
# them the pipeline runs but degrades silently — exactly the July 2026
# incident class this panel exists to make visible.
_DH_MIN_DECISION_TF_BARS = 210
_DH_MIN_D1_BARS = 50
# A decision older than this = the live feed for that symbol has gone
# quiet (2h scheduler cadence -> 3 missed runs).
_DH_STALE_MINUTES = 360


def _data_health_snapshot() -> dict[str, Any]:
    """Per-symbol/timeframe LIVE-FEED health, derived from decision
    provenance (utils/provenance.py) — the bars the pipeline ACTUALLY
    consumed, per symbol, at its latest run. Shared by /data-health,
    /reports/{kind}, and /alerts.

    History: this used to inspect core/data_manager.py's local
    `*_2y.csv` cache — a path the live pipeline no longer feeds (H4/D1
    come from the provider chains with no local cache), so every symbol
    reported MISSING regardless of live-feed truth (observed 2026-07-16).
    Provenance is the honest source: it exists precisely to answer
    "what data made this decision".

    Statuses: OK · STALE (latest decision too old) · STARVED (bars below
    the engine minimums — the silent-degradation class) · MISSING (no
    provenance-carrying decision yet for this symbol).
    """
    import json as _json

    config = _get_config()
    symbols_cfg = config.get("data", {}).get("twelve_data_symbols", [])
    active_symbols = [s["internal"] for s in symbols_cfg if s.get("enabled")]
    config_timeframes = config.get("data", {}).get("timeframes", ["H1", "H4", "D1"])
    dtf = (config_timeframes or ["H1"])[0]  # decision TF = timeframes[0]

    latest: dict[str, Any] = {}
    try:
        from storage import d1_client
        with d1_client.d1_connection() as con:
            for symbol in active_symbols:
                row = con.execute(
                    "SELECT ts, data_versions FROM decisions "
                    "WHERE symbol=? AND data_versions IS NOT NULL "
                    "ORDER BY id DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
                if row is not None:
                    latest[symbol] = {"ts": row["ts"], "dv": row["data_versions"]}
    except Exception as exc:
        # Pre-migration table / D1 outage: report MISSING rather than 500 —
        # the panel must stay readable when storage is the problem.
        logger.warning(f"data-health: provenance read failed ({exc}) — all MISSING")

    now = datetime.now(timezone.utc)
    results = []
    summary = {"ok": 0, "stale": 0, "gaps": 0, "starved": 0, "missing": 0}

    def _tf_missing() -> dict[str, Any]:
        return {"bars": 0, "last_bar_time": None, "age_minutes": None,
                "provider": None, "gap_count_30d": 0, "duplicate_count": 0,
                "timezone": None, "integrity_score": 0, "status": "MISSING"}

    for symbol in active_symbols:
        tf_status: dict[str, Any] = {}
        worst = "OK"
        rec = latest.get(symbol)
        if rec is None:
            tf_status = {tf: _tf_missing() for tf in config_timeframes}
            worst = "MISSING"
        else:
            try:
                versions = _json.loads(rec["dv"]) or {}
            except (TypeError, ValueError):
                versions = {}
            try:
                decided_at = datetime.fromisoformat(str(rec["ts"]))
                if decided_at.tzinfo is None:
                    decided_at = decided_at.replace(tzinfo=timezone.utc)
                age_minutes = max(0.0, (now - decided_at).total_seconds() / 60)
            except ValueError:
                age_minutes = None

            for tf in config_timeframes:
                v = versions.get(tf) or {}
                bars = int(v.get("row_count") or 0)
                if v.get("error") or (not v):
                    status = "MISSING"
                elif tf == dtf and bars < _DH_MIN_DECISION_TF_BARS:
                    status = "STARVED"
                elif tf == "D1" and tf != dtf and bars < _DH_MIN_D1_BARS:
                    status = "STARVED"
                elif age_minutes is not None and age_minutes > _DH_STALE_MINUTES:
                    status = "STALE"
                else:
                    status = "OK"
                score = {"OK": 100, "STALE": 60, "STARVED": 30, "MISSING": 0}[status]
                tf_status[tf] = {
                    "bars": bars,
                    "last_bar_time": v.get("last_ts"),
                    "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
                    "provider": v.get("provider"),
                    "gap_count_30d": 0,
                    "duplicate_count": 0,
                    "timezone": None,
                    "integrity_score": score,
                    "status": status,
                }
                if _DH_V2_RANK[status] > _DH_V2_RANK[worst]:
                    worst = status
        results.append({"symbol": symbol, "timeframes": tf_status, "overall_status": worst})
        summary[worst.lower()] += 1

    return {
        "checked_at": now.isoformat(),
        "source": "decision_provenance",
        "symbols": results,
        "summary": summary,
    }


def _forward_rule_progress() -> list[dict[str, Any]]:
    """Full progress snapshot for every pre-registered rule, whether or
    not it has triggered — the Forward Demo view. Alert Center derives
    its (much shorter) alert list from this same data.
    """
    import json as _json
    from scripts.forward_review import REGISTRY, FX, CARRIERS, _bucket_stats, _closed_outcomes

    rules = _json.loads(REGISTRY.read_text()).get("_decision_rules", {})
    rows = _closed_outcomes()
    buckets = {"fx": _bucket_stats(rows, FX), "carriers": _bucket_stats(rows, CARRIERS)}

    out: list[dict[str, Any]] = []
    for rule_id, rule in rules.items():
        if rule_id.startswith("_") or not isinstance(rule, dict):
            continue
        b = buckets.get(rule["bucket"]) or {"n": 0, "wr": None, "pf": None}
        n, min_n = b["n"], rule["min_n"]
        metric = b.get(rule["metric"])
        sufficient_n = n >= min_n
        triggered = bool(
            sufficient_n and metric is not None
            and ((rule["op"] == "<" and metric < rule["threshold"])
                 or (rule["op"] == ">=" and metric >= rule["threshold"]))
        )
        # Sanitize AFTER the numeric comparisons above — a bare `Infinity`
        # token (what json.dumps would emit for float("inf")) isn't valid
        # JSON and makes a browser's fetch().json() throw. The frontend
        # renders this string sentinel as "∞".
        if metric == float("inf"):
            json_safe_metric: float | str | None = "Infinity"
        elif metric == float("-inf"):
            json_safe_metric = "-Infinity"
        else:
            json_safe_metric = metric
        out.append({
            "rule_id": rule_id,
            "statement": rule["statement"],
            "bucket": rule["bucket"],
            "metric": rule["metric"],
            "current_value": json_safe_metric,
            "op": rule["op"],
            "threshold": rule["threshold"],
            "n": n,
            "min_n": min_n,
            "progress_pct": round(min(100.0, 100.0 * n / min_n), 1) if min_n else None,
            "sufficient_n": sufficient_n,
            "triggered": triggered,
            "action": rule.get("action"),
        })
    return out


def _forward_rule_alerts() -> list[dict[str, Any]]:
    """The subset of _forward_rule_progress() worth surfacing as an alert:
    a triggered rule, or one that's crossed 80% of its required sample.
    """
    out: list[dict[str, Any]] = []
    for p in _forward_rule_progress():
        if p["triggered"]:
            out.append({
                "severity": "warning", "category": "forward_milestone",
                "message": f"{p['rule_id']} VERDICT REACHED: {p['statement']}",
                "detail": {"rule_id": p["rule_id"], "n": p["n"], "metric": p["metric"],
                           "value": p["current_value"], "action": p["action"]},
            })
        elif not p["sufficient_n"] and p["n"] >= p["min_n"] * 0.8:
            out.append({
                "severity": "info", "category": "forward_milestone",
                "message": f"{p['rule_id']} approaching evaluation: n={p['n']}/{p['min_n']} closed {p['bucket']} trades.",
                "detail": {"rule_id": p["rule_id"], "n": p["n"], "min_n": p["min_n"]},
            })
    return out
