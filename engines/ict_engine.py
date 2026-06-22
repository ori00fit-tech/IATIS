"""
engines/ict_engine.py
------------------------
STUB — Phase 3.

ICT concepts (killzones, judas swing, premium/discount) need precise
session-time data and a more developed liquidity model than Phase 1 has.
Rather than approximate these with fake logic, this engine always
abstains (NEUTRAL, score=0) and is disabled by default in config.yaml
(engines.enabled.ict: false).

TODO (Phase 3):
    - killzones: London/NY session open windows
    - time_bias: directional bias by session
    - judas_swing: false breakout at session open detection
    - premium_discount: position within the current dealing range
"""

from __future__ import annotations

import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput


class ICTEngine(BaseEngine):
    name = "ICT"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        return EngineOutput(
            engine_name=self.name,
            bias=Bias.NEUTRAL,
            score=0.0,
            reasons=["ICT engine not yet implemented (Phase 3) — abstaining"],
            raw={"status": "NOT_IMPLEMENTED_PHASE_3"},
        )
