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
                    return ExecutionResult(
                        executed=False, symbol=symbol,
                        skip_reason=f"cTrader error: {result.error}",
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
