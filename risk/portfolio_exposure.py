"""
risk/portfolio_exposure.py
------------------------------
Tracks currently-open risk across the portfolio so risk_engine.py can
enforce the max_exposure cap. Phase 1: in-memory only. Phase 2+: should
persist to storage (Cloudflare D1) so exposure survives a restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OpenPosition:
    symbol: str
    risk_pct: float
    direction: str   # "BUY" | "SELL"


@dataclass
class PortfolioExposure:
    positions: list[OpenPosition] = field(default_factory=list)

    def add_position(self, position: OpenPosition) -> None:
        self.positions.append(position)
        logger.info(f"Added position: {position.symbol} risk={position.risk_pct:.2%}")

    def remove_position(self, symbol: str) -> None:
        before = len(self.positions)
        self.positions = [p for p in self.positions if p.symbol != symbol]
        if len(self.positions) < before:
            logger.info(f"Removed position: {symbol}")

    def total_open_risk_pct(self) -> float:
        return sum(p.risk_pct for p in self.positions)

    def exposure_for_symbol(self, symbol: str) -> float:
        return sum(p.risk_pct for p in self.positions if p.symbol == symbol)
