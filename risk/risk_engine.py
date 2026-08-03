"""
risk/risk_engine.py
-----------------------
The Risk Management Gate — per the IATIS design this is a "sovereign
layer": it doesn't filter trade ideas, it has the authority to make a
trade not exist at all. Every check here is a hard pass/fail, and ANY
single failure blocks the trade. No partial credit, no overriding by a
high confluence score.

This module is intentionally the most "finished" piece in Phase 1: risk
rules are pure math (no market-judgment heuristics), so unlike the
strategy engines there's no reason to defer them to a later phase.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskCheckResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    recommended_risk_pct: float = 0.0   # fraction of account, e.g. 0.005 = 0.5%
    position_size_units: float | None = None


@dataclass
class RiskInputs:
    account_balance: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    current_open_risk_pct: float = 0.0   # sum of risk % already committed to open trades
    current_drawdown_pct: float = 0.0    # current drawdown from equity peak
    correlated_exposure_pct: float = 0.0  # exposure to instruments correlated with this trade
    correlation_limit_pct: float = 0.10   # block if correlated_exposure_pct exceeds this
    # True when this exact symbol already has an open position/signal.
    # risk/live_portfolio_state.py's correlated-exposure calc deliberately
    # EXCLUDES the candidate symbol from its own correlation group (correct
    # — it measures exposure to OTHER correlated instruments), which means
    # nothing else was checking "is this exact symbol already open" before
    # this field existed: two live EURUSD signals opened ~2h apart with
    # near-identical entry/SL/TP (observed 2026-07-21/22 in production) went
    # through uncontested, doubling real risk on one setup while the
    # exposure-cap math still believed only one EURUSD position existed.
    symbol_already_open: bool = False


def _risk_reward_ratio(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk == 0:
        return 0.0
    return reward / risk


def evaluate_risk(inputs: RiskInputs, config: dict) -> RiskCheckResult:
    """Run every hard-gate risk check. Returns passed=False if ANY check fails."""
    risk_cfg = config.get("risk", {})
    min_rr = risk_cfg.get("min_risk_reward", 3.0)
    max_exposure = risk_cfg.get("max_exposure", 0.05)
    dd_reduce = risk_cfg.get("max_drawdown_reduce", 0.10)
    dd_stop = risk_cfg.get("max_drawdown_stop", 0.15)
    risk_min = risk_cfg.get("risk_per_trade_min", 0.0025)
    risk_max = risk_cfg.get("risk_per_trade_max", 0.01)

    reasons: list[str] = []

    # --- Fail CLOSED on invalid numeric input (Forensic Audit, 2026-08-04
    # — BUG-006). Every hard-gate check below is a >=/</> comparison, and
    # in Python a comparison against NaN is ALWAYS False — which means a
    # single NaN input would silently BYPASS every check in this
    # "sovereign" layer (drawdown stop, RR floor, correlation cap,
    # exposure cap) instead of blocking the trade. Confirmed via direct
    # reproduction: NaN drawdown / NaN entry_price / NaN correlated
    # exposure each returned passed=True before this check existed. A
    # negative or zero account_balance (a real, reachable state — see
    # risk/live_portfolio_state.py's equity accumulation from real
    # closed-trade PnL) also silently produced a NEGATIVE position size.
    # A risk gate must refuse to trade on corrupted/nonsensical input,
    # never silently compute a "passed" result from it. See
    # reports/forensic/13_CONFIRMED_BUGS.md BUG-006.
    numeric_inputs = {
        "account_balance": inputs.account_balance,
        "entry_price": inputs.entry_price,
        "stop_loss_price": inputs.stop_loss_price,
        "take_profit_price": inputs.take_profit_price,
        "current_open_risk_pct": inputs.current_open_risk_pct,
        "current_drawdown_pct": inputs.current_drawdown_pct,
        "correlated_exposure_pct": inputs.correlated_exposure_pct,
        "correlation_limit_pct": inputs.correlation_limit_pct,
    }
    invalid = [name for name, v in numeric_inputs.items() if not math.isfinite(v)]
    if invalid:
        reasons.append(
            f"Invalid (NaN/inf) risk input(s): {', '.join(invalid)} — "
            "refusing to trade on corrupted data"
        )
        return RiskCheckResult(passed=False, reasons=reasons)

    if inputs.account_balance <= 0:
        reasons.append(
            f"Account balance {inputs.account_balance:.2f} is not positive "
            "— refusing to size a trade"
        )
        return RiskCheckResult(passed=False, reasons=reasons)

    # --- Hard stop: system-level drawdown breach ---
    if inputs.current_drawdown_pct >= dd_stop:
        reasons.append(
            f"System drawdown {inputs.current_drawdown_pct:.2%} >= stop threshold {dd_stop:.2%} "
            "— system must halt, no new trades"
        )
        return RiskCheckResult(passed=False, reasons=reasons)

    # --- Risk/reward floor ---
    # Tolerance note: SL/TP are constructed as entry ± atr·mult(·rr), so a
    # signal at the floor computes rr = min_rr EXACTLY in real arithmetic —
    # but the float add/subtract round-trip leaves rr short by ~1e-14 about
    # 25% of the time, and the strict `<` rejected those valid signals with
    # "Risk/reward 2.00 below minimum required 2.00" (17 observed live,
    # philosophy audit follow-up). 1e-9 relative tolerance is ~5 orders
    # above float dust and ~7 below any economically meaningful RR gap.
    rr = _risk_reward_ratio(inputs.entry_price, inputs.stop_loss_price, inputs.take_profit_price)
    if rr < min_rr * (1.0 - 1e-9):
        reasons.append(f"Risk/reward {rr:.2f} below minimum required {min_rr:.2f}")

    # --- Directional sanity: stop must be on the OPPOSITE side of entry
    # from the target (Forensic Audit, 2026-08-04 — BUG-006). RiskInputs
    # carries no explicit direction field; direction is inferred from
    # which side of entry the target sits on. The RR floor above only
    # ever compares magnitudes via abs(), so a backwards stop (on the
    # SAME side as the target) can still compute a technically-passing
    # RR ratio — reproduced: entry=1.10, target=1.30 (implies long,
    # stop should be < entry), stop=1.20 (wrong side, ABOVE entry)
    # computed RR=2.0 and passed before this check existed. Not
    # currently reachable from main.py's own construction (which always
    # builds SL/TP correctly relative to direction), but the sovereign
    # gate must not rely on a caller getting geometry right.
    if inputs.take_profit_price > inputs.entry_price:      # implies long
        if inputs.stop_loss_price >= inputs.entry_price:
            reasons.append(
                "Stop-loss is not below entry for a long setup (target above "
                "entry) — refusing a backwards stop"
            )
    elif inputs.take_profit_price < inputs.entry_price:    # implies short
        if inputs.stop_loss_price <= inputs.entry_price:
            reasons.append(
                "Stop-loss is not above entry for a short setup (target below "
                "entry) — refusing a backwards stop"
            )

    # --- Same-symbol duplicate-position guard ---
    # A new signal on a symbol that already has an open position is not an
    # independent trade — it doubles real risk on one setup while every
    # exposure calculation above still assumes one position per symbol.
    if inputs.symbol_already_open:
        reasons.append(
            "Symbol already has an open position — refusing a second "
            "simultaneous position on the same instrument"
        )

    # --- Correlation exposure cap ---
    if inputs.correlated_exposure_pct >= inputs.correlation_limit_pct:
        reasons.append(
            f"Correlated exposure {inputs.correlated_exposure_pct:.2%} "
            f">= limit {inputs.correlation_limit_pct:.2%}"
        )

    # --- Determine per-trade risk, reduced if in a drawdown-reduce zone ---
    recommended_risk_pct = risk_max
    if inputs.current_drawdown_pct >= dd_reduce:
        recommended_risk_pct = risk_min
        reasons_note = (
            f"Drawdown {inputs.current_drawdown_pct:.2%} >= reduce threshold {dd_reduce:.2%} "
            f"— risk capped to minimum {risk_min:.2%}"
        )
        logger.warning(reasons_note)

    # --- Total exposure cap (existing open risk + this trade) ---
    projected_exposure = inputs.current_open_risk_pct + recommended_risk_pct
    if projected_exposure > max_exposure:
        reasons.append(
            f"Projected total exposure {projected_exposure:.2%} exceeds max {max_exposure:.2%}"
        )

    passed = len(reasons) == 0

    position_size_units = None
    if passed:
        risk_amount = inputs.account_balance * recommended_risk_pct
        per_unit_risk = abs(inputs.entry_price - inputs.stop_loss_price)
        position_size_units = round(risk_amount / per_unit_risk, 4) if per_unit_risk > 0 else 0.0

    result = RiskCheckResult(
        passed=passed,
        reasons=reasons if reasons else ["All risk checks passed"],
        recommended_risk_pct=recommended_risk_pct if passed else 0.0,
        position_size_units=position_size_units,
    )

    logger.info(f"Risk evaluation: passed={result.passed}, reasons={result.reasons}")
    return result
