"""
scripts/backup_d1.py
---------------------
Nightly backup of the decisions database (production-audit item H5 /
tier-1 gap #4). The forward-evidence record in D1 is the one asset the
project cannot regenerate — until now it lived in a single Cloudflare D1
with no export routine, plus a decisions.jsonl on one VPS disk.

What it does:
  1. Dumps every IATIS table (decisions, engine_votes, outcomes,
     engine_performance, experiences) through the existing
     storage/d1_client proxy — no wrangler needed, same credentials the
     pipeline already uses.
  2. Gzips the dump to backups/d1_YYYYMMDD_HHMM.json.gz with per-table
     row counts in a header, then VERIFIES the file re-loads and the
     counts match before declaring success.
  3. Copies storage/decisions.jsonl alongside it (gzipped).
  4. Rotates: keeps the newest KEEP_N (default 14) of each kind.
  5. Telegram-alerts on FAILURE only (a silent backup that quietly stopped
     working is the classic DR failure mode).

Run nightly via iatis-backup.timer, or manually:

    venv/bin/python -m scripts.backup_d1
    venv/bin/python -m scripts.backup_d1 --out-dir /mnt/offsite/iatis

Restore path (documented, rehearse once): the dump is
{table: {columns: [...], rows: [[...], ...]}} — re-insert with the same
d1_client, or convert to INSERTs for `wrangler d1 execute`.

Off-site note: backups/ still lives on the same VPS disk. Ship it off the
box (rclone/scp cron of backups/ to any object storage) — this script
prints a reminder while backups/ has no off-site marker file.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "backups"
TABLES = ["decisions", "engine_votes", "outcomes", "engine_performance",
          "experiences", "shadow_signals"]
KEEP_N = 14


def dump_tables(con) -> dict:
    dump: dict = {
        "kind": "iatis_d1_backup",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
    }
    for table in TABLES:
        try:
            cur = con.execute(f"SELECT * FROM {table}")  # noqa: S608 — fixed names
            rows = cur.fetchall()
        except Exception as exc:
            # A missing table is recorded, not fatal — fresh DBs lack some.
            dump["tables"][table] = {"error": str(exc), "rows": []}
            continue
        if rows:
            first = rows[0]
            columns = list(first.keys()) if hasattr(first, "keys") else None
            as_lists = [[row[c] for c in columns] for row in rows] if columns else [list(r) for r in rows]
        else:
            columns, as_lists = [], []
        dump["tables"][table] = {"columns": columns, "count": len(as_lists), "rows": as_lists}
    return dump


def verify(path: Path, expected_counts: dict[str, int]) -> None:
    with gzip.open(path, "rt") as f:
        reloaded = json.load(f)
    for table, expected in expected_counts.items():
        got = reloaded["tables"][table].get("count", -1)
        if got != expected:
            raise RuntimeError(f"verify failed: {table} wrote {expected} rows, re-read {got}")


def rotate(out_dir: Path, pattern: str, keep: int) -> int:
    files = sorted(out_dir.glob(pattern))
    removed = 0
    for old in files[:-keep] if len(files) > keep else []:
        old.unlink(missing_ok=True)
        removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep", type=int, default=KEEP_N)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    try:
        from storage import d1_client
        with d1_client.d1_connection() as con:
            dump = dump_tables(con)

        counts = {t: v.get("count", 0) for t, v in dump["tables"].items() if "error" not in v}
        db_path = args.out_dir / f"d1_{stamp}.json.gz"
        with gzip.open(db_path, "wt") as f:
            json.dump(dump, f)
        verify(db_path, counts)

        jsonl_src = PROJECT_ROOT / "storage" / "decisions.jsonl"
        jsonl_note = "absent"
        if jsonl_src.exists():
            jsonl_dst = args.out_dir / f"decisions_{stamp}.jsonl.gz"
            with open(jsonl_src, "rb") as fin, gzip.open(jsonl_dst, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            jsonl_note = jsonl_dst.name

        removed = (rotate(args.out_dir, "d1_*.json.gz", args.keep)
                   + rotate(args.out_dir, "decisions_*.jsonl.gz", args.keep))

        total_rows = sum(counts.values())
        size_kb = db_path.stat().st_size / 1024
        print(f"✓ backup OK: {db_path.name} ({size_kb:.0f} KB, {total_rows} rows "
              f"across {len(counts)} tables, verified) + {jsonl_note}; "
              f"rotated out {removed}; {time.monotonic()-t0:.1f}s")
        for t, c in counts.items():
            print(f"    {t:20s} {c:6d} rows")
        if not (args.out_dir / ".offsite-configured").exists():
            print("  ! backups/ is still on the SAME disk as the database it protects.\n"
                  "    Ship it off-box (rclone/scp cron), then `touch backups/.offsite-configured`\n"
                  "    to silence this reminder.")
        return 0

    except Exception as exc:
        msg = f"D1 backup FAILED: {type(exc).__name__}: {exc}"
        print(f"✗ {msg}", file=sys.stderr)
        try:
            from execution.telegram_bot import send_raw
            send_raw(f"🚨 <b>IATIS backup failed</b>\n{msg}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
