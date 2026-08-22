"""
risk/pretrade_limits.py
--------------------------
Pre-trade hard limits — P0 (RTS 6 Art.15 / PRA SS5/18 pre-trade controls).
The CENTRAL, deterministic authority every live order must clear
immediately before broker submission. Thresholds live in config/risk.yaml
under `pretrade_limits:` (merged into config["risk"]["pretrade_limits"] by
utils/helpers.py::load_config() — the same governance mechanism the rest
of risk/*.py already uses).

Position in the pipeline (this is a SAFETY layer, never a strategy input
— it never changes WHICH symbol/direction/RR IATIS decides to trade, only
whether the concrete order that decision produced is allowed to reach the
broker):

    DECISION -> RISK GATE (risk/risk_engine.py, main.py) -> KILL SWITCH
             (storage/kill_switch.py, scheduler.py)
             -> PRE-TRADE HARD LIMITS (this module, execution/
                trade_executor.py)
             -> BROKER VALIDATION (execution/ctrader_client.py's own
                symbol-precision handling) -> cTrader/OANDA/Dukascopy

Every check is deterministic and fail-closed: any required input that is
missing, non-finite, or otherwise unknown makes THAT check FAIL, never
pass-by-default. A limit that is not configured (None in PretradeLimits)
is reported SKIP, not PASS — the difference between "verified fine" and
"never checked" is preserved in every decision record.

Bypass prevention ("impossible to bypass ACCIDENTALLY", per the explicit
design requirement — mirrors research/controlled_hypothesis_run.py's
require_controlled_execution() contextvar-guard pattern from this same
codebase): every broker client's place_market_order() calls
require_pretrade_approval() as its first line. That function raises
PretradeNotApproved unless an approval for the EXACT SAME decision_id is
currently active on this call stack — entered only by TradeExecutor,
only after evaluate_pretrade() has returned approved=True. A caller that
reaches for client.place_market_order() directly, bypassing
TradeExecutor entirely, gets an immediate, loud exception instead of a
silently-placed order. A deliberate, non-automated caller (e.g. a manual
smoke-test script) must use manual_override_context() explicitly, which
logs the bypass at WARNING level with the caller's own stated reason —
never silent, never the automated live path.

Known, stated limitation (Phase 11 residual risk — not overclaimed):
notional-value estimates assume the FX-standard 100,000-unit lot
(`standard_contract_units`) applied uniformly to every asset class. This
is exact for forex, an approximation for metals/crypto/indices (whose
real contract sizes differ). Getting a broker-specific contract-size
table wrong would be worse than an honestly-labeled approximation, so
this is documented rather than silently assumed precise.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


# ─── Bypass-prevention guard ────────────────────────────────────────────────

_approved_decision_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_approved_decision_id", default=None,
)


class PretradeNotApproved(Exception):
    """Raised by a broker client's place_market_order() when called
    without a matching, currently-active pre-trade hard-limits approval."""


def require_pretrade_approval(decision_id: str) -> None:
    """Call as the very first line of every broker client's
    place_market_order(). Raises unless approved_context()/
    manual_override_context() for this exact decision_id is active."""
    active = _approved_decision_id.get()
    if not decision_id or active != decision_id:
        raise PretradeNotApproved(
            f"place_market_order() called for decision_id={decision_id!r} without a "
            f"matching pre-trade hard-limits approval (active context={active!r}). "
            f"Route every live order through TradeExecutor.execute_from_report(), or "
            f"for a deliberate manual/test order use "
            f"risk.pretrade_limits.manual_override_context()."
        )


@contextlib.contextmanager
def approved_context(decision_id: str):
    """Entered only by TradeExecutor, only after evaluate_pretrade()
    returns approved=True for this exact decision_id."""
    token = _approved_decision_id.set(decision_id)
    try:
        yield
    finally:
        _approved_decision_id.reset(token)


@contextlib.contextmanager
def manual_override_context(decision_id: str, reason: str):
    """Explicit, loud escape hatch for legitimate manual/test order
    placement OUTSIDE the automated TradeExecutor path (e.g. a manual
    broker-connectivity smoke test). Bypasses pre-trade hard limits ON
    PURPOSE — every use is logged at WARNING level with the caller-
    supplied reason. Never used by scheduler.py/trade_executor.py's own
    automated path."""
    if not reason or not reason.strip():
        raise ValueError("manual_override_context requires a non-empty reason.")
    logger.warning(
        f"⚠️  PRE-TRADE HARD LIMITS MANUALLY OVERRIDDEN for "
        f"decision_id={decision_id!r}: {reason}"
    )
    token = _approved_decision_id.set(decision_id)
    try:
        yield
    finally:
        _approved_decision_id.reset(token)


# ─── Data model ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SymbolSpec:
    """Broker-native symbol precision/limits. A None field means unknown —
    NEVER guessed or defaulted (Phase 3 item 14: symbol metadata failure
    must fail closed)."""
    price_digits: int | None = None
    quantity_step: float | None = None
    min_quantity: float | None = None
    max_quantity: float | None = None


@dataclass(frozen=True)
class PendingOrder:
    """The concrete order about to be sent to a broker — not the abstract
    trading decision. decision_id is a deterministic hash of the
    underlying decision (symbol/direction/bar_time/entry/sl/tp), so two
    submission attempts of the SAME decision (e.g. a retried call) produce
    the SAME id — the basis for duplicate-order protection — while two
    independent decisions never collide."""
    decision_id: str
    symbol: str
    direction: str            # "BUY" | "SELL"
    quantity: float | None    # broker-native units already computed by the caller (e.g. centi-lots)
    reference_price: float | None   # the price the decision was built from (report["entry_price"])
    stop_loss: float | None
    take_profit: float | None
    bar_time: str | None      # ISO timestamp of the market-data bar the decision used
    strategy: str = "IATIS"
    broker: str = ""


@dataclass(frozen=True)
class PretradeContext:
    """Everything the validator needs to know about CURRENT reality,
    gathered fresh at submission time (not reused from earlier in the
    pipeline) to keep the validate-then-submit gap as small as possible.
    See PHASE 5 / TOCTOU note in the module this feeds (execution/
    trade_executor.py) for what "fresh" actually guarantees here."""
    now_utc: datetime
    account_equity: float | None = None
    open_positions_notional: dict[str, float] | None = None   # symbol -> current notional, None = unknown
    portfolio_notional: float | None = None                    # None = unknown
    symbol_spec: SymbolSpec | None = None
    executable_price: float | None = None                      # a fresh price at submission time, if available


@dataclass(frozen=True)
class PretradeLimits:
    """Loaded from config["risk"]["pretrade_limits"]. Every field is
    Optional — None means "not configured", reported SKIP (never treated
    as PASS)."""
    enabled: bool = True
    max_notional_usd: float | None = None
    max_quantity: float | None = None
    max_position_per_symbol_usd: float | None = None
    max_portfolio_notional_usd: float | None = None
    max_symbol_concentration_pct: float | None = None
    price_collar_pct: float | None = None
    max_slippage_pct: float | None = None
    require_stop_loss: bool = True
    min_stop_distance_pct: float | None = None
    max_stop_distance_pct: float | None = None
    min_risk_reward: float | None = None
    max_stale_data_seconds: float | None = None
    max_orders_per_window: int | None = None
    order_window_seconds: float | None = None
    duplicate_window_seconds: float | None = None
    standard_contract_units: float = 100_000.0   # FX-standard lot size — see module docstring's residual-risk note


@dataclass(frozen=True)
class Violation:
    check: str
    detail: str


@dataclass(frozen=True)
class PretradeDecision:
    approved: bool
    decision_id: str
    violations: tuple[Violation, ...] = field(default_factory=tuple)
    checks: dict[str, str] = field(default_factory=dict)   # check name -> PASS | REJECT | SKIP (reason)
    estimated_notional_usd: float | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def compute_decision_id(symbol: str, direction: str, bar_time: str | None,
                         entry: float | None, sl: float | None, tp: float | None) -> str:
    """Deterministic per-decision identifier — same underlying decision
    (unchanged symbol/direction/bar/entry/sl/tp) always hashes to the same
    id, which is exactly what duplicate-order protection needs."""
    raw = f"{symbol}|{direction}|{bar_time}|{entry}|{sl}|{tp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def load_pretrade_limits(config: dict) -> PretradeLimits:
    """Loaded fresh on every order (never cached across process lifetime),
    so a config change takes effect on the very next order without a
    restart. config["risk"] is itself merged from config/risk.yaml by
    utils/helpers.py::load_config() — the same "explicit, versioned,
    auditable" governance every other risk parameter already uses."""
    raw = (config.get("risk") or {}).get("pretrade_limits") or {}
    return PretradeLimits(
        enabled=bool(raw.get("enabled", True)),
        max_notional_usd=raw.get("max_notional_usd"),
        max_quantity=raw.get("max_quantity"),
        max_position_per_symbol_usd=raw.get("max_position_per_symbol_usd"),
        max_portfolio_notional_usd=raw.get("max_portfolio_notional_usd"),
        max_symbol_concentration_pct=raw.get("max_symbol_concentration_pct"),
        price_collar_pct=raw.get("price_collar_pct"),
        max_slippage_pct=raw.get("max_slippage_pct"),
        require_stop_loss=bool(raw.get("require_stop_loss", True)),
        min_stop_distance_pct=raw.get("min_stop_distance_pct"),
        max_stop_distance_pct=raw.get("max_stop_distance_pct"),
        min_risk_reward=raw.get("min_risk_reward"),
        max_stale_data_seconds=raw.get("max_stale_data_seconds"),
        max_orders_per_window=raw.get("max_orders_per_window"),
        order_window_seconds=raw.get("order_window_seconds"),
        duplicate_window_seconds=raw.get("duplicate_window_seconds"),
        standard_contract_units=float(raw.get("standard_contract_units", 100_000.0)),
    )


