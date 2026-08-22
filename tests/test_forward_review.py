"""tests/test_forward_review.py
---------------------------------
scripts/forward_review.py's evaluate_rules() — extracted out of main()'s
own inline loop so execution/post_trade_monitor.py's scan_forward_review()
can reuse the identical comparison logic instead of re-deriving it.
"""
from __future__ import annotations

from scripts.forward_review import evaluate_rules

_RULE = {
    "R1": {
        "statement": "cut FX if PF < 1.0 at n >= 40",
        "bucket": "fx", "metric": "pf", "op": "<", "threshold": 1.0,
        "min_n": 40, "action": "disable FX",
    },
    "R2": {
        "statement": "confirm carriers if PF >= 1.2 at n >= 100",
        "bucket": "carriers", "metric": "pf", "op": ">=", "threshold": 1.2,
        "min_n": 100, "action": "open live-capital discussion",
    },
}


def test_insufficient_n_is_never_triggered():
    buckets = {"fx": {"n": 10, "pf": 0.5, "wr": 30.0}, "carriers": {"n": 0, "pf": None, "wr": None}}
    results = evaluate_rules(_RULE, buckets)
    by_id = {r["rule_id"]: r for r in results}
    assert by_id["R1"]["insufficient_n"] is True
    assert by_id["R1"]["triggered"] is False
    assert by_id["R2"]["insufficient_n"] is True


def test_less_than_rule_triggers_when_metric_below_threshold():
    buckets = {"fx": {"n": 45, "pf": 0.8, "wr": 40.0}, "carriers": {"n": 0, "pf": None, "wr": None}}
    results = evaluate_rules(_RULE, buckets)
    by_id = {r["rule_id"]: r for r in results}
    assert by_id["R1"]["triggered"] is True
    assert by_id["R1"]["value"] == 0.8
    assert by_id["R1"]["action"] == "disable FX"


def test_less_than_rule_does_not_trigger_when_metric_at_or_above_threshold():
    buckets = {"fx": {"n": 45, "pf": 1.5, "wr": 60.0}, "carriers": {"n": 0, "pf": None, "wr": None}}
    results = evaluate_rules(_RULE, buckets)
    assert {r["rule_id"]: r["triggered"] for r in results}["R1"] is False


def test_gte_rule_triggers_when_metric_at_or_above_threshold():
    buckets = {"fx": {"n": 0, "pf": None, "wr": None}, "carriers": {"n": 120, "pf": 1.2, "wr": 55.0}}
    results = evaluate_rules(_RULE, buckets)
    by_id = {r["rule_id"]: r for r in results}
    assert by_id["R2"]["triggered"] is True


def test_gte_rule_does_not_trigger_below_threshold():
    buckets = {"fx": {"n": 0, "pf": None, "wr": None}, "carriers": {"n": 120, "pf": 1.19, "wr": 55.0}}
    results = evaluate_rules(_RULE, buckets)
    assert {r["rule_id"]: r["triggered"] for r in results}["R2"] is False


def test_underscore_prefixed_and_non_dict_keys_are_skipped():
    rules = {**_RULE, "_meta": {"note": "not a rule"}, "_comment": "also not a rule"}
    buckets = {"fx": {"n": 45, "pf": 0.5, "wr": 30.0}, "carriers": {"n": 0, "pf": None, "wr": None}}
    results = evaluate_rules(rules, buckets)
    assert {r["rule_id"] for r in results} == {"R1", "R2"}


def test_missing_bucket_treated_as_zero_n_insufficient():
    buckets = {"fx": {"n": 45, "pf": 0.5, "wr": 30.0}}  # "carriers" bucket absent entirely
    results = evaluate_rules(_RULE, buckets)
    by_id = {r["rule_id"]: r for r in results}
    assert by_id["R2"]["n"] == 0
    assert by_id["R2"]["insufficient_n"] is True
    assert by_id["R2"]["triggered"] is False


def test_result_shape_carries_every_documented_field():
    buckets = {"fx": {"n": 45, "pf": 0.5, "wr": 30.0}, "carriers": {"n": 0, "pf": None, "wr": None}}
    result = evaluate_rules(_RULE, buckets)[0]
    for key in ("rule_id", "statement", "bucket", "n", "min_n", "metric", "value",
                "op", "threshold", "triggered", "action", "insufficient_n"):
        assert key in result
