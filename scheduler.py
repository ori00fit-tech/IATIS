"""
scheduler.py
--------------
Runs the IATIS pipeline on a schedule without any external dependency.
Uses Python's built-in sched module — no celery, no cron, no Redis.

Schedule logic:
  - Runs once immediately on startup
  - Then repeats every `interval_minutes` (default: 60, i.e. once per H1 candle)
  - Skips a run if the previous one is still executing (overlap protection)
  - Sends a startup message to Telegram so you know it's alive
  - Sends a daily budget warning if Twelve Data credits fall below threshold

Usage:
  python scheduler.py                    # runs every 60 minutes
  python scheduler.py --interval 15      # runs every 15 minutes (M15)
  python scheduler.py --once             # runs once and exits (useful for cron)
  python scheduler.py --symbols EUR/USD XAU/USD   # override symbols

Budget awareness (Free plan: 800 req/day):
  With 4 timeframes per symbol:
    1 symbol  × 4 TFs = 4  req/run → 200 full runs/day (safe for hourly)
    2 symbols × 4 TFs = 8  req/run → 100 full runs/day (safe for hourly)
    3 symbols × 4 TFs = 12 req/run →  66 full runs/day (safe for hourly)
  Cache kicks in within the same candle period, so consecutive runs
  in the same hour consume far fewer credits.
"""

from __future__ import annotations

import argparse
import json
import os
import sched
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from execution.telegram_bot import send_raw, send_signal
from storage.outcome_tracker import auto_close_outcomes
from execution.trade_executor import TradeExecutor
from main import run_pipeline
from risk.correlation_engine import check_correlation, portfolio_exposure_summary
from utils.helpers import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

_running = threading.Event()
_running.set()
_lock = threading.Lock()


_error_cooldown: dict[str, float] = {}
_COOLDOWN_SECONDS = 1800


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _send_error_once(key: str, message: str) -> None:
    """Send error alert at most once per COOLDOWN_SECONDS per key."""
    now = time.time()
    if key in _error_cooldown and now - _error_cooldown[key] < _COOLDOWN_SECONDS:
        logger.debug(f"Error alert for '{key}' suppressed (cooldown active)")
        return
    _error_cooldown[key] = now
    send_raw(message)


def _credits_warning(config: dict) -> str | None:
    """Return a warning string if Twelve Data credits are running low."""
    if config.get("data", {}).get("source") != "twelve_data":
        return None
    try:
        from core.twelve_data_client import RateLimiter
        remaining = RateLimiter().remaining_today()
        if remaining < 50:
            return f"⚠️ Twelve Data credits low: {remaining} remaining today"
    except Exception:
        pass
    return None


def run_once(config: dict, symbols: list[str] | None = None) -> list[dict]:
    """Run the pipeline for all configured symbols. Returns list of reports."""
    if not _lock.acquire(blocking=False):
        logger.warning("Previous run still in progress — skipping this cycle")
        return []

    reports = []
    failed_symbols = []
    try:
        active_symbols = symbols or _get_symbols(config)
        logger.info(
            f"=== Scheduler run @ {_now_utc()} "
            f"| {len(active_symbols)} symbol(s) ==="
        )

        execute_signals: list[str] = []  # track for correlation filter

        for sym in active_symbols:
            sym_config = dict(config)
            sym_config["data"] = dict(config["data"])
            # Use 'internal' name if available (e.g. SPX → SPX500, DJI → US30)
            internal = sym.replace("/", "")
            for sym_entry in config["data"].get("twelve_data_symbols", []):
                if sym_entry["symbol"] == sym and "internal" in sym_entry:
                    internal = sym_entry["internal"]
                    break
            sym_config["data"]["symbol"] = internal
            sym_config["data"]["twelve_data_symbol"] = sym

            # A1: Correlation Filter — skip if correlated symbol already EXECUTE
            corr_check = check_correlation(internal, execute_signals)
            if not corr_check.allowed:
                logger.info(
                    f"[CORRELATION] {internal} blocked: {corr_check.message}"
                )
                reports.append({
                    "final_verdict": "NO_TRADE",
                    "symbol": internal,
                    "summary": f"NO_TRADE: {corr_check.message}",
                    "correlation_blocked": True,
                })
                continue

            # Symbol Health Index — skip paused symbols
            try:
                from storage.symbol_health import get_symbol_health
                shi = get_symbol_health(internal)
                if shi.status == "PAUSED":
                    logger.info(f"[HEALTH] {internal} PAUSED (SHI={shi.shi_score:.0f}): {shi.reason}")
                    reports.append({
                        "final_verdict": "NO_TRADE",
                        "symbol": internal,
                        "summary": f"NO_TRADE: Symbol PAUSED (SHI={shi.shi_score:.0f}) — {shi.reason}",
                        "health_paused": True,
                    })
                    continue
            except Exception as exc:
                logger.debug(f"Symbol health check skipped for {internal}: {exc}")

            try:
                report = run_pipeline(sym_config)
                reports.append(report)
                if report.get("final_verdict") == "EXECUTE":
                    execute_signals.append(internal)
                    # B1: Execute trade via OANDA (dry_run=True until configured)
                    oanda_enabled = config.get("execution", {}).get("oanda_enabled", False)
                    dry_run = config.get("execution", {}).get("dry_run", True)
                    if oanda_enabled or dry_run:
                        try:
                            executor = TradeExecutor(
                                dry_run=dry_run,
                                max_open_trades=config.get("execution", {}).get("max_open_trades", 5),
                                min_score=config.get("execution", {}).get("min_score_to_execute", 60.0),
                            )
                            exec_result = executor.execute_from_report(report)
                            if exec_result.executed and not exec_result.dry_run:
                                logger.info(
                                    f"✅ TRADE EXECUTED: {exec_result.direction} "
                                    f"{exec_result.symbol} trade_id={exec_result.trade_id}"
                                )
                        except Exception as exc:
                            logger.warning(f"Trade execution skipped for {internal}: {exc}")
            except Exception as exc:
                logger.error(f"Pipeline failed for {sym}: {exc}")
                failed_symbols.append(sym)
                _send_error_once(
                    key=sym,
                    message=(
                        f"🚨 <b>IATIS pipeline error</b> — {sym}\n"
                        f"<code>{type(exc).__name__}: {str(exc)[:200]}</code>"
                    )
                )

        # Log portfolio exposure summary
        if execute_signals:
            exposure = portfolio_exposure_summary(execute_signals)
            if exposure:
                logger.info(f"Portfolio exposure: {exposure}")

        # Budget warning
        warning = _credits_warning(config)
        if warning:
            send_raw(warning)

        # Auto-close open outcomes when SL/TP hit
        try:
            closed = auto_close_outcomes()
            if closed:
                for c in closed:
                    icon = "✅" if c["outcome"] == "win" else "❌"
                    send_raw(
                        f"{icon} <b>Auto-closed:</b> {c['symbol']} "
                        f"→ {c['outcome'].upper()} at {c['exit_price']}"
                    )
        except Exception as exc:
            logger.warning(f"Auto-close check failed (non-fatal): {exc}")

        # Log run summary
        execute_count = sum(1 for r in reports if r.get("final_verdict") == "EXECUTE")
        logger.info(
            f"=== Run complete: {len(reports)} OK, {len(failed_symbols)} failed, "
            f"{execute_count} EXECUTE signals ==="
        )

    finally:
        _lock.release()

    return reports