def _is_finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ─── Rate / duplicate tracking ──────────────────────────────────────────────
# In-process, module-level state. Safe because scheduler.py's run_once()
# loop is the sole, single-process, sequential live-order submitter
# (confirmed by repo-wide audit — see the Phase-10 bypass-audit section of
# the final report) — no cross-process coordination is needed for either
# window to be meaningful.

_recent_decision_ids: dict[str, float] = {}      # decision_id -> monotonic timestamp of last APPROVED submission
_recent_order_timestamps: list[float] = []        # monotonic timestamps of recent APPROVED submissions


def reset_rate_state() -> None:
    """Test-only: clears the in-process duplicate/rate-limit trackers so
    tests don't leak state into each other."""
    _recent_decision_ids.clear()
    _recent_order_timestamps.clear()


def _check_duplicate(order: PendingOrder, limits: PretradeLimits, now_mono: float) -> Violation | None:
    if limits.duplicate_window_seconds is None:
        return None
    last = _recent_decision_ids.get(order.decision_id)
    if last is not None and (now_mono - last) < limits.duplicate_window_seconds:
        return Violation(
            "duplicate_order",
            f"decision_id {order.decision_id} was already approved {now_mono - last:.1f}s ago "
            f"(< duplicate_window_seconds={limits.duplicate_window_seconds})",
        )
    return None


