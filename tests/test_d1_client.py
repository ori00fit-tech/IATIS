"""
tests/test_d1_client.py
--------------------------
Tests for storage/d1_client.py — the optional D1-backed alternate
storage backend. All HTTP calls are mocked (no real Cloudflare account
needed), same convention as tests/test_twelve_data.py.

These tests never touch the default sqlite path — is_d1_enabled() is
only true when IATIS_STORAGE_BACKEND=d1 is explicitly set, which the
rest of the suite never does.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from storage import d1_client
from storage.d1_client import D1Connection, D1Error, D1Row, is_d1_enabled


# ---------------------------------------------------------------------------
# is_d1_enabled()
# ---------------------------------------------------------------------------

def test_d1_disabled_by_default(monkeypatch):
    monkeypatch.delenv("IATIS_STORAGE_BACKEND", raising=False)
    assert is_d1_enabled() is False


def test_d1_enabled_when_set(monkeypatch):
    monkeypatch.setenv("IATIS_STORAGE_BACKEND", "d1")
    assert is_d1_enabled() is True


def test_d1_enabled_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("IATIS_STORAGE_BACKEND", "D1")
    assert is_d1_enabled() is True


# ---------------------------------------------------------------------------
# D1Row — sqlite3.Row-compatible access
# ---------------------------------------------------------------------------

def test_d1row_supports_string_and_int_access():
    row = D1Row({"symbol": "EURUSD", "score": 72})
    assert row["symbol"] == "EURUSD"
    assert row[0] == "EURUSD"
    assert row[1] == 72


def test_d1row_converts_to_dict():
    row = D1Row({"a": 1, "b": 2})
    assert dict(row) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# D1Connection.execute() — mocked HTTP
# ---------------------------------------------------------------------------

def _mock_post(json_body: dict, status_ok: bool = True) -> MagicMock:
    """A worker.js response is JSON either way — on a caught D1 exception
    it still returns {success: false, error: "..."} but with a real 500
    status. raise_for_status() must reflect that (raising
    requests.HTTPError, a RequestException subclass) so tests actually
    exercise the same order-of-operations bug that hit production:
    resp.raise_for_status() must NOT be called before resp.json() is
    read, or the real `error` message gets discarded. See
    _parse_worker_response()."""
    import requests as _requests

    resp = MagicMock()
    resp.json.return_value = json_body
    resp.status_code = 200 if status_ok else 500
    resp.raise_for_status = (
        MagicMock()
        if status_ok
        else MagicMock(side_effect=_requests.HTTPError("500 Server Error: Internal Server Error"))
    )
    return resp


def test_d1_connection_requires_worker_url(monkeypatch):
    monkeypatch.delenv("D1_WORKER_URL", raising=False)
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    with pytest.raises(D1Error):
        D1Connection()


def test_d1_connection_requires_proxy_token(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.delenv("D1_PROXY_TOKEN", raising=False)
    with pytest.raises(D1Error):
        D1Connection()


def test_d1_connection_execute_select_returns_rows(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    fake_resp = _mock_post({
        "success": True,
        "results": [{"symbol": "EURUSD", "verdict": "EXECUTE"}],
        "meta": {"last_row_id": None, "changes": 0},
    })
    with patch("storage.d1_client.requests.post", return_value=fake_resp) as post:
        con = D1Connection()
        cur = con.execute("SELECT symbol, verdict FROM decisions WHERE id=?", (1,))
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0]["symbol"] == "EURUSD"
    call_kwargs = post.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer secret"
    assert call_kwargs["json"] == {"sql": "SELECT symbol, verdict FROM decisions WHERE id=?", "params": [1]}


def test_d1_connection_execute_insert_returns_lastrowid(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    fake_resp = _mock_post({
        "success": True,
        "results": [],
        "meta": {"last_row_id": 42, "changes": 1},
    })
    with patch("storage.d1_client.requests.post", return_value=fake_resp):
        con = D1Connection()
        cur = con.execute("INSERT INTO decisions (symbol) VALUES (?)", ("EURUSD",))

    assert cur.lastrowid == 42
    assert cur.fetchone() is None  # no result rows for an INSERT


def test_d1_connection_raises_on_proxy_error_response(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    fake_resp = _mock_post({"success": False, "error": "no such table: decisions"})
    with patch("storage.d1_client.requests.post", return_value=fake_resp):
        con = D1Connection()
        with pytest.raises(D1Error, match="no such table"):
            con.execute("SELECT * FROM decisions")


def test_d1_connection_surfaces_real_error_on_actual_http_500(monkeypatch):
    """Regression test: a real Worker 500 (env.DB threw, caught, and
    returned {success: false, error: "..."} with an actual 500 status)
    must surface that real error message, not a generic 'HTTP 500'
    string. This is exactly the bug that hit production: the old code
    called resp.raise_for_status() before resp.json(), so the real
    D1 error was discarded and replaced with 'D1 batch request failed:
    500 Server Error: Internal Server Error'."""
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    fake_resp = _mock_post(
        {"success": False, "error": "D1_ERROR: FOREIGN KEY constraint failed"},
        status_ok=False,
    )
    with patch("storage.d1_client.requests.post", return_value=fake_resp):
        con = D1Connection()
        with pytest.raises(D1Error, match="FOREIGN KEY constraint failed"):
            con.execute("INSERT INTO engine_votes (decision_id) VALUES (999999)")


