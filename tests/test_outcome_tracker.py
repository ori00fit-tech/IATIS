"""tests/test_outcome_tracker.py"""
from __future__ import annotations
import pytest
from storage.outcome_tracker import (
    log_signal, close_signal, get_open_signals,
    performance_summary, recent_signals, _pip_size
)


def _make_report(symbol="EURUSD", score=72.0, regime="TRENDING"):
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {
            "score": score,
            "vote": {"winning_bias": "BEARISH"},
        },
        "regime": {"state": regime},
        "news": {"news_risk_score": 5.0},
        "engine_outputs": [
            {"engine": "SMC", "bias": "BEARISH", "score": 52},
            {"engine": "NNFX", "bias": "BEARISH", "score": 65},
        ],
    }


def test_log_signal_creates_record():
    signal_id = log_signal(_make_report())
    assert signal_id != ""
    signals = get_open_signals()
    assert len(signals) == 1
    assert signals[0]["symbol"] == "EURUSD"
    assert signals[0]["outcome"] == "open"


def test_close_signal_win():
    signal_id = log_signal(_make_report())
    success = close_signal(signal_id, exit_price=1.0640, outcome="win")
    assert success is True
    open_sigs = get_open_signals()
    assert len(open_sigs) == 0


def test_close_signal_loss():
    signal_id = log_signal(_make_report())
    success = close_signal(signal_id, exit_price=1.0920, outcome="loss")
    assert success is True


def test_close_nonexistent_signal():
    success = close_signal("NONEXISTENT_ID", 1.0, "win")
    assert success is False


def test_performance_summary_empty():
    summary = performance_summary()
    assert summary["total_closed"] == 0
    assert summary["win_rate"] == 0


def test_performance_summary_with_data():
    for i in range(3):
        sid = log_signal(_make_report(symbol=f"EUR{i}"))
        close_signal(sid, 1.064, "win")
    sid = log_signal(_make_report(symbol="LOSS"))
    close_signal(sid, 1.092, "loss")

    summary = performance_summary()
    assert summary["total_closed"] == 4
    assert summary["wins"] == 3
    assert summary["losses"] == 1
    assert summary["win_rate"] == 75.0


def test_pnl_pips_calculated():
    signal_id = log_signal(_make_report("EURUSD"))
    # BEARISH entry=1.0850 exit=1.0640 → (1.0850-1.0640)/0.0001 = 210 pips profit
    close_signal(signal_id, 1.0640, "win")
    recent = recent_signals(limit=1)
    assert recent[0]["pnl_pips"] == pytest.approx(210.0, abs=1.0)


def test_pip_size_by_asset_class():
    # Broker-confirmed (IC Markets cTrader, 2026-07-16): FX 0.0001, JPY 0.01,
    # metals/energy/crypto 0.01. Regression guard for the millions-of-pips bug.
    assert _pip_size("EURUSD") == 0.0001
    assert _pip_size("USDJPY") == 0.01
    assert _pip_size("EURJPY") == 0.01
    assert _pip_size("XAUUSD") == 0.01
    assert _pip_size("XAGUSD") == 0.01   # was wrongly 0.001 before the fix
    assert _pip_size("USOIL") == 0.01
    assert _pip_size("BTCUSD") == 0.01   # was 0.0001 → millions of phantom pips
    assert _pip_size("ETHUSD") == 0.01
    assert _pip_size("US30") == 0.1      # broker pip=1
    assert _pip_size("NAS100") == 0.1
    assert _pip_size("SPX500") == 0.1
    # Unknown symbol never falls back to a value that re-triggers the bug.
    assert _pip_size("FOOBAR") == 0.0001


def test_crypto_pnl_pips_not_exploded():
    # A $2,000 BTC move must be 200,000 pips (÷0.01), not 20,000,000 (÷0.0001).
    report = _make_report(symbol="BTCUSD")
    report["entry_price"] = 100_000.0
    report["stop_loss"] = 98_000.0
    report["take_profit"] = 104_000.0  # BEARISH, so exit below entry = win
    sid = log_signal(report)
    close_signal(sid, 98_000.0, "win")
    recent = recent_signals(limit=1)
    assert recent[0]["pnl_pips"] == pytest.approx(200_000.0, rel=1e-3)


def test_regime_breakdown():
    for regime in ["TRENDING", "TRENDING", "RANGING"]:
        # Force different signal_ids by varying symbol
        sid = log_signal(_make_report(symbol=f"EUR{regime[:3]}", regime=regime))
        close_signal(sid, 1.064, "win")

    summary = performance_summary()
    regimes = {r["regime"]: r for r in summary["by_regime"]}
    assert "TRENDING" in regimes
    assert regimes["TRENDING"]["n"] >= 1


def test_multiple_symbols():
    for sym in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]:
        log_signal(_make_report(symbol=sym))
    assert len(get_open_signals()) == 4


def test_duplicate_signal_id_ignored():
    r = _make_report()
    log_signal(r)
    log_signal(r)  # same timestamp+symbol → INSERT OR IGNORE
    assert len(get_open_signals()) == 1


# ---------- profit factor / avg R-multiple (Forward Demo, module 6) ----------

def test_performance_summary_empty_has_no_profit_factor():
    summary = performance_summary()
    assert summary["profit_factor"] is None
    assert summary["avg_r_multiple"] is None


def test_performance_summary_profit_factor_and_avg_r_multiple():
    # BEARISH: entry=1.0850, stop_loss=1.0920 (sl_distance=0.0070), tp=1.0640
    # Win at TP: diff = entry-exit = 0.0210 → r = 0.0210/0.0070 = 3.0 → pnl_usd = 300
    sid_win = log_signal(_make_report(symbol="WIN1"))
    close_signal(sid_win, 1.0640, "win", risk_usd=100.0)

    # Loss at SL: diff = entry-exit = -0.0070 → r = -1.0 → pnl_usd = -100
    sid_loss = log_signal(_make_report(symbol="LOSS1"))
    close_signal(sid_loss, 1.0920, "loss", risk_usd=100.0)

    summary = performance_summary()
    assert summary["profit_factor"] == pytest.approx(3.0, abs=0.01)
    assert summary["avg_r_multiple"] == pytest.approx(1.0, abs=0.01)


def test_performance_summary_profit_factor_infinite_with_only_wins():
    # Zero losing trades → PF is mathematically infinite. Must be a JSON-safe
    # string sentinel, not a raw float("inf") — Python's json.dumps would
    # emit a bare `Infinity` token, which is not valid JSON and makes a
    # browser's fetch().json() throw client-side.
    sid = log_signal(_make_report(symbol="ALLWIN"))
    close_signal(sid, 1.0640, "win", risk_usd=100.0)
    summary = performance_summary()
    assert summary["profit_factor"] == "Infinity"


def test_performance_summary_all_breakeven_profit_factor_is_not_infinite():
    """Regression for docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-9: an
    all-breakeven book (zero wins AND zero losses) is 0/0, undefined — not
    infinite. This exact bug existed independently in this function's own
    inline profit_factor logic (fixed by consolidating onto
    utils.trade_math.profit_factor, the same fix already applied to
    storage/journal.py's sibling copy under P3-2)."""
    # BEARISH entry=1.0850, exit=1.0850 -> price_diff=0, r=0 (breakeven).
    sid = log_signal(_make_report(symbol="BE1"))
    close_signal(sid, 1.0850, "breakeven", risk_usd=100.0)
    summary = performance_summary()
    assert summary["profit_factor"] is None
