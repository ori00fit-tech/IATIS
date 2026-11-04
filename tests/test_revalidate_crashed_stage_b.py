"""tests/test_revalidate_crashed_stage_b.py -- the manual Stage B re-run tool
never reopens a cell and never re-litigates a real verdict."""
from __future__ import annotations

import sys

from backtest import research_matrix as rm
from scripts import revalidate_crashed_stage_b as tool
from storage import research_matrix as storage

_BUNDLE = {"name": "NNFX only", "timeframes": ["H1"], "engines": ["nnfx"], "indicators": [], "context_filters": []}


def _terminal_cell(reason: str, status: str = rm.REJECTED) -> str:
    storage.upsert_family("fam-reval", planned_n=1, family_alpha=0.05)
    storage.upsert_cells([rm.MatrixCellSpec(symbol="BTCUSD", bundle=_BUNDLE, risk_preset="balanced")], "fam-reval")
    cell_id = storage.list_cells(family_id="fam-reval")[0]["cell_id"]
    storage.update_cell(cell_id, status=status, rejection_reason=reason, stage_a_mission_id="mtx-x", stage_a_trial_number=0)
    return cell_id


def test_refuses_a_cell_with_a_real_stage_b_verdict(monkeypatch, capsys):
    cell_id = _terminal_cell("Stage B verdict: SAME_SYMBOL_NOT_CONFIRMED")
    monkeypatch.setattr(sys, "argv", ["x", cell_id, "--dry-run"])
    assert tool.main() == 1
    assert "not a Stage B crash" in capsys.readouterr().out


def test_dry_run_on_a_crashed_cell_runs_nothing_and_leaves_the_cell_unchanged(monkeypatch, capsys):
    cell_id = _terminal_cell("Stage B crashed: D1 exec failed (HTTP 500): UNIQUE constraint failed")
    before = storage.get_cell(cell_id)
    import backtest.mission_validator as mv
    monkeypatch.setattr(mv, "run_validation", lambda vc: (_ for _ in ()).throw(AssertionError("must not run")))
    monkeypatch.setattr(sys, "argv", ["x", cell_id, "--dry-run"])
    assert tool.main() == 0
    assert "-manual1" in capsys.readouterr().out
    assert storage.get_cell(cell_id) == before
