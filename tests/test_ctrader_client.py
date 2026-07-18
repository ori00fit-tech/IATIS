"""tests/test_ctrader_client.py — cTrader client unit tests (no API connection)."""
from __future__ import annotations
import pytest
from execution.ctrader_client import (
    IATIS_TO_CTRADER, CTRADER_TO_IATIS, ASSET_CLASS,
    RECOMMENDED_LEVERAGE, CTraderClient, CTraderOrder, CTraderResult
)
from execution.trade_executor import TradeExecutor


# ─── Symbol mapping ───────────────────────────────────────────────────────────

def test_all_19_iatis_symbols_mapped():
    symbols = [
        "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
        "EURJPY","GBPJPY","AUDJPY","EURGBP","EURCHF",
        "XAUUSD","XAGUSD","USOIL",
        "US30","NAS100","SPX500",
        "BTCUSD","ETHUSD",
    ]
    for sym in symbols:
        assert sym in IATIS_TO_CTRADER, f"{sym} missing from cTrader mapping"


def test_reverse_mapping_consistent():
    for iatis, ct in IATIS_TO_CTRADER.items():
        assert CTRADER_TO_IATIS[ct] == iatis


def test_usoil_maps_to_xtiusd():
    assert IATIS_TO_CTRADER["USOIL"] == "XTIUSD"


def test_crypto_supported():
    assert "BTCUSD" in IATIS_TO_CTRADER
    assert "ETHUSD" in IATIS_TO_CTRADER


def test_leverage_recommendations():
    assert RECOMMENDED_LEVERAGE["forex"] <= 50
    assert RECOMMENDED_LEVERAGE["metal"] <= 20
    assert RECOMMENDED_LEVERAGE["crypto"] <= 10


def test_asset_classes_complete():
    for sym in IATIS_TO_CTRADER:
        assert sym in ASSET_CLASS, f"{sym} missing from ASSET_CLASS"


# ─── Credentials validation ───────────────────────────────────────────────────

def test_client_raises_without_credentials():
    with pytest.raises(ValueError, match="CTRADER_CLIENT_ID"):
        CTraderClient(client_id="", client_secret="", account_id=0, access_token="")


def test_client_raises_without_account():
    with pytest.raises(ValueError, match="CTRADER_ACCOUNT_ID"):
        CTraderClient(
            client_id="test", client_secret="test",
            account_id=0, access_token="test"
        )


# ─── Volume calculation ───────────────────────────────────────────────────────

def _make_client():
    """Make client skipping validation (for unit tests)."""
    c = object.__new__(CTraderClient)
    c.client_id = "test"
    c.client_secret = "test"
    c.account_id = 12345
    c.access_token = "test"
    c.host = CTraderClient.DEMO_HOST
    c.environment = "demo"
    c._client = None
    c._connected = False
    c._symbol_list = {}
    return c


def test_volume_eurusd_basic():
    client = _make_client()
    # $10,000 balance, 1% risk, 30 pip SL
    vol = client.calculate_volume("EURUSD", 10_000, 0.01, 0.0030)
    assert vol > 0
    assert vol <= 10000  # max 100 lots


def test_volume_xauusd():
    client = _make_client()
    vol = client.calculate_volume("XAUUSD", 10_000, 0.01, 5.0)
    assert vol > 0


def test_volume_zero_sl_returns_zero():
    client = _make_client()
    vol = client.calculate_volume("EURUSD", 10_000, 0.01, 0.0)
    assert vol == 0


def test_volume_jpy_pair():
    client = _make_client()
    # USDJPY: SL distance = 0.50 (50 pips at pip_size=0.01)
    vol = client.calculate_volume("USDJPY", 10_000, 0.01, 0.50)
    assert vol > 0


# ─── TradeExecutor with cTrader ───────────────────────────────────────────────

def _make_execute_report(symbol="EURUSD", score=75.0, bias="BEARISH"):
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {"score": score, "vote": {"winning_bias": bias}},
        "risk": {"recommended_risk_pct": 0.01},
        "news": {"blackout_active": False, "blackout_reason": ""},
    }


def test_executor_ctrader_dry_run():
    executor = TradeExecutor(dry_run=True, broker="ctrader")
    result = executor.execute_from_report(_make_execute_report())
    assert result.executed is True
    assert result.dry_run is True
    assert result.trade_id == "DRY_RUN"


def test_executor_ctrader_dry_run_btc():
    executor = TradeExecutor(dry_run=True, broker="ctrader")
    result = executor.execute_from_report(_make_execute_report("BTCUSD"))
    assert result.executed is True  # dry_run doesn't hit broker
    assert result.dry_run is True


