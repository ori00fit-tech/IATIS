"""
tests/test_decision_timeframe.py
-----------------------------------
D1-primary decision timeframe (config data.timeframes[0] = D1, with
H4/H1 as auxiliary context). Pins the five behaviors the switch relies
on:

  1. engines vote on the configured decision timeframe, not hardcoded H1
  2. MTF confirmation self-disables when the signal TF is D1 (no +8
     self-confirmation from comparing D1 with itself)
  3. MQS session scoring is neutral on a daily decision TF (a D1
     position lives through every session)
  4. build_multi_timeframe_view never fabricates finer bars from a
     coarser base
  5. Telegram alerts dedup per decision bar (a 2-hourly scheduler
     re-evaluates the same D1 candle ~12 times)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engines.nnfx_engine import NNFXEngine
from engines.price_action_engine import PriceActionEngine
from confluence.mtf_confirmation import check_mtf_confirmation
from core.market_quality import assess_market_quality
from core.timeframe_sync import build_multi_timeframe_view
from main import build_active_engines, decision_timeframe
from utils.helpers import load_config


def _bars(n=300, freq="1D", start="2024-01-02", trend=0.0005):
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    drift = np.cumsum(np.full(n, trend))
    close = 1.10 + drift
    return pd.DataFrame({
        "open": close - 0.0005, "high": close + 0.002,
        "low": close - 0.002, "close": close,
        "volume": np.full(n, 1000.0),
    }, index=idx)


# 1 ─ engines follow decision_tf ------------------------------------------------

def test_engine_votes_on_configured_decision_tf():
    d1 = _bars(300, "1D")
    h1 = _bars(300, "1h")
    mtf = {"D1": d1, "H4": _bars(300, "4h"), "H1": h1}

    eng = NNFXEngine()
    eng.decision_tf = "D1"
    tf, df = eng.decision_frame(mtf)
    assert tf == "D1" and df is d1

    eng_default = PriceActionEngine()  # decision_tf defaults to H1
    tf, df = eng_default.decision_frame(mtf)
    assert tf == "H1" and df is h1


def test_decision_frame_falls_back_when_tf_missing():
    h1 = _bars(60, "1h")
    eng = NNFXEngine()
    eng.decision_tf = "D1"
    tf, df = eng.decision_frame({"H1": h1})
    assert tf == "H1" and df is h1


def test_build_active_engines_propagates_decision_tf():
    config = load_config()
    config["data"]["timeframes"] = ["D1", "H4", "H1"]
    engines = build_active_engines(config)
    assert engines, "no engines enabled in config"
    assert all(e.decision_tf == "D1" for e in engines)
    assert decision_timeframe(config) == "D1"


# 2 ─ MTF confirmation self-disables on D1 --------------------------------------

def test_mtf_confirmation_skipped_when_signal_tf_is_d1():
    mtf = {"D1": _bars(300, "1D")}
    res = check_mtf_confirmation(h1_bias="BULLISH", mtf_data=mtf, signal_tf="D1")
    assert res.score_adjustment == 0.0
    assert not res.confirming
    assert "skipped" in res.reason.lower()


def test_mtf_confirmation_unchanged_for_h1_signals():
    mtf = {"D1": _bars(300, "1D", trend=0.001)}  # clear uptrend
    res = check_mtf_confirmation(h1_bias="BULLISH", mtf_data=mtf, signal_tf="H1")
    # Behavior identical to before the signal_tf parameter existed:
    # a real evaluation happens (reason is not the skip message).
    assert "skipped" not in res.reason.lower()


# 3 ─ MQS session neutrality on daily TF ----------------------------------------

def test_mqs_session_neutral_on_d1(monkeypatch):
    from datetime import datetime, timezone

    df = _bars(300, "1D")
    # 03:00 UTC = dead Asian hours — heavily penalized on H1
    dead_hour = datetime(2024, 3, 6, 3, 0, tzinfo=timezone.utc)
    d1 = assess_market_quality(df=df, symbol="EURUSD", now=dead_hour, timeframe="D1")
    h1 = assess_market_quality(df=df, symbol="EURUSD", now=dead_hour, timeframe="H1")

    assert any("session neutral" in r.lower() for r in d1.reasons)
    assert d1.score > h1.score  # the dead-hour penalty no longer applies


# 4 ─ no fabricated finer bars ---------------------------------------------------

def test_mtf_view_skips_finer_timeframes():
    daily = _bars(300, "1D")
    views = build_multi_timeframe_view(daily, ["D1", "H4", "H1"])
    assert list(views.keys()) == ["D1"]
    assert views["D1"] is daily


def test_mtf_view_still_downsamples_upward():
    hourly = _bars(500, "1h")
    views = build_multi_timeframe_view(hourly, ["H1", "H4", "D1"])
    assert set(views.keys()) == {"H1", "H4", "D1"}
    assert len(views["D1"]) < len(views["H4"]) < len(views["H1"])


# 5 ─ per-bar alert dedup ---------------------------------------------------------

def _execute_report(symbol="EURUSD", bar_time="2026-07-04 00:00:00+00:00"):
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "bar_time": bar_time,
        "summary": "test",
        "regime": {"state": "TRENDING"},
        "confluence": {"score": 70.0, "vote": {"winning_bias": "BULLISH"},
                       "passed": True, "fail_reasons": []},
        "risk": {"passed": True},
        "engine_outputs": [],
    }


def test_execute_alert_dedup_per_bar(fake_d1):
    from storage.decision_db import log_decision_db, execute_alert_exists_for_bar

    assert execute_alert_exists_for_bar("EURUSD", "2026-07-04 00:00:00+00:00") is False

    log_decision_db(_execute_report())
    assert execute_alert_exists_for_bar("EURUSD", "2026-07-04 00:00:00+00:00") is True
    # different bar or symbol → not a duplicate
    assert execute_alert_exists_for_bar("EURUSD", "2026-07-05 00:00:00+00:00") is False
    assert execute_alert_exists_for_bar("GBPUSD", "2026-07-04 00:00:00+00:00") is False


def test_dedup_fails_open_when_db_unreachable(monkeypatch):
    import requests
    from storage.decision_db import execute_alert_exists_for_bar

    def _down(*a, **k):
        raise requests.ConnectionError("D1 down")

    monkeypatch.setattr("storage.d1_client._post", _down)
    # fail-open: a duplicate alert beats a silently dropped signal
    assert execute_alert_exists_for_bar("EURUSD", "2026-07-04 00:00:00+00:00") is False
