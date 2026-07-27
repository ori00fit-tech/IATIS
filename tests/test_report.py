"""
tests/test_report.py — backtest/report.py's chart_data.json output
(Backtesting Lab professional Results page, 2026-07-27).

Pins three real, additive facts the ResultsStep frontend rewrite depends on:
1. chart_data.json's "kpis" object is the SAME BacktestMetrics values passed
   in — never recomputed, never fabricated — so it can never silently
   disagree with the HTML report's own KPI cards.
2. "html_report" is the exact HTML filename written in the same call.
3. A "Chart data written: <filename>" log line fires after the write — the
   one observable marker ResultsStep parses out of job.log to discover which
   report belongs to the run that just finished.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from backtest import report
from backtest.metrics import BacktestMetrics, TradeRecord


def _make_metrics(**overrides) -> BacktestMetrics:
    defaults = dict(
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        win_rate=60.0,
        profit_factor=1.543,
        sharpe_ratio=1.234,
        sortino_ratio=1.876,
        max_drawdown=12.34,
        net_profit=1234.567,
        total_return_pct=12.345,
        expectancy=45.678,
    )
    defaults.update(overrides)
    return BacktestMetrics(**defaults)


def _make_trade() -> TradeRecord:
    return TradeRecord(
        trade_id="t1",
        symbol="EURUSD",
        direction="BUY",
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        entry_price=1.1,
        exit_price=1.105,
        stop_loss=1.095,
        take_profit=1.12,
        position_size=1000.0,
        pnl_usd=50.0,
        is_win=True,
        exit_reason="tp",
        rr_actual=2.0,
        regime="TRENDING",
        holding_bars=10,
    )


def _generate(tmp_path, monkeypatch, metrics=None, symbol="EURUSD", timeframe="H4"):
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
    metrics = metrics or _make_metrics()
    trades = [_make_trade()]
    out_path = report.generate_html_report(metrics, trades, mc=None, symbol=symbol, timeframe=timeframe)
    chart_path = tmp_path / (out_path.stem + "_chart_data.json")
    data = json.loads(chart_path.read_text())
    return out_path, chart_path, data, metrics


def test_chart_data_kpis_match_real_metrics(tmp_path, monkeypatch):
    _, _, data, metrics = _generate(tmp_path, monkeypatch)
    kpis = data["kpis"]
    assert kpis["total_trades"] == metrics.total_trades
    assert kpis["win_rate"] == round(metrics.win_rate, 2)
    assert kpis["profit_factor"] == round(metrics.profit_factor, 3)
    assert kpis["sharpe_ratio"] == round(metrics.sharpe_ratio, 3)
    assert kpis["sortino_ratio"] == round(metrics.sortino_ratio, 3)
    assert kpis["max_drawdown_pct"] == round(metrics.max_drawdown, 2)
    assert kpis["net_profit"] == round(metrics.net_profit, 2)
    assert kpis["total_return_pct"] == round(metrics.total_return_pct, 2)
    assert kpis["expectancy_usd"] == round(metrics.expectancy, 2)


def test_chart_data_html_report_matches_written_html_filename(tmp_path, monkeypatch):
    out_path, _, data, _ = _generate(tmp_path, monkeypatch)
    assert data["html_report"] == out_path.name
    assert (tmp_path / data["html_report"]).exists()


def test_chart_data_written_log_line_fires_with_exact_filename(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
    metrics = _make_metrics()
    trades = [_make_trade()]
    with caplog.at_level(logging.INFO, logger="backtest.report"):
        out_path = report.generate_html_report(metrics, trades, mc=None, symbol="EURUSD", timeframe="H4")
    chart_filename = out_path.stem + "_chart_data.json"
    assert any(f"Chart data written: {chart_filename}" in rec.message for rec in caplog.records)


def test_chart_data_kpis_differ_from_zero_for_nonzero_metrics(tmp_path, monkeypatch):
    """Regression guard: kpis must reflect the real passed-in metrics object,
    not a hardcoded/default-constructed one."""
    metrics = _make_metrics(sortino_ratio=9.999, sharpe_ratio=-2.5)
    _, _, data, _ = _generate(tmp_path, monkeypatch, metrics=metrics)
    assert data["kpis"]["sortino_ratio"] == 9.999
    assert data["kpis"]["sharpe_ratio"] == -2.5
