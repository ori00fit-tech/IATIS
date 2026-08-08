"""tests/test_sentiment_engine.py — Sentiment engine tests, incl. H021's
MarketAux news-sentiment wiring (config-gated infra, engine stays disabled
in config/engines.yaml regardless — see research/results/registry.json)."""
from __future__ import annotations
from unittest.mock import patch
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.sentiment_engine import SentimentEngine


def _flat_df(n=60, price=1.1000, live=True):
    """Price sits exactly mid-range so the retail-proxy fallback stays
    neutral, isolating whatever else drives bias (COT / MarketAux).

    live=True (default, 2026-08-04 — BUG-005): the index ends at real
    wall-clock "now" so COT/MarketAux are not gated off by the
    is_live-only fix below — most of this file's existing tests are
    about COT/MarketAux's own logic, which only ever runs on a live
    (near-"now") bar post-fix. live=False anchors to a fixed historical
    date instead, for the tests that specifically exercise the gate."""
    if live:
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n, freq="1h")
    else:
        idx = pd.date_range("2021-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": [price] * n, "high": [price + 0.0005] * n,
        "low": [price - 0.0005] * n, "close": [price] * n,
        "volume": [100.0] * n,
    }, index=idx)


def _engine(symbol="EURUSD"):
    e = SentimentEngine()
    e.decision_tf = "H1"
    e._symbol = symbol
    return e


def test_no_cot_no_marketaux_stays_neutral(monkeypatch):
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    with patch("engines.sentiment_engine._load_cot_data", return_value=None):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.NEUTRAL
    assert out.raw["marketaux_available"] is False


def test_marketaux_bullish_drives_bias_when_cot_absent(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    fake_sentiment = {"symbol": "EURUSD", "article_count": 12, "mean_sentiment": 0.45, "scores": [0.45] * 12}
    with patch("engines.sentiment_engine._load_cot_data", return_value=None), \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.BULLISH
    assert out.raw["marketaux_available"] is True
    assert out.raw["marketaux_mean_sentiment"] == 0.45
    assert any("MarketAux" in r for r in out.reasons)


def test_marketaux_bearish_drives_bias_when_cot_absent(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    fake_sentiment = {"symbol": "EURUSD", "article_count": 8, "mean_sentiment": -0.3, "scores": [-0.3] * 8}
    with patch("engines.sentiment_engine._load_cot_data", return_value=None), \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.BEARISH


def test_marketaux_confirms_cot_boosts_score(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    cot = {"large_spec_net": 20000, "net_change_4w": 3000}
    fake_sentiment = {"symbol": "EURUSD", "article_count": 10, "mean_sentiment": 0.4, "scores": [0.4] * 10}
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot), \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.BULLISH
    assert any("MarketAux confirms" in r for r in out.reasons)


def test_marketaux_unavailable_falls_back_to_existing_behavior(monkeypatch):
    """No MARKETAUX_API_KEY set — engine behaves exactly as it did before
    H021's wiring (neutral here, since COT/retail proxy are both silent)."""
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    with patch("engines.sentiment_engine._load_cot_data", return_value=None):
        out = _engine().analyze({"H1": _flat_df()})
    assert out.bias == Bias.NEUTRAL
    assert out.raw["marketaux_mean_sentiment"] is None


# ── Bar-time lookahead gate (Forensic Audit, 2026-08-04 — BUG-005) ──────────
# Neither COT (_load_cot_data reads a single most-recently-downloaded
# snapshot) nor MarketAux (get_news_sentiment's hours_back window is
# measured from real wall-clock time.time()) has any per-date history — both
# always answer with TODAY's data. Reproduced directly: a bar dated
# 2021-01-10 returned BULLISH/score=48 from a COT snapshot captured
# 2026-08-03 before this fix. These tests pin the fix: both are skipped
# whenever the bar's own timestamp isn't close to real "now".

def test_cot_and_marketaux_are_skipped_for_a_historical_bar(monkeypatch):
    """Authoritative proof: even with real, valid COT + MarketAux data
    available, a historical (non-"live") bar must get NEITHER — only the
    causally-safe retail price-position proxy."""
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    cot = {"large_spec_net": 90000, "net_change_4w": 15000}  # would be BULLISH if used
    fake_sentiment = {"symbol": "EURUSD", "article_count": 10, "mean_sentiment": 0.4, "scores": [0.4] * 10}
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot) as mock_cot, \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment) as mock_ma:
        out = _engine().analyze({"H1": _flat_df(live=False)})
    mock_cot.assert_not_called()
    mock_ma.assert_not_called()
    assert out.raw["is_live"] is False
    assert out.raw["cot_available"] is False
    assert out.raw["marketaux_available"] is False
    assert any("skipped" in r.lower() for r in out.reasons)


