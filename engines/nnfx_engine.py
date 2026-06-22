"""
engines/nnfx_engine.py
-------------------------
STUB — Phase 3.

No-Nonsense Forex System logic (trend filter, baseline, confirmation
indicators, ATR-based exits) is a well-defined indicator stack, but
implementing it properly means picking and tuning specific indicators
(not just naming them). Deferred to Phase 3 to avoid a shallow, untested
implementation. Disabled by default in config.yaml (engines.enabled.nnfx: false).

TODO (Phase 3):
    - trend_filter: e.g. ADX or moving-average-slope based
    - entry_signal: confirmation indicator stack
    - atr_stop: ATR-multiple based stop distance
    - strength_score: composite signal strength
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput


class NNFXEngine(BaseEngine):
    name = "NNFX"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        return EngineOutput(
            engine_name=self.name,
            bias=Bias.NEUTRAL,
            score=0.0,
            reasons=["NNFX engine not yet implemented (Phase 3) — abstaining"],
            raw={"status": "NOT_IMPLEMENTED_PHASE_3"},
        )
