"""
storage/market_bars.py
--------------------------
Canonical D1-backed historical OHLCV warehouse — the operator's own
explicit design (2026-08-11): one unified `market_bars` fact table
across every symbol/timeframe (never a per-symbol/per-timeframe table),
keyed so a re-push is idempotent, plus a `dataset_manifest` table
recording per-(symbol, timeframe) data-quality state so a caller (e.g.
Mission Center) can check `is_ready()` BEFORE trusting a dataset for
research, rather than discovering a data artifact after the fact.

Same `_DDL` + `_init(con)` idiom as storage/provider_benchmark.py:
idempotent `CREATE TABLE IF NOT EXISTS`, called at the top of every
public function. No storage/migrations.py entry needed (brand-new
tables — migrations are ALTER-only, per every other storage/*.py
module's own precedent).

Scope, per the operator's own explicit sequencing instruction: this
module is the STORAGE layer only — write bars, read bars back as a
DataFrame, and compute/track per-dataset quality state. It does NOT
wire into backtest/runner.py or Mission Center yet; that "native D1
read path, loaded once per Mission and reused across every trial" is a
deliberate, separate follow-up phase, built only after the 24-symbol x
4-timeframe warehouse here is populated and every dataset's manifest
says READY.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from storage import d1_client

_DDL_BARS = """
CREATE TABLE IF NOT EXISTS market_bars (
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    timestamp   INTEGER NOT NULL,   -- epoch seconds, UTC
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL DEFAULT 0,
    source      TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe, timestamp)
)
"""

_DDL_MANIFEST = """
CREATE TABLE IF NOT EXISTS dataset_manifest (
    symbol              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    source              TEXT,
    start_ts            INTEGER,
    end_ts              INTEGER,
    row_count           INTEGER NOT NULL DEFAULT 0,
    expected_rows       INTEGER,
    coverage_pct        REAL,
    gap_count           INTEGER,
    duplicate_count     INTEGER,
    invalid_ohlc_count  INTEGER,
    timezone            TEXT NOT NULL DEFAULT 'UTC',
    status              TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|READY|INCOMPLETE|INVALID|EMPTY
    error               TEXT,
    checksum            TEXT,
    last_updated        TEXT,
    PRIMARY KEY (symbol, timeframe)
)
"""

# A dataset below this real completeness_score (backtest.price_benchmark's
# own session-aware gap classification — expected weekly/session closures
# never count against it) is INCOMPLETE, not READY. 95% is a deliberately
# real, checkable bar, not a rubber stamp — an operator running Mission
# Center research against a dataset with 20% real (non-closure) gaps would
# be measuring an edge partly made of data artifacts.
MIN_COVERAGE_PCT_FOR_READY = 95.0

# 2026-08-11 — confirmed live on the VPS: batch_rows=100 (900 bound
# params/statement, 9 params/row) crashed every real push with
# "D1_ERROR: too many SQL variables at offset 376". The original
# comment's assumption (SQLite's usual 999/32766-param ceiling) was
# wrong for Cloudflare D1 specifically — D1's real, documented limit is
# 100 TOTAL bound parameters per statement, independent of the
# underlying SQLite engine's own default. At 9 params/row that's a
# maximum of 11 rows/statement; 10 leaves a safety margin.
_INSERT_BATCH_ROWS_DEFAULT = 10


def _init(con) -> None:
    con.execute(_DDL_BARS)
    con.execute(_DDL_MANIFEST)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_epoch_seconds(idx: pd.DatetimeIndex) -> list[int]:
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    return (idx.astype("int64") // 1_000_000_000).tolist()


def _to_epoch_second(ts) -> int:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return int(t.timestamp())


def upsert_bars(symbol: str, timeframe: str, df: pd.DataFrame, source: str,
                batch_rows: int = _INSERT_BATCH_ROWS_DEFAULT) -> int:
    """Idempotent bulk write: INSERT OR REPLACE so re-pushing the same
    (symbol, timeframe, timestamp) — e.g. a re-run of the download
    orchestrator, or backfilling more recent bars — updates in place
    rather than erroring on the primary key or silently duplicating.
    Batches multiple rows per statement (one D1 HTTP round-trip per
    batch, not per row — pushing years of M15 history one row at a time
    would be thousands of requests). Returns the number of rows written."""
    if df.empty:
        return 0
    timestamps = _to_epoch_seconds(df.index)
    rows = list(zip(
        timestamps, df["open"].tolist(), df["high"].tolist(), df["low"].tolist(),
        df["close"].tolist(), df["volume"].tolist(),
    ))
    written = 0
    with d1_client.d1_connection() as con:
        _init(con)
        for i in range(0, len(rows), batch_rows):
            chunk = rows[i:i + batch_rows]
            placeholders = ",".join(["(?,?,?,?,?,?,?,?,?)"] * len(chunk))
            params: list[Any] = []
            for ts, o, h, low, c, vol in chunk:
                params.extend([symbol, timeframe, ts, o, h, low, c, vol, source])
            con.execute(
                f"""INSERT OR REPLACE INTO market_bars
                    (symbol, timeframe, timestamp, open, high, low, close, volume, source)
                    VALUES {placeholders}""",
                tuple(params),
            )
            written += len(chunk)
    return written


def load_bars(symbol: str, timeframe: str, start=None, end=None) -> pd.DataFrame:
    """Reads bars back as a DataFrame shaped exactly like
    backtest.runner.load_symbol_data's CSV-sourced output — UTC
    DatetimeIndex named 'datetime', columns open/high/low/close/volume,
    sorted ascending — so a future loader integration is a drop-in, not
    a reshape."""
    query = "SELECT timestamp, open, high, low, close, volume FROM market_bars WHERE symbol=? AND timeframe=?"
    params: list[Any] = [symbol, timeframe]
    if start is not None:
        query += " AND timestamp >= ?"
        params.append(_to_epoch_second(start))
    if end is not None:
        query += " AND timestamp < ?"
        params.append(_to_epoch_second(end))
    query += " ORDER BY timestamp ASC"
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame([{k: r[k] for k in r.keys()} for r in rows])
    df.index = pd.to_datetime(df.pop("timestamp"), unit="s", utc=True)
    df.index.name = "datetime"
    return df[["open", "high", "low", "close", "volume"]]


def bar_count(symbol: str, timeframe: str) -> int:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT COUNT(*) AS n FROM market_bars WHERE symbol=? AND timeframe=?", (symbol, timeframe)
        ).fetchone()
    return int(row["n"]) if row else 0


def bar_range(symbol: str, timeframe: str) -> tuple[int | None, int | None]:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT MIN(timestamp) AS lo, MAX(timestamp) AS hi FROM market_bars WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
    if row is None or row["lo"] is None:
        return None, None
    return int(row["lo"]), int(row["hi"])


def _status_for(row_count: int, ohlc_valid: bool, coverage_pct: float | None) -> str:
    if row_count == 0:
        return "EMPTY"
    if not ohlc_valid:
        return "INVALID"
    if coverage_pct is None or coverage_pct < MIN_COVERAGE_PCT_FOR_READY:
        return "INCOMPLETE"
    return "READY"


def compute_manifest(symbol: str, timeframe: str, source: str, asset_class: str) -> dict[str, Any]:
    """Reloads the symbol/timeframe's bars straight back out of D1 (the
    real, stored state — never trusts the in-memory frame that was just
    pushed, since the point of a manifest is to certify what's actually
    IN the warehouse) and runs the same correctness tooling already
    built this session: core.data_validator.validate_ohlcv (structural
    integrity) and backtest.price_benchmark.classify_gaps/completeness_score
    (session-aware gap classification, so a real weekly/session closure
    is never counted against coverage). Computes but does not persist —
    call upsert_manifest() with the result to store it."""
    from core.data_validator import DataValidationError, validate_ohlcv
    from backtest.price_benchmark import completeness_score

    df = load_bars(symbol, timeframe)
    row_count = len(df)
    manifest: dict[str, Any] = {
        "symbol": symbol, "timeframe": timeframe, "source": source,
        "row_count": row_count, "timezone": "UTC",
    }

    if row_count == 0:
        manifest.update(
            start_ts=None, end_ts=None, expected_rows=0, coverage_pct=None,
            gap_count=None, duplicate_count=0, invalid_ohlc_count=0,
            error="no bars stored for this (symbol, timeframe)",
            status="EMPTY",
        )
        return manifest

    start_ts, end_ts = bar_range(symbol, timeframe)
    duplicate_count = int(df.index.duplicated().sum())  # always 0 by PK construction — computed for schema honesty

    try:
        validate_ohlcv(df)
        ohlc_valid, invalid_count, error = True, 0, None
    except DataValidationError as exc:
        ohlc_valid, invalid_count, error = False, row_count, str(exc)

    if ohlc_valid:
        comp_score, comp_detail = completeness_score(df, timeframe, asset_class)
        gap_count = comp_detail.get("real_gap")
        expected_rows = row_count + (gap_count or 0)
    else:
        comp_score, gap_count, expected_rows = None, None, None

    manifest.update(
        start_ts=start_ts, end_ts=end_ts, expected_rows=expected_rows,
        coverage_pct=comp_score, gap_count=gap_count,
        duplicate_count=duplicate_count, invalid_ohlc_count=invalid_count,
        error=error, status=_status_for(row_count, ohlc_valid, comp_score),
    )
    return manifest


def upsert_manifest(manifest: dict[str, Any], checksum: str | None = None) -> None:
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO dataset_manifest
               (symbol, timeframe, source, start_ts, end_ts, row_count, expected_rows,
                coverage_pct, gap_count, duplicate_count, invalid_ohlc_count, timezone,
                status, error, checksum, last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol, timeframe) DO UPDATE SET
                   source=excluded.source, start_ts=excluded.start_ts, end_ts=excluded.end_ts,
                   row_count=excluded.row_count, expected_rows=excluded.expected_rows,
                   coverage_pct=excluded.coverage_pct, gap_count=excluded.gap_count,
                   duplicate_count=excluded.duplicate_count, invalid_ohlc_count=excluded.invalid_ohlc_count,
                   timezone=excluded.timezone, status=excluded.status, error=excluded.error,
                   checksum=excluded.checksum, last_updated=excluded.last_updated""",
            (manifest["symbol"], manifest["timeframe"], manifest.get("source"),
             manifest.get("start_ts"), manifest.get("end_ts"), manifest.get("row_count", 0),
             manifest.get("expected_rows"), manifest.get("coverage_pct"), manifest.get("gap_count"),
             manifest.get("duplicate_count"), manifest.get("invalid_ohlc_count"),
             manifest.get("timezone", "UTC"), manifest.get("status", "PENDING"), manifest.get("error"),
             checksum, _now()),
        )


