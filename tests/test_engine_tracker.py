"""tests/test_engine_tracker.py — per-engine trade attribution (Engine
Analytics, Mission Control module 8)."""
from __future__ import annotations

from storage.engine_tracker import engine_trade_attribution, record_engine_votes
from storage.outcome_tracker import close_signal, log_signal


def _report(symbol="EURUSD", winning_bias="BULLISH", engines=None):
    engines = engines if engines is not None else [
        {"engine": "SMC", "bias": "BULLISH", "score": 65},
        {"engine": "NNFX", "bias": "BEARISH", "score": 40},
    ]
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": 1.0850,
        "stop_loss": 1.0800,
        "take_profit": 1.0950,
        "confluence": {"score": 70.0, "vote": {"winning_bias": winning_bias}},
        "regime": {"state": "TRENDING"},
        "news": {"news_risk_score": 5.0},
        "engine_outputs": engines,
    }


def test_attribution_empty_state():
    result = engine_trade_attribution()
    assert result["total_closed_trades"] == 0
    assert result["matched_trades"] == 0
    assert result["engines"] == []


def test_attribution_matches_vote_to_trade_and_computes_win_rate():
    report = _report()
    record_engine_votes(report)
    signal_id = log_signal(report)
    close_signal(signal_id, exit_price=1.0950, outcome="win", risk_usd=100.0)

    result = engine_trade_attribution()
    assert result["matched_trades"] == 1
    by_engine = {e["engine"]: e for e in result["engines"]}

    assert by_engine["SMC"]["matched_trades"] == 1
    assert by_engine["SMC"]["wins"] == 1
    assert by_engine["SMC"]["win_rate"] == 100.0
    # SMC voted BULLISH and the trade direction was BULLISH -> agrees.
    assert by_engine["SMC"]["direction_agreement_pct"] == 100.0

    # NNFX voted BEARISH against a BULLISH trade -> counted, not agreeing.
    assert by_engine["NNFX"]["matched_trades"] == 1
    assert by_engine["NNFX"]["direction_agreement_pct"] == 0.0


def test_attribution_excludes_neutral_votes():
    report = _report(engines=[
        {"engine": "SMC", "bias": "NEUTRAL", "score": 0},
        {"engine": "NNFX", "bias": "BULLISH", "score": 60},
    ])
    record_engine_votes(report)
    signal_id = log_signal(report)
    close_signal(signal_id, exit_price=1.0950, outcome="win", risk_usd=100.0)

    result = engine_trade_attribution()
    engines_present = {e["engine"] for e in result["engines"]}
    assert "SMC" not in engines_present
    assert "NNFX" in engines_present


def test_attribution_profit_factor_is_json_safe_infinity_sentinel_with_only_wins():
    report = _report()
    record_engine_votes(report)
    signal_id = log_signal(report)
    close_signal(signal_id, exit_price=1.0950, outcome="win", risk_usd=100.0)

    result = engine_trade_attribution()
    by_engine = {e["engine"]: e for e in result["engines"]}
    assert by_engine["SMC"]["profit_factor"] == "Infinity"


def test_attribution_mixed_wins_and_losses_profit_factor():
    # entry=1.0850, stop=1.0800 -> sl_distance=0.0050.
    # Win at tp=1.0950: diff=+0.0100 -> r=+2.0 -> pnl_usd=+200 (risk_usd=100).
    report = _report(symbol="WINSYM")
    record_engine_votes(report)
    sid_win = log_signal(report)
    close_signal(sid_win, exit_price=1.0950, outcome="win", risk_usd=100.0)

    # Loss at stop=1.0800: diff=-0.0050 -> r=-1.0 -> pnl_usd=-100.
    report2 = _report(symbol="LOSSSYM")
    record_engine_votes(report2)
    sid_loss = log_signal(report2)
    close_signal(sid_loss, exit_price=1.0800, outcome="loss", risk_usd=100.0)

    result = engine_trade_attribution()
    by_engine = {e["engine"]: e for e in result["engines"]}
    assert by_engine["SMC"]["matched_trades"] == 2
    assert by_engine["SMC"]["wins"] == 1
    assert by_engine["SMC"]["losses"] == 1
    # gross_win=200, gross_loss=100 -> PF=2.0
    assert by_engine["SMC"]["profit_factor"] == 2.0


def test_attribution_trade_without_engine_votes_is_unmatched_not_crashed():
    report = _report(symbol="GBPUSD")
    signal_id = log_signal(report)  # no record_engine_votes call
    close_signal(signal_id, exit_price=1.0950, outcome="win", risk_usd=100.0)

    result = engine_trade_attribution()
    assert result["total_closed_trades"] == 1
    assert result["matched_trades"] == 0
    assert result["engines"] == []
