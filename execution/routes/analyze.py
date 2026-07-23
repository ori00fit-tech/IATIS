"""
execution/routes/analyze.py
------------------------------
Pipeline analysis: /analyze/{symbol} (runs the real pipeline synchronously
via a thread executor), /decisions (Decision Explorer), /candles/{symbol}
(OHLCV + the latest logged signal for chart overlay).
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from execution.api_core import (
    _check_auth,
    _executor,
    _get_config,
    _validate_candle_symbol,
    _validate_symbol,
    logger,
)

router = APIRouter()


class AnalyzeRequest(BaseModel):
    source: str = "twelve_data"
    # Match config.yaml bars_to_load: below ~210 decision-TF bars NNFX is
    # mute and below 50 D1 bars the MTF gate is inert (philosophy audit).
    bars: int = 3000
    timeframes: list[str] = ["M15", "H1", "H4", "D1"]


@router.post("/analyze/{symbol}")
async def analyze(
    symbol: str,
    req: AnalyzeRequest = AnalyzeRequest(),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> JSONResponse:
    """Run full pipeline for one symbol. Symbol: EURUSD or EUR/USD."""
    _check_auth(x_api_key, iatis_session)
    clean_symbol = _validate_symbol(symbol)  # issue #2

    td_symbol = clean_symbol if "/" in clean_symbol else (
        f"{clean_symbol[:3]}/{clean_symbol[3:]}" if len(clean_symbol) == 6 else clean_symbol
    )
    internal_symbol = clean_symbol.replace("/", "")

    config = dict(_get_config())
    config["data"] = dict(config["data"])
    config["data"].update({
        "source": req.source,
        "symbol": internal_symbol,
        "twelve_data_symbol": td_symbol,
        "bars_to_load": req.bars,
        "timeframes": req.timeframes,
    })
    config["telegram"] = {"enabled": False}

    loop = asyncio.get_event_loop()
    try:
        from main import run_pipeline
        start = time.monotonic()
        report = await loop.run_in_executor(_executor, run_pipeline, config)
        report["processing_time_sec"] = round(time.monotonic() - start, 3)
        return JSONResponse(content=report)
    except Exception as exc:
        logger.error(f"Pipeline error for {internal_symbol}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal processing error.")  # issue #7


@router.get("/decisions")
async def decisions(
    limit: int = Query(default=20, ge=1, le=200),
    verdict: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="ISO date/timestamp, inclusive lower bound"),
    date_to: str | None = Query(default=None, description="ISO date/timestamp, inclusive upper bound"),
    engine: str | None = Query(default=None, description="Engine name that voted on this decision"),
    min_score: float | None = Query(default=None, ge=0, le=100),
    risk_rejected: bool = Query(default=False, description="Only decisions the risk gate rejected"),
    reason: str | None = Query(default=None, description="Substring search over NO_TRADE/rejection reasons"),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.decision_log import read_decisions, summarize_decisions, filter_decisions
        all_d = read_decisions()
        total_in_log = len(all_d)
        if verdict:
            all_d = [d for d in all_d if d.get("final_verdict") == verdict.upper()]
        matched = filter_decisions(
            all_d,
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
            engine=engine,
            min_score=min_score,
            risk_rejected=risk_rejected or None,
            reason=reason,
        )
        return {
            "total_in_log": total_in_log,
            "matched": len(matched),
            "returned": len(matched[-limit:]),
            "summary": summarize_decisions(),
            "decisions": list(reversed(matched[-limit:])),
        }
    except Exception as exc:
        logger.error(f"Decisions error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


_CANDLE_INTERVALS = ("M15", "H1", "H4", "D1")


def _fetch_symbol_for_internal(internal: str, config: dict) -> str:
    """internal name (EURUSD, US30) -> the fetch-symbol form the provider
    chain expects (EUR/USD, DJI). Uses config/symbols.yaml's own
    internal<->symbol pairing (merged into config["data"]["twelve_data_symbols"]
    by utils.helpers.load_config()) rather than guessing from string shape —
    the naive "insert a slash at position 3" heuristic used by /analyze
    silently mis-splits indices (NAS100 -> "NAS/100", not "NDX")."""
    for entry in config.get("data", {}).get("twelve_data_symbols", []):
        entry_internal = entry.get("internal") or str(entry.get("symbol", "")).replace("/", "")
        if entry_internal.upper() == internal:
            return entry["symbol"]
    return internal


@router.get("/candles/{symbol}")
async def candles(
    symbol: str,
    interval: str = Query(default="H4"),
    outputsize: int = Query(default=300, ge=10, le=1000),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """OHLCV bars for the price chart, plus the latest logged decision's
    entry/SL/TP for the same symbol so the frontend can overlay IATIS's
    own signal on the bars it was computed from."""
    _check_auth(x_api_key, iatis_session)
    clean_symbol = _validate_candle_symbol(symbol)
    internal = clean_symbol.replace("/", "")
    interval = interval.upper()
    if interval not in _CANDLE_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval must be one of {_CANDLE_INTERVALS}")

    try:
        from core.data_providers import fetch_with_failover, provider_chain_for, DataFetchError

        fetch_symbol = _fetch_symbol_for_internal(internal, _get_config())
        chain = provider_chain_for(fetch_symbol, _get_config().get("data", {}).get("provider_chains"))
        try:
            df, provider = fetch_with_failover(fetch_symbol, interval, outputsize=outputsize, providers=chain)
        except DataFetchError as exc:
            raise HTTPException(status_code=502, detail=f"No provider could deliver candles: {str(exc)[:200]}")

        bars = [
            {
                "time": int(ts.timestamp()),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
            }
            for ts, row in df.iterrows()
        ]

        from storage.decision_log import read_decisions
        matching = [d for d in read_decisions() if d.get("symbol") == internal]
        signal = None
        if matching:
            latest = matching[-1]["report"]
            signal = {
                "timestamp": matching[-1].get("timestamp"),
                "verdict": matching[-1].get("final_verdict"),
                "entry_price": latest.get("entry_price"),
                "stop_loss": latest.get("stop_loss"),
                "take_profit": latest.get("take_profit"),
            }

        return {
            "symbol": internal,
            "interval": interval,
            "provider": provider,
            "bars": bars,
            "signal": signal,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Candles error for {internal}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


