"""
core/data_confidence.py
------------------------
Runtime data-confidence layer (institutional gap analysis S1, addendum A4
"Market Health" — the data-integrity component, monitoring-only).

What it does: periodically cross-checks ONE symbol's recent closes
between the top two providers in its chain and records the divergence.
The comparison math is scripts/cross_provider_diff.py's — reused, not
duplicated (addendum A2), because it is already pure and tested.

Why: the platform has shipped one silent data-degradation incident
already (live addendum, July 2026 — 614 decisions on starved windows).
Provenance (M2) makes such incidents *diagnosable after the fact*; this
layer is the *before* alarm: two providers materially disagreeing about
the same closes means at least one of them is wrong, and the pipeline
has no way to know which — a human must look.

Budget discipline: ONE symbol per scheduler run, round-robin (an extra
~1-2 provider calls per run, not per symbol per run). Results land in a
small D1 table so the dashboard and GET /data-confidence read HISTORY —
they never trigger fetches themselves.

This is monitoring, never a gate: nothing here can block or downgrade a
decision (addendum A4's explicit boundary — a composite health gate
without pre-registered evidence is a threshold change that resets the
forward sample).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error
from utils.logger import get_logger

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS data_confidence_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    interval      TEXT NOT NULL,
    provider_a    TEXT,
    provider_b    TEXT,
    bars_common   INTEGER,
    mean_diff_pct REAL,
    max_diff_pct  REAL,
    pct_exceeding REAL,
    verdict       TEXT NOT NULL,
    raw_json      TEXT
)
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_dcc_ts ON data_confidence_checks(ts)",
    "CREATE INDEX IF NOT EXISTS idx_dcc_symbol ON data_confidence_checks(symbol)",
]

# Round-robin cursor across scheduler runs (process-local is fine: the
# point is coverage over time, not exactness across restarts).
_rr_counter = 0


def _init(con) -> None:
    con.execute(_DDL)
    for idx in _INDEXES:
        con.execute(idx)


def pick_symbol(symbols: list[str]) -> str | None:
    """Next symbol in the round-robin rotation."""
    global _rr_counter
    if not symbols:
        return None
    sym = symbols[_rr_counter % len(symbols)]
    _rr_counter += 1
    return sym


def check_and_record(symbol: str, config: dict) -> dict[str, Any] | None:
    """Cross-check `symbol` between its chain's top two providers and
    persist the result. Never raises; returns the stored summary (or
    None when the check could not run — e.g. <2 providers reachable).
    """
    try:
        from core.data_providers import provider_chain_for
        from scripts.cross_provider_diff import run as diff_run

        chain = provider_chain_for(symbol, config.get("data", {}).get("provider_chains"))
        if len(chain) < 2:
            logger.debug(f"data-confidence: {symbol} chain has <2 providers — skipped")
            return None

        dc_cfg = config.get("features", {})
        interval = str(dc_cfg.get("data_confidence_interval", "H1"))
        bars = int(dc_cfg.get("data_confidence_bars", 48))
        tolerance = float(dc_cfg.get("data_confidence_tolerance_pct", 0.05))

        result = diff_run(symbol, interval, chain[:2], bars, tolerance, config)
        comparisons = result.get("comparisons") or []
        if not comparisons:
            logger.info(
                f"data-confidence: {symbol} — could not fetch two providers "
                f"({result.get('fetch_errors')})"
            )
            return None

        comp = comparisons[0]
        diff = comp.get("close_diff_pct") or {}
        summary = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "interval": interval,
            "provider_a": comp.get("provider_a"),
            "provider_b": comp.get("provider_b"),
            "bars_common": comp.get("bars_common", 0),
            "mean_diff_pct": diff.get("mean"),
            "max_diff_pct": diff.get("max"),
            "pct_exceeding": comp.get("pct_bars_exceeding_tolerance"),
            "verdict": str(comp.get("verdict", "UNKNOWN")).split(" ")[0],
        }

        with d1_client.d1_connection() as con:
            _init(con)
            con.execute(
                """INSERT INTO data_confidence_checks
                   (ts, symbol, interval, provider_a, provider_b, bars_common,
                    mean_diff_pct, max_diff_pct, pct_exceeding, verdict, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (summary["ts"], symbol, interval,
                 summary["provider_a"], summary["provider_b"],
                 summary["bars_common"], summary["mean_diff_pct"],
                 summary["max_diff_pct"], summary["pct_exceeding"],
                 summary["verdict"], json.dumps(comp, default=str)),
            )

        level = logger.warning if summary["verdict"].startswith("MATERIAL") else logger.info
        level(
            f"data-confidence: {symbol} {interval} "
            f"{summary['provider_a']} vs {summary['provider_b']} → "
            f"{summary['verdict']} (mean {summary['mean_diff_pct']}%, "
            f"max {summary['max_diff_pct']}%, n={summary['bars_common']})"
        )
        return summary
    except D1Error as exc:
        logger.warning(f"data-confidence: store failed (non-fatal): {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 — monitoring must never hurt the run
        logger.warning(f"data-confidence check failed (non-fatal): {exc}")
        return None


def recent_checks(limit: int = 50) -> dict[str, Any]:
    """History for the dashboard / GET /data-confidence — reads the table,
    never fetches from providers."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT ts, symbol, interval, provider_a, provider_b, bars_common, "
            "mean_diff_pct, max_diff_pct, pct_exceeding, verdict "
            "FROM data_confidence_checks ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    checks = [{k: r[k] for k in r.keys()} for r in rows]
    material = [c for c in checks if str(c.get("verdict", "")).startswith("MATERIAL")]
    return {
        "checks": checks,
        "n": len(checks),
        "material_disagreements": len(material),
        "note": (
            "Monitoring only — never a gate. MATERIAL means two providers "
            "disagree about the same closes: at least one is wrong and the "
            "pipeline cannot know which. Investigate before trusting either."
        ),
    }
