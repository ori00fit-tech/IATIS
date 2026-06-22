"""
ai/ai_analyzer.py
--------------------
STUB — Phase 4.

Per the IATIS design, the AI layer's role is strictly explanation/
summarization of engine outputs — it must never set bias, score, or
override risk/confluence decisions. This file exists now mainly to
document that contract so Phase 4 implementation can't quietly drift
into "AI decides the trade."

TODO (Phase 4):
    - summarize(decision_payload: dict) -> str
      Takes the already-finalized output of main.py's pipeline
      (regime + engine outputs + confluence + risk result) and produces
      a human-readable explanation. Must not read raw price data itself
      and must not be able to change `final_verdict`.
"""

from __future__ import annotations


def summarize(decision_payload: dict) -> str:
    raise NotImplementedError("AI explanation layer is planned for Phase 4.")
