"""
risk/correlation_engine.py
---------------------------
A1: Portfolio Correlation Filter

Prevents over-exposure when multiple correlated symbols signal in the
same direction simultaneously.

Problem solved:
  USDJPY EXECUTE + EURJPY EXECUTE + AUDJPY EXECUTE (all SHORT JPY)
  = 3× risk on JPY weakness, not 3 independent trades

Correlation groups (hardcoded — matches IATIS 19-symbol universe):
  USD_LONG:  EURUSD, GBPUSD, AUDUSD, NZDUSD bearish = short USD
  JPY_SHORT: USDJPY, EURJPY, GBPJPY, AUDJPY bullish = short JPY  
  GOLD:      XAUUSD, XAGUSD (highly correlated metals)
  RISK_ON:   BTCUSD, ETHUSD, NAS100, SPX500, US30

Rule: max 2 signals from same correlation group per run
"""
from __future__ import annotations
from dataclasses import dataclass, field

# Correlation groups: symbol → group membership
CORRELATION_GROUPS: dict[str, list[str]] = {
    "USD_MAJORS": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCHF", "USDCAD"],
    "JPY_CROSSES": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY"],
    "EUR_CROSSES": ["EURUSD", "EURJPY", "EURGBP", "EURCHF"],
    "METALS": ["XAUUSD", "XAGUSD"],
    "RISK_ASSETS": ["BTCUSD", "ETHUSD", "NAS100", "SPX500", "US30"],
}

# Max concurrent signals per group
MAX_PER_GROUP = 2


@dataclass
class CorrelationCheckResult:
    allowed: bool
    symbol: str
    blocking_group: str = ""
    existing_signals: list[str] = field(default_factory=list)
    message: str = ""


def check_correlation(
    symbol: str,
    active_executes: list[str],
    max_per_group: int = MAX_PER_GROUP,
) -> CorrelationCheckResult:
    """Check if adding symbol would exceed correlation group limit.

    Args:
        symbol: symbol being evaluated (e.g. "AUDJPY")
        active_executes: symbols already EXECUTE in this run
        max_per_group: max signals from same correlation group

    Returns:
        CorrelationCheckResult with allowed=True/False
    """
    for group_name, members in CORRELATION_GROUPS.items():
        if symbol not in members:
            continue

        # Count how many active executes are in this group
        group_active = [s for s in active_executes if s in members]

        if len(group_active) >= max_per_group:
            return CorrelationCheckResult(
                allowed=False,
                symbol=symbol,
                blocking_group=group_name,
                existing_signals=group_active,
                message=(
                    f"Correlation limit: {symbol} is in {group_name} group "
                    f"which already has {len(group_active)} active signals "
                    f"({', '.join(group_active)}). Max={max_per_group}."
                ),
            )

    return CorrelationCheckResult(
        allowed=True,
        symbol=symbol,
        message="No correlation conflicts.",
    )


def portfolio_exposure_summary(active_executes: list[str]) -> dict[str, list[str]]:
    """Show which correlation groups are active."""
    summary = {}
    for group_name, members in CORRELATION_GROUPS.items():
        active = [s for s in active_executes if s in members]
        if active:
            summary[group_name] = active
    return summary