def get_manifest(symbol: str, timeframe: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM dataset_manifest WHERE symbol=? AND timeframe=?", (symbol, timeframe)
        ).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def list_manifests() -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute("SELECT * FROM dataset_manifest ORDER BY symbol, timeframe").fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def is_ready(symbol: str, timeframe: str) -> bool:
    m = get_manifest(symbol, timeframe)
    return bool(m and m.get("status") == "READY")


def _print_status_table() -> int:
    """`python -m storage.market_bars --status` — a real, operator-facing
    readiness table (mirrors storage/migrations.py's own --status CLI
    convention), so 'is the D1 warehouse actually ready for Mission
    Center research' is checkable directly against real D1 state,
    without a manual SQL query. Returns 0 if every listed dataset is
    READY, 1 otherwise (usable as a CI/deploy-step exit code)."""
    rows = list_manifests()
    if not rows:
        print("dataset_manifest is empty — nothing pushed yet (scripts/push_bars_to_d1.py).")
        return 1
    header = f"{'SYMBOL':10s} {'TF':5s} {'STATUS':11s} {'ROWS':>10s} {'COVERAGE':>9s} {'SOURCE':20s} {'LAST_UPDATED'}"
    print(header)
    print("-" * len(header))
    all_ready = True
    for r in rows:
        if r["status"] != "READY":
            all_ready = False
        coverage = f"{r['coverage_pct']:.1f}%" if r.get("coverage_pct") is not None else "—"
        print(f"{r['symbol']:10s} {r['timeframe']:5s} {r['status']:11s} {r['row_count']:>10d} "
              f"{coverage:>9s} {(r.get('source') or ''):20s} {r.get('last_updated') or ''}")
    n_ready = sum(1 for r in rows if r["status"] == "READY")
    print("-" * len(header))
    print(f"{n_ready}/{len(rows)} READY")
    return 0 if all_ready else 1


