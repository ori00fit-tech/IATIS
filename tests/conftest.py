"""
tests/conftest.py
-----------------
Hermetic test isolation for the whole suite.

Root cause addressed (VPS incident, 2026-07-02):
    Running ``pytest`` on the production VPS failed 3 credential-guard
    tests and took 22 minutes (vs ~5s clean). The suite was reading REAL
    credentials from the process environment (.env exported on the VPS):

    - ``send_signal(report, token="", chat_id="")`` fell back to the real
      TELEGRAM_BOT_TOKEN via ``token or env_token`` and actually delivered
      a Telegram message during the test run (returned True).
    - cTrader "raises without credentials" tests found real credentials.
    - Data-provider tests found real API keys and made live HTTP calls,
      burning Twelve Data / Alpha Vantage daily credits on every test run.

Two guarantees are enforced here, for every test, on every machine:

1. **No production credentials are visible to tests.** All known secret
   env vars are removed before each test. Tests that need credentials
   must set them explicitly (monkeypatch.setenv), which documents intent.

2. **No real network I/O.** Outbound socket connections are refused with
   a loud, actionable error. Correctly mocked tests never open sockets,
   so this only trips when a test accidentally reaches a live service —
   turning a silent 22-minute, credit-burning, message-sending run into
   an immediate, explainable failure.
"""

from __future__ import annotations

import socket
import sqlite3
from unittest.mock import MagicMock

import pytest

# Every secret/credential the codebase reads from the environment.
# Extend this list whenever a new integration is added.
_CREDENTIAL_ENV_VARS = [
    "TWELVE_DATA_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCOUNT_ID",
    "CTRADER_ACCESS_TOKEN",
    "OANDA_API_TOKEN",
    "OANDA_ACCOUNT_ID",
    "IATIS_API_KEY",
]


@pytest.fixture(autouse=True)
def _isolate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all production credentials from the environment per test.

    Guarantees credential-guard tests (e.g. "raises without credentials")
    behave identically on developer laptops, CI, and the production VPS.
    """
    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class NetworkAccessBlockedError(RuntimeError):
    """A test attempted a real outbound network connection."""


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse real outbound socket connections during tests.

    In-process clients (FastAPI TestClient), SQLite, and properly mocked
    HTTP never open outbound sockets, so legitimate tests are unaffected.
    """
    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        # Allow loopback for any test that spins up a genuinely local server.
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *args, **kwargs)
        raise NetworkAccessBlockedError(
            f"Test attempted a real network connection to {address!r}. "
            f"Mock the HTTP layer (e.g. monkeypatch requests.post) instead. "
            f"Real calls from tests send live Telegram messages and burn "
            f"API credits — see tests/conftest.py."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def fake_d1(monkeypatch: pytest.MonkeyPatch):
    """Give every test a working, isolated Cloudflare D1 backend.

    storage/*.py has no local SQLite fallback — every read/write goes
    over HTTPS to the D1 proxy Worker (storage/d1_client.py). Real
    network is refused above, so tests need a stand-in Worker. Rather
    than hand-writing a `{success, results, meta}` JSON fixture per
    query (unmaintainable for the real GROUP BY/JOIN/aggregate SQL in
    storage/decision_db.py, outcome_tracker.py, etc.), this fixture
    fakes `requests.post` and executes the exact same SQL against a
    private in-memory `sqlite3` connection standing in for D1 itself —
    real SQL semantics stay under test, only the transport is faked.
    Fresh, empty database per test: the same isolation guarantee the
    old per-test tmp_path SQLite files gave before the D1 migration.

    Autouse so the whole suite gets this for free. Tests asserting on
    the exact HTTP request/response shape (tests/test_d1_client.py)
    override it with their own `patch("storage.d1_client.requests.post")`
    for the duration of the test, which composes fine on top of this.
    """
    monkeypatch.setenv("D1_WORKER_URL", "https://fake-d1-test.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "test-token")

    # check_same_thread=False: the real Worker is stateless HTTP, reachable
    # from any thread — execution/api_server.py runs run_pipeline() in a
    # thread-pool executor, so the fake must tolerate cross-thread access
    # too, unlike sqlite3's single-thread-only default.
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row

    def _exec_one(sql: str, params) -> dict:
        try:
            cur = con.execute(sql, list(params or []))
            con.commit()
        except sqlite3.Error as exc:
            return {"success": False, "error": str(exc)}
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        return {
            "success": True,
            "results": rows,
            "meta": {"last_row_id": cur.lastrowid, "changes": cur.rowcount},
        }

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        import requests as _requests

        body = json or {}
        resp = MagicMock()
        if url.endswith("/d1/batch"):
            results = []
            error = None
            for stmt in body.get("statements", []):
                result = _exec_one(stmt["sql"], stmt.get("params", []))
                if not result["success"]:
                    error = result["error"]
                    break
                results.append(result)
            data = {"success": True, "results": results} if error is None else {
                "success": False, "error": error,
            }
        else:  # /d1/exec
            data = _exec_one(body.get("sql", ""), body.get("params", []))

        resp.json.return_value = data
        if data["success"]:
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
        else:
            resp.status_code = 500
            resp.raise_for_status = MagicMock(
                side_effect=_requests.HTTPError("500 Server Error: Internal Server Error")
            )
        return resp

    monkeypatch.setattr("storage.d1_client.requests.post", fake_post)
    yield con
    con.close()
