"""tests/test_inspect_cell_trades.py -- the read-only trade diagnostic runs
end-to-end on stubbed data and never calls run_mission()/record_trial()."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd

from backtest import research_matrix as rm
from scripts import inspect_cell_trades as tool
from storage import research_matrix as storage


def test_runs_and_reports_extreme_trades_without_touching_missions(monkeypatch, capsys):
    bundle = {"name": "Q", "timeframes": ["H1"], "engines": ["quant"], "indicators": [], "context_filters": []}
    storage.upsert_family("fam-inspect", planned_n=1, family_alpha=0.05)
    storage.upsert_cells([rm.MatrixCellSpec(symbol="XAUUSD", bundle=bundle, risk_preset="balanced",
                                            confluence_overrides={"min_engines_agreeing": 1.0})], "fam-inspect")
    cell_id = storage.list_cells(family_id="fam-inspect")[0]["cell_id"]

    idx = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    import backtest.runner as runner
    monkeypatch.setattr(runner, "load_symbol_data", lambda *a, **k: pd.DataFrame({"close": [1, 2, 3]}, index=idx))

    def _t(r, stop):
        return SimpleNamespace(rr_actual=r, entry_price=100.0, stop_loss=100.0 - stop, take_profit=102.0,
                               exit_price=100.0 + r * stop, exit_reason="TP_GAP" if r > 5 else "SL",
                               entry_time=idx[0], exit_time=idx[1], direction="BUY", pnl_usd=r * 10, position_size=1.0)

    trades = [_t(-1.0, 1.0), _t(2.0, 1.0), _t(80.0, 0.0015)]
    metrics = SimpleNamespace(total_trades=3, profit_factor=2.0, avg_rr=27.0, std_rr=46.0)
    import backtest.optimizer as opt
    monkeypatch.setattr(opt, "evaluate_point", lambda *a, **k: SimpleNamespace(metrics=metrics, trade_records=trades))
    import backtest.mission_runner as mr
    monkeypatch.setattr(mr, "run_mission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")), raising=False)

    monkeypatch.setattr(sys, "argv", ["x", cell_id, "--top", "2"])
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert "|R| > 20: 1" in out
    assert "trades with stop < 10% of median stop: 1" in out
    assert "TP_GAP" in out
