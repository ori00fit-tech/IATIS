"""
Tests for backtest/metrics.py::json_safe and its use at the three report
writers that can carry a real float('inf') profit_factor (a symbol/window/
sweep-point with zero losing trades) into a JSON payload:
backtest/robustness.py, backtest/walk_forward.py, backtest/runner.py.

Regression for a real bug found via live browser testing: json.dumps
emits the bare token `Infinity` for float('inf') by default, which is
NOT valid JSON — any strict JSON.parse() (every browser's fetch().json()
included) throws on the resulting report file.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from backtest.metrics import json_safe


# ─────────────────────────────────────────────────────────────────────────
# json_safe — unit
# ─────────────────────────────────────────────────────────────────────────

def test_json_safe_converts_positive_infinity_to_string_sentinel():
    assert json_safe(float("inf")) == "Infinity"


def test_json_safe_converts_negative_infinity_to_string_sentinel():
    assert json_safe(float("-inf")) == "-Infinity"


def test_json_safe_converts_nan_to_string_sentinel():
    assert json_safe(float("nan")) == "NaN"


def test_json_safe_leaves_normal_floats_and_other_types_untouched():
    assert json_safe(1.71) == 1.71
    assert json_safe(0) == 0
    assert json_safe("STABLE") == "STABLE"
    assert json_safe(True) is True
    assert json_safe(None) is None


def test_json_safe_recurses_into_nested_dicts_and_lists():
    payload = {
        "symbols": {
            "EURUSD": {
                "sweeps": [
                    {"param": "min_rr", "points": [{"profit_factor": float("inf")}, {"profit_factor": 1.5}]},
                ]
            }
        }
    }
    safe = json_safe(payload)
    assert safe["symbols"]["EURUSD"]["sweeps"][0]["points"][0]["profit_factor"] == "Infinity"
    assert safe["symbols"]["EURUSD"]["sweeps"][0]["points"][1]["profit_factor"] == 1.5


def test_json_safe_output_always_survives_strict_json_round_trip():
    payload = {"a": float("inf"), "b": [float("-inf"), float("nan"), 2.5], "c": {"d": float("inf")}}
    text = json.dumps(json_safe(payload))
    # json.loads is not the strict browser parser, but a bare `Infinity`
    # token would already have broken json.dumps' own default encoder
    # into emitting something json.loads also chokes on differently —
    # the real assertion that matters is no bare Infinity/NaN token in
    # the text at all.
    assert "Infinity" not in text.replace('"Infinity"', '').replace('"-Infinity"', '')
    assert "NaN" not in text.replace('"NaN"', '')
    reloaded = json.loads(text)
    assert reloaded["a"] == "Infinity"
    assert reloaded["b"] == ["-Infinity", "NaN", 2.5]


def _reject_bare_constants(token: str) -> float:
    """Mimics a browser's strict JSON.parse(), which has no equivalent of
    Python json.loads' lenient built-in Infinity/-Infinity/NaN constants."""
    raise json.JSONDecodeError(f"bare {token} is not valid JSON", token, 0)


def test_json_safe_without_sanitizing_would_produce_invalid_json():
    # Establishes the bug this whole module fixes: json.dumps' default
    # behavior on a bare float('inf') is NOT valid JSON — Python's own
    # json.loads only accepts it because it's a deliberately lenient
    # extension, one a browser's fetch().json() does not share.
    text = json.dumps({"profit_factor": float("inf")})
    with pytest.raises(json.JSONDecodeError):
        json.loads(text, parse_constant=_reject_bare_constants)


# ─────────────────────────────────────────────────────────────────────────
# Regression: the three real report writers, forced into the inf-PF case
# ─────────────────────────────────────────────────────────────────────────

def _ohlcv(n: int, seed: int = 7, trend: float = 0.06) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, close) + 0.0008,
            "low": np.minimum(o, close) - 0.0008,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


def test_robustness_report_survives_strict_json_when_a_sweep_point_has_zero_losses(tmp_path, monkeypatch):
    import backtest.robustness as m

    df = _ohlcv(2400, trend=0.10)
    df.to_csv(tmp_path / "EURUSD_H1_2y.csv")

    # Force every sweep point to report zero losing trades (PF = inf) —
    # deterministic, without depending on the real engine ever actually
    # producing a lossless run.
    monkeypatch.setattr(m, "_run_point", lambda *a, **k: (15, float("inf"), 100.0, 0.0))

    out_dir = tmp_path / "reports"
    m.run_robustness_suite(["EURUSD"], tmp_path, m.RobustnessConfig(params=("min_rr",), min_trades=1), output_dir=out_dir)

    report = next(out_dir.glob("robustness_*.json"))
    payload = json.loads(report.read_text())  # must not raise
    point = payload["symbols"]["EURUSD"]["sweeps"][0]["points"][0]
    assert point["profit_factor"] == "Infinity"


def test_walk_forward_report_survives_strict_json_when_a_window_has_zero_losses(tmp_path, monkeypatch):
    import backtest.walk_forward as m

    class _InfMetrics:
        total_trades = 15
        profit_factor = float("inf")
        win_rate = 1.0
        max_drawdown = 0.0
        expectancy = 50.0

    monkeypatch.setattr(m, "calculate_metrics", lambda *a, **k: _InfMetrics())

    df = _ohlcv(2400, trend=0.10)
    df.to_csv(tmp_path / "EURUSD_H1_2y.csv")

    out_dir = tmp_path / "reports"
    m.run_walk_forward_suite(
        ["EURUSD"], tmp_path, m.WalkForwardConfig(n_windows=2, min_pf=1.5, min_trades_per_window=1),
        output_dir=out_dir,
    )

    report = next(out_dir.glob("walk_forward_*.json"))
    payload = json.loads(report.read_text())  # must not raise
    window = payload["symbols"]["EURUSD"]["windows"][0]
    assert window["profit_factor"] == "Infinity"


def test_backtest_summary_survives_strict_json_when_a_symbol_has_zero_losses(tmp_path, monkeypatch):
    import backtest.runner as m

    df = _ohlcv(2400, trend=0.10)
    df.to_csv(tmp_path / "EURUSD_H1_2y.csv")

    cfg = m.RunnerConfig(symbols=("EURUSD",), data_dir=tmp_path, run_mc=False, write_html=False)
    results = m.run_all(cfg)
    assert "EURUSD" in results

    # Force the metrics object's profit_factor to inf post-hoc — simplest
    # deterministic way to hit the exact write_summary() code path without
    # depending on a real lossless run.
    results["EURUSD"].metrics.profit_factor = float("inf")

    out_dir = tmp_path / "reports"
    path = m.write_summary(results, out_dir)
    payload = json.loads(path.read_text())  # must not raise
    assert payload["symbols"]["EURUSD"]["profit_factor"] == "Infinity"
