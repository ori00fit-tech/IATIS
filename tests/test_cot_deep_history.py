"""
tests/test_cot_deep_history.py
--------------------------------
scripts/download_cot_deep_history.py (H012 deep-archive backfill). No
network — synthetic zip/text fixtures mirror the deafut.txt column
layout (see tests/test_cot_download.py's FIXTURE) extended across
multiple weekly report dates per contract, the way one year's real
deacotYYYY.zip archive actually looks.
"""
from __future__ import annotations

import io
import json
import zipfile

from scripts.download_cot_deep_history import (
    EARLIEST_YEAR,
    extract_annual_text,
    fetch_year_zip,
    merge_into_history,
    parse_year,
    probe,
    run,
    write_deep_history,
)


def _row(name: str, date_ymd: str, date_iso: str, oi: int, nc_long: int, nc_short: int) -> str:
    return (f'"{name}",{date_ymd},"{date_iso}",099741,00,0,099,'
            f"{oi},{nc_long},{nc_short},15000,300000,410000,0,0,0,0")


# Three weekly reports for EURUSD + one for BTCUSD — a miniature "annual.txt".
YEAR_FIXTURE = "\n".join([
    _row("EURO FX - CHICAGO MERCANTILE EXCHANGE", 260103, "2026-01-03", 650000, 200000, 120000),
    _row("EURO FX - CHICAGO MERCANTILE EXCHANGE", 260110, "2026-01-10", 655000, 210000, 118000),
    _row("EURO FX - CHICAGO MERCANTILE EXCHANGE", 260117, "2026-01-17", 660000, 205000, 125000),
    _row("BITCOIN - CHICAGO MERCANTILE EXCHANGE", 260103, "2026-01-03", 30000, 12000, 9000),
])


