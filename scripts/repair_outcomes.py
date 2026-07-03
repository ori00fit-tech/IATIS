#!/usr/bin/env python3
"""
scripts/repair_outcomes.py
--------------------------
One-time repair of the outcomes database after two defects corrupted
portfolio state (found in the 2026-07-03 scheduler run):

1. ``pnl_usd`` was recorded on a "1 standard lot" basis, inconsistent
   with the risk layer's fixed-fraction assumption. Crypto/metal moves
   inflated the realized equity curve to ~$202M, silently disabling the
   drawdown hard-stop and distorting SHI/calibration inputs.
   → Recompute pnl_usd for every closed row as
     R-multiple × risk_usd, where R = signed(price_diff) / |entry − SL|.

2. The scheduler's auto-close call crashed on every run
   (UnboundLocalError), so open signals accumulated forever
   (73+ phantom opens → open_risk 313% → risk gate rejected everything).
   → Expire open signals older than ``--max-age-days`` as
     outcome='expired' with pnl 0, so they stop counting toward
     open/correlated exposure. Genuine recent opens are left alone;
     the fixed scheduler will now close them on SL/TP normally.

Safe by default: runs in DRY-RUN mode unless ``--apply`` is given.
A timestamped backup of the DB file is written before any change.

Usage (on the VPS):
    python3 scripts/repair_outcomes.py                 # dry run, show plan
    python3 scripts/repair_outcomes.py --apply         # execute
    python3 scripts/repair_outcomes.py --apply --max-age-days 7
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.outcome_tracker import DB_PATH, DEFAULT_RISK_USD  # noqa: E402


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def recompute_closed_pnl(
    con: sqlite3.Connection, risk_usd: float, apply: bool
) -> tuple[int, int]:
    """Recompute pnl_usd for all closed rows as R-multiple × risk_usd.

    Returns:
        (rows_updated, rows_skipped_no_sl)
    """
    rows = con.execute(
        "SELECT signal_id, symbol, direction, entry_price, stop_loss, "
        "exit_price, pnl_usd FROM outcomes "
        "WHERE outcome IS NOT NULL AND outcome != 'open' "
        "AND exit_price IS NOT NULL"
    ).fetchall()

    updated, skipped = 0, 0
    for r in rows:
        entry = r["entry_price"] or 0.0
        sl = r["stop_loss"] or 0.0
        exit_px = r["exit_price"]
        sl_distance = abs(entry - sl)

        if sl_distance <= 0 or not entry:
            skipped += 1
            print(f"  SKIP {r['signal_id']} ({r['symbol']}): no usable SL/entry "
                  f"— pnl_usd set to NULL (was {r['pnl_usd']})")
            if apply:
                con.execute(
                    "UPDATE outcomes SET pnl_usd=NULL, "
                    "notes=COALESCE(notes,'') || ' | repair: no SL, pnl nulled' "
                    "WHERE signal_id=?",
                    (r["signal_id"],),
                )
            continue

        is_buy = (r["direction"] or "") in ("BUY", "BULLISH")
        price_diff = (exit_px - entry) if is_buy else (entry - exit_px)
        new_pnl = round((price_diff / sl_distance) * risk_usd, 2)

        old = r["pnl_usd"]
        if old is not None and abs((old or 0.0) - new_pnl) < 0.01:
            continue  # already correct

        print(f"  FIX  {r['signal_id']} ({r['symbol']}): "
              f"pnl_usd {old} → {new_pnl}")
        if apply:
            con.execute(
                "UPDATE outcomes SET pnl_usd=? WHERE signal_id=?",
                (new_pnl, r["signal_id"]),
            )
        updated += 1
    return updated, skipped


def expire_stale_opens(
    con: sqlite3.Connection, max_age_days: int, apply: bool
) -> int:
    """Close open signals older than max_age_days as 'expired' with pnl 0.

    Rationale: these signals accumulated only because auto-close was
    broken. Their true outcomes are unknowable now; recording pnl 0
    keeps the equity curve neutral instead of inventing wins/losses,
    while removing them from open-risk / correlated-exposure counts.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    rows = con.execute(
        "SELECT signal_id, symbol, entry_time FROM outcomes "
        "WHERE (outcome IS NULL OR outcome = 'open') AND entry_time < ?",
        (cutoff,),
    ).fetchall()

    for r in rows:
        print(f"  EXPIRE {r['signal_id']} ({r['symbol']}, "
              f"opened {r['entry_time']})")
        if apply:
            con.execute(
                "UPDATE outcomes SET outcome='expired', exit_time=?, "
                "pnl_pips=0, pnl_usd=0, "
                "notes=COALESCE(notes,'') || ' | repair: expired stale open "
                "(auto-close was broken)' WHERE signal_id=?",
                (now_iso, r["signal_id"]),
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH),
                        help=f"Path to outcomes DB (default: {DB_PATH})")
    parser.add_argument("--risk-usd", type=float, default=DEFAULT_RISK_USD,
                        help="Per-trade risk budget in USD for R-multiple "
                             f"pnl (default: {DEFAULT_RISK_USD})")
    parser.add_argument("--max-age-days", type=int, default=14,
                        help="Open signals older than this are expired "
                             "(default: 14)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes (default: dry run)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        sys.exit(1)

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== repair_outcomes ({mode}) on {db_path} ===")

    if args.apply:
        backup = db_path.with_suffix(
            f".backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
        )
        shutil.copy2(db_path, backup)
        print(f"Backup written: {backup}")

    con = _connect(db_path)
    try:
        print(f"\n[1/2] Recompute closed pnl_usd (risk_usd={args.risk_usd}):")
        updated, skipped = recompute_closed_pnl(con, args.risk_usd, args.apply)
        print(f"  → {updated} row(s) corrected, {skipped} nulled (no SL)")

        print(f"\n[2/2] Expire stale open signals (> {args.max_age_days}d):")
        expired = expire_stale_opens(con, args.max_age_days, args.apply)
        print(f"  → {expired} stale open(s) expired")

        if args.apply:
            con.commit()
            print("\nCommitted. Re-run the scheduler; portfolio state "
                  "(balance/open_risk/correlated exposure) should now be sane.")
        else:
            print("\nDry run only — re-run with --apply to write changes.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
