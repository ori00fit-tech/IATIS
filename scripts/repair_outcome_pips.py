"""
scripts/repair_outcome_pips.py
-------------------------------
One-shot data-hygiene repair for the ``outcomes`` table.

Rows closed before the 2026-07-16 pip-size fix were written with the FX
pip size (0.0001) for crypto/indices/energy, so their stored ``pnl_pips``
carries millions of phantom pips (a BTC move of a few thousand USD became
tens of millions of "pips"), and pre-R-normalization rows carry a
``pnl_usd`` from the old "1 standard lot" approximation. Reads were
already hardened to recompute from prices (storage/outcome_tracker.py
performance_summary, storage/journal.py) — this script fixes the STORED
values too, so any other consumer of the raw table sees sane numbers.

Recomputation uses exactly the current close_signal() formulas:
    pnl_pips = price_diff / _pip_size(symbol)
    pnl_usd  = (price_diff / |entry − SL|) × DEFAULT_RISK_USD   (NULL without SL)

Idempotent: re-running converges to the same values. Only closed rows
with both entry and exit prices are touched; nothing else is modified —
no entries, exits, or thresholds change (CLAUDE.md rule 6 untouched).

Usage:
    python -m scripts.repair_outcome_pips            # dry-run: report only
    python -m scripts.repair_outcome_pips --apply    # write corrections
"""
from __future__ import annotations

import sys

from storage import d1_client
from storage.outcome_tracker import DEFAULT_RISK_USD, _init_db, _pip_size
from utils.logger import get_logger

logger = get_logger(__name__)

# A stored value further than this (in pips) from the recomputed one is
# corrected. Tolerant of old rounding, strict on real corruption.
_TOLERANCE_PIPS = 1.0
_TOLERANCE_USD = 1.0


def repair(apply: bool = False) -> dict:
    _init_db()
    with d1_client.d1_connection() as con:
        rows = con.execute("""
            SELECT signal_id, symbol, direction, entry_price, stop_loss,
                   exit_price, pnl_pips, pnl_usd
            FROM outcomes
            WHERE outcome != 'open'
              AND entry_price IS NOT NULL AND exit_price IS NOT NULL
        """).fetchall()

        checked = 0
        fixed = 0
        for row in rows:
            checked += 1
            entry, exit_px = row["entry_price"], row["exit_price"]
            is_buy = row["direction"] in ("BUY", "BULLISH")
            diff = (exit_px - entry) if is_buy else (entry - exit_px)

            want_pips = round(diff / _pip_size(row["symbol"] or ""), 1)
            sl = row["stop_loss"]
            sl_dist = abs(entry - sl) if sl is not None else 0.0
            want_usd = round(diff / sl_dist * DEFAULT_RISK_USD, 2) if sl_dist > 0 else None

            have_pips, have_usd = row["pnl_pips"], row["pnl_usd"]
            pips_off = have_pips is None or abs(have_pips - want_pips) > _TOLERANCE_PIPS
            usd_off = (
                (want_usd is None) != (have_usd is None)
                or (want_usd is not None and have_usd is not None
                    and abs(have_usd - want_usd) > _TOLERANCE_USD)
            )
            if not (pips_off or usd_off):
                continue

            fixed += 1
            print(
                f"{'FIX ' if apply else 'WOULD FIX '}{row['signal_id']:<28} "
                f"pips {have_pips} -> {want_pips}   usd {have_usd} -> {want_usd}"
            )
            if apply:
                con.execute(
                    "UPDATE outcomes SET pnl_pips=?, pnl_usd=? WHERE signal_id=?",
                    (want_pips, want_usd, row["signal_id"]),
                )

    result = {"checked": checked, "corrected": fixed, "applied": apply}
    print(f"\n{checked} closed rows checked, {fixed} "
          f"{'corrected' if apply else 'need correction (dry-run — use --apply)'}")
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
