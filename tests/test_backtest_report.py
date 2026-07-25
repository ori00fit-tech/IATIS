"""
Tests for backtest/report.py's Interactive Charts sidecar (Phase 5,
2026-07-24): generate_html_report must ALSO persist a JSON file with the
same base name (equity curve, monthly/yearly returns, by_regime/symbol/
direction/session breakdowns, MC summary) so a dashboard chart can fetch
real series data instead of scraping the HTML report.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from backtest.metrics import TradeRecord, calculate_metrics
from backtest.monte_carlo import run_monte_carlo


def _trade(i: int, pnl: float, win: bool) -> TradeRecord:
    entry = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=i)
    exit_ = entry + pd.Timedelta(hours=5)
    return TradeRecord(
        trade_id=f"t{i}", symbol="EURUSD", direction="BUY",
        entry_time=entry, exit_time=exit_,
        entry_price=1.1000, exit_price=1.1000 + (0.0050 if win else -0.0050),
        stop_loss=1.0950, take_profit=1.1100, position_size=1.0,
        pnl_usd=pnl, pnl_pips=50.0 if win else -50.0,
        rr_actual=1.0 if win else -1.0, rr_planned=2.0,
        holding_bars=5, exit_reason="TP" if win else "SL",
        regime="trending", session="London", is_win=win,
    )


def _trades() -> list[TradeRecord]:
    return [_trade(i, 120.0, True) for i in range(6)] + [_trade(i + 6, -60.0, False) for i in range(4)]


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    import backtest.report as m
    monkeypatch.setattr(m, "REPORTS_DIR", tmp_path)
    return tmp_path


def test_generates_html_and_chart_data_sidecar_with_matching_basename(reports_dir):
    from backtest.report import generate_html_report

    trades = _trades()
    metrics = calculate_metrics(trades)
    html_path = generate_html_report(metrics, trades, symbol="EURUSD", timeframe="H1")

    assert html_path.exists()
    assert html_path.suffix == ".html"

    chart_path = html_path.with_name(html_path.stem + "_chart_data.json")
    assert chart_path.exists()

    payload = json.loads(chart_path.read_text())
    assert payload["symbol"] == "EURUSD"
    assert payload["timeframe"] == "H1"


def test_chart_data_equity_curve_starts_at_initial_balance_and_reflects_trade_pnl(reports_dir):
    from backtest.report import generate_html_report

    trades = _trades()
    metrics = calculate_metrics(trades)
    html_path = generate_html_report(metrics, trades, symbol="EURUSD", timeframe="H1")
    chart_path = html_path.with_name(html_path.stem + "_chart_data.json")
    payload = json.loads(chart_path.read_text())

    curve = payload["equity_curve"]
    assert curve[0] == {"x": "Start", "y": 10_000.0}
    total_pnl = sum(t.pnl_usd for t in trades)
    assert curve[-1]["y"] == pytest.approx(10_000.0 + total_pnl, abs=0.01)
    assert len(curve) == 1 + len(trades)  # start point + one point per closed trade


def test_chart_data_includes_breakdowns_from_metrics(reports_dir):
    from backtest.report import generate_html_report

    trades = _trades()
    metrics = calculate_metrics(trades)
    html_path = generate_html_report(metrics, trades, symbol="EURUSD", timeframe="H1")
    chart_path = html_path.with_name(html_path.stem + "_chart_data.json")
    payload = json.loads(chart_path.read_text())

    for key in ("monthly_returns", "yearly_returns", "by_regime", "by_symbol", "by_direction", "by_session"):
        assert key in payload
        # JSON round-trips dict keys to strings (e.g. yearly_returns' int
        # year keys) — compare stringified keys, not raw equality.
        expected = {str(k): v for k, v in getattr(metrics, key).items()}
        assert payload[key] == expected


def test_chart_data_monte_carlo_is_null_when_not_provided(reports_dir):
    from backtest.report import generate_html_report

    trades = _trades()
    metrics = calculate_metrics(trades)
    html_path = generate_html_report(metrics, trades, symbol="EURUSD", timeframe="H1")
    chart_path = html_path.with_name(html_path.stem + "_chart_data.json")
    payload = json.loads(chart_path.read_text())
    assert payload["monte_carlo"] is None


def test_chart_data_monte_carlo_summary_when_provided(reports_dir):
    from backtest.report import generate_html_report

    trades = _trades()
    metrics = calculate_metrics(trades)
    mc = run_monte_carlo(trades, n_simulations=100)
    html_path = generate_html_report(metrics, trades, mc=mc, symbol="EURUSD", timeframe="H1")
    chart_path = html_path.with_name(html_path.stem + "_chart_data.json")
    payload = json.loads(chart_path.read_text())

    assert payload["monte_carlo"] is not None
    assert set(payload["monte_carlo"]) == {
        "median_return", "p5_return", "p95_return",
        "median_max_dd", "worst_max_dd", "risk_of_ruin", "probability_profit",
    }
