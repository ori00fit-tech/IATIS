"""
tests/test_market_bars_storage.py
------------------------------------
Round-trip tests for storage/market_bars.py — the D1-backed historical
OHLCV warehouse. tests/conftest.py's autouse fake_d1 fixture gives every
test an isolated, in-memory D1 stand-in (real SQL semantics, faked
transport).
"""
from __future__ import annotations

import runpy
import sys
from unittest.mock import patch

import pandas as pd
import pytest

from storage import market_bars


# ---------------------------------------------------------------------------
# CLI (__main__ block): .env PermissionError -> actionable SystemExit, not a
# raw traceback (2026-08-11 — confirmed live on the VPS: this crashed with
# the raw PermissionError traceback instead of the friendly message
# scripts/download_ctrader_fx_history.py already has). The CLI block only
# runs under `if __name__ == "__main__":`, so it's exercised here via
# runpy.run_module(..., run_name="__main__"), which re-executes the module
# fresh without touching the already-imported storage.market_bars in
# sys.modules.
# ---------------------------------------------------------------------------


def test_cli_reports_actionable_message_on_env_permission_error(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_bars.py", "--status"])
    with patch("dotenv.load_dotenv", side_effect=PermissionError("denied")):
        with pytest.raises(SystemExit, match="sudo -u iatis"):
            runpy.run_module("storage.market_bars", run_name="__main__")


def _ohlcv(n: int = 10, start: str = "2024-01-02", freq: str = "15min", seed: float = 1.10) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": [seed + 0.0001 * i for i in range(n)],
         "high": [seed + 0.0002 * i + 0.0001 for i in range(n)],
         "low": [seed + 0.0001 * i - 0.0001 for i in range(n)],
         "close": [seed + 0.0001 * i for i in range(n)],
         "volume": [10.0 + i for i in range(n)]},
        index=idx,
    )


# ---------------------------------------------------------------------------
# upsert_bars / load_bars round-trip
# ---------------------------------------------------------------------------


def test_upsert_and_load_bars_round_trip():
    df = _ohlcv(5)
    written = market_bars.upsert_bars("EURUSD", "M15", df, source="dukascopy")
    assert written == 5

    loaded = market_bars.load_bars("EURUSD", "M15")
    assert len(loaded) == 5
    assert list(loaded.columns) == ["open", "high", "low", "close", "volume"]
    assert loaded.index.tz is not None
    assert loaded.index.is_monotonic_increasing
    pd.testing.assert_series_equal(loaded["close"], df["close"], check_names=False, check_freq=False)


def test_load_bars_returns_empty_frame_with_expected_columns_when_nothing_stored():
    loaded = market_bars.load_bars("NOTHING_HERE", "M15")
    assert loaded.empty
    assert list(loaded.columns) == ["open", "high", "low", "close", "volume"]


def test_upsert_bars_is_idempotent_on_replay():
    df = _ohlcv(5)
    market_bars.upsert_bars("GBPUSD", "H1", df, source="ctrader")
    market_bars.upsert_bars("GBPUSD", "H1", df, source="ctrader")  # same rows, re-pushed
    assert market_bars.bar_count("GBPUSD", "H1") == 5  # not 10 — INSERT OR REPLACE, not duplicated


def test_upsert_bars_updates_values_on_replay_with_different_source():
    df1 = _ohlcv(3, seed=1.0)
    df2 = _ohlcv(3, seed=2.0)  # same timestamps, different prices — a real re-fetch scenario
    market_bars.upsert_bars("USDJPY", "H1", df1, source="dukascopy")
    market_bars.upsert_bars("USDJPY", "H1", df2, source="twelve_data")
    loaded = market_bars.load_bars("USDJPY", "H1")
    assert len(loaded) == 3
    assert loaded["close"].iloc[0] == pytest.approx(2.0)  # overwritten, not appended


def test_upsert_bars_empty_dataframe_writes_nothing():
    written = market_bars.upsert_bars("EURUSD", "M15", pd.DataFrame(), source="dukascopy")
    assert written == 0
    assert market_bars.bar_count("EURUSD", "M15") == 0


