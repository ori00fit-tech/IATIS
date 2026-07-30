"""
tests/test_research_missions_storage.py
-------------------------------------------
AI Research Lab Phase 4 (2026-07-30) — tests for storage/
research_missions.py's list_recent_missions() accessor, added so
execution/routes/ai.py's _build_copilot_context() can ground a
suggestion in recent Mission Center findings without already knowing a
mission ID (unlike every other function in that module).
"""
from __future__ import annotations

from storage import research_missions


def _seed(mission_id: str, status: str = "finished") -> None:
    research_missions.upsert_mission(
        mission_id=mission_id, name=mission_id, sampler="random", objective_metric="profit_factor",
        symbols=["EURUSD"], n_trials_per_symbol=1, min_trades=1, seed=42,
        search_space={"timeframes_choices": [["H1"]], "engine_set_choices": [["nnfx"]],
                      "indicator_set_choices": [[]], "risk_param_ranges": {}, "risk_param_grid": {}},
        config={}, status=status,
    )


def test_list_recent_missions_empty_by_default():
    assert research_missions.list_recent_missions() == []


def test_list_recent_missions_newest_first_and_respects_limit():
    for mid in ["m-a", "m-b", "m-c"]:
        _seed(mid)
    rows = research_missions.list_recent_missions(limit=2)
    assert len(rows) == 2
    ids = {r["id"] for r in research_missions.list_recent_missions(limit=10)}
    assert ids == {"m-a", "m-b", "m-c"}


def test_list_recent_missions_filters_by_status():
    _seed("m-finished", status="finished")
    _seed("m-failed", status="failed")
    finished = research_missions.list_recent_missions(limit=10, status="finished")
    assert {r["id"] for r in finished} == {"m-finished"}
