"""tests/test_calibration.py — Phase 4 calibration tests."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from unittest.mock import patch
import pytest
from storage.calibration import SCORE_BUCKETS, suggested_dynamic_weights
from walk_forward_validation import grade_consistency


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