def test_d1_connection_raises_on_network_error(monkeypatch):
    import requests as _requests
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    with patch("storage.d1_client.requests.post", side_effect=_requests.RequestException("timeout")):
        con = D1Connection()
        with pytest.raises(D1Error):
            con.execute("SELECT 1")


# ---------------------------------------------------------------------------
# d1_batch() — atomic multi-statement writes
# ---------------------------------------------------------------------------

def test_d1_batch_sends_all_statements_and_parses_results(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    fake_resp = _mock_post({
        "success": True,
        "results": [
            {"results": [], "meta": {"last_row_id": 5, "changes": 1}},
            {"results": [], "meta": {"last_row_id": 6, "changes": 1}},
        ],
    })
    with patch("storage.d1_client.requests.post", return_value=fake_resp) as post:
        cursors = d1_client.d1_batch([
            ("INSERT INTO decisions (symbol) VALUES (?)", ("EURUSD",)),
            ("INSERT INTO engine_votes (decision_id, engine) VALUES (last_insert_rowid(), ?)", ("SMC",)),
        ])

    assert len(cursors) == 2
    assert cursors[0].lastrowid == 5
    assert cursors[1].lastrowid == 6
    body = post.call_args.kwargs["json"]
    assert len(body["statements"]) == 2
    assert post.call_args.args[0].endswith("/d1/batch")


def test_d1_batch_raises_on_failure(monkeypatch):
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    fake_resp = _mock_post({"success": False, "error": "batch failed"})
    with patch("storage.d1_client.requests.post", return_value=fake_resp):
        with pytest.raises(D1Error, match="batch failed"):
            d1_client.d1_batch([("INSERT INTO decisions (symbol) VALUES (?)", ("EURUSD",))])


def test_d1_batch_surfaces_real_error_on_actual_http_500(monkeypatch):
    """Same regression as test_d1_connection_surfaces_real_error_on_actual_http_500,
    for the /d1/batch path used by decision_db.log_decision_db()."""
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")
    fake_resp = _mock_post(
        {"success": False, "error": "D1_ERROR: table engine_votes has no column named foo"},
        status_ok=False,
    )
    with patch("storage.d1_client.requests.post", return_value=fake_resp):
        with pytest.raises(D1Error, match="no column named foo"):
            d1_client.d1_batch([("INSERT INTO engine_votes (foo) VALUES (?)", (1,))])


# ---------------------------------------------------------------------------
# Storage modules pick up the D1 backend when enabled (still mocked HTTP)
# ---------------------------------------------------------------------------

def test_decision_db_uses_d1_when_enabled(monkeypatch):
    monkeypatch.setenv("IATIS_STORAGE_BACKEND", "d1")
    monkeypatch.setenv("D1_WORKER_URL", "https://example.workers.dev")
    monkeypatch.setenv("D1_PROXY_TOKEN", "secret")

    import storage.decision_db as decision_db

    fake_resp = _mock_post({
        "success": True,
        "results": [{"success": True}],  # unused; batch path is used instead
        "meta": {},
    })
    batch_resp = _mock_post({
        "success": True,
        "results": [{"results": [], "meta": {"last_row_id": 1, "changes": 1}}] * 3,
    })

    def fake_post(url, **kwargs):
        if url.endswith("/d1/batch"):
            return batch_resp
        return fake_resp

    report = {
        "symbol": "EURUSD",
        "final_verdict": "EXECUTE",
        "confluence": {"score": 72, "engines_participating": 2, "fail_reasons": []},
        "regime": {"state": "TRENDING", "volatility": "normal", "trend_strength": 0.5},
        "risk": {"passed": True},
        "summary": "test",
        "engine_outputs": [{"engine": "SMC", "bias": "BULLISH", "score": 65}],
    }
    with patch("storage.d1_client.requests.post", side_effect=fake_post) as post:
        decision_db.log_decision_db(report)

    # init_db (table creation) + the atomic batch insert
    assert post.called
    batch_calls = [c for c in post.call_args_list if c.args[0].endswith("/d1/batch")]
    assert len(batch_calls) == 1
