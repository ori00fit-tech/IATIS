"""tests/test_symbol_health.py"""
from __future__ import annotations
from storage.symbol_health import get_symbol_health, get_all_symbol_health, SHI_HEALTHY, SHI_CAUTION
from storage.outcome_tracker import _init_db


def _insert(fake_d1, symbol, outcomes_newest_first):
    """Insert trades with correct dates (index 0 = most recent)."""
    _init_db()
    for i, outcome in enumerate(outcomes_newest_first):
        day = 28 - i  # descending dates → index 0 is most recent
        pnl = 62.0 if outcome == "win" else -20.0
        fake_d1.execute("""INSERT OR IGNORE INTO outcomes
            (signal_id,symbol,direction,entry_price,stop_loss,take_profit,
             entry_time,outcome,pnl_pips,pnl_usd,cf_score,regime)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"T{i:04d}_{symbol}", symbol, "BEARISH", 1.085, 1.092, 1.064,
             f"2026-06-{max(1,day):02d}T10:00:00+00:00",
             outcome, pnl, pnl*10, 70.0, "TRENDING"))
    fake_d1.commit()


def test_no_data_healthy():
    r = get_symbol_health("EURUSD")
    assert r.status == "HEALTHY"
    assert r.trades_count == 0


def test_high_wr_good_score(fake_d1):
    _insert(fake_d1, "EURUSD", ["win"]*15 + ["loss"]*5)
    r = get_symbol_health("EURUSD")
    assert r.win_rate >= 0.70
    assert r.shi_score >= SHI_CAUTION
    assert r.status != "PAUSED"


def test_low_wr_poor(fake_d1):
    _insert(fake_d1, "GBPJPY", ["loss"]*14 + ["win"]*6)
    r = get_symbol_health("GBPJPY")
    assert r.win_rate < 0.45
    assert r.status in ("PAUSED", "CAUTION")


def test_consecutive_losses_counted(fake_d1):
    # 4 recent losses + 10 older wins (newest first)
    _insert(fake_d1, "USDJPY", ["loss","loss","loss","loss"] + ["win"]*10)
    r = get_symbol_health("USDJPY")
    assert r.consecutive_losses == 4


def test_position_multiplier():
    r = get_symbol_health("AUDUSD")
    assert r.position_multiplier in (0.0, 0.5, 1.0)


def test_to_dict_keys():
    d = get_symbol_health("NZDUSD").to_dict()
    for k in ["symbol","shi_score","status","position_multiplier","trades_count","reason"]:
        assert k in d


def test_get_all_sorted(fake_d1):
    _insert(fake_d1, "EUR1", ["win"]*16 + ["loss"]*4)
    _insert(fake_d1, "GBP2", ["loss"]*16 + ["win"]*4)
    results = get_all_symbol_health(["EUR1", "GBP2"])
    assert results[0]["shi_score"] >= results[1]["shi_score"]
