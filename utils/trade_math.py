"""
utils/trade_math.py
---------------------
The single home for closed-trade P&L math shared across
storage/journal.py, storage/outcome_tracker.py, and
scripts/repair_outcome_pips.py — three independent reimplementations of
the same sign-sensitive direction/diff/R-multiple/profit-factor formulas
(audit docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-9), in the same
spirit as utils/indicators.py's consolidation (institutional gap analysis
addendum A2): LOCATION changes, not the numbers.

This consolidation is not purely cosmetic. Writing test coverage for the
duplicated copies surfaced that storage/journal.py's profit_factor branch
had a real bug (an all-breakeven book — zero wins AND zero losses —
reported "Infinity" instead of undefined) that storage/outcome_tracker.py's
independent copy of the *same* formula also had, silently, until this
module replaced both. Three copies of a formula is three chances for
exactly this kind of divergence to go undetected in one of them.
"""
from __future__ import annotations

BUY_DIRECTIONS = ("BUY", "BULLISH")


def is_buy_direction(direction: str | None) -> bool:
    """True for BUY/BULLISH, False for everything else (including None
    or an unrecognized string) — the direction vocabulary is exactly
    these four values across the codebase (log_signal stores the vote's
    winning bias as BULLISH/BEARISH; broker paths may store BUY/SELL)."""
    return direction in BUY_DIRECTIONS


def price_diff(entry: float, exit_price: float, direction: str | None) -> float:
    """Directional price move: positive = favorable, negative = adverse,
    regardless of whether the position was long or short."""
    return (exit_price - entry) if is_buy_direction(direction) else (entry - exit_price)


def realized_r(
    entry: float | None,
    stop_loss: float | None,
    exit_price: float | None,
    direction: str | None,
) -> float | None:
    """R-multiple: price_diff / |entry - stop_loss|.

    None if entry/stop_loss/exit_price is missing, or the stop distance
    is zero (nothing to normalize risk against) — never a division by
    zero or a fabricated value.
    """
    if entry is None or stop_loss is None or exit_price is None:
        return None
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    return price_diff(entry, exit_price, direction) / risk


def profit_factor(r_values: list[float]) -> float | str | None:
    """gross_win / gross_loss over a list of R-multiples (or any other
    consistently-scaled P&L values).

    Returns:
      - a float ratio when there's at least one loss to divide by.
      - the string "Infinity" (a JSON-safe sentinel — a bare
        float("inf") is not valid JSON, and a browser's fetch().json()
        throws on it) when there are wins but zero losses — genuinely
        undefined-but-infinite.
      - None when there is nothing to compute a ratio from: no values,
        or every value is exactly zero (an all-breakeven book). 0/0 is
        undefined, not infinite — see this module's docstring for why
        that distinction is the reason this function exists.
    """
    gross_win = sum(r for r in r_values if r > 0)
    gross_loss = -sum(r for r in r_values if r < 0)
    if gross_loss > 0:
        return gross_win / gross_loss
    if gross_win > 0:
        return "Infinity"
    return None
