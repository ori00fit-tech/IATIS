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

Flat JSONL file (storage/decisions.jsonl), append-only — always local,
never migrated to D1 (an append-only log gains nothing from a queryable
store). storage/decision_db.py is the queryable layer built on top,
backed by Cloudflare D1.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_LOG_PATH = Path(__file__).resolve().parent / "decisions.jsonl"


def log_decision(report: dict, path: Path | str | None = None) -> None:
    """Append one pipeline report to the decision log.

    Stores the full report plus a timestamp. Never raises on write
    failure beyond logging a warning — a logging failure must never
    crash or block the trading pipeline itself.
    """
    # `path` defaults to None (not DEFAULT_LOG_PATH directly) so that
    # monkeypatching the module-level DEFAULT_LOG_PATH in tests takes
    # effect on every call that omits path=. A default parameter value
    # is bound once at function-definition time; a name looked up in the
    # function body is resolved at call time and sees the patched value.
    path = Path(path) if path is not None else DEFAULT_LOG_PATH
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


def read_decisions(path: Path | str | None = None) -> list[dict]:
    """Read all logged decisions. Returns [] if the log doesn't exist yet."""
    path = Path(path) if path is not None else DEFAULT_LOG_PATH
    if not path.exists():
        return []

    decisions = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                decisions.append(json.loads(line))
    return decisions


def _decision_reasons(entry: dict) -> list[str]:
    """All human-readable reason strings attached to one decision entry.

    Used by filter_decisions' `reason` search — deliberately broader than
    summarize_decisions' mutually-exclusive reason-counting logic, since a
    filter should match *any* reason surface, not just the primary one.
    """
    report = entry.get("report", {})
    reasons: list[str] = []
    if "reason" in report:
        reasons.append(str(report["reason"]))
    reasons.extend(report.get("confluence", {}).get("fail_reasons", []))
    risk = report.get("risk", {})
    if risk and risk.get("passed") is False:
        reasons.extend(risk.get("reasons", []))
    return reasons


def filter_decisions(
    decisions: list[dict],
    *,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    engine: str | None = None,
    min_score: float | None = None,
    risk_rejected: bool | None = None,
    reason: str | None = None,
) -> list[dict]:
    """Apply Decision Explorer filters to an already-loaded decision list.

    Every filter is optional and they AND together. Timestamps are ISO-8601
    strings, so lexical prefix comparison is chronological comparison.
    """
    out = decisions
    if symbol:
        sym = symbol.upper()
        out = [d for d in out if d.get("symbol") == sym]
    if date_from:
        out = [d for d in out if d.get("timestamp", "") >= date_from]
    if date_to:
        out = [d for d in out if d.get("timestamp", "") <= date_to]
    if engine:
        eng = engine.lower()
        out = [
            d for d in out
            if any(eng == str(e.get("engine", "")).lower()
                   for e in d.get("report", {}).get("engine_outputs", []))
        ]
    if min_score is not None:
        out = [
            d for d in out
            if (d.get("report", {}).get("confluence", {}).get("score") or 0) >= min_score
        ]
    if risk_rejected:
        out = [d for d in out if d.get("report", {}).get("risk", {}).get("passed") is False]
    if reason:
        needle = reason.lower()
        out = [d for d in out if needle in " ".join(_decision_reasons(d)).lower()]
    return out


def summarize_decisions(path: Path | str | None = None) -> dict:
    """Quick aggregate stats: how often does the system trade vs abstain,
    and what are the most common NO_TRADE reasons?
    """
    decisions = read_decisions(path)  # path=None resolves inside read_decisions
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
