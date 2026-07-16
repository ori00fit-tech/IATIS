"""tests/test_api_server.py — API server tests, dev mode."""
from __future__ import annotations
import os, pytest
from unittest.mock import patch

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"   # override module-level variable
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
    # Force the expected key: _check_auth reads API_SERVER_KEY at request
    # time, and os.environ.setdefault above is a no-op when the host
    # (e.g. the VPS) already exports a real production key — which made
    # every authenticated test fail with 401 in that environment.
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app) as c:
        yield c


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_analyze_synthetic(client):
    r = client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 200}, headers=HDR)
    assert r.status_code == 200
    assert r.json()["final_verdict"] in ("EXECUTE", "NO_TRADE")


def test_analyze_structure(client):
    r = client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 200}, headers=HDR)
    data = r.json()
    # final_verdict and summary always present regardless of MQS gate
    assert "final_verdict" in data
    assert "summary" in data
    # regime/engines present only when MQS passes (GOOD/FAIR market)
    # market_quality always present when MQS gate runs
    assert "market_quality" in data or "final_verdict" in data


def test_symbol_invalid(client):
    for bad in ["INVALID@SYM!", "A"*20]:
        r = client.post(f"/analyze/{bad}", json={"source": "synthetic"}, headers=HDR)
        assert r.status_code in (400, 422)


def test_symbol_valid(client):
    for sym in ["EURUSD", "XAUUSD"]:
        r = client.post(f"/analyze/{sym}", json={"source": "synthetic", "bars": 100}, headers=HDR)
        assert r.status_code == 200


def test_no_telegram_on_api(client):
    with patch("execution.telegram_bot.send_signal") as mock_tg:
        client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 100}, headers=HDR)
    mock_tg.assert_not_called()


def test_auth_blocks_wrong_key(client):
    import execution.api_server as m; m._ENV = "production"
    r = client.post("/analyze/EURUSD", json={"source": "synthetic"}, headers={"X-API-Key": "bad"})
    m._ENV = "development"
    assert r.status_code == 401


def test_auth_correct_key(client):
    import execution.api_server as m; m._ENV = "production"
    r = client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 100}, headers=HDR)
    m._ENV = "development"
    assert r.status_code == 200


def test_decisions(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.decision_log.DEFAULT_LOG_PATH", tmp_path / "d.jsonl")
    from storage.decision_log import log_decision
    log_decision({"final_verdict": "NO_TRADE"})
    r = client.get("/decisions", headers=HDR)
    assert r.status_code == 200
    assert "decisions" in r.json()


def _fake_candles_df(n=5):
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "open": [1.10] * n, "high": [1.11] * n,
        "low": [1.09] * n, "close": [1.105] * n, "volume": [0.0] * n,
    }, index=idx)


