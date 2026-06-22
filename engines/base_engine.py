"""
engines/base_engine.py
-------------------------
Every strategy engine (SMC, ICT, NNFX, Price Action, Quant, Macro) must
implement this same contract, so the Confluence Court System can treat
them interchangeably without knowing each engine's internals.

This is the single most important file for keeping the "independent
expert agents" design honest: if an engine can't express its opinion as
an EngineOutput, it doesn't get a vote.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class EngineOutput:
    engine_name: str
    bias: Bias
    score: float                 # 0-100, confidence in this bias
    reasons: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)   # engine-specific details (zones, levels, etc.)

    def to_dict(self) -> dict:
        return {
            "engine": self.engine_name,
            "bias": self.bias.value,
            "score": round(self.score, 2),
            "reasons": self.reasons,
            "raw": self.raw,
        }


class BaseEngine(ABC):
    """Abstract base class all strategy engines must inherit from."""

    name: str = "base"

    @abstractmethod
    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        """Analyze multi-timeframe OHLCV data and return an opinion.

        Args:
            mtf_data: dict mapping timeframe label (e.g. "H1") to OHLCV DataFrame.

        Returns:
            EngineOutput expressing this engine's bias and confidence.
        """
        raise NotImplementedError

    def safe_analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        """Wraps analyze() so an engine crashing never takes down the whole
        pipeline — it just abstains (NEUTRAL, score=0) and logs the reason.
        Per IATIS rule: unclear data -> no opinion, never a guess.
        """
        try:
            return self.analyze(mtf_data)
        except Exception as exc:  # noqa: BLE001 — intentional broad catch at this boundary
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=[f"Engine error, abstaining: {exc}"],
            )
