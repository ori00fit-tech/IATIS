"""
Tests for risk/live_portfolio_state.py

These tests prove the Critical fix: RiskInputs is now fed real portfolio
state, so the drawdown hard-stop, drawdown-reduce zone, exposure cap and
correlated-exposure limit can actually trigger.
"""

from __future__ import annotations

from risk.live_portfolio_state import PortfolioState, compute_portfolio_state
from risk.risk_engine import RiskInputs, evaluate_risk
from storage.outcome_tracker import DEFAULT_RISK_USD

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
    """A closed row that recomputes to exactly `pnl_usd` via the REAL
    formula compute_portfolio_state() now uses (trade_math.realized_r *
    DEFAULT_RISK_USD) — not the stored pnl_usd column, which the fix this
    pins no longer trusts. entry=100/sl_distance=1 keeps the algebra
    simple: R = pnl_usd / DEFAULT_RISK_USD, exit = entry + R."""
    r = pnl_usd / DEFAULT_RISK_USD
    entry, sl_distance = 100.0, 1.0
    return {
        "symbol": symbol, "outcome": "loss" if pnl_usd < 0 else "win",
        "entry_price": entry, "stop_loss": entry - sl_distance,
        "exit_price": entry + r * sl_distance, "direction": "BUY",
    }


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


def test_correlated_exposure_excludes_the_candidate_symbol_itself():
    """_correlated_symbols() deliberately discards the candidate symbol
    from its own group (correct — it measures exposure to OTHER
    correlated instruments) — but that means a second open position on
    the EXACT SAME symbol contributes zero to correlated_exposure_pct.
    open_symbols (asserted below) is what main.py's symbol_already_open
    wiring uses to close that gap — this test pins the data it depends on."""
    s = _state("EURUSD", [], [_open("EURUSD")])
    assert s.correlated_exposure_pct == 0.0
    assert "EURUSD" in s.open_symbols


def test_open_symbols_includes_candidate_when_already_open_end_to_end():
    """The exact production gap (2026-07-21/22): two EURUSD signals ~2h
    apart both went through because nothing checked open_symbols against
    the candidate symbol. Confirms the risk gate now blocks it when main.py
    wires symbol_already_open = (symbol in portfolio_state.open_symbols)."""
    s = _state("EURUSD", [], [_open("EURUSD")])
    inputs = RiskInputs(
        account_balance=s.account_balance,
        entry_price=1.0850,
        stop_loss_price=1.0820,
        take_profit_price=1.0940,
        current_open_risk_pct=s.current_open_risk_pct,
        current_drawdown_pct=s.current_drawdown_pct,
        correlated_exposure_pct=s.correlated_exposure_pct,
        symbol_already_open="EURUSD" in s.open_symbols,
    )
    result = evaluate_risk(inputs, CONFIG)
    assert result.passed is False
    assert any("already" in r.lower() and "open" in r.lower() for r in result.reasons)


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


# ── Fail-safe direction (D1/storage outage must fail CLOSED) ─────────────────

def test_portfolio_read_failure_fails_closed_and_blocks_trade():
    """If the portfolio state can't be read (e.g. an unreachable D1 proxy), the
    fallback must BLOCK new trades — assume the book is full — not wave them
    through on a zero book. Regression for the observed live fail-OPEN."""
    def _boom(*a, **kw):
        raise RuntimeError("D1 proxy unreachable")

    s = compute_portfolio_state(
        symbol="EURUSD",
        config=CONFIG,
        _recent_signals_fn=_boom,
        _open_signals_fn=_boom,
    )
    # Fail CLOSED: 100% open + correlated exposure.
    assert s.current_open_risk_pct == 1.0
    assert s.correlated_exposure_pct == 1.0

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


# ── pnl_usd recompute (regression: the stored column is untrusted) ──────

def test_poisoned_stored_pnl_usd_is_ignored_in_favor_of_the_recompute():
    """Legacy rows can carry a corrupted pnl_usd (pre-2026-07-16 pip-size
    bug / old '1 standard lot' approximation, per
    storage/outcome_tracker.py's own performance_summary() comments) —
    compute_portfolio_state() must recompute from entry/stop/exit/direction,
    never trust the stored column, exactly like performance_summary()
    already does."""
    row = _closed("EURUSD", 500.0)  # recomputes to +500 for real
    row["pnl_usd"] = 999_999.0      # poisoned/stale stored value — must be ignored
    s = _state("EURUSD", [row], [])
    assert s.account_balance == 10_500.0  # the recomputed +500, not the poisoned column


def test_missing_stop_loss_contributes_zero_not_a_crash():
    """close_signal() leaves pnl_usd NULL when no stop_loss was stored
    ('cannot size the trade'). The recompute must treat that the same
    way — a real $0 contribution to the equity curve — not raise."""
    row = {"symbol": "EURUSD", "outcome": "win",
           "entry_price": 100.0, "stop_loss": None, "exit_price": 105.0,
           "direction": "BUY"}
    s = _state("EURUSD", [row], [])
    assert s.account_balance == 10_000.0


def test_null_stored_pnl_usd_does_not_prevent_a_real_recompute():
    """The exact bug this fix closes: `row.get('pnl_usd') or 0.0` treated
    a NULL stored pnl_usd as $0 even when entry/stop/exit were present
    and a real R-multiple was computable."""
    row = _closed("EURUSD", -300.0)
    row["pnl_usd"] = None
    s = _state("EURUSD", [row], [])
    assert s.account_balance == 9_700.0  # NOT 10_000.0