def _get_symbols(config: dict) -> list[str]:
    """Get enabled symbols from config.yaml's twelve_data_symbols list."""
    symbols_cfg = config.get("data", {}).get("twelve_data_symbols", [])
    enabled = [
        s["symbol"] for s in symbols_cfg
        if isinstance(s, dict) and s.get("enabled", True)
    ]
    if enabled:
        return enabled
    # fallback: single symbol from data.twelve_data_symbol or data.symbol
    sym = (
        config["data"].get("twelve_data_symbol")
        or config["data"].get("symbol", "EURUSD")
    )
    return [sym]


def run_loop(config: dict, interval_minutes: int, symbols: list[str] | None) -> None:
    """Main scheduling loop. Runs indefinitely until SIGINT/SIGTERM."""
    interval_sec = interval_minutes * 60

    # startup Telegram ping
    sym_list = symbols or _get_symbols(config)
    source = config.get("data", {}).get("source", "synthetic")
    send_raw(
        f"🚀 <b>IATIS Scheduler started</b>\n"
        f"⏱ Interval: every {interval_minutes} min\n"
        f"📊 Symbols: {', '.join(sym_list)}\n"
        f"💾 Source: {source}\n"
        f"🕐 {_now_utc()}"
    )

    logger.info(
        f"Scheduler started: interval={interval_minutes}min "
        f"symbols={sym_list} source={source}"
    )

    while _running.is_set():
        run_once(config, symbols)
        # wait interval_sec in 1-second chunks so SIGINT is responsive
        for _ in range(interval_sec):
            if not _running.is_set():
                break
            time.sleep(1)

    logger.info("Scheduler stopped cleanly")
    send_raw("🛑 <b>IATIS Scheduler stopped</b>")


def _handle_signal(signum, frame):
    logger.info(f"Signal {signum} received — stopping scheduler after current run")
    _running.clear()


def main() -> None:
    parser = argparse.ArgumentParser(description="IATIS Pipeline Scheduler")
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Minutes between runs (default: 60)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run once and exit (for use with external cron)"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="Override symbols, e.g. --symbols EUR/USD XAU/USD"
    )
    parser.add_argument(
        "--source", default=None,
        help="Override data source (synthetic | csv | twelve_data)"
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config = load_config()
    if args.source:
        config["data"]["source"] = args.source
    elif os.environ.get("TWELVE_DATA_API_KEY") and config["data"].get("source") == "synthetic":
        # Auto-switch to live data if API key is available and config is still on synthetic
        logger.info("TWELVE_DATA_API_KEY found in .env — switching source to twelve_data")
        config["data"]["source"] = "twelve_data"

    if config["data"]["source"] == "twelve_data":
        api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
        if not api_key:
            sys.exit("ERROR: TWELVE_DATA_API_KEY not set in .env")
        config["data"]["twelve_data_api_key"] = api_key

    if args.once:
        reports = run_once(config, args.symbols)
        for r in reports:
            print(json.dumps({
                "symbol": r.get("symbol"),
                "verdict": r.get("final_verdict"),
                "summary": r.get("summary"),
            }, indent=2))
    else:
        run_loop(config, args.interval, args.symbols)


if __name__ == "__main__":
    main()
