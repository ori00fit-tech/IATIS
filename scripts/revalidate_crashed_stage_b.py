#!/usr/bin/env python3
"""
scripts/revalidate_crashed_stage_b.py
-------------------------------------
Re-runs Stage B for ONE Matrix cell whose earlier Stage B attempt crashed
on infrastructure (never produced a verdict), recording the result as a
SEPARATE validation. Run manually by a human operator -- never scheduled.

Why this exists (2026-11-04): before the orchestrator fix, a Stage B
crash (e.g. the D1 `UNIQUE constraint` on research_mission_validation_
results when a still-running VALIDATING cell was requeued and validated
twice) was recorded as a terminal REJECTED. Terminal cells are immutable
by design (storage.research_matrix.update_cell), so this script NEVER
reopens or edits the cell: the cell keeps its historical crash record,
and the fresh verdict lives next to it under its own validation_id
(`mtxval-<fingerprint>-manual<N>`), readable via
storage.research_mission_validations.get_validation().

Identical inputs to the orchestrator's own Stage B call (same Stage A
mission/trial, SAME_SYMBOL mode, the cell's own symbol). Refuses any cell
whose recorded reason is not a Stage B crash -- a real verdict is never
re-litigated here.

Usage (VPS):
    python3 -m scripts.revalidate_crashed_stage_b MATRIX-CELL-xxxx [--dry-run]
"""
from __future__ import annotations

import argparse
from pathlib import Path

_CRASH_MARKERS = ("Stage B crashed", "unexpected Stage B failure")


def main() -> int:
    from backtest.mission_validator import SAME_SYMBOL, ValidationConfig, run_validation
    from storage import research_matrix as storage
    from storage import research_mission_validations

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cell_id")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--dry-run", action="store_true", help="show what would run, run nothing")
    args = parser.parse_args()

    cell = storage.get_cell(args.cell_id)
    if cell is None:
        print(f"Unknown cell {args.cell_id!r}.")
        return 1
    reason = cell.get("rejection_reason") or ""
    if not any(m in reason for m in _CRASH_MARKERS):
        print(f"Refusing: cell status={cell['status']!r}, reason={reason[:160]!r} is not a Stage B crash.")
        return 1
    if not cell.get("stage_a_mission_id"):
        print("Refusing: cell has no stage_a_mission_id -- it never completed Stage A.")
        return 1

    n = 1
    while research_mission_validations.get_validation(f"mtxval-{cell['fingerprint']}-manual{n}") is not None:
        n += 1
    validation_id = f"mtxval-{cell['fingerprint']}-manual{n}"
    print(
        f"cell={cell['cell_id']} symbol={cell['symbol']} family={cell['family_id']} "
        f"stage_a_mission={cell['stage_a_mission_id']} trial={cell.get('stage_a_trial_number') or 0} "
        f"p={cell.get('stage_a_p_value')} -> validation_id={validation_id}"
    )
    if args.dry_run:
        print("(dry-run -- nothing run)")
        return 0

    run_validation(ValidationConfig(
        validation_id=validation_id, mission_id=cell["stage_a_mission_id"],
        trial_number=cell.get("stage_a_trial_number") or 0, trial_symbol=cell["symbol"],
        validation_symbols=(cell["symbol"],), data_dir=Path(args.data_dir), start=None, end=None,
        validation_mode=SAME_SYMBOL, output_dir=Path(args.output_dir),
    ))
    validation = research_mission_validations.get_validation(validation_id) or {}
    print(f"overall_verdict={validation.get('overall_verdict')!r} (cell itself left unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
