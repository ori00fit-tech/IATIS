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


# ─── Cross-process session lock (audit P0-3) ───────────────────────────────
# The 2026-07-22 ALREADY_LOGGED_IN fix made a second, colliding auth attempt
# non-fatal for THIS process's own reconnects, but its own code comment
# admitted a genuinely conflicting session was assumed, not verified, to
# fail downstream. These tests cover the cross-process guard added to close
# that gap: connect() must refuse to proceed if another OS process already
# holds the session lock, using flock's real per-open-file-description
# semantics (which distinguish two independent opens of the same path even
# within one test process, exactly like two real OS processes would race).

@pytest.fixture(autouse=True)
def _reset_process_lock(monkeypatch, tmp_path):
    """Every test gets its own lock file path and a clean module-level
    lock-holder slot, so tests can't leak a held lock into each other or
    into the rest of the suite (a real connect() attempt elsewhere would
    otherwise silently always succeed after the first test acquires it)."""
    import execution.ctrader_client as m
    monkeypatch.setattr(m, "_PROCESS_LOCK_PATH", tmp_path / "ctrader_session.lock")
    monkeypatch.setattr(m, "_process_lock_file", None)
    yield
    # Best-effort close so a held fd from this test doesn't linger.
    if m._process_lock_file is not None:
        try:
            m._process_lock_file.close()
        except OSError:
            pass
        m._process_lock_file = None


def test_acquire_process_lock_succeeds_when_uncontended():
    import execution.ctrader_client as m
    m._acquire_process_lock(m._PROCESS_LOCK_PATH)
    assert m._process_lock_file is not None
    assert m._PROCESS_LOCK_PATH.exists()


def test_acquire_process_lock_is_idempotent_within_one_process():
    import execution.ctrader_client as m
    m._acquire_process_lock(m._PROCESS_LOCK_PATH)
    held = m._process_lock_file
    m._acquire_process_lock(m._PROCESS_LOCK_PATH)  # second call, same process
    assert m._process_lock_file is held  # no-op, didn't re-open/re-lock


def test_acquire_process_lock_rejects_a_second_holder():
    """Simulates a second OS process: a fresh open() of the SAME path, held
    independently of this test's own lock, must be rejected. flock locks
    attach to the open-file-description, not the process, so this is a
    faithful stand-in for a genuinely separate process without needing to
    actually fork/spawn one."""
    import execution.ctrader_client as m

    m._acquire_process_lock(m._PROCESS_LOCK_PATH)  # "process A" acquires it
    # Keep process A's file object alive — flock releases on close/GC, and
    # dropping the only reference (just clearing the module attribute)
    # would silently free the lock, defeating the whole test.
    holder_fh = m._process_lock_file
    m._process_lock_file = None  # simulate "process B": hasn't acquired yet

    with pytest.raises(m.DuplicateSessionError, match="Another process already holds"):
        m._acquire_process_lock(m._PROCESS_LOCK_PATH)

    holder_fh.close()


def test_connect_refuses_when_lock_is_held_by_another_process(monkeypatch):
    """End-to-end through the real connect() entry point: a held lock must
    make connect() fail closed (return False) rather than proceed to
    authenticate a second, colliding session."""
    import execution.ctrader_client as m

    c = _mk_client()
    m._acquire_process_lock(m._PROCESS_LOCK_PATH)  # someone else holds it
    holder_fh = m._process_lock_file  # keep it alive — see comment above
    m._process_lock_file = None  # this client's process hasn't acquired yet

    assert c.connect(timeout=1) is False

    holder_fh.close()
