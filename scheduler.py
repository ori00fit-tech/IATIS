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
from storage.outcome_tracker import auto_close_outcomes, log_signal as log_outcome_signal
from execution.trade_executor import TradeExecutor
from main import run_pipeline
from risk.correlation_engine import (
    check_correlation, portfolio_exposure_summary, MAX_PER_GROUP, CorrelationCheckResult,
)
from utils.config_validator import validate_config
from utils.helpers import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

_running = threading.Event()
_running.set()
_lock = threading.Lock()


_error_cooldown: dict[str, float] = {}
_COOLDOWN_SECONDS = 1800


# Written at the end of every completed run; read by the API server's
# scheduler-status panel (execution/api_server.py::_scheduler_status).
RUN_MARKER_PATH = "storage/last_run.json"


def _write_run_marker(ok: int, failed: int, execute_count: int) -> None:
    with open(RUN_MARKER_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "symbols_ok": ok,
            "symbols_failed": failed,
            "execute_count": execute_count,
        }, f)


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

        # Correlation filter (A1): seeded with symbols already open from a
        # PREVIOUS run, not just this run's new executes — otherwise the
        # filter is blind to any position older than one scheduler tick,
        # since check_correlation() only ever sees whatever list it's
        # handed (2026-07-25 audit finding: a correlated position open for
        # hours/days provided zero protection against a new same-group
        # signal on a later tick).
        #
        # 2026-08-15 red-team audit (RE-F3): a storage failure here used to
        # be swallowed at debug level and the run proceeded with an EMPTY
        # seed list — silently disabling correlation protection for every
        # already-open position for the whole tick (fail-open). Per
        # CLAUDE.md's own rule ("UNKNOWN/INVALID/INCOMPLETE -> NO-TRADE
        # unless explicit and justified exception"), an unreadable open-
        # positions list means correlation safety cannot be verified for
        # this tick, so every new EXECUTE this tick must be blocked rather
        # than silently risking an undetected correlated pile-up.
        execute_signals: list[str] = []
        correlation_seed_failed = False
        try:
            from storage.outcome_tracker import get_open_signals
            execute_signals.extend(str(r.get("symbol") or "") for r in get_open_signals())
        except Exception as exc:
            correlation_seed_failed = True
            logger.warning(
                f"Could not seed correlation filter with open positions — "
                f"blocking new EXECUTEs this tick (fail-closed, RE-F3): {exc}"
            )
        max_per_group = config.get("portfolio", {}).get("max_per_group", MAX_PER_GROUP)
        correlation_filter_enabled = config.get("features", {}).get("correlation_filter", True)

        # cTrader OAuth: proactive token refresh, well ahead of real expiry —
        # cheap no-op (one env read + one float compare) unless within
        # margin_seconds of expiry. This is what keeps a long-lived
        # connected session from ever reaching a live CH_ACCESS_TOKEN_INVALID
        # rejection in practice; execution/ctrader_client.py's connect()
        # also self-heals reactively on that exact rejection as a backstop.
        try:
            from integrations.ctrader.token_manager import get_valid_access_token
            get_valid_access_token(margin_seconds=86400)
        except Exception as exc:
            logger.debug(f"cTrader proactive token refresh check failed (non-fatal): {exc}")

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
            if correlation_seed_failed and correlation_filter_enabled:
                corr_check = CorrelationCheckResult(
                    allowed=False, symbol=internal,
                    message="Correlation filter degraded — cannot verify already-open "
                            "positions this tick, blocking (fail-closed, RE-F3)",
                )
            else:
                corr_check = (
                    check_correlation(internal, execute_signals, max_per_group=max_per_group)
                    if correlation_filter_enabled
                    else CorrelationCheckResult(allowed=True, symbol=internal, message="Correlation filter disabled by config")
                )
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

            # Symbol Health Index — skip paused symbols.
            # 2026-08-15 red-team audit (RE-F1): a failure evaluating this
            # gate used to be swallowed at debug level and the pipeline ran
            # anyway — i.e. an error in the ONE check meant to pause a
            # misbehaving symbol silently defaulted to ALLOWING the trade.
            # Per CLAUDE.md's fail-closed rule, an unreadable health status
            # must block the trade, not skip the check.
            try:
                from storage.symbol_health import get_symbol_health
                shi = get_symbol_health(internal)
            except Exception as exc:
                logger.warning(
                    f"[HEALTH] {internal} health check failed — blocking "
                    f"(fail-closed, RE-F1): {exc}"
                )
                reports.append({
                    "final_verdict": "NO_TRADE",
                    "symbol": internal,
                    "summary": f"NO_TRADE: Symbol health check failed ({type(exc).__name__}) — blocking (fail-closed)",
                    "health_check_failed": True,
                })
                continue
            if shi.status == "PAUSED":
                logger.info(f"[HEALTH] {internal} PAUSED (SHI={shi.shi_score:.0f}): {shi.reason}")
                reports.append({
                    "final_verdict": "NO_TRADE",
                    "symbol": internal,
                    "summary": f"NO_TRADE: Symbol PAUSED (SHI={shi.shi_score:.0f}) — {shi.reason}",
                    "health_paused": True,
                })
                continue

            try:
                report = run_pipeline(sym_config)
                reports.append(report)
                if report.get("final_verdict") == "EXECUTE":
                    # Kill switch (storage/kill_switch.py): a manually-
                    # activated operational halt on NEW order submission
                    # (RTS 6 Art.12 / PRA SS5/18 "kill functionality").
                    # Checked fresh every EXECUTE, fail-closed on any read
                    # error — an unreadable state must block, never allow.
                    try:
                        from storage.kill_switch import get_state as _kill_switch_state
                        ks_state = _kill_switch_state()
                    except Exception as exc:
                        logger.error(f"[KILL-SWITCH] state check failed — blocking (fail-closed): {exc}")
                        ks_state = {"active": True, "reason": f"kill switch check failed: {exc}"}
                    if ks_state.get("active"):
                        logger.warning(
                            f"[KILL-SWITCH] EXECUTE for {internal} suppressed — "
                            f"kill switch ACTIVE ({ks_state.get('reason')}). "
                            f"Manual reactivation required."
                        )
                        report["kill_switch_blocked"] = True
                        report["summary"] = f"NO_TRADE (blocked): kill switch active — {ks_state.get('reason')}"
                        continue
                    execute_signals.append(internal)
                    # B1: Execute the trade. Broker execution runs when the
                    # matching broker is enabled; otherwise dry_run simulates.
                    #   ctrader_enabled + dry_run:false → REAL orders on the
                    #     cTrader account (demo unless allow_live_trading).
                    #   oanda_enabled  + dry_run:false → OANDA.
                    #   dry_run:true (default) → simulate, place nothing.
                    exec_cfg = config.get("execution", {})
                    oanda_enabled = exec_cfg.get("oanda_enabled", False)
                    ctrader_enabled = exec_cfg.get("ctrader_enabled", False)
                    dukascopy_jforex_enabled = exec_cfg.get("dukascopy_jforex_enabled", False)
                    dry_run = exec_cfg.get("dry_run", True)
                    broker = exec_cfg.get("broker", "ctrader")
                    broker_live = (ctrader_enabled and broker == "ctrader") or \
                                  (oanda_enabled and broker == "oanda") or \
                                  (dukascopy_jforex_enabled and broker == "dukascopy_jforex")
                    if dry_run or broker_live:
                        try:
                            from risk.pretrade_limits import load_pretrade_limits

                            executor = TradeExecutor(
                                dry_run=dry_run,
                                broker=broker,
                                max_open_trades=exec_cfg.get("max_open_trades", 5),
                                min_score=exec_cfg.get("min_score_to_execute", 60.0),
                                allow_live_trading=exec_cfg.get("allow_live_trading", False),
                                dukascopy_jforex_fixed_quantity=exec_cfg.get("dukascopy_jforex_fixed_quantity", 0.0),
                                # Loaded fresh from THIS tick's already-loaded
                                # config — avoids a second load_config() read
                                # per order (config/risk.yaml's own "loaded
                                # fresh on every order" contract is still met:
                                # config itself is reloaded every run_once()
                                # tick, scheduler.py:591).
                                pretrade_limits=load_pretrade_limits(config),
                            )
                            exec_result = executor.execute_from_report(report)
                            if exec_result.executed:
                                # Outcome tracker only ever records a signal
                                # that was actually attempted — a real fill
                                # or an intentional dry-run simulation
                                # (moved here from main.py, 2026-07-25: see
                                # main.py's comment at the old call site for
                                # why logging on the bare EXECUTE verdict
                                # created permanently orphaned "open" rows).
                                try:
                                    log_outcome_signal(report)
                                except Exception as exc:
                                    logger.warning(f"Outcome tracker log failed (non-fatal): {exc}")
                                if not exec_result.dry_run:
                                    logger.info(
                                        f"✅ TRADE EXECUTED: {exec_result.direction} "
                                        f"{exec_result.symbol} trade_id={exec_result.trade_id}"
                                    )
                                    # TCA: record intended-vs-fill for every real
                                    # broker fill (storage/execution_quality.py).
                                    # record_or_queue_fill() records immediately
                                    # when the fill price is already known
                                    # (OANDA/Dukascopy JForex, or a cTrader
                                    # response that happened to carry one), or
                                    # durably QUEUES it when it isn't yet (the
                                    # common cTrader ORDER_ACCEPTED shape,
                                    # 2026-08-17 fix) — resolved by the pending-
                                    # fill pass below on a LATER tick, once
                                    # execution/ctrader_client.py's async event
                                    # stream reports the real broker price.
                                    # Never raises; dry-run is excluded inside.
                                    from storage.execution_quality import record_or_queue_fill
                                    record_or_queue_fill(report, exec_result, broker=broker)
                            else:
                                logger.info(
                                    f"Signal for {internal} not logged to outcome tracker: "
                                    f"execution declined ({exec_result.skip_reason})"
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

        # Data-confidence rotation (core/data_confidence.py, gap analysis
        # S1): ONE symbol per run, cross-checked between its top two
        # providers. Monitoring only — never gates a decision. Off by
        # default: it costs ~1-2 extra provider calls per run.
        if config.get("features", {}).get("data_confidence_check", False):
            try:
                from core.data_confidence import check_and_record, pick_symbol
                dc_sym = pick_symbol([s.replace("/", "") for s in active_symbols])
                if dc_sym:
                    dc = check_and_record(dc_sym, config)
                    if dc and str(dc.get("verdict", "")).startswith("MATERIAL"):
                        _send_error_once(
                            key=f"data_confidence_{dc_sym}",
                            message=(
                                f"⚠️ <b>Data confidence: MATERIAL disagreement</b> — {dc_sym}\n"
                                f"{dc['provider_a']} vs {dc['provider_b']}: "
                                f"mean {dc['mean_diff_pct']}%, max {dc['max_diff_pct']}% "
                                f"({dc['bars_common']} bars). At least one provider is "
                                f"wrong — investigate before trusting either."
                            ),
                        )
            except Exception as exc:
                logger.debug(f"data-confidence rotation skipped: {exc}")

        # Log portfolio exposure summary
        if execute_signals:
            exposure = portfolio_exposure_summary(execute_signals)
            if exposure:
                logger.info(f"Portfolio exposure: {exposure}")

        # Budget warning
        warning = _credits_warning(config)
        if warning:
            send_raw(warning)

        # Log run summary
        execute_count = sum(1 for r in reports if r.get("final_verdict") == "EXECUTE")
        logger.info(
            f"=== Run complete: {len(reports)} OK, {len(failed_symbols)} failed, "
            f"{execute_count} EXECUTE signals ==="
        )

        # Run marker for /health/full's scheduler panel. The old detection
        # text-mined "Run complete" out of a log file that doesn't exist
        # when logging.file is unset (the default) and out of journalctl the
        # API service user can't always read — so Mission Control showed
        # "no run seen" against a green, actively-running service. A tiny
        # JSON file is readable by the API process unconditionally.
        try:
            _write_run_marker(len(reports), len(failed_symbols), execute_count)
        except Exception as exc:
            logger.debug(f"run marker write skipped: {exc}")

        # Auto-close open outcomes based on current prices.
        # ``current_price`` is populated on EVERY report (EXECUTE and
        # NO_TRADE) by main.py, so open signals can close on any run.
        # NOTE: do not re-import auto_close_outcomes inside this function —
        # a local import shadows the module-level name for the WHOLE
        # function scope and previously caused an UnboundLocalError.
        try:
            current_prices: dict[str, float] = {}
            bar_ranges: dict[str, tuple[float, float]] = {}
            for r in reports:
                sym = r.get("symbol") or r.get("data", {}).get("symbol")
                price = r.get("current_price") or r.get("entry_price")
                if sym and price:
                    current_prices[str(sym)] = float(price)
                    # Decision-bar range → intrabar TP/SL detection
                    # (open-outcome hygiene, audit priority 4).
                    if r.get("bar_high") is not None and r.get("bar_low") is not None:
                        bar_ranges[str(sym)] = (float(r["bar_high"]), float(r["bar_low"]))
            if current_prices:
                max_open_h = config.get("execution", {}).get("max_open_trade_hours", 0)
                closed = auto_close_outcomes(
                    current_prices,
                    bar_ranges=bar_ranges,
                    max_open_hours=max_open_h,
                )
                # Shadow counterfactuals resolve with the same mechanics —
                # silent (measurements, not alerts).
                try:
                    from storage.shadow_book import auto_close_shadows
                    auto_close_shadows(current_prices, bar_ranges=bar_ranges,
                                       max_open_hours=max_open_h)
                except Exception as exc:
                    logger.warning(f"Shadow auto-close failed (non-fatal): {exc}")
                for c in closed:
                    icon = ("✅" if c["outcome"] == "win"
                            else "➖" if c["outcome"] == "breakeven" else "❌")
                    send_raw(
                        f"{icon} <b>Auto-closed:</b> {c['symbol']} "
                        f"→ {c['outcome'].upper()} at {c['exit_price']}"
                    )
                if closed:
                    logger.info(f"Auto-closed {len(closed)} outcome(s) this run")
        except Exception as exc:
            logger.warning(f"Auto-close outcomes failed (non-fatal): {exc}")

        # Broker-vs-internal position reconciliation (gap analysis M3):
        # runs every tick, acts only when the cTrader path is live
        # (reconcile() self-gates on ctrader_enabled + dry_run). Alert
        # with the standard per-key cooldown on any mismatch.
        rec = None
        repair = None
        if config.get("features", {}).get("broker_reconciliation", True):
            try:
                from execution.reconciliation import format_alert, reconcile, repair_mismatches, store_result
                rec = reconcile(config)
                store_result(rec)  # the dashboard reads STORED results only
                if rec.get("status") == "mismatch":
                    _send_error_once(key="reconciliation", message=format_alert(rec))
                    # Auto-repair (2026-07-30): internal_only rows are pure
                    # bookkeeping drift (broker already closed the position;
                    # outcome_tracker never observed it) that otherwise
                    # inflates open-risk/exposure forever — close them here
                    # rather than waiting on a manual dashboard action.
                    # Never fabricates win/loss (reconcile_close_signal only).
                    if config.get("features", {}).get("reconciliation_auto_repair", True):
                        try:
                            repair = repair_mismatches(rec)
                            if repair.get("repaired"):
                                logger.warning(
                                    f"Reconciliation auto-repair closed "
                                    f"{len(repair['repaired'])} stale signal(s): "
                                    f"{repair['repaired']}"
                                )
                        except Exception as exc:
                            logger.warning(f"Reconciliation auto-repair failed (non-fatal): {exc}")
            except Exception as exc:
                logger.warning(f"Reconciliation failed (non-fatal): {exc}")

        # Unified Post-Trade Control / Incident Register (execution/
        # post_trade_monitor.py): turns this tick's ALREADY-COMPUTED
        # reconciliation/execution_attempts/execution_quality/kill_switch/
        # forward_review evidence into durable incidents. Runs every tick
        # regardless of feature flags above (rec/repair are None when
        # reconciliation didn't run this tick — scan_reconciliation()
        # handles that by falling back to the last stored result). Its own
        # internal per-scan try/except already isolates one subsystem's
        # failure from the others; this outer try/except additionally
        # ensures a monitoring-layer failure can never affect trading.
        if config.get("features", {}).get("post_trade_monitoring", True):
            try:
                from execution.post_trade_monitor import run_all_scans
                run_all_scans(reconciliation_report=rec, reconciliation_repair=repair)
            except Exception as exc:
                logger.warning(f"Post-trade incident monitoring failed (non-fatal): {exc}")

        # TCA async-fill resolution pass (2026-08-17 fix): completes any
        # fill queued as PENDING by record_or_queue_fill() earlier this
        # tick (or an earlier one) once execution/ctrader_client.py's
        # async event stream — or the next ProtoOAReconcileRes — has
        # reported the real broker price. Gated the same way
        # reconciliation is (cTrader path actually live): a paper/dry-run
        # deployment has no broker session to poll and nothing would ever
        # resolve. take_fill_update() pops each update so a second poll
        # before the next one arrives is a no-op — combined with
        # resolve_pending_fill()'s own PENDING-status guard on the D1
        # side, a fill can only ever be recorded once.
        exec_cfg = config.get("execution", {})
        if exec_cfg.get("ctrader_enabled", False) and not exec_cfg.get("dry_run", True):
            try:
                from storage.execution_quality import (
                    pending_fill_position_ids,
                    resolve_pending_fill,
                    sweep_stale_pending_fills,
                )
                from core.data_providers import get_shared_ctrader_client
                client = get_shared_ctrader_client()
                resolved = 0
                for position_id in pending_fill_position_ids():
                    update = client.take_fill_update(position_id)
                    if update and update.get("price"):
                        if resolve_pending_fill(position_id, float(update["price"])):
                            resolved += 1
                if resolved:
                    logger.info(f"TCA: resolved {resolved} pending fill(s) this tick")
                sweep_stale_pending_fills()
            except Exception as exc:
                logger.warning(f"TCA pending-fill resolution failed (non-fatal): {exc}")

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

    # Bring the D1 schema up to the current version before the first run
    # (storage/migrations.py). Non-fatal: a failure logs loudly and the
    # pipeline keeps running on the old schema.
    from storage.migrations import apply_migrations_safe
    applied = apply_migrations_safe()
    if applied:
        logger.info(f"Schema migrations applied at boot: {applied}")

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
    for warning in validate_config(config):
        logger.warning(f"config consistency: {warning}")
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

    # Hard gate: the unattended scheduler must never run live on fabricated
    # bars. Reached only if source is still "synthetic" after the auto-switch
    # above — i.e. no TWELVE_DATA_API_KEY and no --source override.
    if config.get("system", {}).get("mode") == "live" and config["data"]["source"] == "synthetic":
        sys.exit(
            "ERROR: system.mode=live but data.source=synthetic (no real data source "
            "available). Set TWELVE_DATA_API_KEY in .env, pass --source ctrader/twelve_data, "
            "or set system.mode to something other than 'live' in config.yaml."
        )

    # 2026-08-15 red-team audit (DB-3): apply_migrations_safe() was
    # previously only reached via run_loop() below — `--once` mode
    # (used by external cron per this file's own module docstring)
    # skipped it entirely, so a `--once`-only deployment could run
    # indefinitely on a stale D1 schema. Calling it here too (in
    # addition to run_loop()'s own call, harmless since it's idempotent
    # — it checks the current schema version before doing any work)
    # covers both paths from one call site.
    from storage.migrations import apply_migrations_safe
    applied = apply_migrations_safe()
    if applied:
        logger.info(f"Schema migrations applied at boot: {applied}")

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
