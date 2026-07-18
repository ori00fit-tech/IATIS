"""
risk/live_portfolio_state.py
----------------------------
Computes the REAL portfolio state used to feed ``RiskInputs`` in main.py.

Motivation (Critical finding, code review 2026-07-02):
    main.py previously passed hardcoded zeros for
    ``current_open_risk_pct``, ``current_drawdown_pct`` and
    ``correlated_exposure_pct``, and a hardcoded ``account_balance``.
    That silently disabled the drawdown hard-stop (15%), the drawdown
    risk-reduce zone (10%), the exposure cap (5%) and the correlated
    exposure limit — the "sovereign risk layer" was only checking RR.

This module derives those values from the outcome tracker (single source
of truth for signal history) so every risk rule actually operates on
live data. All inputs are injected — no hidden globals, no direct DB
path assumptions beyond outcome_tracker's own default.

Design notes:
- Drawdown is computed from the realized equity curve (closed-trade
  pnl_usd applied cumulatively to the configured starting balance),
  measured as the current distance from the running equity peak.
- Open risk assumes each open signal risks ``risk_per_trade_max`` of
  the account (entry→SL distance is already sized to that budget by
  the pipeline). This is conservative and deterministic.
- Correlated exposure sums the open-risk of open signals sharing a
  correlation group with the candidate symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from risk.correlation_engine import CORRELATION_GROUPS
from storage import outcome_tracker
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PortfolioState:
    """Snapshot of live portfolio risk state, in the exact units
    expected by ``risk.risk_engine.RiskInputs``."""

    account_balance: float
    equity_peak: float
    current_open_risk_pct: float        # fraction, e.g. 0.02 = 2%
    current_drawdown_pct: float         # fraction from equity peak
    correlated_exposure_pct: float      # fraction, for the candidate symbol
    open_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "account_balance": round(self.account_balance, 2),
            "equity_peak": round(self.equity_peak, 2),
            "current_open_risk_pct": round(self.current_open_risk_pct, 4),
            "current_drawdown_pct": round(self.current_drawdown_pct, 4),
            "correlated_exposure_pct": round(self.correlated_exposure_pct, 4),
            "open_symbols": list(self.open_symbols),
        }


def _correlated_symbols(symbol: str) -> set[str]:
    """All symbols sharing at least one correlation group with ``symbol``
    (excluding the symbol itself)."""
    related: set[str] = set()
    for members in CORRELATION_GROUPS.values():
        if symbol in members:
            related.update(members)
    related.discard(symbol)
    return related


def compute_portfolio_state(
    symbol: str,
    config: dict,
    db_path: Path | None = None,
    _recent_signals_fn: Callable | None = None,
    _open_signals_fn: Callable | None = None,
) -> PortfolioState:
    """Derive live portfolio state from the outcomes database.

    Args:
        symbol: candidate symbol being evaluated (internal form, e.g. "EURUSD").
        config: full app config; reads ``risk.starting_balance`` and
            ``risk.risk_per_trade_max``.
        db_path: optional override of the outcomes DB path (tests).
        _recent_signals_fn / _open_signals_fn: injectable data accessors
            (tests); default to outcome_tracker functions.

    Returns:
        PortfolioState. On any storage error, returns a FAIL-SAFE state:
        zero balance is never returned — instead the configured starting
        balance with zero derived risk, and the error is logged loudly,
        because blocking all trades on a telemetry read failure is worse
        handled explicitly by the caller than by silently zeroing balance.
    """
    risk_cfg = config.get("risk", {})
    starting_balance = float(risk_cfg.get("starting_balance", 10_000.0))
    per_trade_risk = float(risk_cfg.get("risk_per_trade_max", 0.01))

    # Backtests / replays have no live positions to read, so a CLEAN book is
    # the correct state here — never touch storage, and never fail closed. The
    # fail-CLOSED fallback below is a LIVE-only safety response to an unreadable
    # D1; applying it offline would block every simulated trade (100% exposure)
    # and make the backtest produce nothing but NO_TRADE.
    system_cfg = config.get("system", {})
    if system_cfg.get("replay_mode") or system_cfg.get("backtest_mode"):
        return PortfolioState(
            account_balance=starting_balance,
            equity_peak=starting_balance,
            current_open_risk_pct=0.0,
            current_drawdown_pct=0.0,
            correlated_exposure_pct=0.0,
        )

    kwargs = {"path": db_path} if db_path is not None else {}
    recent_fn = _recent_signals_fn or outcome_tracker.recent_signals
    open_fn = _open_signals_fn or outcome_tracker.get_open_signals

    try:
        # Full closed history, oldest → newest, for the equity curve.
        history = recent_fn(limit=100_000, **kwargs)
        closed = [
            r for r in reversed(history)
            if r.get("outcome") not in (None, "open")
        ]
        open_rows = open_fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 — storage failure must not crash pipeline
        # Fail CLOSED, not open. If we cannot read our current exposure we do
        # NOT know how much risk is already committed, so we must assume the
        # book is full and block any new trade rather than wave it through
        # blind. Returning a clean/zero book here (the previous behaviour) let
        # trades EXECUTE during a storage/D1 outage — the opposite of a
        # fail-safe (observed live: portfolio read failing on an unreachable D1
        # proxy while the risk gate passed). 100% open + correlated exposure
        # trips both the projected-exposure and correlation gates in
        # risk_engine, so the decision is blocked with a clear reason.
        logger.error(f"Portfolio state read failed — failing CLOSED (blocking new trades): {exc}")
        return PortfolioState(
            account_balance=starting_balance,
            equity_peak=starting_balance,
            current_open_risk_pct=1.0,
            current_drawdown_pct=0.0,
            correlated_exposure_pct=1.0,
        )

    # ── Realized equity curve → balance + drawdown from peak ──────────
    equity = starting_balance
    peak = starting_balance
    for row in closed:
        pnl = row.get("pnl_usd") or 0.0
        equity += float(pnl)
        peak = max(peak, equity)

    drawdown_pct = 0.0 if peak <= 0 else max(0.0, (peak - equity) / peak)

    # ── Open risk: conservative fixed budget per open signal ──────────
    open_symbols = [str(r.get("symbol") or "") for r in open_rows]
    open_risk_pct = per_trade_risk * len(open_rows)

    # ── Correlated exposure vs. the candidate symbol ───────────────────
    related = _correlated_symbols(symbol)
    correlated_open = [s for s in open_symbols if s in related]
    correlated_pct = per_trade_risk * len(correlated_open)

    state = PortfolioState(
        account_balance=equity,
        equity_peak=peak,
        current_open_risk_pct=open_risk_pct,
        current_drawdown_pct=drawdown_pct,
        correlated_exposure_pct=correlated_pct,
        open_symbols=open_symbols,
    )
    logger.info(
        f"Portfolio state [{symbol}]: balance={equity:.2f} "
        f"dd={drawdown_pct:.2%} open_risk={open_risk_pct:.2%} "
        f"corr_exposure={correlated_pct:.2%} ({len(correlated_open)} correlated open)"
    )
    return state
