"""
research/survivorship_checker.py
------------------------------------
Survivorship-bias governance check (audit follow-up, 2026-07-11).

The philosophy audit already found this bias CONFIRMED and EXPLICIT once:
config.yaml disables AUDUSD/USDCAD/NZDUSD/EURGBP/EURCHF "because their 6.4y
in-sample PF < 1.0" — an honest comment, but a post-hoc selection all the
same. It also found the mirror problem: US30/NAS100/SPX500 are `enabled:
true` in production with NO committed backtest evidence anywhere in the
repo. Both findings were made by hand, once, by reading config.yaml and
research/results/ side by side. This module makes that check runnable and
repeatable instead of relying on the next person to notice.

What it checks (siblings to edge_gate.py's engine-level gate, at the
symbol level):
  1. Every symbol in config.yaml's universe: is it currently enabled, and
     does ANY committed manifest in research/results/ reference it? Flags
     ENABLED_NO_EVIDENCE (the US30/NAS100/SPX500 pattern) and
     DISABLED_NO_EVIDENCE (a disable decision this repo cannot substantiate
     from its own artifacts) as the two governance gaps; DISABLED_WITH_
     EVIDENCE (the AUDUSD-family pattern — an honest, evidenced choice,
     not a violation) is reported for transparency, not flagged.
  2. Whether each manifest under research/results/ declares a
     `params.symbol_selection` field — the convention this module
     introduces going forward ("fixed_before_test" |
     "selected_after_seeing_results" | "not_applicable"). Manifests
     written before 2026-07-11 are grandfathered UNDISCLOSED, not
     violations; new research scripts should pass this in `params` to
     research.manifest.build_manifest().

Read-only: scans committed JSON, changes nothing, gates nothing at boot
(unlike edge_gate — this is an audit report, not a production invariant).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "research" / "results"

# The convention starts here — manifests before this date are grandfathered.
CONVENTION_INTRODUCED = "2026-07-11"

VALID_SELECTION_LABELS = {
    "fixed_before_test", "selected_after_seeing_results", "not_applicable",
}


def _load_manifests() -> dict[str, dict]:
    out = {}
    for path in sorted(RESULTS_DIR.glob("*_manifest.json")):
        try:
            out[path.name] = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _mentions_symbol(payload: Any, symbol: str) -> bool:
    """Recursively search a manifest's JSON structure for the symbol as a
    string token, so it catches params, results tables, dataset filenames,
    and per-symbol result keys alike."""
    if isinstance(payload, str):
        return symbol in payload
    if isinstance(payload, dict):
        return any(_mentions_symbol(k, symbol) or _mentions_symbol(v, symbol)
                   for k, v in payload.items())
    if isinstance(payload, (list, tuple)):
        return any(_mentions_symbol(v, symbol) for v in payload)
    return False


# Manifest keys that reflect what a run ACTUALLY tested. Deliberately
# excludes "config": research/manifest.py embeds the full config.yaml
# "data" block (including twelve_data_symbols — the entire 20-symbol
# universe) into EVERY manifest for reproducibility, so searching it makes
# every symbol appear to have evidence in every manifest regardless of
# what that run covered. First version of this checker had exactly that
# bug (caught by manual inspection before this module was trusted).
_EVIDENCE_KEYS = ("params", "datasets", "results")


def check_symbol_evidence(config: dict, manifests: dict[str, dict] | None = None) -> dict[str, Any]:
    """Per-symbol enabled/evidence cross-check. `config` is the loaded
    config.yaml dict; `manifests` defaults to everything in
    research/results/ but may be injected for testing."""
    manifests = _load_manifests() if manifests is None else manifests
    symbols = config.get("data", {}).get("twelve_data_symbols", [])

    rows = []
    for s in symbols:
        internal = s.get("internal")
        if not internal:
            continue
        enabled = bool(s.get("enabled"))
        referencing = [
            name for name, payload in manifests.items()
            if any(_mentions_symbol(payload.get(k), internal) for k in _EVIDENCE_KEYS)
        ]
        has_evidence = len(referencing) > 0

        if enabled and not has_evidence:
            verdict = "ENABLED_NO_EVIDENCE"
        elif not enabled and not has_evidence:
            verdict = "DISABLED_NO_EVIDENCE"
        elif not enabled and has_evidence:
            verdict = "DISABLED_WITH_EVIDENCE"
        else:
            verdict = "ENABLED_WITH_EVIDENCE"

        rows.append({
            "symbol": internal,
            "enabled": enabled,
            "manifest_count": len(referencing),
            "manifests": referencing,
            "verdict": verdict,
        })

    return {
        "caveat": ("'evidence' here means AT LEAST ONE committed manifest mentions the "
                   "symbol in params/datasets/results — necessary, not sufficient. A "
                   "spread measurement or a data-collection manifest counts the same as "
                   "a full PF backtest. ENABLED_WITH_EVIDENCE is NOT a claim that the "
                   "symbol has a validated strategy edge — read the listed manifests "
                   "before treating a symbol as proven."),
        "total_symbols": len(rows),
        "enabled_no_evidence": [r["symbol"] for r in rows if r["verdict"] == "ENABLED_NO_EVIDENCE"],
        "disabled_no_evidence": [r["symbol"] for r in rows if r["verdict"] == "DISABLED_NO_EVIDENCE"],
        "rows": rows,
    }


def check_selection_disclosure(manifests: dict[str, dict] | None = None) -> dict[str, Any]:
    """Which manifests declare params.symbol_selection, and whether the
    value (when present) is one of the recognized labels."""
    manifests = _load_manifests() if manifests is None else manifests

    disclosed, undisclosed, invalid_label = [], [], []
    for name, payload in manifests.items():
        label = (payload.get("params") or {}).get("symbol_selection")
        if label is None:
            undisclosed.append(name)
        elif label not in VALID_SELECTION_LABELS:
            invalid_label.append({"manifest": name, "label": label})
        else:
            disclosed.append({"manifest": name, "label": label})

    return {
        "convention_introduced": CONVENTION_INTRODUCED,
        "note": ("Manifests without a declared label are GRANDFATHERED, not "
                 "violations, if generated before the convention date above."),
        "disclosed": disclosed,
        "undisclosed": undisclosed,
        "invalid_label": invalid_label,
    }


def main() -> int:
    from utils.helpers import load_config

    config = load_config()
    manifests = _load_manifests()

    symbol_report = check_symbol_evidence(config, manifests)
    selection_report = check_selection_disclosure(manifests)

    print(f"Symbols checked: {symbol_report['total_symbols']}  ({symbol_report['caveat']})\n")
    if symbol_report["enabled_no_evidence"]:
        print(f"  ENABLED with NO committed backtest evidence: "
              f"{', '.join(symbol_report['enabled_no_evidence'])}")
    else:
        print("  No enabled symbols lack evidence.")
    if symbol_report["disabled_no_evidence"]:
        print(f"  DISABLED with NO committed evidence for the decision: "
              f"{', '.join(symbol_report['disabled_no_evidence'])}")

    print(f"\nManifests: {len(manifests)} total, "
          f"{len(selection_report['disclosed'])} declare symbol_selection, "
          f"{len(selection_report['undisclosed'])} undisclosed (convention "
          f"introduced {selection_report['convention_introduced']} — pre-date OK).")
    if selection_report["invalid_label"]:
        print(f"  INVALID labels: {selection_report['invalid_label']}")

    return 1 if symbol_report["enabled_no_evidence"] else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
