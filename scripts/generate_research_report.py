#!/usr/bin/env python3
"""
scripts/generate_research_report.py
--------------------------------------
Turns research/results/registry.json + every research/results/*_manifest.json
into one Markdown summary (audit follow-up, 2026-07-11).

Every audit doc in docs/ (PHILOSOPHY_AUDIT, PRODUCTION_AUDIT,
STRATEGY_EVIDENCE) was hand-written by reading the registry and manifests
side by side. This automates the mechanical part — the hypothesis table and
the manifest ledger — so a fresh snapshot is one command, not an afternoon
of transcription. It does NOT replace the audits' judgment/prose; it
produces the raw evidence table they'd otherwise build by hand.

Deliberately does not attempt to parse per-symbol PF out of each manifest's
`results` block — manifest schemas vary by experiment kind (backtest vs
spread measurement vs pairs-trading vs subset search), and a generic
parser would either be fragile or overclaim structure that isn't there.
Each row links to its manifest file for the real numbers.

Read-only, no network, no config changes.

Usage:
    python3 -m scripts.generate_research_report
    python3 -m scripts.generate_research_report --out research/results/REPORT.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REGISTRY_PATH = PROJECT_ROOT / "research" / "results" / "registry.json"
RESULTS_DIR = PROJECT_ROOT / "research" / "results"

_STATUS_ORDER = ["PASSED", "RESOLVED", "PLANNED", "RESEARCH", "ABANDONED", "FAILED"]


def _status_rank(status: str) -> int:
    return _STATUS_ORDER.index(status) if status in _STATUS_ORDER else len(_STATUS_ORDER)


def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text())


def load_manifests() -> dict[str, dict]:
    out = {}
    for path in sorted(RESULTS_DIR.glob("*_manifest.json")):
        try:
            out[path.name] = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _escape_md(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def build_hypotheses_table(hypotheses: dict[str, Any]) -> str:
    rows = sorted(hypotheses.items(), key=lambda kv: (_status_rank(kv[1].get("status", "")), kv[0]))
    lines = ["| ID | Status | Title | Last updated |", "|---|---|---|---|"]
    for hid, h in rows:
        title = _escape_md(h.get("title") or "")
        status = h.get("status", "?")
        updated = h.get("last_updated", "")
        lines.append(f"| {hid} | {status} | {title[:90]} | {updated} |")
    return "\n".join(lines)


def build_manifest_table(manifests: dict[str, dict]) -> str:
    lines = ["| Manifest | Kind | Generated | Commit | Reproducible | Note |", "|---|---|---|---|---|---|"]
    for name, m in sorted(manifests.items(), key=lambda kv: kv[1].get("generated_at", "")):
        kind = m.get("kind", "?")
        gen = (m.get("generated_at") or "")[:10]
        commit = (m.get("git", {}) or {}).get("commit", "unknown")[:8]
        reproducible = "yes" if m.get("reproducible") else "NO"
        note = _escape_md((m.get("params") or {}).get("note", ""))[:80]
        lines.append(f"| `{name}` | {kind} | {gen} | `{commit}` | {reproducible} | {note} |")
    return "\n".join(lines)


def build_report(registry: dict[str, Any], manifests: dict[str, dict]) -> str:
    hypotheses = registry.get("hypotheses", {})
    status_counts: dict[str, int] = {}
    for h in hypotheses.values():
        s = h.get("status", "?")
        status_counts[s] = status_counts.get(s, 0) + 1

    n_repro = sum(1 for m in manifests.values() if m.get("reproducible"))
    n_total = len(manifests)

    generated_at = datetime.now(timezone.utc).isoformat()
    parts = [
        "# IATIS Research Snapshot (auto-generated)",
        "",
        f"Generated {generated_at} by `scripts/generate_research_report.py`. "
        "Mechanical aggregation of `research/results/registry.json` + every "
        "`*_manifest.json` — not a substitute for the hand-written audits in "
        "`docs/`, which interpret this evidence.",
        "",
        "## Hypothesis status counts",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for status in sorted(status_counts, key=_status_rank):
        parts.append(f"| {status} | {status_counts[status]} |")
    parts += [
        "",
        f"## Hypotheses ({len(hypotheses)})",
        "",
        build_hypotheses_table(hypotheses),
        "",
        f"## Manifests ({n_total} total, {n_repro} reproducible, "
        f"{n_total - n_repro} NOT reproducible)",
        "",
        build_manifest_table(manifests),
        "",
    ]
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None, help="Write to this path instead of stdout")
    args = ap.parse_args()

    registry = load_registry()
    manifests = load_manifests()
    report = build_report(registry, manifests)

    if args.out:
        Path(args.out).write_text(report)
        print(f"Report written: {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