def test_executor_ctrader_blocks_news():
    executor = TradeExecutor(dry_run=True, broker="ctrader")
    report = _make_execute_report()
    report["news"]["blackout_active"] = True
    report["news"]["blackout_reason"] = "NFP in 15 min"
    result = executor.execute_from_report(report)
    assert result.executed is False
    assert "blackout" in result.skip_reason.lower()


def test_executor_ctrader_reuses_shared_data_provider_client(monkeypatch):
    """Regression (2026-07-14): TradeExecutor used to open its own
    independent CTraderClient in _get_client(), separate from
    core.data_providers's module-level singleton used for data fetching.
    Two live sessions against the same cTrader account+app collide
    (ALREADY_LOGGED_IN, permanent reconnect storm) — both call sites
    must share exactly one client."""
    import core.data_providers as dp
    sentinel = object()
    monkeypatch.setattr(dp, "get_shared_ctrader_client", lambda: sentinel)
    executor = TradeExecutor(dry_run=False, broker="ctrader")
    assert executor._get_client() is sentinel


# ─── Spot resolution for the full broker universe (sweep re-cost, 2026-07-17) ─

def test_get_spot_by_name_resolves_unmapped_symbols(monkeypatch):
    """Every broker-enumerated symbol must be quotable — not just the 20
    in IATIS_TO_CTRADER (the '72 of 77 winners never paid a real spread'
    sweep caveat)."""
    import threading
    c = _make_client()
    c._lock = threading.Lock()
    c._symbol_name_to_id = {"ENSUSD": 4242, "EURUSD": 1}
    monkeypatch.setattr(c, "_get_spot_scaled",
                        lambda sid: (2050000, 2055000) if sid == 4242 else None)

    q = c.get_spot_by_name("ENSUSD")
    assert q is not None
    bid, ask = q
    assert bid == 2050000 / CTraderClient.SPOT_SCALE
    assert ask == 2055000 / CTraderClient.SPOT_SCALE
    assert c.get_spot_by_name("NOSUCH") is None


def test_get_spot_delegates_to_by_name(monkeypatch):
    import threading
    c = _make_client()
    c._lock = threading.Lock()
    c._symbol_name_to_id = {"XTIUSD": 7}
    monkeypatch.setattr(c, "_get_spot_scaled", lambda sid: (6500000, 6510000))
    assert c.get_spot("USOIL") is not None      # USOIL → XTIUSD via the map
    assert c.get_spot("UNMAPPED") is None       # not an IATIS symbol


def test_sweep_pip_size_matches_engine_convention():
    """The unit contract that makes measured spreads enter the cost model
    at the right scale — crypto was 100x off before 2026-07-17."""
    from scripts.backtest_ic_symbols import _pip_size
    from storage.execution_quality import pip_size_for
    for sym in ("BTCUSD", "ETHUSD", "XAUUSD", "USDJPY", "EURUSD", "ENSUSD", "XTIUSD"):
        assert _pip_size(sym) == pip_size_for(sym), sym
    assert _pip_size("BTCUSD") == 0.01   # the previously-wrong case


# ─── Reconnect teardown / superseded-client guard (fd-leak fix) ────────────────

def _mk_client():
    return CTraderClient(
        client_id="test", client_secret="test",
        account_id=12345, access_token="test",
    )


def test_on_disconnect_ignores_superseded_client(monkeypatch):
    """A torn-down previous client's disconnect callback must not clobber the
    live connection or trigger a reconnect (the ALREADY_LOGGED_IN/fd-leak storm)."""
    c = _mk_client()
    current = object()
    stale = object()
    c._client = current
    c._intentional_disconnect = False
    scheduled = {"n": 0}
    monkeypatch.setattr(c, "_schedule_reconnect", lambda: scheduled.__setitem__("n", scheduled["n"] + 1))

    c._on_disconnect(stale, "connection lost")   # superseded → ignored
    assert scheduled["n"] == 0

    c._on_disconnect(current, "connection lost")  # the live client → reconnect
    assert scheduled["n"] == 1


def test_stop_client_is_best_effort(monkeypatch):
    """_stop_client never raises — None is a no-op, and a missing/other reactor
    is swallowed (it runs during teardown when things are already broken)."""
    c = _mk_client()
    c._stop_client(None)  # no-op, must not raise
    c._stop_client(object())  # reactor import/branch guarded → must not raise
