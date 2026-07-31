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

    # Confluence Engine Overhaul Phase 2 (2026-07-31) — additive-only,
    # never read by tally_votes()/the live gate (those still consult only
    # bias/score). `features` is the Feature-Extraction-layer snapshot an
    # engine's decide() logic actually consumed — a clean, decision-
    # agnostic record of what was measured, produced by the
    # extract_features()/decide() split now used by smc/price_action/
    # nnfx/wyckoff/ict/market_structure (see each engine module). The
    # remaining fields all default to "no measured evidence yet": none of
    # today's engines has a backtested win-rate behind its score, so
    # fabricating a probability/expected-return number here would be
    # exactly the false precision CLAUDE.md's evidence discipline warns
    # against. They exist so a FUTURE engine variant that has been through
    # Mission Center validation (Backtesting Lab Pro Phase C/D lineage)
    # can report real, measured values through the same schema, and so
    # every engine "speaks the same statistical language" once any of
    # them actually have evidence — without a second schema migration.
    features: dict = field(default_factory=dict)
    probability: float | None = None
    confidence_interval: tuple[float, float] | None = None
    expected_return: float | None = None
    expected_drawdown: float | None = None
    sample_size: int | None = None
    evidence_level: str = "HEURISTIC"   # HEURISTIC | MEASURED (no engine is MEASURED yet)

    def to_dict(self) -> dict:
        return {
            "engine": self.engine_name,
            "bias": self.bias.value,
            "score": round(self.score, 2),
            "reasons": self.reasons,
            "raw": self.raw,
            "features": self.features,
            "probability": self.probability,
            "confidence_interval": list(self.confidence_interval) if self.confidence_interval else None,
            "expected_return": self.expected_return,
            "expected_drawdown": self.expected_drawdown,
            "sample_size": self.sample_size,
            "evidence_level": self.evidence_level,
        }


class BaseEngine(ABC):
    """Abstract base class all strategy engines must inherit from."""

    name: str = "base"

    # The timeframe an engine's *vote* is computed on. Historically this
    # was hardcoded to "H1" inside each engine; it is now set by whoever
    # builds the engines (main.build_active_engines / the backtest) from
    # config.yaml's data.timeframes[0], so the system can decide on D1
    # while keeping H4/H1 in mtf_data as auxiliary context.
    decision_tf: str = "H1"

    # Confluence Engine Overhaul Phase 1 (config extraction) — set from
    # config/engines.yaml's engines.thresholds.<engine_key> by whoever
    # builds the engines (main.build_active_engines / the backtest engine
    # construction loop), mirroring the existing decision_tf/full_spec
    # attribute-assignment pattern exactly. Empty by default so a
    # zero-arg-constructed engine (every test/script that never sets this)
    # falls through to each engine's own hardcoded default via
    # self.thresholds.get(key, DEFAULT) — identical behavior to before
    # this attribute existed.
    thresholds: dict = {}

    def decision_frame(self, mtf_data: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame]:
        """Return (label, df) for the configured decision timeframe,
        falling back to H1 and then to the first available frame —
        exactly the old per-engine behavior when decision_tf is H1."""
        if self.decision_tf in mtf_data:
            return self.decision_tf, mtf_data[self.decision_tf]
        if "H1" in mtf_data:
            return "H1", mtf_data["H1"]
        tf = next(iter(mtf_data))
        return tf, mtf_data[tf]

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
