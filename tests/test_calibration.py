"""tests/test_calibration.py — Phase 4 calibration tests."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from unittest.mock import patch
import pytest
from storage.calibration import (
    SCORE_BUCKETS, suggested_dynamic_weights,
    calibration_from_db, regime_performance_matrix,
)
from storage.outcome_tracker import log_signal, close_signal
from storage.decision_db import log_decision_db
from walk_forward_validation import grade_consistency


def _make_report(symbol="EURUSD", score=72.0, regime="TRENDING", verdict="EXECUTE"):
    return {
        "symbol": symbol,
        "final_verdict": verdict,
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {
            "score": score,
            "vote": {"winning_bias": "BEARISH"},
            "fail_reasons": [] if verdict == "EXECUTE" else ["insufficient engine agreement"],
        },
        "risk": {"passed": True if verdict == "EXECUTE" else None},
        "regime": {"state": regime},
        "news": {"news_risk_score": 5.0},
        "engine_outputs": [
            {"engine": "SMC", "bias": "BEARISH", "score": 52},
            {"engine": "NNFX", "bias": "BEARISH", "score": 65},
        ],
    }


def test_score_buckets_cover_range():
    assert SCORE_BUCKETS[0][0] == 55
    assert SCORE_BUCKETS[-1][1] == 101
    for i in range(len(SCORE_BUCKETS) - 1):
        assert SCORE_BUCKETS[i][1] == SCORE_BUCKETS[i+1][0]


def test_grade_consistent():
    assert "CONSISTENT" in grade_consistency([2.5, 2.1, 1.9])


def test_grade_inconsistent():
    assert "INCONSISTENT" in grade_consistency([2.5, 0.8, 1.9])


def test_grade_empty():
    assert grade_consistency([]) == "INSUFFICIENT_DATA"


def test_grade_with_none():
    result = grade_consistency([None, 2.5, 2.1])
    assert result in ("CONSISTENT ✅", "ACCEPTABLE ⚠️", "INCONSISTENT ❌")


def test_suggested_weights_no_data():
    current = {"smc": 0.30, "price_action": 0.25, "ict": 0.15,
               "nnfx": 0.15, "quant": 0.10, "wyckoff": 0.05}
    with patch("storage.engine_tracker.engine_stats", return_value=[]):
        result = suggested_dynamic_weights(current, min_votes=30)
    assert result["status"] == "insufficient_data"
    assert result["weights"] == current


# ---------- regression tests: calibration.py used to query columns that
# don't exist (final_verdict / outcome on `decisions`), fail every call,
# and get silently swallowed into an empty list. See storage/calibration.py
# docstrings on calibration_from_db / regime_performance_matrix. ----------

def test_calibration_from_db_empty_when_no_closed_trades():
    assert calibration_from_db() == []


def test_calibration_from_db_does_not_error_on_real_schema():
    sid_win = log_signal(_make_report(symbol="CALWIN", score=72.0))
    close_signal(sid_win, 1.0640, "win")
    sid_loss = log_signal(_make_report(symbol="CALLOSS", score=73.0))
    close_signal(sid_loss, 1.0920, "loss")

    buckets = calibration_from_db()
    assert buckets == []  # bucket needs n>=5; 2 closed trades isn't enough
    # but the query itself must not have failed silently
    from storage.calibration import _conn
    with _conn() as con:
        rows = con.execute(
            "SELECT cf_score, outcome FROM outcomes WHERE outcome != 'open'"
        ).fetchall()
    assert len(rows) == 2


def test_regime_performance_matrix_empty_when_no_data():
    assert regime_performance_matrix() == []


def test_regime_performance_matrix_computes_from_real_tables():
    # Two NO_TRADE decisions and one EXECUTE decision in TRENDING regime,
    # via the real decisions table (storage/decision_db.py).
    log_decision_db(_make_report(symbol="EURUSD", regime="TRENDING", verdict="NO_TRADE"))
    log_decision_db(_make_report(symbol="GBPUSD", regime="TRENDING", verdict="NO_TRADE"))
    log_decision_db(_make_report(symbol="USDJPY", regime="TRENDING", verdict="EXECUTE"))

    # One closed winning trade in TRENDING, via the outcomes table.
    sid = log_signal(_make_report(symbol="USDJPY", regime="TRENDING", score=80.0))
    close_signal(sid, 1.0640, "win", risk_usd=100.0)

    matrix = regime_performance_matrix()
    assert len(matrix) == 1
    row = matrix[0]
    assert row["regime"] == "TRENDING"
    assert row["total_decisions"] == 3
    assert row["executes"] == 1
    assert row["execute_rate"] == pytest.approx(33.3, abs=0.1)
    assert row["trades"] == 1
    assert row["wins"] == 1
    assert row["win_rate"] == 100.0


def test_suggested_weights_bounds():
    current = {"smc": 0.30, "price_action": 0.25, "ict": 0.15,
               "nnfx": 0.15, "quant": 0.10, "wyckoff": 0.05}
    mock_stats = [
        {"engine": e, "agreement_rate": 70, "neutral_pct": 20, "avg_score_when_voting": 65}
        for e in ["SMC","PriceAction","ICT","NNFX","Quant","Wyckoff"]
    ]
    with patch("storage.engine_tracker.engine_stats", return_value=mock_stats):
        result = suggested_dynamic_weights(current, min_votes=5, min_weight=0.10, max_weight=0.35)
    if result["status"] == "ready":
        total = sum(result["weights"].values())
        assert abs(total - sum(current.values())) < 0.02
