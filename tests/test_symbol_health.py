"""tests/test_symbol_health.py"""
from __future__ import annotations
import sqlite3, tempfile
from pathlib import Path
import pytest
from storage.symbol_health import get_symbol_health, get_all_symbol_health, SHI_HEALTHY, SHI_CAUTION
from storage.outcome_tracker import _init_db


def _tmp_db():
    t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Path(t.name)


def _insert(db, symbol, outcomes_newest_first):
    """Insert trades with correct dates (index 0 = most recent)."""
    con = sqlite3.connect(str(db))
    for i, outcome in enumerate(outcomes_newest_first):
        day = 28 - i  # descending dates → index 0 is most recent
        pnl = 62.0 if outcome == "win" else -20.0
        con.execute("""INSERT OR IGNORE INTO outcomes
            (signal_id,symbol,direction,entry_price,stop_loss,take_profit,
             entry_time,outcome,pnl_pips,pnl_usd,cf_score,regime)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"T{i:04d}_{symbol}", symbol, "BEARISH", 1.085, 1.092, 1.064,
             f"2026-06-{max(1,day):02d}T10:00:00+00:00",
             outcome, pnl, pnl*10, 70.0, "TRENDING"))
    con.commit(); con.close()


def test_no_data_healthy():
    import storage.symbol_health as sh
    orig = sh.DB_PATH
    sh.DB_PATH = Path("/tmp/nonexistent_iatis_xyz.db")
    r = get_symbol_health("EURUSD")
    sh.DB_PATH = orig
    assert r.status == "HEALTHY"
    assert r.trades_count == 0


def test_high_wr_good_score():
    db = _tmp_db(); _init_db(db)
    _insert(db, "EURUSD", ["win"]*15 + ["loss"]*5)
    import storage.symbol_health as sh; orig = sh.DB_PATH; sh.DB_PATH = db
    r = get_symbol_health("EURUSD")
    sh.DB_PATH = orig
    assert r.win_rate >= 0.70
    assert r.shi_score >= SHI_CAUTION
    assert r.status != "PAUSED"


def test_low_wr_poor():
    db = _tmp_db(); _init_db(db)
    _insert(db, "GBPJPY", ["loss"]*14 + ["win"]*6)
    import storage.symbol_health as sh; orig = sh.DB_PATH; sh.DB_PATH = db
    r = get_symbol_health("GBPJPY")
    sh.DB_PATH = orig
    assert r.win_rate < 0.45
    assert r.status in ("PAUSED", "CAUTION")


def test_consecutive_losses_counted():
    db = _tmp_db(); _init_db(db)
    # 4 recent losses + 10 older wins (newest first)
    _insert(db, "USDJPY", ["loss","loss","loss","loss"] + ["win"]*10)
    import storage.symbol_health as sh; orig = sh.DB_PATH; sh.DB_PATH = db
    r = get_symbol_health("USDJPY")
    sh.DB_PATH = orig
    assert r.consecutive_losses == 4


def test_position_multiplier():
    import storage.symbol_health as sh; orig = sh.DB_PATH
    sh.DB_PATH = Path("/tmp/nonexistent_iatis_xyz.db")
    r = get_symbol_health("AUDUSD")
    sh.DB_PATH = orig
    assert r.position_multiplier in (0.0, 0.5, 1.0)


def test_to_dict_keys():
    import storage.symbol_health as sh; orig = sh.DB_PATH
    sh.DB_PATH = Path("/tmp/nonexistent_iatis_xyz.db")
    d = get_symbol_health("NZDUSD").to_dict()
    sh.DB_PATH = orig
    for k in ["symbol","shi_score","status","position_multiplier","trades_count","reason"]:
        assert k in d


def test_get_all_sorted():
    db = _tmp_db(); _init_db(db)
    _insert(db, "EUR1", ["win"]*16 + ["loss"]*4)
    _insert(db, "GBP2", ["loss"]*16 + ["win"]*4)
    import storage.symbol_health as sh; orig = sh.DB_PATH; sh.DB_PATH = db
    results = get_all_symbol_health(["EUR1", "GBP2"])
    sh.DB_PATH = orig
    assert results[0]["shi_score"] >= results[1]["shi_score"]
