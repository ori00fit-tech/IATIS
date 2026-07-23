"""
tests/test_journal.py — Trade Journal (storage/journal.py + /journal API).

Follows the tests/test_api_contract.py pattern: auth required on every
endpoint, agreed response shape with auth, all against the fake in-memory
D1 (tests/conftest.py). Also pins the journal's core promise: every
derived figure is recomputed from row prices, so rows carrying the legacy
poisoned pnl_pips still report sane numbers.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"
    from execution.api_server import app
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="fastapi not installed")

HDR = {"X-API-Key": "test-key-123"}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _seed_closed_trade(symbol="EURUSD", direction="BEARISH",
                       entry=1.0850, sl=1.0920, tp=1.0640, exit_px=1.0640,
                       outcome="win"):
    from storage.outcome_tracker import log_signal, close_signal

    report = {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "confluence": {"score": 72.0, "vote": {"winning_bias": direction}},
        "regime": {"state": "TRENDING"},
        "news": {"news_risk_score": 5.0},
        "engine_outputs": [
            {"engine": "SMC", "bias": direction, "score": 52},
            {"engine": "NNFX", "bias": direction, "score": 65},
        ],
    }
    signal_id = log_signal(report)
    close_signal(signal_id, exit_price=exit_px, outcome=outcome)
    return signal_id


# ── storage layer ──────────────────────────────────────────────────────────

def test_list_trades_enriches_rows():
    from storage.journal import list_trades

    _seed_closed_trade()
    result = list_trades()
    assert result["total"] >= 1
    trade = result["trades"][0]
    # 1.0850 short, SL 1.0920 (risk 0.0070), exit 1.0640 → +3.0R
    assert trade["realized_r"] == pytest.approx(3.0, abs=0.01)
    assert trade["planned_rr"] == pytest.approx(3.0, abs=0.01)
    assert trade["pnl_pips_clean"] == pytest.approx(210.0, abs=0.5)
    assert isinstance(trade["engines"], dict)
    assert "NNFX" in trade["engines"]
    assert trade["tags"] == []


def test_list_trades_filters():
    from storage.journal import list_trades

    _seed_closed_trade(symbol="EURUSD")
    assert list_trades(symbol="EURUSD")["total"] >= 1
    assert list_trades(symbol="GBPUSD")["total"] == 0
    assert list_trades(outcome="loss")["total"] == 0
    assert list_trades(direction="SELL")["total"] >= 1  # BEARISH matches SELL
    assert list_trades(direction="BUY")["total"] == 0


def test_journal_stats_recomputes_from_prices():
    """A row whose stored pnl_pips is absurd (legacy corruption) must not
    leak into the stats — everything is recomputed from prices."""
    from storage import d1_client
    from storage.journal import journal_stats

    sid = _seed_closed_trade()
    with d1_client.d1_connection() as con:
        con.execute(
            "UPDATE outcomes SET pnl_pips = -857553.3 WHERE signal_id = ?", (sid,)
        )

    stats = journal_stats()
    assert stats["closed"] == 1
    assert stats["total_r"] == pytest.approx(3.0, abs=0.01)
    assert stats["win_rate"] == 100.0
    assert stats["profit_factor"] == "Infinity"
    assert len(stats["equity_curve"]) == 1
    assert stats["equity_curve"][0]["cum_r"] == pytest.approx(3.0, abs=0.01)
    assert stats["by_symbol"][0]["symbol"] == "EURUSD"


def test_performance_summary_ignores_poisoned_pips():
    """The Mission Control expectancy bug: stored pnl_pips corruption must
    not surface in performance_summary().total_pips."""
    from storage import d1_client
    from storage.outcome_tracker import performance_summary

    sid = _seed_closed_trade()
    with d1_client.d1_connection() as con:
        con.execute(
            "UPDATE outcomes SET pnl_pips = -19725725.0, pnl_usd = -857553.3 "
            "WHERE signal_id = ?", (sid,)
        )

    summary = performance_summary()
    assert summary["total_pips"] == pytest.approx(210.0, abs=0.5)
    assert summary["profit_factor"] == "Infinity"
    assert summary["avg_r_multiple"] == pytest.approx(3.0, abs=0.01)
    assert summary["total_r"] == pytest.approx(3.0, abs=0.01)


def test_annotate_notes_and_missing_signal():
    from storage.journal import annotate, trade_detail

    sid = _seed_closed_trade()
    assert annotate(sid, notes="reviewed — clean trend day") == (True, True)
    assert trade_detail(sid)["notes"] == "reviewed — clean trend day"
    assert annotate("nope_123", notes="x") == (False, False)


def test_annotate_tags_roundtrip():
    from storage.journal import annotate, trade_detail
    from storage.migrations import apply_migrations

    apply_migrations()  # tags column arrives with migration 3
    sid = _seed_closed_trade()
    assert annotate(sid, tags=["news-spike", "a-plus-setup"]) == (True, True)
    assert trade_detail(sid)["tags"] == ["news-spike", "a-plus-setup"]


def test_annotate_tags_without_migration_reports_found_but_not_applied():
    """Regression for docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-5: the
    signal exists (found=True) but nothing was persisted (applied=False)
    when tags are requested without notes and the tags column is missing —
    previously this silently reported success."""
    from storage.journal import annotate, trade_detail

    sid = _seed_closed_trade()  # no apply_migrations() — tags column absent
    before = trade_detail(sid).get("notes")
    found, applied = annotate(sid, tags=["news-spike"])
    assert found is True
    assert applied is False
    assert trade_detail(sid).get("notes") == before  # nothing changed


def test_annotate_notes_still_applied_even_if_tags_column_missing():
    """When both notes and tags are given but the tags column is missing,
    the notes write must still go through — applied reflects the request
    as a whole, but nothing that *could* be written is silently dropped."""
    from storage.journal import annotate, trade_detail

    sid = _seed_closed_trade()
    found, applied = annotate(sid, notes="partial write ok", tags=["x"])
    assert found is True
    assert applied is True
    assert trade_detail(sid)["notes"] == "partial write ok"


# ── API contract ───────────────────────────────────────────────────────────

JOURNAL_ENDPOINTS = [
    ("/journal", {"total", "returned", "offset", "trades"}),
    ("/journal/stats", {"closed", "equity_curve", "by_symbol", "win_rate"}),
]


@pytest.mark.parametrize("path,keys", JOURNAL_ENDPOINTS)
def test_journal_endpoints_require_auth(client, path, keys):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path,keys", JOURNAL_ENDPOINTS)
def test_journal_endpoints_shape(client, path, keys):
    _seed_closed_trade()
    res = client.get(path, headers=HDR)
    assert res.status_code == 200
    assert keys <= set(res.json().keys())


def test_journal_detail_and_404(client):
    sid = _seed_closed_trade()
    res = client.get(f"/journal/{sid}", headers=HDR)
    assert res.status_code == 200
    body = res.json()
    assert body["signal_id"] == sid
    assert body["realized_r"] == pytest.approx(3.0, abs=0.01)
    assert client.get("/journal/unknown_id", headers=HDR).status_code == 404


def test_journal_annotate_endpoint(client):
    sid = _seed_closed_trade()
    res = client.post(f"/journal/{sid}/annotate", headers=HDR,
                      json={"notes": "via API"})
    assert res.status_code == 200
    assert client.get(f"/journal/{sid}", headers=HDR).json()["notes"] == "via API"
    # no body / empty body → 400
    assert client.post(f"/journal/{sid}/annotate", headers=HDR, json={}).status_code == 400
    assert client.post("/journal/nope/annotate", headers=HDR,
                       json={"notes": "x"}).status_code == 404


def test_journal_annotate_endpoint_409s_on_no_op(client):
    """P2-5: tags-only request with no migration applied must not report
    200/success:true for a write that didn't happen."""
    sid = _seed_closed_trade()  # tags column absent — no apply_migrations()
    res = client.post(f"/journal/{sid}/annotate", headers=HDR, json={"tags": ["x"]})
    assert res.status_code == 409


def test_journal_export_csv(client):
    _seed_closed_trade()
    res = client.get("/journal/export", headers=HDR)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    lines = res.text.strip().splitlines()
    assert lines[0].startswith("signal_id,symbol,direction,outcome")
    assert len(lines) >= 2
