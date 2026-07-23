"""
tests/test_repair_outcome_pips.py
-----------------------------------
Regression coverage for scripts/repair_outcome_pips.py (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P1-4): this script directly
rewrites pnl_pips/pnl_usd in the `outcomes` table CLAUDE.md calls "the only
prospective evidence" for the whole philosophy — it shipped with zero test
coverage. Covers dry-run vs. --apply, idempotency, the golden-value formula
identity with close_signal(), untouched-open-rows, and the tolerance band.
"""
from __future__ import annotations

from storage import d1_client
from storage.outcome_tracker import DEFAULT_RISK_USD, close_signal, log_signal
from scripts.repair_outcome_pips import repair


def _report(symbol="EURUSD", direction="BULLISH", entry=1.0850, sl=1.0800, tp=1.0950):
    return {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "confluence": {"vote": {"winning_bias": direction}, "score": 70},
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "regime": {"regime": "TRENDING"},
        "news": {"news_risk_score": 0},
    }


def _row(signal_id: str) -> dict:
    with d1_client.d1_connection() as con:
        row = con.execute(
            "SELECT * FROM outcomes WHERE signal_id=?", (signal_id,)
        ).fetchone()
    return dict(row)


def _corrupt(signal_id: str, pnl_pips: float, pnl_usd: float | None) -> None:
    """Simulate the pre-2026-07-16 bug: wrong stored pnl_pips/pnl_usd."""
    with d1_client.d1_connection() as con:
        con.execute(
            "UPDATE outcomes SET pnl_pips=?, pnl_usd=? WHERE signal_id=?",
            (pnl_pips, pnl_usd, signal_id),
        )


# ── formula identity with close_signal() ────────────────────────────────

def test_repair_agrees_with_close_signal_formula_and_finds_nothing_to_fix():
    """A row written by close_signal() must already satisfy repair's own
    recomputation — if these two ever diverge, this is the test that must
    catch it before a future edit to either formula alone."""
    sid = log_signal(_report(direction="BULLISH", entry=1.0850, sl=1.0800))
    close_signal(sid, exit_price=1.0950, outcome="win")

    result = repair(apply=False)

    assert result["checked"] >= 1
    assert result["corrected"] == 0


# ── dry-run vs --apply ───────────────────────────────────────────────────

def test_dry_run_reports_but_does_not_write():
    sid = log_signal(_report(symbol="BTCUSD", direction="BULLISH", entry=50000, sl=49000))
    close_signal(sid, exit_price=52000, outcome="win")
    # Simulate the legacy bug: FX pip size (0.0001) applied to a crypto
    # move, producing a wildly wrong stored value.
    _corrupt(sid, pnl_pips=20_000_000.0, pnl_usd=999.0)

    result = repair(apply=False)

    assert result["corrected"] == 1
    row = _row(sid)
    # Unchanged — dry-run must never write.
    assert row["pnl_pips"] == 20_000_000.0
    assert row["pnl_usd"] == 999.0


def test_apply_writes_the_recomputed_values():
    sid = log_signal(_report(symbol="BTCUSD", direction="BULLISH", entry=50000, sl=49000))
    close_signal(sid, exit_price=52000, outcome="win")
    correct_row_before_corruption = _row(sid)

    _corrupt(sid, pnl_pips=20_000_000.0, pnl_usd=999.0)
    result = repair(apply=True)

    assert result["corrected"] == 1
    row = _row(sid)
    assert row["pnl_pips"] == correct_row_before_corruption["pnl_pips"]
    assert row["pnl_usd"] == correct_row_before_corruption["pnl_usd"]


# ── idempotency ───────────────────────────────────────────────────────────

def test_apply_is_idempotent():
    sid = log_signal(_report(symbol="XAUUSD", direction="BEARISH", entry=2400.0, sl=2420.0))
    close_signal(sid, exit_price=2360.0, outcome="win")
    _corrupt(sid, pnl_pips=1.0, pnl_usd=1.0)  # both off, but not enough to matter here

    first = repair(apply=True)
    assert first["corrected"] == 1

    second = repair(apply=True)
    assert second["corrected"] == 0, "re-running repair must converge, not keep 'fixing'"


# ── untouched rows ────────────────────────────────────────────────────────

def test_open_rows_are_never_touched():
    sid = log_signal(_report(symbol="EURUSD"))  # still open, no close_signal()
    _corrupt(sid, pnl_pips=123456.0, pnl_usd=555.0)

    result = repair(apply=True)

    row = _row(sid)
    assert row["outcome"] == "open"
    assert row["pnl_pips"] == 123456.0  # untouched
    assert row["pnl_usd"] == 555.0


def test_rows_missing_prices_are_skipped_without_crashing():
    sid = log_signal(_report(symbol="EURUSD"))
    # Force a closed-but-incomplete row (outcome tracker itself wouldn't
    # normally produce this, but a partial/corrupted write could).
    with d1_client.d1_connection() as con:
        con.execute(
            "UPDATE outcomes SET outcome='win', exit_price=NULL WHERE signal_id=?",
            (sid,),
        )

    result = repair(apply=True)  # must not raise

    assert result["checked"] == 0  # excluded by the WHERE clause (entry/exit both required)


# ── tolerance band ────────────────────────────────────────────────────────

def test_small_rounding_drift_within_tolerance_is_not_flagged():
    sid = log_signal(_report(direction="BULLISH", entry=1.0850, sl=1.0800))
    close_signal(sid, exit_price=1.0950, outcome="win")
    correct = _row(sid)

    # Nudge by less than the 1.0-pip / 1.0-usd tolerance — must NOT be flagged.
    _corrupt(sid, pnl_pips=correct["pnl_pips"] + 0.5, pnl_usd=correct["pnl_usd"] + 0.5)

    result = repair(apply=False)
    assert result["corrected"] == 0


def test_drift_beyond_tolerance_is_flagged():
    sid = log_signal(_report(direction="BULLISH", entry=1.0850, sl=1.0800))
    close_signal(sid, exit_price=1.0950, outcome="win")
    correct = _row(sid)

    _corrupt(sid, pnl_pips=correct["pnl_pips"] + 5.0, pnl_usd=correct["pnl_usd"])

    result = repair(apply=False)
    assert result["corrected"] == 1


def test_null_stored_pnl_pips_is_flagged():
    sid = log_signal(_report(direction="BULLISH", entry=1.0850, sl=1.0800))
    close_signal(sid, exit_price=1.0950, outcome="win")
    _corrupt(sid, pnl_pips=None, pnl_usd=None)

    result = repair(apply=False)
    assert result["corrected"] == 1
