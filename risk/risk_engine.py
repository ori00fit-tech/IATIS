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

    # --- Hard stop: system-level drawdown breach ---
    if inputs.current_drawdown_pct >= dd_stop:
        reasons.append(
            f"System drawdown {inputs.current_drawdown_pct:.2%} >= stop threshold {dd_stop:.2%} "
            "— system must halt, no new trades"
        )
        return RiskCheckResult(passed=False, reasons=reasons)

    # --- Risk/reward floor ---
    rr = _risk_reward_ratio(inputs.entry_price, inputs.stop_loss_price, inputs.take_profit_price)
    if rr < min_rr:
        reasons.append(f"Risk/reward {rr:.2f} below minimum required {min_rr:.2f}")

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
