"""
scripts/repair_decision_risk_flags.py
--------------------------------------
One-shot data-hygiene repair for the ``decisions`` table.

Philosophy-audit check 1.4 ("risk_passed fabricated on confluence-failed
rows") flags rows carrying a risk verdict although the risk gate never
ran for them — main.py's _risk_gate returns None when confluence fails,
and the tri-state write (1/0/NULL) in storage/decision_db.py has recorded
NULL correctly since that fix. Rows written BEFORE it, however, still
hold a fabricated 0/1. This script nulls exactly the rows the audit's
own query matches, restoring the tri-state's meaning ("NULL = never
evaluated") for the whole history.

No verdict, score, reason, or any other decision field is touched — the
decision record itself stays intact; only the value the audit already
declared fabricated is cleared. Idempotent.

Usage:
    python -m scripts.repair_decision_risk_flags            # dry-run
    python -m scripts.repair_decision_risk_flags --apply    # write
"""
from __future__ import annotations

import sys

from storage import d1_client
from utils.logger import get_logger

logger = get_logger(__name__)

# The exact predicate scripts/philosophy_audit.py check 1.4 warns on.
_PREDICATE = "risk_passed IS NOT NULL AND fail_reason LIKE '%engine(s) agree%'"


def repair(apply: bool = False) -> dict:
    with d1_client.d1_connection() as con:
        rows = con.execute(
            f"SELECT id, ts, symbol, verdict, risk_passed FROM decisions "
            f"WHERE {_PREDICATE} ORDER BY ts"
        ).fetchall()
        for r in rows:
            print(
                f"{'FIX ' if apply else 'WOULD FIX '}decision {r['id']} "
                f"{r['ts']} {r['symbol']} verdict={r['verdict']} "
                f"risk_passed={r['risk_passed']} -> NULL"
            )
        if apply and rows:
            con.execute(f"UPDATE decisions SET risk_passed = NULL WHERE {_PREDICATE}")

    result = {"matched": len(rows), "applied": apply}
    print(f"\n{len(rows)} fabricated risk verdict(s) "
          f"{'cleared' if apply else 'found (dry-run — use --apply)'}")
    return result


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    repair(apply="--apply" in sys.argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