if __name__ == "__main__":
    import sys
    from pathlib import Path

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent

    try:
        from dotenv import load_dotenv
        # Explicit path, not bare load_dotenv() — the no-arg form locates
        # .env by walking up from the CALLING file's own directory
        # (stack-frame inspection, not os.getcwd()), which is ambiguous
        # depending on how this module is invoked. Anchoring to the repo
        # root removes that ambiguity, but NOT a genuine OS permission
        # error (.env is 600, owned by the iatis service user) — that's
        # a real "wrong user" condition, caught explicitly below with an
        # actionable message instead of a raw traceback (confirmed live
        # on the VPS 2026-08-11 — see scripts/download_ctrader_fx_history.py
        # for the pattern this mirrors).
        load_dotenv(_PROJECT_ROOT / ".env")
    except ImportError:
        pass
    except PermissionError:
        raise SystemExit(
            f"Permission denied reading {_PROJECT_ROOT / '.env'} as the current user. "
            f".env is owned by the iatis service user (600) — run this as "
            f"that user instead:\n"
            f"  sudo -u iatis {sys.executable} -m storage.market_bars ..."
        )

    if "--status" in sys.argv:
        raise SystemExit(_print_status_table())
    print(__doc__)
    print("\nUsage: python -m storage.market_bars --status")
