"""tests/test_collect_marketaux_sentiment.py — the accumulation logic
(collect_once/append_records), mocked at fundamentals.marketaux_client's
boundary so no real API calls or MARKETAUX_API_KEY are needed."""
from __future__ import annotations

import json
from unittest.mock import patch

from scripts.collect_marketaux_sentiment import append_records, collect_once


def _result(symbol="EURUSD", article_count=3, mean=0.25):
    return {"symbol": symbol, "article_count": article_count,
            "mean_sentiment": mean, "scores": [mean] * article_count}


def test_collect_once_appends_collected_at_to_each_record():
    with patch("fundamentals.marketaux_client.get_news_sentiment",
              side_effect=lambda symbol: _result(symbol)) as mocked:
        records = collect_once(["EURUSD", "BTCUSD"])
    assert mocked.call_count == 2
    assert len(records) == 2
    assert all("collected_at" in r for r in records)
    assert records[0]["symbol"] == "EURUSD"
    assert records[1]["symbol"] == "BTCUSD"


def test_collect_once_skips_symbols_with_no_signal():
    def _fake(symbol):
        return _result(symbol) if symbol == "BTCUSD" else None

    with patch("fundamentals.marketaux_client.get_news_sentiment", side_effect=_fake):
        records = collect_once(["XAUUSD", "BTCUSD"])  # XAUUSD unmapped -> None
    assert len(records) == 1
    assert records[0]["symbol"] == "BTCUSD"


def test_collect_once_with_no_signal_anywhere_returns_empty_list():
    with patch("fundamentals.marketaux_client.get_news_sentiment", return_value=None):
        records = collect_once(["EURUSD"])
    assert records == []


def test_append_records_is_append_only_across_calls(tmp_path):
    path = tmp_path / "log.jsonl"
    append_records([{"collected_at": "t1", **_result("EURUSD")}], path=path)
    append_records([{"collected_at": "t2", **_result("BTCUSD")}], path=path)

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert rows[0]["symbol"] == "EURUSD" and rows[0]["collected_at"] == "t1"
    assert rows[1]["symbol"] == "BTCUSD" and rows[1]["collected_at"] == "t2"


def test_append_records_with_empty_list_does_not_create_the_file(tmp_path):
    path = tmp_path / "log.jsonl"
    append_records([], path=path)
    assert not path.exists()


def test_append_records_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "log.jsonl"
    append_records([{"collected_at": "t1", **_result()}], path=path)
    assert path.exists()
