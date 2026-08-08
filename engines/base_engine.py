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

from utils.logger import get_logger

logger = get_logger(__name__)


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

    # Forensic Audit (2026-08-04) — a NEUTRAL/score=0 EngineOutput is
    # ambiguous on its own: it could mean "the engine looked and honestly
    # found no pattern" or "the engine raised an unhandled exception and
    # safe_analyze() swallowed it." tally_votes()/the live gate still only
    # ever read bias/score (unchanged, zero decision-logic impact) — this
    # field exists purely so a human or a future monitoring metric can
    # distinguish "no opinion" from "broken" without parsing `reasons`
    # strings. False for every real analyze() call; only safe_analyze()'s
    # except branch ever sets it True.
    #
    # Engine Refinement V1 (2026-08-08) — until this pass, `crashed` was
    # set here but never READ by anything downstream (confirmed by a
    # repo-wide grep before this change — see reports/engine_refinement/
    # BASELINE.md §6). backtesting/backtest_engine.py now consumes it to
    # keep a distinct per-run crash count, so a bar where an engine threw
    # an exception no longer looks statistically identical to a bar where
    # every engine genuinely had no opinion.
    crashed: bool = False

    # Engine Refinement V1 (2026-08-08) — base-contract hardening (§3 of
    # the refinement plan). Five additive fields, all default to "no
    # information" so every existing engine/caller that never sets them
    # behaves exactly as before this change.
    #
    # score_type answers a DIFFERENT question from evidence_level above:
    # evidence_level says whether this score has been through real
    # statistical validation (HEURISTIC | MEASURED); score_type says what
    # KIND of number `score` itself is. Every engine today emits an
    # arbitrary 0-100 confidence heuristic, never a calibrated
    # probability — score_type exists so that fact is explicit and
    # machine-checkable rather than assumed. A score of 70 must never be
    # read as "70% probability of being right"; that would require a
    # PROBABILITY score_type, which no engine sets today.
    score_type: str = "HEURISTIC"   # HEURISTIC | PROBABILITY (no engine emits PROBABILITY yet)

    # The timestamp of the last bar this decision was causally computed
    # against (the close time of decision_frame()'s own last row) —
    # filled in generically by safe_analyze() from the SAME decision_
    # frame() call every engine already makes, unless an engine's own
    # analyze() has already set something more specific (respected, never
    # overwritten). None only when decision_frame() itself couldn't
    # resolve any frame (e.g. an engine crashed before reaching it).
    causal_timestamp: str | None = None

    # Best-effort provenance for the input data this decision used —
    # which timeframe was requested vs. actually used (decision_frame()
    # falls back to H1 or the first available frame when the configured
    # decision_tf is missing) and how many bars were available. Filled in
    # generically by safe_analyze(); an engine's own analyze() may
    # overwrite with richer detail.
    data_quality: dict = field(default_factory=dict)

    # Populated ONLY by safe_analyze()'s except branch (mirrors `crashed`
    # exactly — error_type/error_message are always both None or both
    # set together with crashed=True). error_type is the exception class
    # name (e.g. "KeyError"), error_message is str(exc). Exists so
    # research/backtest tooling can classify and count failures by type
    # instead of only having crashed=True as an undifferentiated flag.
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "engine": self.engine_name,
            "bias": self.bias.value,
            "score": round(self.score, 2),
            "score_type": self.score_type,
            "reasons": self.reasons,
            "raw": self.raw,
            "features": self.features,
            "probability": self.probability,
            "confidence_interval": list(self.confidence_interval) if self.confidence_interval else None,
            "expected_return": self.expected_return,
            "expected_drawdown": self.expected_drawdown,
            "sample_size": self.sample_size,
            "evidence_level": self.evidence_level,
            "crashed": self.crashed,
            "causal_timestamp": self.causal_timestamp,
            "data_quality": self.data_quality,
            "error_type": self.error_type,
            "error_message": self.error_message,
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

        Engine Refinement V1 (2026-08-08) — two changes, both additive,
        zero change to the live fail-closed contract above:
        1. A crash is now logged (logger.warning), not just captured as a
           string inside `reasons` — CLAUDE.md-adjacent research-mode
           discipline: a failure must be discoverable, never silently
           swallowed, even though the pipeline still safely abstains.
        2. causal_timestamp/data_quality are filled in generically here
           from the SAME decision_frame() call every engine's own
           analyze() already makes internally — best-effort, computed
           BEFORE calling analyze() so it's available even on the crash
           path. An engine's own analyze() may set a more specific value;
           that is respected, never overwritten.
        """
        causal_timestamp: str | None = None
        data_quality: dict = {}
        try:
            tf, df = self.decision_frame(mtf_data)
            if len(df) > 0:
                causal_timestamp = str(df.index[-1])
            data_quality = {
                "decision_tf_requested": self.decision_tf,
                "decision_tf_used": tf,
                "bars_available": len(df),
            }
        except Exception:
            pass  # best-effort metadata only — must never itself crash the wrapper

        try:
            output = self.analyze(mtf_data)
            if output.causal_timestamp is None:
                output.causal_timestamp = causal_timestamp
            if not output.data_quality:
                output.data_quality = data_quality
            return output
        except Exception as exc:  # noqa: BLE001 — intentional broad catch at this boundary
            logger.warning(f"{self.name} engine crashed, abstaining: {type(exc).__name__}: {exc}")
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=[f"Engine error, abstaining: {exc}"],
                crashed=True,
                error_type=type(exc).__name__,
                error_message=str(exc),
                causal_timestamp=causal_timestamp,
                data_quality=data_quality,
            )
