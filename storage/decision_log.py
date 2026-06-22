"""
storage/decision_log.py
---------------------------
Logs every pipeline decision — EXECUTE *and* NO_TRADE — to a local
JSONL file. Most systems only persist executed trades; this project
explicitly also persists every NO_TRADE with its reasons, because over
time the pattern of *why the system refused to trade* is often more
valuable signal than the trades themselves (e.g. "we abstain on this
setup type 80% of the time — is the contradiction rule too strict, or
catching something real?").

Phase 1: flat JSONL file (storage/decisions.jsonl), append-only.
Phase 2+: consider migrating to storage/performance.db (sqlite) once
query needs grow beyond "scan the file."
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_LOG_PATH = Path(__file__).resolve().parent / "decisions.jsonl"


def log_decision(report: dict, path: Path | str = DEFAULT_LOG_PATH) -> None:
    """Append one pipeline report to the decision log.

    Stores the full report plus a timestamp. Never raises on write
    failure beyond logging a warning — a logging failure must never
    crash or block the trading pipeline itself.
    """
    path = Path(path)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "final_verdict": report.get("final_verdict"),
        "symbol": report.get("symbol"),
        "report": report,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        logger.info(f"Decision logged: {entry['final_verdict']} -> {path}")
    except OSError as exc:
        logger.warning(f"Failed to write decision log (non-fatal): {exc}")


def read_decisions(path: Path | str = DEFAULT_LOG_PATH) -> list[dict]:
    """Read all logged decisions. Returns [] if the log doesn't exist yet."""
    path = Path(path)
    if not path.exists():
        return []

    decisions = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                decisions.append(json.loads(line))
    return decisions


def summarize_decisions(path: Path | str = DEFAULT_LOG_PATH) -> dict:
    """Quick aggregate stats: how often does the system trade vs abstain,
    and what are the most common NO_TRADE reasons?
    """
    decisions = read_decisions(path)
    if not decisions:
        return {"total": 0, "execute": 0, "no_trade": 0, "no_trade_reasons": {}}

    execute = sum(1 for d in decisions if d["final_verdict"] == "EXECUTE")
    no_trade = sum(1 for d in decisions if d["final_verdict"] == "NO_TRADE")

    reason_counts: dict[str, int] = {}
    for d in decisions:
        if d["final_verdict"] != "NO_TRADE":
            continue
        report = d.get("report", {})
        # top-level data validation failure
        if "reason" in report:
            key = report["reason"]
            reason_counts[key] = reason_counts.get(key, 0) + 1
            continue
        # confluence/contradiction/risk-level reasons
        confluence = report.get("confluence", {})
        for r in confluence.get("fail_reasons", []):
            reason_counts[r] = reason_counts.get(r, 0) + 1
        risk = report.get("risk", {})
        if risk and risk.get("passed") is False:
            for r in risk.get("reasons", []):
                reason_counts[r] = reason_counts.get(r, 0) + 1

    return {
        "total": len(decisions),
        "execute": execute,
        "no_trade": no_trade,
        "no_trade_reasons": reason_counts,
    }
