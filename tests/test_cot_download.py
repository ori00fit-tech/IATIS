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

from scripts.download_cot import iter_cot_rows, parse_cot_text, update_caches
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


# ── Cross-rate / alt-venue contamination regression (found 2026-07-24 via
# a real CFTC yearly-archive probe, H012/registry.json: EURUSD returned
# 120 rows/year instead of ~52 because a bare startswith() also matched
# CFTC's separately-listed cross-rate and alt-exchange contracts). ──

def _row(name: str, oi: int, nc_long: int, nc_short: int) -> str:
    return (f'"{name}",260706,"2026-07-06",099741,00,0,099,'
            f"{oi},{nc_long},{nc_short},15000,300000,410000,0,0,0,0")


def test_parse_excludes_eur_cross_rate_contracts():
    text = "\n".join([
        _row("EURO FX - CHICAGO MERCANTILE EXCHANGE", 650000, 220000, 120000),
        _row("EURO FX/BRITISH POUND XRATE - CHICAGO MERCANTILE EXCHANGE", 50000, 10000, 8000),
        _row("EURO FX/JAPANESE YEN XRATE - CHICAGO MERCANTILE EXCHANGE", 40000, 9000, 7000),
    ])
    matched = [rec["market"] for _, rec in iter_cot_rows(text) if _ == "EURUSD"]
    assert matched == ["EURO FX - CHICAGO MERCANTILE EXCHANGE"]
    parsed = parse_cot_text(text)
    assert parsed["EURUSD"]["large_spec_net"] == 100_000  # 220k − 120k, the real contract only


def test_parse_excludes_unrelated_gold_contract_sharing_a_prefix():
    text = "\n".join([
        _row("GOLD - COMMODITY EXCHANGE INC.", 500000, 150000, 100000),
        _row("GOLD -1 TROY OUNCE - COINBASE DERIVATIVES, LLC", 1000, 300, 200),
    ])
    matched = [rec["market"] for _, rec in iter_cot_rows(text) if _ == "XAUUSD"]
    assert matched == ["GOLD - COMMODITY EXCHANGE INC."]


def test_iter_cot_rows_requires_the_dash_delimiter_not_a_bare_prefix():
    # A market name that merely starts with the contract text but has no
    # ' - ' delimiter right after it must not match at all.
    text = _row("EURO FXTRA WEIRD CONTRACT NAME - SOME EXCHANGE", 1000, 100, 50)
    assert list(iter_cot_rows(text)) == []


# ── Renamed CFTC contracts, found via real archive probes (H012/
# registry.json, 2026-07-24). A 2025-only probe first found NZDUSD
# matching zero rows under "NEW ZEALAND DOLLAR" and "NZ DOLLAR" looked
# like the fix — but a full 1986-present backfill then showed
# GBPUSD/NZDUSD's history silently truncated to 2022-02-08 onward: CFTC
# renamed both contracts around then ("BRITISH POUND STERLING" ->
# "BRITISH POUND", "NEW ZEALAND DOLLAR" -> "NZ DOLLAR"). COT_SYMBOLS now
# maps each symbol to a tuple of accepted aliases so BOTH eras match.
# USOIL's old name ("CRUDE OIL, LIGHT SWEET") only bare-prefix-matched a
# DIFFERENT, unintended contract at ICE Futures Europe — the real NYMEX
# WTI contract is listed as "WTI FINANCIAL CRUDE OIL". ──

def test_nzdusd_matches_both_the_current_and_pre_2022_contract_name():
    current = _row("NZ DOLLAR - CHICAGO MERCANTILE EXCHANGE", 40000, 9000, 7000)
    pre_2022 = _row("NEW ZEALAND DOLLAR - CHICAGO MERCANTILE EXCHANGE", 30000, 5000, 4000)
    parsed_current = parse_cot_text(current)
    parsed_pre_2022 = parse_cot_text(pre_2022)
    assert parsed_current["NZDUSD"]["large_spec_net"] == 2_000
    assert parsed_pre_2022["NZDUSD"]["large_spec_net"] == 1_000


def test_gbpusd_matches_both_the_current_and_pre_2022_contract_name():
    current = _row("BRITISH POUND - CHICAGO MERCANTILE EXCHANGE", 80000, 30000, 20000)
    pre_2022 = _row("BRITISH POUND STERLING - CHICAGO MERCANTILE EXCHANGE", 70000, 25000, 15000)
    parsed_current = parse_cot_text(current)
    parsed_pre_2022 = parse_cot_text(pre_2022)
    assert parsed_current["GBPUSD"]["large_spec_net"] == 10_000
    assert parsed_pre_2022["GBPUSD"]["large_spec_net"] == 10_000


def test_usoil_matches_the_nymex_contract_not_the_ice_europe_one():
    text = "\n".join([
        _row("WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE", 900000, 300000, 250000),
        _row("CRUDE OIL, LIGHT SWEET-WTI - ICE FUTURES EUROPE", 200000, 60000, 55000),
    ])
    parsed = parse_cot_text(text)
    assert parsed["USOIL"]["market"] == "WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE"
    assert parsed["USOIL"]["large_spec_net"] == 50_000


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
