"""
scripts/revive_manifests.py
----------------------------
Revive NOT-REPRODUCIBLE evidence: list every manifest whose
`reproducible` flag is false, explain why, and re-run its experiment
from the current (clean) tree so a fresh, verifiable manifest replaces
the stale badge.

Context: 8 of 13 manifests were labeled NOT REPRODUCIBLE. The dominant
cause was a measurement bug, now fixed — `git status --porcelain`
counted the VPS's untracked runtime files as "dirty", so every
server-side run was doomed to a red badge regardless of what actually
ran. With `--untracked-files=no` in research/manifest.py and the runtime
artifacts gitignored, a re-run from an unmodified tree earns
reproducible=true honestly.

Usage (on the VPS, where the datasets live):

    venv/bin/python -m scripts.revive_manifests             # list + plan
    venv/bin/python -m scripts.revive_manifests --run h008c_oos_bosfvg
    venv/bin/python -m scripts.revive_manifests --run-all   # sequential

Only one experiment kind runs per --run invocation; --run-all executes
them sequentially and stops on the first failure. Data-collection kinds
(deep_history_collection) re-download from providers — expect minutes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent / "research" / "results"

# manifest kind → (re-run command, note). Commands are the modules that
# wrote each manifest, with their production arguments.
REVIVAL_COMMANDS: dict[str, tuple[list[str], str]] = {
    "engine_subset_search": (
        [sys.executable, "-m", "scripts.engine_subset_search"],
        "H015 — OOS subset search (deep H4 CSVs required)",
    ),
    "h008c_oos_bosfvg": (
        [sys.executable, "scripts/run_h008c.py"],  # moved from repo root 2026-07-23 (audit P2-3)
        "H008c — BOS+FVG OOS re-test (real M15; scripts/fetch_m15_twelvedata.py rebuilds inputs)",
    ),
    "crypto_volume_experiment": (
        [sys.executable, "-m", "scripts.experiment_crypto_volume"],
        "volume A/B on BTC/ETH (ccxt fetches its own bars)",
    ),
    "pairs_trading_research": (
        [sys.executable, "-m", "scripts.research_pairs_trading"],
        "Engle-Granger cointegration + OOS z-score",
    ),
    "ctrader_spread_measurement": (
        [sys.executable, "-m", "scripts.measure_ctrader_spread"],
        "live spread snapshot — needs cTrader credentials + market hours",
    ),
    "ic_symbols_backtest": (
        [sys.executable, "-m", "scripts.backtest_ic_symbols", "--all"],
        "351-symbol IC sweep — HOURS of runtime; in-sample discovery only",
    ),
    "deep_history_collection": (
        [sys.executable, "-m", "scripts.download_all_symbols"],
        "re-collects the deep H4/D1 datasets from providers",
    ),
}


def _tree_is_clean() -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    return not out


def load_stale() -> list[dict]:
    stale = []
    for path in sorted(RESULTS_DIR.glob("*_manifest.json")):
        try:
            m = json.loads(path.read_text())
        except Exception:
            continue
        if not m.get("reproducible", False):
            stale.append({
                "file": path.name,
                "kind": m.get("kind", "?"),
                "generated_at": (m.get("generated_at") or "")[:10],
                "commit": (m.get("git", {}).get("commit") or "?")[:8],
                "dirty": m.get("git", {}).get("dirty"),
            })
    return stale


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", metavar="KIND", help="re-run one experiment kind")
    ap.add_argument("--run-all", action="store_true")
    args = ap.parse_args()

    stale = load_stale()
    if not stale:
        print("All manifests are reproducible — nothing to revive. ✅")
        return 0

    print(f"{len(stale)} NOT-REPRODUCIBLE manifest(s):\n")
    kinds_to_run: list[str] = []
    for s in stale:
        cmd = REVIVAL_COMMANDS.get(s["kind"])
        print(f"  {s['kind']:28s} {s['generated_at']}  commit={s['commit']}  ({s['file']})")
        if cmd:
            print(f"    ↻ revive: {' '.join(cmd[0])}   # {cmd[1]}"
                  if isinstance(cmd[0], list) else "")
            kinds_to_run.append(s["kind"])
        else:
            print("    ✗ no automated revival mapped — re-run manually and keep the new manifest")
    print()

    targets = ([args.run] if args.run else
               sorted(set(kinds_to_run)) if args.run_all else [])
    if not targets:
        print("Dry list only. Use --run KIND (or --run-all) to execute.")
        return 0

    if not _tree_is_clean():
        print("✗ Tracked files are modified — a re-run now would be labeled "
              "NOT REPRODUCIBLE again. Commit/stash first.")
        return 1

    for kind in targets:
        entry = REVIVAL_COMMANDS.get(kind)
        if not entry:
            print(f"✗ {kind}: no revival command mapped")
            return 1
        cmd, note = entry
        print(f"\n━━━ reviving {kind} — {note}\n    $ {' '.join(cmd)}")
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            print(f"✗ {kind} exited {rc} — stopping (fix, then resume)")
            return rc
        print(f"✓ {kind} re-run complete — fresh manifest written")
    print("\nDone. Commit the new research/results/*_manifest.json files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
