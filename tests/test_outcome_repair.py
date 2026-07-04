"""
Regression tests for the 2026-07-03 outcome-tracking fixes:

1. ``auto_close_outcomes`` returns structured records and the scheduler
   calls it without shadowing imports (the UnboundLocalError bug).
2. ``close_signal`` records risk-normalized pnl_usd (R-multiple × risk),
   never the old 1-lot approximation that corrupted the equity curve.
3. Portfolio state derived from R-multiple pnl stays in a sane range
   (a full-SL loss costs exactly the per-trade risk budget).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from storage.outcome_tracker import (
    DEFAULT_RISK_USD,
    auto_close_outcomes,
    close_signal,
    get_open_signals,
    log_signal,
    recent_signals,
)


def _report(symbol: str = "EURUSD", direction: str = "BULLISH",
            entry: float = 1.0850, sl: float = 1.0800,
            tp: float = 1.0950) -> dict:
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "confluence": {"vote": {"winning_bias": direction}, "score": 70},
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "regime": {"regime": "TRENDING"},
        "news": {"news_risk_score": 0},
    }


# ── Fix 1: auto_close contract ──────────────────────────────────────────

def test_auto_close_returns_structured_records():
    log_signal(_report("EURUSD", entry=1.0850, sl=1.0800, tp=1.0950))
    # Price at TP → should close as win
    closed = auto_close_outcomes({"EURUSD": 1.0960})
    assert isinstance(closed, list)
    assert len(closed) == 1
    rec = closed[0]
    assert rec["symbol"] == "EURUSD"
    assert rec["outcome"] == "win"
    assert rec["exit_price"] == pytest.approx(1.0950)
    assert get_open_signals() == []


def test_auto_close_empty_when_no_hit():
    log_signal(_report("EURUSD", entry=1.0850, sl=1.0800, tp=1.0950))
    closed = auto_close_outcomes({"EURUSD": 1.0870})  # between SL/TP
    assert closed == []
    assert len(get_open_signals()) == 1


def test_scheduler_has_no_shadowing_local_import():
    """Guard against reintroducing the UnboundLocalError: scheduler.py
    must not locally re-import auto_close_outcomes inside a function
    that also calls it via the module-level import."""
    sched = Path(__file__).resolve().parent.parent / "scheduler.py"
    src = sched.read_text(encoding="utf-8")
    # exactly one import of the symbol, at module level
    imports = [
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
        and "auto_close_outcomes" in line
    ]
    assert len(imports) == 1, (
        "auto_close_outcomes must be imported exactly once (module level); "
        f"found: {imports}"
    )
    assert not imports[0].startswith((" ", "\t")), (
        "auto_close_outcomes import must be at module level, not inside a "
        "function (local import shadows the name for the whole scope)"
    )


# ── Fix 2: risk-normalized pnl_usd ──────────────────────────────────────

def test_full_sl_loss_costs_exactly_risk_budget():
    sid = log_signal(_report("EURUSD", direction="BULLISH",
                             entry=1.0850, sl=1.0800))
    close_signal(sid, exit_price=1.0800, outcome="loss")
    row = recent_signals(limit=1)[0]
    assert row["pnl_usd"] == pytest.approx(-DEFAULT_RISK_USD)


def test_two_r_win_pays_twice_risk_budget():
    # SL distance 50 pips, exit +100 pips → R = 2.0
    sid = log_signal(_report("EURUSD", direction="BULLISH",
                             entry=1.0850, sl=1.0800))
    close_signal(sid, exit_price=1.0950, outcome="win")
    row = recent_signals(limit=1)[0]
    assert row["pnl_usd"] == pytest.approx(2 * DEFAULT_RISK_USD)


def test_crypto_pnl_is_risk_normalized_not_price_diff():
    """The old code recorded price_diff 1:1 in USD for crypto —
    a 3 000-point BTC move must NOT record ±3 000 USD."""
    sid = log_signal(_report("BTCUSD", direction="BULLISH",
                             entry=60_000.0, sl=58_500.0))
    close_signal(sid, exit_price=63_000.0, outcome="win")  # R = 2.0
    row = recent_signals(limit=1)[0]
    assert row["pnl_usd"] == pytest.approx(2 * DEFAULT_RISK_USD)
    assert abs(row["pnl_usd"]) < 1_000  # sanity: never the raw price diff


def test_missing_sl_records_null_pnl_usd_not_fantasy_lot():
    rep = _report("EURUSD")
    rep["stop_loss"] = None
    sid = log_signal(rep)
    close_signal(sid, exit_price=1.0950, outcome="win")
    row = recent_signals(limit=1)[0]
    assert row["pnl_usd"] is None


def test_custom_risk_usd_scales_pnl():
    sid = log_signal(_report("EURUSD", entry=1.0850, sl=1.0800))
    close_signal(sid, exit_price=1.0900, outcome="win", risk_usd=250.0)
    row = recent_signals(limit=1)[0]
    assert row["pnl_usd"] == pytest.approx(250.0)  # exactly 1R


# ── Fix 3: report exposes current_price for auto-close ─────────────────

def test_pipeline_report_always_has_current_price():
    import numpy as np
    import pandas as pd
    from main import run_pipeline
    from utils.helpers import load_config

    config = load_config("config.yaml")
    config["data"]["source"] = "injected"
    config["execution"] = {**config.get("execution", {}), "telegram_enabled": False}
    config["fundamentals"] = {**config.get("fundamentals", {}),
                              "news_filter_enabled": False}

    idx = pd.date_range("2026-01-01", periods=400, freq="1h", tz="UTC")
    rng = np.random.default_rng(42)
    close = 1.08 + np.cumsum(rng.normal(0, 0.0005, 400))
    df = pd.DataFrame({
        "open": close, "high": close + 0.0008,
        "low": close - 0.0008, "close": close,
        "volume": 1000.0,
    }, index=idx)

    config["data"]["_injected_df"] = df
    report = run_pipeline(config)
    assert "current_price" in report
    assert report["current_price"] == pytest.approx(float(close[-1]))
