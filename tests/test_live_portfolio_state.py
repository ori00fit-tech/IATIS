"""
Tests for risk/live_portfolio_state.py

These tests prove the Critical fix: RiskInputs is now fed real portfolio
state, so the drawdown hard-stop, drawdown-reduce zone, exposure cap and
correlated-exposure limit can actually trigger.
"""

from __future__ import annotations

from risk.live_portfolio_state import PortfolioState, compute_portfolio_state
from risk.risk_engine import RiskInputs, evaluate_risk

CONFIG = {
    "risk": {
        "starting_balance": 10_000.0,
        "risk_per_trade_max": 0.01,
        "risk_per_trade_min": 0.0025,
        "max_drawdown_reduce": 0.10,
        "max_drawdown_stop": 0.15,
        "max_exposure": 0.05,
        "min_risk_reward": 2.0,
    }
}


def _closed(symbol: str, pnl_usd: float) -> dict:
    return {"symbol": symbol, "outcome": "loss" if pnl_usd < 0 else "win",
            "pnl_usd": pnl_usd}


def _open(symbol: str) -> dict:
    return {"symbol": symbol, "outcome": "open"}


def _state(symbol: str, closed: list[dict], open_rows: list[dict]) -> PortfolioState:
    # recent_signals returns newest-first; module reverses internally.
    return compute_portfolio_state(
        symbol=symbol,
        config=CONFIG,
        _recent_signals_fn=lambda limit, **kw: list(reversed(closed)),
        _open_signals_fn=lambda **kw: open_rows,
    )


# ── Equity curve & drawdown ─────────────────────────────────────────────

def test_no_history_returns_starting_balance_and_zero_drawdown():
    s = _state("EURUSD", [], [])
    assert s.account_balance == 10_000.0
    assert s.current_drawdown_pct == 0.0
    assert s.current_open_risk_pct == 0.0


def test_drawdown_measured_from_equity_peak_not_starting_balance():
    # +2000 (peak 12000) then -1800 → equity 10200, dd = 1800/12000 = 15%
    closed = [_closed("EURUSD", 2000.0), _closed("EURUSD", -1800.0)]
    s = _state("EURUSD", closed, [])
    assert s.account_balance == 10_200.0
    assert s.equity_peak == 12_000.0
    assert abs(s.current_drawdown_pct - 0.15) < 1e-9


def test_drawdown_hard_stop_now_actually_triggers_in_risk_engine():
    """Regression test for the Critical finding: with real state wired in,
    a 16% drawdown must block the trade at the risk gate."""
    closed = [_closed("EURUSD", 5000.0), _closed("EURUSD", -2400.0)]
    s = _state("EURUSD", closed, [])
    assert s.current_drawdown_pct >= 0.15

    result = evaluate_risk(
        RiskInputs(
            account_balance=s.account_balance,
            entry_price=1.1000, stop_loss_price=1.0950, take_profit_price=1.1100,
            current_open_risk_pct=s.current_open_risk_pct,
            current_drawdown_pct=s.current_drawdown_pct,
            correlated_exposure_pct=s.correlated_exposure_pct,
        ),
        CONFIG,
    )
    assert result.passed is False
    assert any("drawdown" in r.lower() for r in result.reasons)


# ── Open risk & exposure cap ────────────────────────────────────────────

def test_open_risk_scales_with_open_signal_count():
    s = _state("EURUSD", [], [_open("GBPUSD"), _open("XAUUSD"), _open("USDCAD")])
    assert abs(s.current_open_risk_pct - 0.03) < 1e-9


def test_exposure_cap_blocks_when_open_risk_at_limit():
    # 5 open trades × 1% = 5% = max_exposure → adding another must fail
    open_rows = [_open(x) for x in ["GBPUSD", "XAUUSD", "USDCAD", "NZDUSD", "USOIL"]]
    s = _state("EURUSD", [], open_rows)
    result = evaluate_risk(
        RiskInputs(
            account_balance=s.account_balance,
            entry_price=1.1000, stop_loss_price=1.0950, take_profit_price=1.1100,
            current_open_risk_pct=s.current_open_risk_pct,
            current_drawdown_pct=s.current_drawdown_pct,
            correlated_exposure_pct=s.correlated_exposure_pct,
        ),
        CONFIG,
    )
    assert result.passed is False


# ── Correlated exposure ─────────────────────────────────────────────────

def test_correlated_exposure_counts_same_group_only():
    # EURUSD is in USD_MAJORS with GBPUSD; XAUUSD is METALS (unrelated).
    s = _state("EURUSD", [], [_open("GBPUSD"), _open("XAUUSD")])
    assert abs(s.correlated_exposure_pct - 0.01) < 1e-9


def test_uncorrelated_symbol_has_zero_correlated_exposure():
    s = _state("XAUUSD", [], [_open("EURUSD"), _open("GBPUSD")])
    assert s.correlated_exposure_pct == 0.0


# ── Fail-safe behavior ──────────────────────────────────────────────────

def test_storage_failure_returns_fail_safe_state_not_crash():
    def boom(**kw):
        raise RuntimeError("db unavailable")

    s = compute_portfolio_state(
        "EURUSD", CONFIG,
        _recent_signals_fn=boom, _open_signals_fn=boom,
    )
    assert s.account_balance == 10_000.0
    assert s.current_drawdown_pct == 0.0
