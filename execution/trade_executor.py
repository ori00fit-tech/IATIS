"""
execution/trade_executor.py
-----------------------------
Bridge between IATIS pipeline output and OANDA order placement.

Flow:
  IATIS report (EXECUTE) → TradeExecutor → OANDA market order

Safety checks before placing any order:
  1. Duplicate check: no open position for this symbol already
  2. Score threshold: confluence score >= min_score_to_execute
  3. News check: blackout_active = False
  4. Max open trades: configurable limit (default: 5)
  5. Paper mode: can run without placing real orders (dry_run=True)

Usage:
  executor = TradeExecutor(dry_run=True)   # paper mode, logs but no real orders
  result = executor.execute_from_report(iatis_report)

For paper trading validation:
  Run for 30+ days with dry_run=True
  Compare reported signals vs market outcome
  When satisfied → set dry_run=False + OANDA_ENVIRONMENT=practice
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from utils.logger import get_logger

logger = get_logger(__name__)


def _record_ctrader_attempt(
    *, report: dict, symbol: str, direction: str, volume: float,
    entry: float, sl: float, tp: float, status: str,
    normalized_entry: float | None = None, position_id: str | None = None,
    error: str | None = None,
) -> None:
    """Best-effort persistence into storage.execution_attempts (2026-08-17
    live-execution fix). `report.get("signal_id")` is currently always
    None — no pipeline stage stamps a signal_id onto the report dict today
    (storage.outcome_tracker.log_signal() only generates one internally,
    at ITS OWN log time, never written back onto `report`) — kept here
    so this call auto-wires for free if a future pipeline stage ever adds
    one, rather than being a second thing to remember to update. Never
    raises: storage.execution_attempts.record_execution_attempt() already
    swallows its own D1 errors, and the import itself is wrapped here too
    so a missing/misconfigured storage layer (e.g. in a test environment)
    can never turn a real execution outcome into an unhandled exception."""
    try:
        from storage.execution_attempts import record_execution_attempt
        record_execution_attempt(
            symbol=symbol, broker="ctrader", direction=direction,
            status=status,
            signal_id=report.get("signal_id"),
            requested_volume=float(volume),
            requested_entry=float(entry), requested_sl=float(sl), requested_tp=float(tp),
            normalized_entry=normalized_entry,
            broker_error_message=error,
            position_id=position_id,
        )
    except Exception as exc:
        logger.debug(f"execution_attempts recording skipped for {symbol}: {exc}")


@dataclass
class ExecutionResult:
    executed: bool
    symbol: str
    direction: str = ""
    units: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trade_id: str = ""
    skip_reason: str = ""
    dry_run: bool = False
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "executed": self.executed,
            "symbol": self.symbol,
            "direction": self.direction,
            "units": self.units,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "trade_id": self.trade_id,
            "skip_reason": self.skip_reason,
            "dry_run": self.dry_run,
            "timestamp": self.timestamp,
        }


class TradeExecutor:
    """Executes IATIS signals via broker API (OANDA or cTrader/IC Markets).

    Args:
        dry_run: if True, logs orders but doesn't place them (default: True)
        broker: "ctrader" (IC Markets) or "oanda"
        max_open_trades: maximum concurrent positions allowed
        min_score: minimum confluence score to execute (extra safety)
    """

    def __init__(
        self,
        dry_run: bool = True,
        broker: str = "ctrader",
        max_open_trades: int = 5,
        min_score: float = 60.0,
        allow_live_trading: bool = False,
        dukascopy_jforex_fixed_quantity: float = 0.0,
    ):
        self.dry_run = dry_run
        self.broker = broker
        self.max_open_trades = max_open_trades
        self.min_score = min_score
        # Money-safety gate: real orders are only ever placed on a DEMO
        # account unless this is explicitly True. Layer-2 forward evidence
        # runs on demo, so this stays False — enabling ctrader execution
        # can never touch a live account by accident.
        self.allow_live_trading = allow_live_trading
        # dukas-api has no documented account-balance endpoint, so JForex
        # orders can't use the risk_pct-based sizing cTrader/OANDA use
        # (execution/dukascopy_jforex_client.py's module docstring has the
        # full reasoning) — a fixed quantity, set explicitly per config.yaml
        # execution.dukascopy_jforex_fixed_quantity. 0.0 (unset) means
        # "refuse to trade" (see the dukascopy_jforex branch below), never
        # a silently-guessed default size.
        self.dukascopy_jforex_fixed_quantity = dukascopy_jforex_fixed_quantity
        self._client = None

        mode = "DRY RUN" if dry_run else f"LIVE ({broker.upper()})"
        logger.info(
            f"TradeExecutor: mode={mode}, max_trades={max_open_trades}, "
            f"min_score={min_score}, allow_live={allow_live_trading}"
        )

    def _get_client(self):
        """Lazy-load broker client.

        ctrader: reuses the process-wide shared session from
        core.data_providers.get_shared_ctrader_client() instead of opening
        a second, independent connection. cTrader's Open API allows only
        one authenticated session per account+app — two live clients in
        one process (this executor's own + the data-fetch singleton)
        fight over that slot in a permanent ALREADY_LOGGED_IN reconnect
        storm (diagnosed 2026-07-14 from live scheduler logs).
        """
        if self._client is None:
            if self.broker == "ctrader":
                from core.data_providers import get_shared_ctrader_client
                self._client = get_shared_ctrader_client()
            elif self.broker == "dukascopy_jforex":
                from execution.dukascopy_jforex_client import DukascopyJForexClient
                self._client = DukascopyJForexClient()
            else:
                from execution.oanda_client import OandaClient
                self._client = OandaClient()
        return self._client

    def execute_from_report(self, report: dict) -> ExecutionResult:
        """Execute a trade from an IATIS pipeline report.

        Args:
            report: dict returned by run_pipeline()

        Returns:
            ExecutionResult with execution details
        """
        symbol = report.get("symbol", "")
        verdict = report.get("final_verdict", "NO_TRADE")

        # Only execute on EXECUTE verdict
        if verdict != "EXECUTE":
            return ExecutionResult(
                executed=False,
                symbol=symbol,
                skip_reason=f"Verdict is {verdict}, not EXECUTE",
                dry_run=self.dry_run,
            )

        # Extract signal details
        entry = report.get("entry_price")
        sl = report.get("stop_loss")
        tp = report.get("take_profit")
        score = report.get("confluence", {}).get("score", 0)
        news = report.get("news", {})
        vote = report.get("confluence", {}).get("vote", {})
        bias = vote.get("winning_bias", "NEUTRAL")

        # Safety: entry/sl/tp must be present
        if not all([entry, sl, tp]):
            return ExecutionResult(
                executed=False,
                symbol=symbol,
                skip_reason="Missing entry/SL/TP prices",
                dry_run=self.dry_run,
            )

        # Safety: extra score threshold
        if score < self.min_score:
            return ExecutionResult(
                executed=False,
                symbol=symbol,
                skip_reason=f"Score {score} below executor threshold {self.min_score}",
                dry_run=self.dry_run,
            )

        # Safety: news blackout
        if news.get("blackout_active"):
            return ExecutionResult(
                executed=False,
                symbol=symbol,
                skip_reason=f"News blackout: {news.get('blackout_reason', '')}",
                dry_run=self.dry_run,
            )

        direction = "BUY" if bias == "BULLISH" else "SELL"

        # Dry run — log and return without placing order
        if self.dry_run:
            logger.info(
                f"[DRY RUN] Would place: {direction} {symbol} "
                f"entry={entry:.5f} sl={sl:.5f} tp={tp:.5f} score={score}"
            )
            return ExecutionResult(
                executed=True,
                symbol=symbol,
                direction=direction,
                entry_price=float(entry),
                stop_loss=float(sl),
                take_profit=float(tp),
                trade_id="DRY_RUN",
                dry_run=True,
            )

        # Live/practice execution
        try:
            client = self._get_client()

            if self.broker == "ctrader":
                # cTrader execution path
                from execution.ctrader_client import CTraderOrder

                # HARD money-safety gate: refuse to place a real order on a
                # LIVE cTrader account unless explicitly allowed. Demo runs
                # (forward paper-trade evidence) pass freely.
                env = getattr(client, "environment", "demo")
                if env != "demo" and not self.allow_live_trading:
                    logger.error(
                        f"REFUSING live order for {symbol}: CTRADER_ENVIRONMENT="
                        f"{env} but allow_live_trading is False. Set "
                        f"execution.allow_live_trading only when you truly mean it."
                    )
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Live trading blocked (env={env}, allow_live_trading=False)",
                        dry_run=False,
                    )

                # Duplicate-position guard (2026-08-15 red-team audit,
                # EXEC-DUP): the OANDA and Dukascopy JForex branches both
                # already had this check; the cTrader branch was missing
                # it. client._positions is rebuilt from ProtoOAReconcileRes
                # on every (re)connect and kept current by execution
                # events while connected — so even when a PREVIOUS order's
                # confirmation event arrived late (past place_market_
                # order()'s own ORDER_TIMEOUT, making that call return
                # success=False even though the broker had actually filled
                # it), that late event still updates _positions, and this
                # check catches it before a second real order is placed on
                # the same symbol. This is independent of, and a backstop
                # for, storage.outcome_tracker-derived duplicate checks
                # upstream (risk/risk_engine.py's symbol_already_open),
                # which a timed-out order never populates.
                if client.has_open_position(symbol):
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Already have open position for {symbol}",
                        dry_run=False,
                    )

                # Get account info for position sizing
                account = client.get_account_info()
                if not account:
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason="Could not fetch cTrader account info",
                        dry_run=False,
                    )

                sl_distance = abs(float(entry) - float(sl))
                risk_per_trade = report.get("risk", {}).get("recommended_risk_pct", 0.01)
                volume = client.calculate_volume(
                    symbol=symbol,
                    balance=account.balance,
                    risk_pct=risk_per_trade,
                    sl_distance_price=sl_distance,
                )

                if volume <= 0:
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Invalid volume calculated ({volume})",
                        dry_run=False,
                    )

                ct_order = CTraderOrder(
                    symbol=symbol,
                    direction=direction,
                    volume=volume,
                    stop_loss=float(sl),
                    take_profit=float(tp),
                    comment=f"IATIS_{symbol}",
                )

                result = client.place_market_order(ct_order)

                if result.success:
                    _record_ctrader_attempt(
                        report=report, symbol=symbol, direction=direction,
                        volume=volume, entry=entry, sl=sl, tp=tp,
                        status="ACCEPTED",
                        normalized_entry=result.entry_price,
                        position_id=result.position_id,
                    )
                    logger.info(
                        f"✅ cTrader ORDER: {direction} {symbol} "
                        f"vol={volume} pos_id={result.position_id}"
                    )
                    return ExecutionResult(
                        executed=True, symbol=symbol,
                        direction=direction, units=volume,
                        entry_price=result.entry_price,
                        stop_loss=float(sl), take_profit=float(tp),
                        trade_id=result.position_id, dry_run=False,
                    )
                else:
                    # First-class execution outcome (2026-08-17 live-execution
                    # fix): a declined order is not just a log line — it is
                    # ALWAYS persisted to storage.execution_attempts (never
                    # storage.outcome_tracker's `outcomes` table — that would
                    # reopen the orphaned-position bug the reconciliation fix
                    # closed; see execution_attempts.py's own module
                    # docstring). A timed-out confirmation gets ONE immediate
                    # broker-truth check — has_open_position() reads
                    # client._positions, which ProtoOAExecutionEvent already
                    # updates asynchronously even if THIS call's own response
                    # timed out — so a late fill is recorded accurately
                    # instead of being reported as a plain failure. This is
                    # observation only: it never retries the order (the
                    # existing has_open_position() pre-check above already
                    # blocks a second real attempt on the NEXT call, whether
                    # this one timed out or was cleanly rejected).
                    is_timeout = "timed out" in (result.error or "").lower()
                    if is_timeout:
                        status = "TIMEOUT_UNKNOWN"
                        try:
                            broker_confirms_open = client.has_open_position(symbol)
                        except Exception:
                            broker_confirms_open = False
                        skip_reason = (
                            f"cTrader order confirmation timed out; broker "
                            f"{'now reports a position IS open — do not retry' if broker_confirms_open else 'reports no open position (yet)'}"
                        )
                    else:
                        status = "REJECTED"
                        skip_reason = f"cTrader error: {result.error}"
                    _record_ctrader_attempt(
                        report=report, symbol=symbol, direction=direction,
                        volume=volume, entry=entry, sl=sl, tp=tp,
                        status=status, error=result.error,
                    )
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=skip_reason,
                        dry_run=False,
                    )

            elif self.broker == "dukascopy_jforex":
                # Dukascopy JForex execution path, via the local dukas-api
                # bridge — see execution/dukascopy_jforex_client.py's module
                # docstring for the fixed-quantity/pip-size/duplicate-check
                # design decisions (dukas-api documents no account-balance
                # or list-open-positions endpoint).
                from execution.dukascopy_jforex_client import DukascopyJForexOrder

                # HARD money-safety gate: identical structure to the cTrader
                # branch above — self-reported environment, refuse a
                # non-demo order unless explicitly allowed.
                env = getattr(client, "environment", "demo")
                if env != "demo" and not self.allow_live_trading:
                    logger.error(
                        f"REFUSING live order for {symbol}: DUKASCOPY_JFOREX_ENVIRONMENT="
                        f"{env} but allow_live_trading is False. Set "
                        f"execution.allow_live_trading only when you truly mean it."
                    )
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Live trading blocked (env={env}, allow_live_trading=False)",
                        dry_run=False,
                    )

                if self.dukascopy_jforex_fixed_quantity <= 0:
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason="dukascopy_jforex_fixed_quantity not configured (must be > 0)",
                        dry_run=False,
                    )

                if client.has_open_position(symbol):
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Already have open position for {symbol}",
                        dry_run=False,
                    )

                jf_order = DukascopyJForexOrder(
                    symbol=symbol,
                    direction=direction,
                    quantity=self.dukascopy_jforex_fixed_quantity,
                    stop_loss=float(sl),
                    take_profit=float(tp),
                    client_order_id=f"IATIS_{symbol}",
                )
                result = client.place_market_order(jf_order)

                if result.success:
                    logger.info(
                        f"✅ Dukascopy JForex ORDER: {direction} {symbol} "
                        f"qty={self.dukascopy_jforex_fixed_quantity} "
                        f"order_id={result.dukas_order_id}"
                    )
                    return ExecutionResult(
                        executed=True, symbol=symbol,
                        # ExecutionResult.units is int-typed; a fractional
                        # quantity (e.g. 0.01 lots) rounds for display only —
                        # the log line above and the order actually sent to
                        # the bridge both used the precise float value.
                        direction=direction, units=round(self.dukascopy_jforex_fixed_quantity),
                        entry_price=result.fill_price,
                        stop_loss=float(sl), take_profit=float(tp),
                        trade_id=result.dukas_order_id, dry_run=False,
                    )
                else:
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Dukascopy JForex error: {result.error}",
                        dry_run=False,
                    )

            else:
                # OANDA execution path (original)
                if client.has_open_position(symbol):
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Already have open position for {symbol}",
                        dry_run=False,
                    )

                account = client.get_account_summary()
                if account.open_trade_count >= self.max_open_trades:
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Max open trades ({account.open_trade_count}/{self.max_open_trades})",
                        dry_run=False,
                    )

                sl_distance = abs(float(entry) - float(sl))
                risk_per_trade = report.get("risk", {}).get("recommended_risk_pct", 0.01)
                risk_usd = account.balance * risk_per_trade
                units = client.calculate_units(symbol, risk_usd, sl_distance)

                if units <= 0:
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"Invalid units ({units})", dry_run=False,
                    )

                from execution.oanda_client import TradeOrder
                order = TradeOrder(
                    symbol=symbol, direction=direction, units=units,
                    stop_loss=float(sl), take_profit=float(tp),
                    client_id=f"IATIS_{symbol}_{datetime.now(timezone.utc).strftime('%H%M')}",
                )
                result = client.place_market_order(order)

                if result.success:
                    return ExecutionResult(
                        executed=True, symbol=symbol,
                        direction=direction, units=units,
                        entry_price=result.entry_price,
                        stop_loss=float(sl), take_profit=float(tp),
                        trade_id=result.trade_id, dry_run=False,
                    )
                else:
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"OANDA error: {result.error}", dry_run=False,
                    )

        except Exception as exc:
            logger.error(f"Execution failed for {symbol}: {exc}", exc_info=True)
            return ExecutionResult(
                executed=False,
                symbol=symbol,
                skip_reason=f"Exception: {type(exc).__name__}: {str(exc)[:100]}",
                dry_run=self.dry_run,
            )
