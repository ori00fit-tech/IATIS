"""
scripts/purge_unexecuted_outcomes.py
---------------------------------------
Interactive fixer for outcome_tracker rows logged before the 2026-07-25
reconciliation-mismatch fix: a signal whose EXECUTE verdict was logged as
"open" unconditionally, before knowing whether TradeExecutor actually
placed the order. When execution failed or was declined (broker rejection,
max_open_trades, an exception, or the broker path simply not being
configured), the row was never retracted — it stayed "open" forever even
though it never became a real position at the broker.

This is deliberately NOT the same tool as scripts/close_orphaned_trades.py:
that script is for a position that WAS real and closed outside IATIS's own
order flow (it asks for the real broker exit price). This script is for a
signal that was NEVER real in the first place — there is no exit price to
ask for, and forcing a fake win/loss close would fabricate a trade that
never happened.

Deliberately narrow, by design (same conventions as close_orphaned_trades.py):
  - Runs on the VPS using the storage layer directly (D1 credentials
    already in .env) — no API key, no HTTP round-trip.
  - Never touches the cTrader connection — read/write the outcomes table
    only.
  - Never assumes — the operator confirms EACH row individually against
    their own broker history (Positions + History tabs) before deletion.
    Default answer is "no" (skip), so an accidental Enter never deletes
    anything.
  - Deletes rather than closes: a row confirmed never-executed has no real
    win/loss/breakeven to record, so close_signal()'s win/loss classifier
    doesn't apply here.

Usage (on the VPS, from the repo root):
    python3 -m scripts.purge_unexecuted_outcomes

For each open signal: shows symbol/direction/entry/SL/TP/entry_time,
prompts "never executed at the broker — delete this row? [y/N]", deletes
on 'y' (or 'yes', case-insensitive), skips on anything else, and reports
how many were deleted vs. remain at the end.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    # Explicit path, not bare load_dotenv() — see close_orphaned_trades.py's
    # identical comment: anchoring to the repo root removes the "how was
    # this invoked" variable entirely.
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from storage.outcome_tracker import delete_signal, get_open_signals

_CONFIRM_YES = ("y", "yes")


def _check_d1_env() -> str | None:
    """Return a diagnostic message if D1_WORKER_URL isn't set, else None."""
    if os.environ.get("D1_WORKER_URL"):
        return None
    env_path = _REPO_ROOT / ".env"
    lines = [
        "D1_WORKER_URL is not set — .env did not load correctly.",
        f"  expected .env at: {env_path}",
        f"  exists: {env_path.is_file()}",
    ]
    if env_path.is_file():
        try:
            env_path.read_text()
            lines.append("  readable: yes")
        except PermissionError:
            lines.append(
                f"  readable: NO — permission denied as this user. "
                f"Run this as the user that owns {env_path} "
                f"(e.g. `sudo -u iatis {sys.executable} -m scripts.purge_unexecuted_outcomes`)."
            )
    else:
        lines.append(
            "  fix: copy/create .env at the repo root shown above, or set "
            "D1_WORKER_URL directly in the environment before running this script."
        )
    return "\n".join(lines)


def main() -> int:
    diag = _check_d1_env()
    if diag:
        print(diag)
        return 1

    open_signals = get_open_signals()
    if not open_signals:
        print("No open signals in the tracker — nothing to do.")
        return 0

    print(f"{len(open_signals)} open signal(s) found:\n")
    deleted = 0
    for sig in open_signals:
        sid = sig["signal_id"]
        symbol = sig["symbol"]
        direction = sig["direction"]
        entry = sig["entry_price"]
        sl = sig.get("stop_loss")
        tp = sig.get("take_profit")
        entry_time = sig.get("entry_time")

        print(f"- {sid}  {symbol} ({direction})  entry={entry}  SL={sl}  TP={tp}  opened={entry_time}")
        raw = input("  Never executed at the broker — delete this row? [y/N]: ").strip().lower()
        if raw not in _CONFIRM_YES:
            print("  skipped.\n")
            continue

        ok = delete_signal(sid)
        print(f"  -> deleted: {'OK' if ok else 'FAILED'}\n")
        deleted += int(ok)

    remaining = get_open_signals()
    print(f"Deleted {deleted} of {len(open_signals)}. {len(remaining)} still open.")
    if remaining:
        print("Remaining open:", ", ".join(f"{r['signal_id']}" for r in remaining))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
