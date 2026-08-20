"""Tests for research/h021_readiness.py — the single, real, computed-fresh
H021 (MarketAux sentiment A/B) data-readiness verdict. Every assertion here
is checked against records this test itself constructs; none of the
numbers are copied from any real VPS observation."""
import json

import pytest

from research.h021_readiness import (
    MIN_INFORMATIVE_TEST_RECORDS,
    PRIMARY_CARRIERS,
    TRAIN_FRACTION,
    compute_carrier_readiness,
    compute_h021_readiness,
    load_records,
)


def _rec(symbol, day, article_count=1, mean_sentiment=0.1):
    return {
        "collected_at": f"2026-08-{day:02d}T00:00:00+00:00",
        "symbol": symbol,
        "article_count": article_count,
        "mean_sentiment": mean_sentiment,
        "scores": [mean_sentiment],
    }


def test_load_records_missing_file_returns_empty(tmp_path):
    assert load_records(tmp_path / "nope.jsonl") == []


def test_load_records_skips_malformed_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text('{"symbol": "XAUUSD"}\nnot json at all\n{"symbol": "BTCUSD"}\n')
    records = load_records(p)
    assert len(records) == 2
    assert records[0]["symbol"] == "XAUUSD"
    assert records[1]["symbol"] == "BTCUSD"


def test_load_records_skips_blank_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text('{"symbol": "XAUUSD"}\n\n\n{"symbol": "BTCUSD"}\n')
    assert len(load_records(p)) == 2


def test_carrier_readiness_zero_records_not_ready():
    r = compute_carrier_readiness([], "XAUUSD")
    assert r.total_records == 0
    assert r.ready is False
    assert "No collected records" in r.note


def test_carrier_readiness_below_threshold_not_ready():
    # 20 informative TEST records — below the 30-record threshold.
    records = [_rec("XAUUSD", d, mean_sentiment=0.2) for d in range(1, 21)]
    r = compute_carrier_readiness(records, "XAUUSD", train_fraction=0.0, min_informative_test_records=30)
    assert r.total_records == 20
    assert r.test_records == 20
    assert r.test_sentiment_informative == 20
    assert r.ready is False
    assert "INSUFFICIENT_DATA" in r.note or "below" in r.note


def test_carrier_readiness_at_threshold_is_ready():
    records = [_rec("XAUUSD", d, mean_sentiment=0.2) for d in range(1, 31)]
    r = compute_carrier_readiness(records, "XAUUSD", train_fraction=0.0, min_informative_test_records=30)
    assert r.test_sentiment_informative == 30
    assert r.ready is True


def test_carrier_readiness_distinguishes_raw_from_informative():
    # 40 TEST records total, but only 25 have real (nonzero) sentiment —
    # the raw count clears 30, the informative count does NOT. Readiness
    # must follow the informative count, never the raw one.
    records = []
    for d in range(1, 41):
        informative = d <= 25
        records.append(_rec("XAUUSD", d, article_count=1, mean_sentiment=0.3 if informative else 0.0))
    r = compute_carrier_readiness(records, "XAUUSD", train_fraction=0.0, min_informative_test_records=30)
    assert r.test_records == 40
    assert r.test_sentiment_informative == 25
    assert r.ready is False


def test_carrier_readiness_zero_sentiment_not_informative_but_has_article():
    records = [_rec("XAUUSD", 1, article_count=3, mean_sentiment=0.0)]
    r = compute_carrier_readiness(records, "XAUUSD", train_fraction=0.0)
    assert r.test_article_gt_zero == 1
    assert r.test_sentiment_informative == 0


def test_carrier_readiness_chronological_split_by_collected_at():
    # Deliberately out-of-order input — must sort by collected_at before splitting.
    records = [_rec("XAUUSD", d, mean_sentiment=0.5) for d in [5, 1, 3, 2, 4]]
    r = compute_carrier_readiness(records, "XAUUSD", train_fraction=0.6)
    assert r.total_records == 5
    assert r.train_records == 3
    assert r.test_records == 2
    assert r.earliest_collected_at == "2026-08-01T00:00:00+00:00"
    assert r.latest_collected_at == "2026-08-05T00:00:00+00:00"


def test_carrier_readiness_ignores_other_symbols():
    records = [_rec("XAUUSD", 1), _rec("BTCUSD", 2), _rec("ETHUSD", 3)]
    r = compute_carrier_readiness(records, "XAUUSD")
    assert r.total_records == 1


def test_h021_readiness_missing_file(tmp_path):
    report = compute_h021_readiness(tmp_path / "does_not_exist.jsonl")
    assert report.log_exists is False
    assert report.overall_ready is False
    assert report.total_records_all_symbols == 0
    assert len(report.carriers) == len(PRIMARY_CARRIERS)
    assert all(not c.ready for c in report.carriers)
    assert "does not exist" in report.note


def test_h021_readiness_empty_file(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text("")
    report = compute_h021_readiness(p)
    assert report.log_exists is True
    assert report.total_records_all_symbols == 0
    assert report.overall_ready is False
    assert "zero parseable" in report.note


def test_h021_readiness_all_carriers_ready(tmp_path):
    p = tmp_path / "log.jsonl"
    lines = []
    for symbol in PRIMARY_CARRIERS:
        for d in range(1, 51):
            lines.append(json.dumps(_rec(symbol, d, mean_sentiment=0.3)))
    p.write_text("\n".join(lines) + "\n")
    report = compute_h021_readiness(p, train_fraction=0.0, min_informative_test_records=30)
    assert report.total_records_all_symbols == 50 * len(PRIMARY_CARRIERS)
    assert report.overall_ready is True
    assert all(c.ready for c in report.carriers)
    assert "READY" in report.note


def test_h021_readiness_two_of_three_ready_is_overall_not_ready(tmp_path):
    p = tmp_path / "log.jsonl"
    lines = []
    for symbol in ("XAUUSD", "BTCUSD"):
        for d in range(1, 51):
            lines.append(json.dumps(_rec(symbol, d, mean_sentiment=0.3)))
    # ETHUSD gets zero records at all.
    p.write_text("\n".join(lines) + "\n")
    report = compute_h021_readiness(p, train_fraction=0.0, min_informative_test_records=30)
    assert report.overall_ready is False
    eth = next(c for c in report.carriers if c.symbol == "ETHUSD")
    assert eth.ready is False
    assert "ETHUSD" in report.note


def test_h021_readiness_default_constants_match_registered_decision_rule():
    # H021's registry.json decision rule: chronological TRAIN(65%)/TEST(35%)
    # split, ~30 sentiment-informed decisions per carrier on TEST.
    assert TRAIN_FRACTION == pytest.approx(0.65)
    assert MIN_INFORMATIVE_TEST_RECORDS == 30
    assert PRIMARY_CARRIERS == ("XAUUSD", "BTCUSD", "ETHUSD")


def test_to_dict_shapes_are_json_safe(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(json.dumps(_rec("XAUUSD", 1, mean_sentiment=0.3)) + "\n")
    report = compute_h021_readiness(p)
    d = report.to_dict()
    json.dumps(d)  # must not raise
    assert "carriers" in d and isinstance(d["carriers"], list)
    assert d["carriers"][0]["symbol"] == "XAUUSD"
