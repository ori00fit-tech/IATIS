"""
engines/quant_engine.py
---------------------------
STUB — Phase 3.

Statistical/quant confirmation (volatility regime, momentum, z-score,
correlation) is straightforward to compute but needs to be validated
against real data before it's allowed a vote — Phase 1 synthetic data
would make any "confirmation" meaningless. Disabled by default in
config.yaml (engines.enabled.quant: false).

TODO (Phase 3):
    - volatility: realized vol percentile
    - momentum: ROC / RSI-style momentum score
    - z_score: price deviation from rolling mean
    - correlation: cross-asset correlation check (feeds risk engine too)
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput


class QuantEngine(BaseEngine):
    name = "Quant"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        return EngineOutput(
            engine_name=self.name,
            bias=Bias.NEUTRAL,
            score=0.0,
            reasons=["Quant engine not yet implemented (Phase 3) — abstaining"],
            raw={"status": "NOT_IMPLEMENTED_PHASE_3"},
        )
