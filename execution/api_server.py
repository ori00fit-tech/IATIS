"""
execution/api_server.py
---------------------------
Phase 2: FastAPI HTTP server for the IATIS pipeline.

Endpoints:
    GET  /health            — liveness check, returns version + credits remaining
    POST /analyze/{symbol}  — runs full pipeline for one symbol, returns report
    GET  /decisions         — last N decisions from the No-Trade Database
    GET  /budget            — Twelve Data API credits status

Run:
    uvicorn execution.api_server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
load_dotenv()

try:
    from fastapi import FastAPI, Header, HTTPException, Query
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError as exc:
    raise ImportError("Run: pip install fastapi uvicorn") from exc

from utils.helpers import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="IATIS API",
    description="Institutional Adaptive Trading Intelligence System",
    version="0.2.0",
    docs_url="/docs",
    redoc_url=None,
)

_executor = ThreadPoolExecutor(max_workers=4)
_config_cache: dict | None = None


def _get_config() -> dict:
    global _config_cache
    if _config_cache is None:
        _config_cache = load_config()
        api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
        if api_key:
            _config_cache["data"]["twelve_data_api_key"] = api_key
    return _config_cache


def _check_auth(x_api_key: str | None) -> None:
    required = os.environ.get("API_SERVER_KEY", "")
    if required and x_api_key != required:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


class AnalyzeRequest(BaseModel):
    source: str = "twelve_data"
    bars: int = 500
    timeframes: list[str] = ["M15", "H1", "H4", "D1"]


@app.get("/health")
async def health() -> dict[str, Any]:
    config = _get_config()
    credits = None
    try:
        from core.twelve_data_client import RateLimiter
        credits = RateLimiter().remaining_today()
    except Exception:
        pass
    return {
        "status": "ok",
        "version": config.get("system", {}).get("version", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "twelve_data_credits_remaining": credits,
    }


@app.post("/analyze/{symbol}")
async def analyze(
    symbol: str,
    req: AnalyzeRequest = AnalyzeRequest(),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    _check_auth(x_api_key)

    td_symbol = symbol if "/" in symbol else (
        f"{symbol[:3]}/{symbol[3:]}" if len(symbol) == 6 else symbol
    )
    clean_symbol = td_symbol.replace("/", "")

    config = dict(_get_config())
    config["data"] = dict(config["data"])
    config["data"]["source"] = req.source
    config["data"]["symbol"] = clean_symbol
    config["data"]["twelve_data_symbol"] = td_symbol
    config["data"]["bars_to_load"] = req.bars
    config["data"]["timeframes"] = req.timeframes
    config["telegram"] = {"enabled": False}

    loop = asyncio.get_event_loop()
    try:
        from main import run_pipeline
        start = time.monotonic()
        report = await loop.run_in_executor(_executor, run_pipeline, config)
        report["processing_time_sec"] = round(time.monotonic() - start, 3)
        return JSONResponse(content=report)
    except Exception as exc:
        logger.error(f"Pipeline error for {symbol}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/decisions")
async def decisions(
    limit: int = Query(default=20, ge=1, le=200),
    verdict: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key)
    from storage.decision_log import read_decisions, summarize_decisions
    all_d = read_decisions()
    if verdict:
        all_d = [d for d in all_d if d.get("final_verdict") == verdict.upper()]
    recent = list(reversed(all_d[-limit:]))
    return {
        "total_in_log": len(all_d),
        "returned": len(recent),
        "summary": summarize_decisions(),
        "decisions": recent,
    }


@app.get("/budget")
async def budget(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_api_key)
    try:
        from core.twelve_data_client import RateLimiter, MAX_REQUESTS_PER_DAY
        remaining = RateLimiter().remaining_today()
        used = MAX_REQUESTS_PER_DAY - remaining
        return {
            "max_per_day": MAX_REQUESTS_PER_DAY,
            "used_today": used,
            "remaining_today": remaining,
            "percent_used": round(used / MAX_REQUESTS_PER_DAY * 100, 1),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
