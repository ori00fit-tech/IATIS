"""
storage/d1_client.py
-----------------------
HTTP client for the Cloudflare D1 proxy Worker (cloudflare/worker.js).

D1 databases are only reachable from inside a Cloudflare Worker via a
binding — there is no way for this VPS-hosted Python process to talk
to D1 directly. This module calls a small Worker (deployed separately;
see cloudflare/README.md) that forwards parameterized SQL to its D1
binding and returns the rows as JSON.

This is an OPT-IN alternate backend for storage/*.py. The default
remains local SQLite — nothing changes unless IATIS_STORAGE_BACKEND=d1
is set. Every storage module's `_conn()` checks `is_d1_enabled()` and,
if true, uses `d1_connection()` from here instead of `sqlite3.connect()`.

Design goal: `D1Connection`/`D1Cursor`/`D1Row` mimic sqlite3's own
connection/cursor/row interface closely enough (`.execute(sql, params)`,
`.fetchone()`, `.fetchall()`, `.lastrowid`, row access by both
`row["col"]` and `row[0]`) that the SQL query strings throughout
storage/*.py never need to change — only the four `_conn()` functions
do.

Known limitation: each `execute()` call is its own independent HTTPS
request to the Worker, hence its own independent atomic D1 statement —
a sequence of several `execute()` calls within one
`with _conn() as con:` block is NOT atomic as a group the way a local
SQLite connection's commit/rollback is. Use `d1_batch()` (backed by the
Worker's POST /d1/batch, which calls D1's own `.batch()` API) for the
one call site where that matters: storage/decision_db.py's
log_decision_db(), which writes one decisions row plus N engine_votes
rows that must succeed or fail together.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 15.0


class D1Error(Exception):
    """Raised for a failed D1 proxy call. Callers that currently catch
    sqlite3.Error around a _conn() block should also catch this — see
    storage/decision_db.py and storage/experience_db.py for examples."""


def is_d1_enabled() -> bool:
    return os.environ.get("IATIS_STORAGE_BACKEND", "sqlite").strip().lower() == "d1"


def _worker_url() -> str:
    url = os.environ.get("D1_WORKER_URL", "").rstrip("/")
    if not url:
        raise D1Error(
            "IATIS_STORAGE_BACKEND=d1 but D1_WORKER_URL is not set. "
            "See cloudflare/README.md for setup."
        )
    return url


def _parse_worker_response(resp: "requests.Response") -> dict[str, Any]:
    """Parse the Worker's JSON body regardless of HTTP status.

    worker.js returns a JSON body with `success`/`error` even on a 500
    (its handlers catch D1 exceptions and report them as
    `{success: false, error: String(exc)}`) — calling
    resp.raise_for_status() before reading that body would discard the
    real error message and replace it with a generic "500 Server Error",
    which is exactly what happened here before this fix. Only fall back
    to the generic HTTP error if the body isn't valid JSON at all (e.g.
    a Cloudflare edge error page instead of a response from our Worker).
    """
    try:
        return resp.json()
    except ValueError:
        resp.raise_for_status()
        raise D1Error(f"D1 proxy returned a non-JSON response (HTTP {resp.status_code})")


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("D1_PROXY_TOKEN", "")
    if not token:
        raise D1Error(
            "IATIS_STORAGE_BACKEND=d1 but D1_PROXY_TOKEN is not set. "
            "See cloudflare/README.md for setup."
        )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class D1Row:
    """Mimics sqlite3.Row: supports row["col"] and row[0], and dict(row)."""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def __iter__(self) -> Iterator[Any]:
        return iter(self._data.values())

    def __repr__(self) -> str:
        return f"D1Row({self._data!r})"


class D1Cursor:
    """Mimics a sqlite3 cursor after execute() — rows are already fully
    fetched (an HTTP response is inherently all-at-once, no streaming)."""

    def __init__(self, rows: list[dict[str, Any]], last_row_id: int | None, changes: int) -> None:
        self._rows = [D1Row(r) for r in rows]
        self._pos = 0
        self.lastrowid = last_row_id
        self.rowcount = changes

    def fetchone(self) -> D1Row | None:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[D1Row]:
        remaining = self._rows[self._pos:]
        self._pos = len(self._rows)
        return remaining


class D1Connection:
    """Drop-in replacement for a sqlite3.Connection in the narrow way
    storage/*.py uses one: con.execute(sql, params) -> cursor.
    No client-side transaction state — see module docstring.
    """

    def __init__(self) -> None:
        self._url = _worker_url()
        self._headers = _auth_headers()

    def execute(self, sql: str, params: tuple | list = ()) -> D1Cursor:
        try:
            resp = requests.post(
                f"{self._url}/d1/exec",
                json={"sql": sql, "params": list(params)},
                headers=self._headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise D1Error(f"D1 proxy request failed: {exc}") from exc
        data = _parse_worker_response(resp)
        if not data.get("success"):
            raise D1Error(f"D1 exec failed (HTTP {resp.status_code}): {data.get('error', 'unknown error')}")
        meta = data.get("meta", {})
        return D1Cursor(data.get("results", []), meta.get("last_row_id"), meta.get("changes", 0))

    def commit(self) -> None:
        """No-op — every execute() is already committed on D1's side."""

    def rollback(self) -> None:
        """No-op — see module docstring on cross-statement atomicity."""

    def close(self) -> None:
        """No-op — stateless HTTP, nothing to release."""


def d1_batch(statements: list[tuple[str, tuple | list]]) -> list[D1Cursor]:
    """Execute multiple statements atomically via the Worker's
    POST /d1/batch (D1's own env.DB.batch()) — all succeed or all fail.

    Args:
        statements: list of (sql, params) pairs.

    Returns:
        One D1Cursor per statement, in the same order.
    """
    url = _worker_url()
    headers = _auth_headers()
    payload = {"statements": [{"sql": sql, "params": list(params)} for sql, params in statements]}
    try:
        resp = requests.post(f"{url}/d1/batch", json=payload, headers=headers, timeout=_DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise D1Error(f"D1 batch request failed: {exc}") from exc
    data = _parse_worker_response(resp)
    if not data.get("success"):
        raise D1Error(f"D1 batch failed (HTTP {resp.status_code}): {data.get('error', 'unknown error')}")
    return [
        D1Cursor(r.get("results", []), r.get("meta", {}).get("last_row_id"), r.get("meta", {}).get("changes", 0))
        for r in data.get("results", [])
    ]


@contextmanager
def d1_connection() -> Iterator[D1Connection]:
    """Context manager matching the shape of each storage module's own
    `_conn()` — `with d1_connection() as con: con.execute(...)`."""
    con = D1Connection()
    try:
        yield con
    finally:
        con.close()