def test_upsert_bars_batches_large_pushes_without_dropping_rows():
    # batch_rows=9 * 9 params/row = 81 bound params/statement — real,
    # confirmed-live D1 ceiling is 100 total (tests/conftest.py's fake_d1
    # now enforces this; the pre-2026-08-11 default of 100 rows/statement
    # = 900 params crashed every real push with "too many SQL variables").
    df = _ohlcv(250)  # forces multiple batches
    written = market_bars.upsert_bars("XAUUSD", "M15", df, source="dukascopy", batch_rows=9)
    assert written == 250
    assert market_bars.bar_count("XAUUSD", "M15") == 250


def test_upsert_bars_default_batch_size_respects_d1s_real_param_ceiling():
    """Drift guard (2026-08-11): market_bars.py has 9 bound params per
    row (symbol, timeframe, timestamp, o, h, l, c, volume, source) — the
    PRODUCTION default batch_rows must keep 9*batch_rows under D1's real,
    confirmed-live 100-param ceiling, with margin. Fails loudly if
    someone ever bumps _INSERT_BATCH_ROWS_DEFAULT back up without
    re-deriving this math."""
    assert market_bars._INSERT_BATCH_ROWS_DEFAULT * 9 <= 100


def test_upsert_bars_uses_d1_batch_to_collapse_http_round_trips(monkeypatch):
    """Regression pin for the 2026-08-18 'push: timeout' fix (Trusted
    Data Center Warehouse push badge) — before this fix, upsert_bars()
    made one HTTP round-trip PER batch_rows-sized INSERT statement,
    which for a real multi-thousand-row symbol blew past push_to_
    warehouse's own 900s job timeout. Now it groups statements_per_batch
    of those INSERTs into d1_client.d1_batch() calls (Cloudflare D1's
    own env.DB.batch(), one Worker invocation per group). Wraps the
    already-faked transport (tests/conftest.py's fake_d1) to count real
    POST calls without re-faking the D1 semantics."""
    import storage.d1_client as d1_client_module

    original_post = d1_client_module._post
    call_count = {"n": 0}

    def counting_post(*args, **kwargs):
        call_count["n"] += 1
        return original_post(*args, **kwargs)

    monkeypatch.setattr(d1_client_module, "_post", counting_post)

    df = _ohlcv(1000)  # default batch_rows=10 -> 100 INSERT statements
    written = market_bars.upsert_bars("EURUSD", "M15", df, source="dukascopy")
    assert written == 1000
    assert market_bars.bar_count("EURUSD", "M15") == 1000
    # 100 statements / statements_per_batch=50 -> 2 batch POSTs, plus 2
    # more for _init()'s CREATE TABLE IF NOT EXISTS x2 -> 4 total. The
    # load-bearing assertion is "far fewer than 100 (one per statement)",
    # not the exact count, so this doesn't pin an unrelated implementation
    # detail of _init().
    assert call_count["n"] < 10


def test_upsert_bars_at_default_batch_size_works_against_the_real_d1_param_ceiling():
    # Exercises the PRODUCTION default (no explicit batch_rows override)
    # against fake_d1's enforced 100-param ceiling — the exact path that
    # crashed live before this fix, now proven to actually work end to
    # end, not just proven not to exceed the limit on paper.
    df = _ohlcv(250)
    written = market_bars.upsert_bars("XAUUSD", "M15", df, source="dukascopy")
    assert written == 250
    assert market_bars.bar_count("XAUUSD", "M15") == 250


def test_load_bars_respects_start_and_end_range():
    df = _ohlcv(10, start="2024-01-01", freq="1h")
    market_bars.upsert_bars("EURUSD", "H1", df, source="dukascopy")
    loaded = market_bars.load_bars("EURUSD", "H1", start="2024-01-01 03:00", end="2024-01-01 06:00")
    assert len(loaded) == 3  # 03:00, 04:00, 05:00 — end is exclusive
    assert loaded.index[0] == pd.Timestamp("2024-01-01 03:00", tz="UTC")


def test_different_symbols_and_timeframes_do_not_collide():
    market_bars.upsert_bars("EURUSD", "M15", _ohlcv(4), source="dukascopy")
    market_bars.upsert_bars("EURUSD", "H1", _ohlcv(3), source="dukascopy")
    market_bars.upsert_bars("XAUUSD", "M15", _ohlcv(2), source="ctrader")
    assert market_bars.bar_count("EURUSD", "M15") == 4
    assert market_bars.bar_count("EURUSD", "H1") == 3
    assert market_bars.bar_count("XAUUSD", "M15") == 2


