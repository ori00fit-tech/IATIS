"""
storage/d1_client.py
-----------------------
HTTP client for the Cloudflare D1 proxy Worker (cloudflare/worker.js).

D1 databases are only reachable from inside a Cloudflare Worker via a
binding — there is no way for this VPS-hosted Python process to talk
to D1 directly. This module calls a small Worker (deployed separately;
see cloudflare/README.md) that forwards parameterized SQL to its D1
binding and returns the rows as JSON.

D1 is the only storage backend for storage/*.py — there is no local
SQLite fallback (removed to take disk I/O and file locking off the
VPS entirely; see cloudflare/README.md for the history). Every storage
module's `_conn()` uses `d1_connection()` from here.

Design goal: `D1Connection`/`D1Cursor`/`D1Row` mimic sqlite3's own
connection/cursor/row interface closely enough (`.execute(sql, params)`,
`.fetchone()`, `.fetchall()`, `.lastrowid`, row access by both
`row["col"]` and `row[0]`) that the SQL query strings throughout
storage/*.py read the same as they did against local SQLite.

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
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 15.0

# Transport-level retry policy. Deliberately narrow:
#   - connect errors: always safe to retry — the request never left.
#   - HTTP 429/502/503: the edge/Worker was unavailable, the statement
#     (almost certainly) never reached D1.
#   - NOT 500: that's worker.js's envelope for a real D1/SQL error —
#     deterministic, retrying only adds latency.
#   - NOT 504 and NOT read timeouts: the Worker may have already
#     committed the write; blind re-POST would duplicate rows.
# urllib3 excludes POST from retries by default, so allowed_methods
# must name it explicitly.
_RETRY = Retry(
    total=3,
    connect=3,
    read=0,
    status=2,
    status_forcelist=(429, 502, 503),
    allowed_methods=frozenset({"POST"}),
    backoff_factor=0.5,
    raise_on_status=False,
)

_thread_local = threading.local()


def _session() -> requests.Session:
    """Per-thread persistent Session (requests.Session is not documented
    as thread-safe; execution/api_server.py calls storage from a thread
    pool). Reusing the connection avoids a fresh TCP+TLS handshake per
    query — measured at ~600ms median per uncached call to the Worker."""
    ses = getattr(_thread_local, "session", None)
    if ses is None:
        ses = requests.Session()
        adapter = HTTPAdapter(max_retries=_RETRY)
        ses.mount("https://", adapter)
        ses.mount("http://", adapter)
        _thread_local.session = ses
    return ses


def _post(url: str, json: dict | None = None, headers: dict | None = None,
          timeout: float = _DEFAULT_TIMEOUT) -> "requests.Response":
    """Single HTTP seam for this module. Tests monkeypatch THIS function
    (`patch("storage.d1_client._post", ...)`) instead of requests.post,
    so the session/retry plumbing stays out of every fixture."""
    return _session().post(url, json=json, headers=headers, timeout=timeout)


class D1Error(Exception):
    """Raised for a failed D1 proxy call."""


def _worker_url() -> str:
    url = os.environ.get("D1_WORKER_URL", "").rstrip("/")
    if not url:
        raise D1Error(
            "D1_WORKER_URL is not set. See cloudflare/README.md for setup."
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
            "D1_PROXY_TOKEN is not set. See cloudflare/README.md for setup."
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
            resp = _post(
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
        resp = _post(f"{url}/d1/batch", json=payload, headers=headers, timeout=_DEFAULT_TIMEOUT)
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
