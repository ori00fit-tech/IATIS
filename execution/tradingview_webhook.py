"""
execution/tradingview_webhook.py
------------------------------------
STUB — Phase 2.

TODO:
    - FastAPI route that receives TradingView alert webhooks (via the
      Cloudflare Worker gateway in cloudflare/worker.js), validates the
      payload, and triggers the main pipeline for the relevant symbol.
"""

from __future__ import annotations


def handle_webhook(payload: dict) -> dict:
    raise NotImplementedError("TradingView webhook handling is planned for Phase 2.")