def test_bar_range_reports_real_min_max_and_none_when_empty():
    assert market_bars.bar_range("NOTHING", "M15") == (None, None)
    df = _ohlcv(5, start="2024-06-01", freq="1h")
    market_bars.upsert_bars("EURUSD", "H1", df, source="dukascopy")
    lo, hi = market_bars.bar_range("EURUSD", "H1")
    assert lo == int(df.index[0].timestamp())
    assert hi == int(df.index[-1].timestamp())


# ---------------------------------------------------------------------------
# compute_manifest / upsert_manifest / get_manifest / is_ready
# ---------------------------------------------------------------------------


def test_compute_manifest_reports_empty_status_when_no_bars_stored():
    manifest = market_bars.compute_manifest("NOTHING_HERE", "M15", source="dukascopy", asset_class="fx_major")
    assert manifest["status"] == "EMPTY"
    assert manifest["row_count"] == 0
    assert manifest["error"]


def test_compute_manifest_reports_ready_for_clean_dense_data():
    # A dense, gapless intraday M15 series with no weekend/session in it —
    # completeness_score should read ~100, status READY.
    df = _ohlcv(50, start="2024-01-02 00:00", freq="15min")  # a Tuesday, no weekend crossed
    market_bars.upsert_bars("EURUSD", "M15", df, source="dukascopy")
    manifest = market_bars.compute_manifest("EURUSD", "M15", source="dukascopy", asset_class="fx_major")
    assert manifest["status"] == "READY"
    assert manifest["row_count"] == 50
    assert manifest["coverage_pct"] == pytest.approx(100.0)
    assert manifest["duplicate_count"] == 0
    assert manifest["invalid_ohlc_count"] == 0
    assert manifest["start_ts"] is not None and manifest["end_ts"] is not None


def test_compute_manifest_reports_invalid_on_structural_ohlc_violation():
    df = _ohlcv(10)
    df.loc[df.index[0], "high"] = df.loc[df.index[0], "low"] - 0.01  # break high >= low
    market_bars.upsert_bars("EURUSD", "M15", df, source="dukascopy")
    manifest = market_bars.compute_manifest("EURUSD", "M15", source="dukascopy", asset_class="fx_major")
    assert manifest["status"] == "INVALID"
    assert manifest["invalid_ohlc_count"] == 10
    assert manifest["error"]
    assert manifest["coverage_pct"] is None  # never fabricated once OHLC integrity already failed


def test_compute_manifest_reports_incomplete_below_coverage_threshold():
    # Real, non-weekend gaps: an H1 series missing every other hour on a
    # weekday is a genuine data-quality problem, not a market closure.
    idx = pd.to_datetime(
        [f"2024-01-02 {h:02d}:00" for h in range(0, 20, 2)], utc=True
    )  # every 2 hours instead of every 1 hour -> 50% real coverage
    df = pd.DataFrame(
        {"open": [1.1] * len(idx), "high": [1.11] * len(idx), "low": [1.09] * len(idx),
         "close": [1.1] * len(idx), "volume": [10.0] * len(idx)},
        index=idx,
    )
    market_bars.upsert_bars("EURUSD", "H1", df, source="dukascopy")
    manifest = market_bars.compute_manifest("EURUSD", "H1", source="dukascopy", asset_class="fx_major")
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["coverage_pct"] < market_bars.MIN_COVERAGE_PCT_FOR_READY
    assert manifest["gap_count"] > 0


def test_compute_manifest_crypto_asset_class_never_penalizes_weekend_gaps():
    # A crypto series with a real weekend gap must NOT be classified
    # incomplete the way an fx_major one legitimately would be — crypto
    # trades 24/7, so classify_gaps() must treat the whole gap as real,
    # while an fx_major series over the identical timestamps is exempted
    # by the weekly-closure classifier. This proves compute_manifest()
    # actually threads asset_class through to backtest.price_benchmark,
    # not a hardcoded assumption.
    idx = pd.to_datetime(["2024-01-05 20:00", "2024-01-08 02:00"], utc=True)  # spans a real weekend
    df = pd.DataFrame({"open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0],
                       "close": [1.0, 1.0], "volume": [1.0, 1.0]}, index=idx)
    market_bars.upsert_bars("BTCUSD", "H1", df, source="ctrader")
    manifest_crypto = market_bars.compute_manifest("BTCUSD", "H1", source="ctrader", asset_class="crypto")
    manifest_fx = market_bars.compute_manifest("BTCUSD", "H1", source="ctrader", asset_class="fx_major")
    assert manifest_crypto["gap_count"] > manifest_fx["gap_count"]