def _check_rate_limit(limits: PretradeLimits, now_mono: float) -> Violation | None:
    if limits.max_orders_per_window is None or limits.order_window_seconds is None:
        return None
    window_start = now_mono - limits.order_window_seconds
    recent = [t for t in _recent_order_timestamps if t >= window_start]
    if len(recent) >= limits.max_orders_per_window:
        return Violation(
            "max_orders_per_window",
            f"{len(recent)} order(s) already approved in the last {limits.order_window_seconds}s "
            f"(limit {limits.max_orders_per_window})",
        )
    return None


# ─── The 17 hard limits ─────────────────────────────────────────────────────

def evaluate_pretrade(order: PendingOrder, context: PretradeContext, limits: PretradeLimits) -> PretradeDecision:
    """Pure, deterministic. Every check populates checks[name] with
    exactly one of PASS / REJECT / "SKIP (...)"; any REJECT makes the
    whole decision approved=False. Never raises — an internal error in
    THIS function is itself a bug to fix, not something callers should
    have to guard against, but every EXTERNAL input (order/context
    fields) is validated defensively rather than trusted."""
    checks: dict[str, str] = {}
    violations: list[Violation] = []
    now_mono = time.monotonic()

    def fail(name: str, detail: str) -> None:
        checks[name] = "REJECT"
        violations.append(Violation(name, detail))

    def ok(name: str) -> None:
        checks[name] = "PASS"

    def skip(name: str, reason: str = "not configured") -> None:
        checks[name] = f"SKIP ({reason})"

    if not limits.enabled:
        # An operator can explicitly disable this whole layer — never a
        # silent default, always a deliberate config value — but every
        # other sovereign gate (risk_engine, kill switch) is completely
        # unaffected by this flag.
        skip("pretrade_limits", "disabled by config")
        return PretradeDecision(approved=True, decision_id=order.decision_id, checks=checks)

    # --- 13. INVALID MARKET DATA --------------------------------------------
    numeric_fields = {
        "quantity": order.quantity, "reference_price": order.reference_price,
        "stop_loss": order.stop_loss, "take_profit": order.take_profit,
    }
    non_finite = [k for k, v in numeric_fields.items() if not _is_finite_number(v)]
    non_positive = [
        k for k in ("reference_price", "stop_loss", "take_profit", "quantity")
        if _is_finite_number(numeric_fields.get(k)) and numeric_fields[k] <= 0
    ]
    if non_finite or non_positive:
        fail(
            "invalid_market_data",
            f"missing/non-finite field(s): {non_finite}; non-positive field(s): {non_positive}",
        )
    else:
        ok("invalid_market_data")

    ref_price = order.reference_price if _is_finite_number(order.reference_price) and order.reference_price > 0 else None
    quantity = order.quantity if _is_finite_number(order.quantity) and order.quantity > 0 else None

    # --- 12. STALE DATA -------------------------------------------------------
    if limits.max_stale_data_seconds is not None:
        if not order.bar_time:
            fail("stale_data", "no bar_time on the decision — cannot verify data freshness")
        else:
            try:
                bar_dt = datetime.fromisoformat(order.bar_time)
                if bar_dt.tzinfo is None:
                    bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                age = (context.now_utc - bar_dt).total_seconds()
                if age < 0:
                    fail("stale_data", f"bar_time {order.bar_time} is in the future relative to now")
                elif age > limits.max_stale_data_seconds:
                    fail("stale_data", f"market data is {age:.0f}s old (max {limits.max_stale_data_seconds}s)")
                else:
                    ok("stale_data")
            except (ValueError, TypeError) as exc:
                fail("stale_data", f"unparseable bar_time {order.bar_time!r}: {exc}")
    else:
        skip("stale_data")

    # --- 14 / 15. SYMBOL METADATA / BROKER PRECISION ---------------------------
    spec = context.symbol_spec
    if spec is None or spec.price_digits is None or spec.quantity_step is None:
        fail("symbol_metadata", "symbol precision (price_digits/quantity_step) unavailable — refusing to guess")
    else:
        precision_ok = True
        if spec.min_quantity is not None and quantity is not None and quantity < spec.min_quantity:
            fail("symbol_metadata", f"quantity {quantity} below broker minimum {spec.min_quantity}")
            precision_ok = False
        if spec.max_quantity is not None and quantity is not None and quantity > spec.max_quantity:
            fail("symbol_metadata", f"quantity {quantity} above broker maximum {spec.max_quantity}")
            precision_ok = False
        if spec.quantity_step and quantity is not None:
            remainder = quantity % spec.quantity_step
            if remainder > 1e-9 and (spec.quantity_step - remainder) > 1e-9:
                fail("symbol_metadata", f"quantity {quantity} is not a multiple of step {spec.quantity_step}")
                precision_ok = False
        if quantity is None:
            fail("symbol_metadata", "quantity unavailable to validate against symbol precision")
            precision_ok = False
        if precision_ok:
            ok("symbol_metadata")

    # --- 2. MAX ORDER QUANTITY --------------------------------------------------
    if limits.max_quantity is not None:
        if quantity is None:
            fail("max_quantity", "quantity unknown")
        elif quantity > limits.max_quantity:
            fail("max_quantity", f"quantity {quantity} exceeds max_quantity {limits.max_quantity}")
        else:
            ok("max_quantity")
    else:
        skip("max_quantity")

    # --- Notional estimate, used by several checks below -----------------------
    # centi-lots -> real lots -> standard contract units -> notional. FX-
    # calibrated approximation — see module docstring's residual-risk note.
    estimated_notional = None
    if ref_price is not None and quantity is not None:
        lots = quantity / 100.0
        estimated_notional = abs(lots) * limits.standard_contract_units * ref_price

    # --- 1. MAX NOTIONAL -----------------------------------------------------
    if limits.max_notional_usd is not None:
        if estimated_notional is None:
            fail("max_notional", "cannot estimate order notional (missing quantity/reference_price)")
        elif estimated_notional > limits.max_notional_usd * (1.0 + 1e-9):
            fail("max_notional", f"estimated notional ${estimated_notional:,.2f} exceeds max ${limits.max_notional_usd:,.2f}")
        else:
            ok("max_notional")
    else:
        skip("max_notional")

    # --- 3. MAX POSITION PER SYMBOL --------------------------------------------
    if limits.max_position_per_symbol_usd is not None:
        existing = context.open_positions_notional.get(order.symbol, 0.0) if context.open_positions_notional is not None else None
        if existing is None or estimated_notional is None:
            fail("max_position_per_symbol", "cannot verify projected per-symbol exposure (unknown existing position or notional)")
        else:
            projected = existing + estimated_notional
            if projected > limits.max_position_per_symbol_usd:
                fail(
                    "max_position_per_symbol",
                    f"projected {order.symbol} exposure ${projected:,.2f} exceeds max ${limits.max_position_per_symbol_usd:,.2f}",
                )
            else:
                ok("max_position_per_symbol")
    else:
        skip("max_position_per_symbol")

    # --- 4. MAX PORTFOLIO NOTIONAL ------------------------------------------------
    if limits.max_portfolio_notional_usd is not None:
        if context.portfolio_notional is None or estimated_notional is None:
            fail("max_portfolio_notional", "cannot verify projected portfolio exposure (unknown)")
        else:
            projected = context.portfolio_notional + estimated_notional
            if projected > limits.max_portfolio_notional_usd:
                fail(
                    "max_portfolio_notional",
                    f"projected portfolio exposure ${projected:,.2f} exceeds max ${limits.max_portfolio_notional_usd:,.2f}",
                )
            else:
                ok("max_portfolio_notional")
    else:
        skip("max_portfolio_notional")

    # --- 5. MAX SYMBOL CONCENTRATION -----------------------------------------------
    if limits.max_symbol_concentration_pct is not None:
        if context.portfolio_notional is None or estimated_notional is None:
            fail("max_symbol_concentration", "cannot verify concentration (unknown portfolio/order notional)")
        else:
            existing = context.open_positions_notional.get(order.symbol, 0.0) if context.open_positions_notional is not None else 0.0
            projected_portfolio = context.portfolio_notional + estimated_notional
            projected_symbol = existing + estimated_notional
            pct = (projected_symbol / projected_portfolio) if projected_portfolio > 0 else 1.0
            if pct > limits.max_symbol_concentration_pct:
                fail(
                    "max_symbol_concentration",
                    f"{order.symbol} would be {pct:.1%} of portfolio notional (max {limits.max_symbol_concentration_pct:.1%})",
                )
            else:
                ok("max_symbol_concentration")
    else:
        skip("max_symbol_concentration")

    # --- 6 / 7. PRICE COLLAR / MAX SLIPPAGE ------------------------------------------
    if limits.price_collar_pct is not None or limits.max_slippage_pct is not None:
        if context.executable_price is None or ref_price is None:
            fail("price_collar", "no fresh executable price available to compare against the reference price")
        else:
            deviation = abs(context.executable_price - ref_price) / ref_price
            bound = min(v for v in (limits.price_collar_pct, limits.max_slippage_pct) if v is not None)
            if deviation > bound * (1.0 + 1e-9):
                fail("price_collar", f"executable price deviates {deviation:.2%} from reference (max {bound:.2%})")
            else:
                ok("price_collar")
    else:
        skip("price_collar")

    # --- 8 / 9. STOP-LOSS VALIDITY / DISTANCE ------------------------------------------
    sl_present = _is_finite_number(order.stop_loss) and order.stop_loss > 0
    if limits.require_stop_loss and not sl_present:
        fail("stop_loss_validity", "protective stop-loss is required but missing/invalid")
    elif ref_price is not None and sl_present:
        sl_distance_pct = abs(ref_price - order.stop_loss) / ref_price
        if limits.min_stop_distance_pct is not None and sl_distance_pct < limits.min_stop_distance_pct:
            fail("stop_loss_validity", f"SL distance {sl_distance_pct:.4%} below minimum {limits.min_stop_distance_pct:.4%}")
        elif limits.max_stop_distance_pct is not None and sl_distance_pct > limits.max_stop_distance_pct:
            fail("stop_loss_validity", f"SL distance {sl_distance_pct:.4%} exceeds maximum {limits.max_stop_distance_pct:.4%}")
        else:
            ok("stop_loss_validity")
    elif not limits.require_stop_loss and not sl_present:
        skip("stop_loss_validity", "no SL required and none present")
    else:
        fail("stop_loss_validity", "cannot verify SL distance (missing reference price)")

    # --- 10. TAKE-PROFIT / RR (independent re-check of risk_engine's own floor) ------
    if limits.min_risk_reward is not None:
        if ref_price is None or not sl_present or not _is_finite_number(order.take_profit):
            fail("risk_reward", "cannot compute RR (missing price/SL/TP)")
        else:
            risk = abs(ref_price - order.stop_loss)
            reward = abs(order.take_profit - ref_price)
            if risk <= 0:
                fail("risk_reward", "zero-distance stop-loss — RR undefined")
            else:
                rr = reward / risk
                if rr < limits.min_risk_reward * (1.0 - 1e-9):
                    fail("risk_reward", f"RR {rr:.2f} below minimum {limits.min_risk_reward:.2f}")
                else:
                    ok("risk_reward")
    else:
        skip("risk_reward")

    # --- 17. DUPLICATE ORDER PROTECTION -----------------------------------------------
    if limits.duplicate_window_seconds is not None:
        dup = _check_duplicate(order, limits, now_mono)
        if dup:
            violations.append(dup)
            checks["duplicate_order"] = "REJECT"
        else:
            ok("duplicate_order")
    else:
        skip("duplicate_order")

    # --- 16. MAX ORDERS PER TIME WINDOW --------------------------------------------
    if limits.max_orders_per_window is not None and limits.order_window_seconds is not None:
        rate = _check_rate_limit(limits, now_mono)
        if rate:
            violations.append(rate)
            checks["max_orders_per_window"] = "REJECT"
        else:
            ok("max_orders_per_window")
    else:
        skip("max_orders_per_window")

    approved = len(violations) == 0

    # Only a genuinely APPROVED order updates the duplicate/rate trackers —
    # a rejected attempt must not itself consume the rate budget or poison
    # the duplicate window (an operator may legitimately retry after
    # fixing whatever caused the rejection).
    if approved:
        _recent_decision_ids[order.decision_id] = now_mono
        _recent_order_timestamps.append(now_mono)
        if len(_recent_order_timestamps) > 10_000:   # bound unbounded growth
            del _recent_order_timestamps[:-1_000]

    return PretradeDecision(
        approved=approved,
        decision_id=order.decision_id,
        violations=tuple(violations),
        checks=checks,
        estimated_notional_usd=estimated_notional,
    )