def _zip_bytes(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ── parse_year ──

def test_parse_year_accumulates_multiple_weeks_per_symbol():
    parsed = parse_year(YEAR_FIXTURE)
    assert len(parsed["EURUSD"]) == 3
    assert len(parsed["BTCUSD"]) == 1
    dates = sorted(r["report_date"] for r in parsed["EURUSD"])
    assert dates == ["2026-01-03", "2026-01-10", "2026-01-17"]


def test_parse_year_still_excludes_micro_and_unmatched():
    text = YEAR_FIXTURE + "\n" + _row("MICRO BITCOIN - CHICAGO MERCANTILE EXCHANGE",
                                       260103, "2026-01-03", 20000, 9000, 2000)
    parsed = parse_year(text)
    assert all(not r["market"].startswith("MICRO") for r in parsed["BTCUSD"])
    assert "USOIL" not in parsed


# ── merge_into_history ──

def test_merge_into_history_sorts_chronologically():
    unsorted = {"EURUSD": [
        {"report_date": "2026-01-17", "large_spec_net": 1},
        {"report_date": "2026-01-03", "large_spec_net": 2},
        {"report_date": "2026-01-10", "large_spec_net": 3},
    ]}
    merged = merge_into_history(unsorted)
    assert [r["report_date"] for r in merged["EURUSD"]] == ["2026-01-03", "2026-01-10", "2026-01-17"]


def test_merge_into_history_dedupes_by_report_date_last_wins():
    dup = {"EURUSD": [
        {"report_date": "2026-01-03", "large_spec_net": 1},
        {"report_date": "2026-01-03", "large_spec_net": 999},
    ]}
    merged = merge_into_history(dup)
    assert len(merged["EURUSD"]) == 1
    assert merged["EURUSD"][0]["large_spec_net"] == 999


def test_merge_into_history_handles_empty_symbol():
    merged = merge_into_history({"EURUSD": []})
    assert merged["EURUSD"] == []


# ── extract_annual_text ──

def test_extract_annual_text_single_txt_member():
    z = _zip_bytes({"annual.txt": YEAR_FIXTURE})
    text = extract_annual_text(z)
    assert text == YEAR_FIXTURE


def test_extract_annual_text_rejects_zero_txt_members():
    z = _zip_bytes({"readme.pdf": "not a data file"})
    assert extract_annual_text(z) is None


def test_extract_annual_text_rejects_multiple_txt_members():
    z = _zip_bytes({"a.txt": "x", "b.txt": "y"})
    assert extract_annual_text(z) is None


def test_extract_annual_text_rejects_bad_zip_bytes():
    assert extract_annual_text(b"not a zip file at all") is None


# ── fetch_year_zip ──

def test_fetch_year_zip_returns_none_on_404(monkeypatch):
    import urllib.error
    import urllib.request

    def fake_urlopen(*a, **kw):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert fetch_year_zip(1900) is None


def test_fetch_year_zip_returns_none_on_generic_failure(monkeypatch):
    import urllib.request

    def fake_urlopen(*a, **kw):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert fetch_year_zip(2026) is None


# ── run() orchestration (fetch_year_zip mocked, no network) ──

def test_run_stops_after_two_consecutive_missing_years(monkeypatch):
    calls = []

    def fake_fetch(year):
        calls.append(year)
        return None  # every year "missing"

    monkeypatch.setattr("scripts.download_cot_deep_history.fetch_year_zip", fake_fetch)
    monkeypatch.setattr("scripts.download_cot_deep_history.REQUEST_SLEEP_SEC", 0)
    run(2000, 2026, symbols=["EURUSD"])
    # Should give up after 2 consecutive misses, not grind through all 27 years.
    assert len(calls) <= 3


def test_run_collects_and_merges_across_years(monkeypatch):
    year2_fixture = _row("EURO FX - CHICAGO MERCANTILE EXCHANGE", 260703, "2026-07-03", 700000, 250000, 100000)

    def fake_fetch(year):
        if year == 2026:
            return _zip_bytes({"annual.txt": YEAR_FIXTURE})
        if year == 2027:
            return _zip_bytes({"annual.txt": year2_fixture})
        return None

    monkeypatch.setattr("scripts.download_cot_deep_history.fetch_year_zip", fake_fetch)
    monkeypatch.setattr("scripts.download_cot_deep_history.REQUEST_SLEEP_SEC", 0)
    merged = run(2026, 2027, symbols=["EURUSD", "BTCUSD"])
    assert len(merged["EURUSD"]) == 4  # 3 weeks from 2026 + 1 from 2027
    assert merged["EURUSD"][-1]["report_date"] == "2026-07-03"
    assert len(merged["BTCUSD"]) == 1


# ── write_deep_history ──

def test_write_deep_history_writes_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("IATIS_COT_DIR", str(tmp_path))
    merged = {"EURUSD": [
        {"report_date": "2026-01-03", "large_spec_net": 80000, "market": "EURO FX", "open_interest": 650000,
         "large_spec_long": 200000, "large_spec_short": 120000},
    ]}
    written = write_deep_history(merged)
    assert written == ["EURUSD"]
    payload = json.loads((tmp_path / "EURUSD_deep_history.json").read_text())
    assert payload["n_records"] == 1
    assert payload["date_range"] == ["2026-01-03", "2026-01-03"]
    assert payload["source"] == "CFTC yearly archive (deacotYYYY.zip)"
    assert len(payload["history"]) == 1


def test_write_deep_history_does_not_touch_live_rolling_cache_file(tmp_path, monkeypatch):
    monkeypatch.setenv("IATIS_COT_DIR", str(tmp_path))
    write_deep_history({"EURUSD": [{"report_date": "2026-01-03", "large_spec_net": 1}]})
    assert not (tmp_path / "EURUSD.json").exists()
    assert (tmp_path / "EURUSD_deep_history.json").exists()


# ── probe() ──

def test_probe_ok_on_valid_year(monkeypatch, capsys):
    monkeypatch.setattr("scripts.download_cot_deep_history.fetch_year_zip",
                         lambda year: _zip_bytes({"annual.txt": YEAR_FIXTURE}))
    rc = probe(2026)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out
    assert "EURUSD" in out


def test_probe_fails_when_year_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("scripts.download_cot_deep_history.fetch_year_zip", lambda year: None)
    rc = probe(1900)
    assert rc == 1
    assert "FAILED" in capsys.readouterr().out


def test_probe_fails_when_zero_symbols_matched(monkeypatch, capsys):
    unrelated = _row("WHEAT-SRW - CHICAGO BOARD OF TRADE", 260103, "2026-01-03", 400000, 90000, 110000)
    monkeypatch.setattr("scripts.download_cot_deep_history.fetch_year_zip",
                         lambda year: _zip_bytes({"annual.txt": unrelated}))
    rc = probe(2026)
    assert rc == 1
    assert "FAILED" in capsys.readouterr().out


def test_earliest_year_constant_matches_cftc_documented_start():
    assert EARLIEST_YEAR == 1986