def test_cot_and_marketaux_are_used_for_a_live_bar(monkeypatch):
    """Regression pin: a genuinely "live" (near-"now") bar must behave
    exactly as before this fix — real COT/MarketAux data is still used."""
    monkeypatch.setenv("MARKETAUX_API_KEY", "test_key")
    cot = {"large_spec_net": 90000, "net_change_4w": 15000}
    fake_sentiment = {"symbol": "EURUSD", "article_count": 10, "mean_sentiment": 0.4, "scores": [0.4] * 10}
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot) as mock_cot, \
         patch("fundamentals.marketaux_client.get_news_sentiment", return_value=fake_sentiment):
        out = _engine().analyze({"H1": _flat_df(live=True)})
    mock_cot.assert_called_once()
    assert out.raw["is_live"] is True
    assert out.bias == Bias.BULLISH
    assert out.raw["cot_available"] is True


def test_bar_time_is_live_helper_boundary():
    """Direct unit test of the gate helper itself: within tolerance ->
    True, beyond tolerance -> False, None -> False (never a guess)."""
    from datetime import datetime, timedelta, timezone as tz

    from engines.sentiment_engine import _bar_time_is_live

    now = datetime.now(tz.utc)
    assert _bar_time_is_live(now, tolerance_hours=72.0) is True
    assert _bar_time_is_live(now - timedelta(hours=1), tolerance_hours=72.0) is True
    assert _bar_time_is_live(now - timedelta(hours=100), tolerance_hours=72.0) is False
    assert _bar_time_is_live(now - timedelta(days=365 * 3), tolerance_hours=72.0) is False
    assert _bar_time_is_live(None, tolerance_hours=72.0) is False


# ── COT vintage/availability check (Engine Refinement V1, #367) ────────────
# reports/engine_refinement/ENGINE_INVENTORY.md's Sentiment entry: zero hits
# for report_date/publication_date/available_at anywhere in this file, even
# though scripts/download_cot.py has always written a real report_date the
# engine never once read. These tests pin the fix: a loaded COT snapshot's
# own report_date is now checked against the bar's own timestamp.

def test_parse_report_date_valid_and_invalid():
    from datetime import timezone as tz

    from engines.sentiment_engine import _parse_report_date

    parsed = _parse_report_date("2024-03-15")
    assert parsed is not None
    assert parsed.year == 2024 and parsed.month == 3 and parsed.day == 15
    assert parsed.tzinfo == tz.utc
    assert _parse_report_date(None) is None
    assert _parse_report_date("") is None
    assert _parse_report_date("not-a-date") is None
    assert _parse_report_date("2024/03/15") is None


def test_cot_snapshot_dated_after_the_bar_is_discarded(monkeypatch):
    """Authoritative proof of the vintage/availability fix: a COT snapshot
    whose own as-of date is AFTER the bar being evaluated must never be
    used, even on a genuinely "live" bar — the engine falls back exactly
    as if no COT data existed at all."""
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    cot = {"large_spec_net": 90000, "net_change_4w": 15000, "report_date": "2099-12-31"}
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot):
        out = _engine().analyze({"H1": _flat_df(live=True)})
    assert out.raw["cot_available"] is False
    assert out.raw["cot_report_date"] == "2099-12-31"
    assert out.raw["cot_report_age_days"] is None
    assert any("discarded" in r.lower() and "2099-12-31" in r for r in out.reasons)
    # Falls back to the retail proxy (neutral here, per _flat_df's design) —
    # never the BULLISH verdict this COT snapshot would otherwise produce.
    assert out.bias != Bias.BULLISH


def test_cot_snapshot_with_a_valid_past_report_date_is_used_and_aged(monkeypatch):
    """Regression pin: a real, past-dated COT snapshot on a live bar is
    used exactly as before this fix, and its age is now surfaced."""
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    cot = {"large_spec_net": 90000, "net_change_4w": 15000, "report_date": "2020-01-07"}
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot):
        out = _engine().analyze({"H1": _flat_df(live=True)})
    assert out.bias == Bias.BULLISH
    assert out.raw["cot_available"] is True
    assert out.raw["cot_report_date"] == "2020-01-07"
    assert out.raw["cot_report_age_days"] is not None
    assert out.raw["cot_report_age_days"] > 1000  # comfortably years old vs. "now"


def test_cot_snapshot_with_no_report_date_is_not_discarded(monkeypatch):
    """Unknown vintage (a test double, or a cache written before this
    field existed) must NOT be treated as unavailable -- absence of
    vintage information is not evidence of unavailability."""
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    cot = {"large_spec_net": 90000, "net_change_4w": 15000}  # no report_date key at all
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot):
        out = _engine().analyze({"H1": _flat_df(live=True)})
    assert out.bias == Bias.BULLISH
    assert out.raw["cot_available"] is True
    assert out.raw["cot_report_date"] is None
    assert out.raw["cot_report_age_days"] is None


def test_cot_snapshot_with_unparseable_report_date_is_not_discarded(monkeypatch):
    monkeypatch.delenv("MARKETAUX_API_KEY", raising=False)
    cot = {"large_spec_net": 90000, "net_change_4w": 15000, "report_date": "garbage"}
    with patch("engines.sentiment_engine._load_cot_data", return_value=cot):
        out = _engine().analyze({"H1": _flat_df(live=True)})
    assert out.bias == Bias.BULLISH
    assert out.raw["cot_available"] is True
    assert out.raw["cot_report_date"] == "garbage"
    assert out.raw["cot_report_age_days"] is None
