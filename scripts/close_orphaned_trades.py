"""
scripts/close_orphaned_trades.py
-----------------------------------
Interactive fixer for a broker/internal reconciliation mismatch: closes
outcome_tracker rows that a position-reconciliation alert (see
execution/reconciliation.py, GET /alerts) flagged as "internal-only" —
i.e. a position that was closed manually on the broker (or otherwise
outside IATIS's own order flow) while the internal tracker still shows
it "open".

Deliberately narrow, by design:
  - Runs on the VPS using the storage layer directly (D1 credentials
    already in .env) — no API key, no HTTP round-trip, nothing pasted
    into a chat or shell history.
  - Never touches the cTrader connection (no reconciliation.reconcile()
    call here) — a second process opening/reusing that session would
    race the scheduler's own session lock (audit
    docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P0-3). This script only
    reads/writes the outcomes table.
  - Never guesses an exit price. The operator supplies the real fill
    price from the broker's own trade history for each row — a wrong
    price would corrupt the forward-evidence P&L ledger this project's
    entire measurement discipline depends on (CLAUDE.md rule 6).
  - win/loss/breakeven is computed from direction + entry + the supplied
    exit price, not asked for — one less thing to get wrong by hand.

Usage (on the VPS, from the repo root):
    python3 -m scripts.close_orphaned_trades

For each open signal: shows symbol/direction/entry/SL/TP, prompts for
the real exit price (blank = skip), closes it, and reports how many
open signals remain at the end.
"""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from storage.outcome_tracker import close_signal, get_open_signals

_BUY_DIRECTIONS = ("BUY", "BULLISH")


def classify(direction: str, entry: float, exit_price: float, tol: float = 1e-6) -> str:
    """win/loss/breakeven from direction + entry/exit — never asked for."""
    diff = exit_price - entry
    if abs(diff) <= tol:
        return "breakeven"
    is_buy = direction in _BUY_DIRECTIONS
    favorable = diff > 0 if is_buy else diff < 0
    return "win" if favorable else "loss"


def main() -> int:
    open_signals = get_open_signals()
    if not open_signals:
        print("No open signals in the tracker — nothing to do.")
        return 0

    print(f"{len(open_signals)} open signal(s) found:\n")
    closed = 0
    for sig in open_signals:
        sid = sig["signal_id"]
        symbol = sig["symbol"]
        direction = sig["direction"]
        entry = sig["entry_price"]
        sl = sig.get("stop_loss")
        tp = sig.get("take_profit")

        print(f"- {sid}  {symbol} ({direction})  entry={entry}  SL={sl}  TP={tp}")
        raw = input("  Real exit price from the broker (blank = skip): ").strip()
        if not raw:
            print("  skipped.\n")
            continue
        try:
            exit_price = float(raw)
        except ValueError:
            print("  not a valid number — skipped.\n")
            continue

        outcome = classify(direction, entry, exit_price)
        ok = close_signal(
            sid, exit_price, outcome,
            notes="Manually closed on broker — reconciliation fix (scripts/close_orphaned_trades.py)",
        )
        print(f"  -> closed as '{outcome}' @ {exit_price}: {'OK' if ok else 'FAILED'}\n")
        closed += int(ok)

    remaining = get_open_signals()
    print(f"Closed {closed} of {len(open_signals)}. {len(remaining)} still open.")
    if remaining:
        print("Remaining open:", ", ".join(f"{r['signal_id']}" for r in remaining))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
