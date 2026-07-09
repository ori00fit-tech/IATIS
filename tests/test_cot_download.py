"""
tests/test_cot_download.py
---------------------------
COT wiring (H012): CFTC legacy futures-only parse → per-symbol caches →
SentimentEngine consumption. No network — fixture text mirrors the
deafut.txt layout.
"""

import json
import time

import pandas as pd
import pytest

from scripts.download_cot import parse_cot_text, update_caches
from engines.sentiment_engine import SentimentEngine


# Two contracts + one MICRO variant that must NOT match + one unrelated.
FIXTURE = "\n".join([
    '"EURO FX - CHICAGO MERCANTILE EXCHANGE",260706,"2026-07-06",099741,00,0,099,'
    "650000,220000,120000,15000,300000,410000,0,0,0,0",
    '"BITCOIN - CHICAGO MERCANTILE EXCHANGE",260706,"2026-07-06",133741,00,0,133,'
    "30000,12000,9000,500,8000,11000,0,0,0,0",
    '"MICRO BITCOIN - CHICAGO MERCANTILE EXCHANGE",260706,"2026-07-06",133742,00,0,133,'
    "20000,9000,2000,100,5000,9000,0,0,0,0",
    '"WHEAT-SRW - CHICAGO BOARD OF TRADE",260706,"2026-07-06",001602,00,0,001,'
    "400000,90000,110000,50000,150000,120000,0,0,0,0",
])


def test_parse_extracts_net_positions_and_excludes_micro():
    parsed = parse_cot_text(FIXTURE)
    assert parsed["EURUSD"]["large_spec_net"] == 100_000        # 220k − 120k
    assert parsed["BTCUSD"]["large_spec_net"] == 3_000          # 12k − 9k
    assert parsed["BTCUSD"]["market"].startswith("BITCOIN")     # not MICRO
    assert "USOIL" not in parsed                                # absent from file


def test_parse_sanity_check_rejects_layout_drift():
    # Positions exceeding open interest = column drift → row skipped.
    bad = ('"EURO FX - CHICAGO MERCANTILE EXCHANGE",260706,"2026-07-06",099741,00,0,099,'
           "1000,220000,120000,0,0,0")
    assert parse_cot_text(bad) == {}


def test_update_caches_builds_history_and_4w_change(tmp_path, monkeypatch):
    monkeypatch.setenv("IATIS_COT_DIR", str(tmp_path))
    now = time.time()

    week1 = parse_cot_text(FIXTURE)
    update_caches(week1, now=now - 28 * 86400)

    week5 = dict(week1)
    week5["EURUSD"] = dict(week1["EURUSD"],
                           report_date="2026-08-03", large_spec_net=130_000)
    update_caches(week5, now=now)

    data = json.loads((tmp_path / "EURUSD.json").read_text())
    assert data["large_spec_net"] == 130_000
    assert data["net_change_4w"] == 30_000          # vs the 4-week-old baseline
    assert len(data["history"]) == 2


def test_sentiment_engine_consumes_real_cot(tmp_path, monkeypatch):
    monkeypatch.setenv("IATIS_COT_DIR", str(tmp_path))
    now = time.time()
    update_caches(parse_cot_text(FIXTURE), now=now - 28 * 86400)
    week5 = parse_cot_text(FIXTURE)
    week5["EURUSD"] = dict(week5["EURUSD"],
                           report_date="2026-08-03", large_spec_net=130_000)
    update_caches(week5, now=now)

    idx = pd.date_range("2026-07-01", periods=120, freq="4h", tz="UTC")
    close = pd.Series(range(120), index=idx) * 0.0001 + 1.08
    df = pd.DataFrame({"open": close, "high": close + 0.001,
                       "low": close - 0.001, "close": close,
                       "volume": 0.0}, index=idx)

    eng = SentimentEngine()
    eng.decision_tf = "H4"
    eng._symbol = "EURUSD"
    out = eng.analyze({"H4": df})

    assert out.raw["cot_available"] is True
    assert out.bias.value == "BULLISH"              # net long + accumulating
    assert any("COT" in r for r in out.reasons)


def test_sentiment_falls_back_to_proxy_without_cot(tmp_path, monkeypatch):
    monkeypatch.setenv("IATIS_COT_DIR", str(tmp_path))   # empty dir
    idx = pd.date_range("2026-07-01", periods=120, freq="4h", tz="UTC")
    close = pd.Series([1.08] * 120, index=idx)
    df = pd.DataFrame({"open": close, "high": close + 0.001,
                       "low": close - 0.001, "close": close,
                       "volume": 0.0}, index=idx)
    eng = SentimentEngine()
    eng.decision_tf = "H4"
    eng._symbol = "EURUSD"
    out = eng.analyze({"H4": df})
    assert out.raw["cot_available"] is False