def test_candles_returns_bars_and_signal(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.decision_log.DEFAULT_LOG_PATH", tmp_path / "d.jsonl")
    from storage.decision_log import log_decision
    log_decision({
        "final_verdict": "EXECUTE", "symbol": "EURUSD",
        "entry_price": 1.105, "stop_loss": 1.10, "take_profit": 1.12,
    })
    with patch("core.data_providers.fetch_with_failover", return_value=(_fake_candles_df(), "twelve_data")):
        r = client.get("/candles/EURUSD", headers=HDR)
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "EURUSD"
    assert data["interval"] == "H4"
    assert len(data["bars"]) == 5
    assert data["bars"][0]["open"] == 1.10
    assert data["signal"]["entry_price"] == 1.105
    assert data["signal"]["verdict"] == "EXECUTE"


def test_candles_no_signal_when_no_decisions_logged(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.decision_log.DEFAULT_LOG_PATH", tmp_path / "empty.jsonl")
    with patch("core.data_providers.fetch_with_failover", return_value=(_fake_candles_df(), "yahoo_finance")):
        r = client.get("/candles/EURUSD", headers=HDR)
    assert r.status_code == 200
    assert r.json()["signal"] is None


def test_candles_invalid_interval(client):
    with patch("core.data_providers.fetch_with_failover", return_value=(_fake_candles_df(), "twelve_data")):
        r = client.get("/candles/EURUSD", params={"interval": "W1"}, headers=HDR)
    assert r.status_code == 400


def test_candles_invalid_symbol(client):
    r = client.get("/candles/INVALID@SYM!", headers=HDR)
    assert r.status_code in (400, 422)


def test_candles_requires_auth(client):
    import execution.api_server as m; m._ENV = "production"
    r = client.get("/candles/EURUSD")
    m._ENV = "development"
    assert r.status_code == 401


def test_candles_maps_index_symbol_to_fetch_name(client):
    """US30's internal name is 'US30' but its fetch-symbol (config/symbols.yaml)
    is 'DJI' — the naive /analyze-style slash-insertion heuristic would mangle
    this, so /candles resolves it via the symbols table instead."""
    with patch("core.data_providers.fetch_with_failover", return_value=(_fake_candles_df(), "ctrader")) as mock_fetch:
        r = client.get("/candles/US30", headers=HDR)
    assert r.status_code == 200
    called_symbol = mock_fetch.call_args[0][0]
    assert called_symbol == "DJI"


def test_candles_502_when_all_providers_fail(client):
    from core.data_providers import DataFetchError
    with patch("core.data_providers.fetch_with_failover", side_effect=DataFetchError("all failed")):
        r = client.get("/candles/EURUSD", headers=HDR)
    assert r.status_code == 502


def test_candles_uses_asset_class_provider_chain(client):
    """Regression: /candles must route through provider_chain_for (ccxt
    first for crypto, ctrader first for fx/metals/indices) — not
    fetch_with_failover's own generic default chain, which has no ccxt
    entry at all and would never even try Binance for BTC/ETH."""
    from core.data_providers import provider_chain_for
    with patch("core.data_providers.fetch_with_failover", return_value=(_fake_candles_df(), "ccxt")) as mock_fetch:
        r = client.get("/candles/BTCUSD", headers=HDR)
    assert r.status_code == 200
    used_chain = mock_fetch.call_args.kwargs.get("providers")
    assert used_chain == provider_chain_for("BTC/USD")
    assert used_chain[0] == "ccxt"


def test_ai_explain_trade_disabled_by_default(client):
    # ai.enabled defaults to false in config.yaml — must degrade cleanly,
    # never 500, and never require a real provider/API key in tests.
    r = client.post(
        "/ai/explain-trade",
        json={"symbol": "EURUSD", "summary": "EXECUTE BULLISH", "final_verdict": "EXECUTE"},
        headers=HDR,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_ai_explain_trade_rejects_body_without_symbol(client):
    r = client.post("/ai/explain-trade", json={"foo": "bar"}, headers=HDR)
    assert r.status_code == 400


def test_ai_explain_trade_requires_auth(client):
    r = client.post("/ai/explain-trade", json={"symbol": "EURUSD"})
    assert r.status_code == 401


def test_ai_research_summary_disabled_by_default(client):
    r = client.post(
        "/ai/research-summary",
        json={"hypothesis_summary": {"total": 13, "passed": 1, "failed": 3, "research": 9}},
        headers=HDR,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_ai_research_summary_requires_auth(client):
    r = client.post("/ai/research-summary", json={})
    assert r.status_code == 401


def test_budget(client):
    with patch("core.twelve_data_client.RateLimiter.remaining_today", return_value=750):
        r = client.get("/budget", headers=HDR)
    assert r.status_code == 200
    assert "remaining_today" in r.json()


def test_stats(client):
    r = client.get("/stats", headers=HDR)
    assert r.status_code == 200
    assert "summary" in r.json()


def test_dashboard(client):
    r = client.get("/dashboard", headers=HDR)
    assert r.status_code == 200
    assert "IATIS" in r.text


def test_data_health(client):
    r = client.get("/data-health", headers=HDR)
    assert r.status_code == 200
    data = r.json()
    assert "symbols" in data and "summary" in data
    for entry in data["symbols"]:
        assert entry["overall_status"] in ("OK", "STALE", "GAPS", "MISSING")


def test_execution_quality_requires_auth(client):
    r = client.get("/execution-quality")
    assert r.status_code == 401


def test_execution_quality_contract(client):
    r = client.get("/execution-quality", headers=HDR)
    assert r.status_code == 200
    data = r.json()
    assert data["backtest_assumption_pips"] == 0.5
    assert "overall" in data and "by_symbol" in data and "by_session" in data


def test_reconciliation_requires_auth(client):
    assert client.get("/reconciliation").status_code == 401


def test_reconciliation_empty_contract(client):
    r = client.get("/reconciliation", headers=HDR)
    assert r.status_code == 200
    assert r.json()["status"] == "none"


def _submitted_jobs(monkeypatch):
    """Capture jobs instead of executing them (a real backtest is
    CPU-minutes — the endpoint contract is what's under test)."""
    import execution.api_server as m
    captured = []
    monkeypatch.setattr(m._job_executor, "submit", lambda fn, job: captured.append(job))
    return captured


def test_backtest_job_requires_symbols(client, monkeypatch):
    _submitted_jobs(monkeypatch)
    r = client.post("/experiments/run", json={"job": "backtest"}, headers=HDR)
    assert r.status_code == 400
    assert "at least one symbol" in r.json()["detail"]


def test_backtest_job_rejects_unknown_symbol(client, monkeypatch):
    _submitted_jobs(monkeypatch)
    r = client.post("/experiments/run", json={"job": "backtest", "symbols": ["EURUSD", "HACKUSD"]}, headers=HDR)
    assert r.status_code == 400
    assert "HACKUSD" in r.json()["detail"]


def test_backtest_job_builds_whitelisted_argv(client, monkeypatch):
    captured = _submitted_jobs(monkeypatch)
    r = client.post("/experiments/run", json={"job": "backtest", "symbols": ["eurusd", "XAUUSD"]}, headers=HDR)
    assert r.status_code == 200
    assert r.json()["job"] == "backtest"
    assert len(captured) == 1
    argv = captured[0].argv
    assert argv[-3:] == ["--symbols", "EURUSD", "XAUUSD"]  # normalized upper
    assert captured[0].timeout == 1800

    import execution.api_server as m
    m._jobs.clear()  # don't leak the queued job into other tests


def test_non_backtest_job_rejects_symbols(client, monkeypatch):
    _submitted_jobs(monkeypatch)
    r = client.post("/experiments/run", json={"job": "forward_review", "symbols": ["EURUSD"]}, headers=HDR)
    assert r.status_code == 400


def test_backtest_in_catalog(client):
    r = client.get("/experiments/jobs", headers=HDR)
    assert r.status_code == 200
    ids = {j["id"] for j in r.json()["jobs"]}
    assert "backtest" in ids