def test_upsert_manifest_and_get_manifest_round_trip():
    manifest = market_bars.compute_manifest("EURUSD", "M15", source="dukascopy", asset_class="fx_major")
    market_bars.upsert_manifest(manifest, checksum="abc123")
    stored = market_bars.get_manifest("EURUSD", "M15")
    assert stored is not None
    assert stored["status"] == manifest["status"]
    assert stored["checksum"] == "abc123"
    assert stored["last_updated"]


def test_upsert_manifest_is_idempotent_and_updates_in_place():
    df = _ohlcv(20, start="2024-01-02 00:00", freq="15min")
    market_bars.upsert_bars("EURUSD", "M15", df, source="dukascopy")
    m1 = market_bars.compute_manifest("EURUSD", "M15", source="dukascopy", asset_class="fx_major")
    market_bars.upsert_manifest(m1)

    # More bars arrive later — recompute and re-upsert must UPDATE, not duplicate.
    market_bars.upsert_bars(
        "EURUSD", "M15",
        _ohlcv(20, start="2024-01-02 05:00", freq="15min"),  # extends the series forward
        source="dukascopy",
    )
    m2 = market_bars.compute_manifest("EURUSD", "M15", source="dukascopy", asset_class="fx_major")
    market_bars.upsert_manifest(m2)

    all_manifests = market_bars.list_manifests()
    matching = [m for m in all_manifests if m["symbol"] == "EURUSD" and m["timeframe"] == "M15"]
    assert len(matching) == 1
    assert matching[0]["row_count"] == m2["row_count"]


def test_get_manifest_returns_none_when_never_computed():
    assert market_bars.get_manifest("NEVER_TOUCHED", "M15") is None


def test_list_manifests_covers_every_symbol_timeframe_pushed():
    for sym, tf in (("EURUSD", "M15"), ("EURUSD", "H1"), ("XAUUSD", "M15")):
        m = market_bars.compute_manifest(sym, tf, source="dukascopy", asset_class="fx_major")
        market_bars.upsert_manifest(m)
    rows = market_bars.list_manifests()
    pairs = {(r["symbol"], r["timeframe"]) for r in rows}
    assert pairs == {("EURUSD", "M15"), ("EURUSD", "H1"), ("XAUUSD", "M15")}


def test_is_ready_reflects_stored_manifest_status():
    assert market_bars.is_ready("EURUSD", "M15") is False  # never computed yet

    df = _ohlcv(50, start="2024-01-02 00:00", freq="15min")
    market_bars.upsert_bars("EURUSD", "M15", df, source="dukascopy")
    manifest = market_bars.compute_manifest("EURUSD", "M15", source="dukascopy", asset_class="fx_major")
    market_bars.upsert_manifest(manifest)
    assert market_bars.is_ready("EURUSD", "M15") is True


# ---------------------------------------------------------------------------
# --status CLI table
# ---------------------------------------------------------------------------


def test_status_table_returns_nonzero_when_empty(capsys):
    exit_code = market_bars._print_status_table()
    assert exit_code == 1
    assert "nothing pushed yet" in capsys.readouterr().out


def test_status_table_returns_zero_only_when_every_dataset_is_ready(capsys):
    df = _ohlcv(50, start="2024-01-02 00:00", freq="15min")
    market_bars.upsert_bars("EURUSD", "M15", df, source="dukascopy")
    market_bars.upsert_manifest(market_bars.compute_manifest("EURUSD", "M15", source="dukascopy", asset_class="fx_major"))
    assert market_bars._print_status_table() == 0

    # A second, incomplete dataset flips the overall exit code to non-zero.
    idx = pd.to_datetime([f"2024-01-02 {h:02d}:00" for h in range(0, 20, 2)], utc=True)
    sparse = pd.DataFrame({"open": [1.1] * len(idx), "high": [1.11] * len(idx), "low": [1.09] * len(idx),
                           "close": [1.1] * len(idx), "volume": [1.0] * len(idx)}, index=idx)
    market_bars.upsert_bars("GBPUSD", "H1", sparse, source="ctrader")
    market_bars.upsert_manifest(market_bars.compute_manifest("GBPUSD", "H1", source="ctrader", asset_class="fx_major"))

    exit_code = market_bars._print_status_table()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "EURUSD" in out and "GBPUSD" in out
    assert "1/2 READY" in out
