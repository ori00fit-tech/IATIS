"""tests/test_run_h4_yearly_backtest.py

Unit coverage for the yearly-bucketing helpers in
scripts/run_h4_yearly_backtest.py — the new script that re-runs the
frozen production system against scripts/download_deep_history.py's
deeper H4 datasets, bucketed by exit year (h4_yearly_stability_deep).
"""
from __future__ import annotations

import sys
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_h4_yearly_backtest import _yearly_breakdown, _safe_pf


@dataclass
class _FakeTrade:
    exit_time: object
    pnl_usd: float


def test_safe_pf_normal():
    assert _safe_pf(gross_profit=300.0, gross_loss=100.0) == 3.0


def test_safe_pf_no_losses_is_json_safe_sentinel():
    result = _safe_pf(gross_profit=200.0, gross_loss=0.0)
    assert result == "inf (no losses)"
    json.dumps(result)  # would raise on a raw float("inf")


def test_safe_pf_no_trades():
    assert _safe_pf(gross_profit=0.0, gross_loss=0.0) is None


def test_yearly_breakdown_buckets_by_exit_year():
    trades = [
        _FakeTrade(datetime(2020, 3, 1, tzinfo=timezone.utc), 100.0),
        _FakeTrade(datetime(2020, 6, 1, tzinfo=timezone.utc), -50.0),
        _FakeTrade(datetime(2021, 1, 1, tzinfo=timezone.utc), 200.0),
    ]
    result = _yearly_breakdown(trades)
    assert set(result) == {"2020", "2021"}
    assert result["2020"] == {"trades": 2, "wr": 50.0, "pf": 2.0}
    assert result["2021"]["pf"] == "inf (no losses)"


def test_yearly_breakdown_excludes_still_open_trades():
    trades = [
        _FakeTrade(datetime(2020, 3, 1, tzinfo=timezone.utc), 100.0),
        _FakeTrade(None, 999.0),  # never closed — must not appear anywhere
    ]
    result = _yearly_breakdown(trades)
    assert list(result) == ["2020"]
    assert result["2020"]["trades"] == 1


def test_yearly_breakdown_output_is_json_serializable():
    trades = [_FakeTrade(datetime(2022, 5, 1, tzinfo=timezone.utc), 50.0)]
    json.dumps(_yearly_breakdown(trades))
