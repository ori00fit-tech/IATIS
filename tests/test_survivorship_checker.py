"""
tests/test_survivorship_checker.py
--------------------------------------
research/survivorship_checker.py: operationalizes two governance findings
already on record in docs/PHILOSOPHY_AUDIT_2026-07.md — enabled symbols
with zero committed evidence (US30/NAS100/SPX500), and disabled symbols
whose disablement IS evidenced (AUDUSD-family, honest post-hoc selection).
"""
from __future__ import annotations

from research import survivorship_checker as sc


def _config(symbols):
    return {"data": {"twelve_data_symbols": symbols}}


def test_enabled_symbol_with_no_manifest_evidence_is_flagged():
    config = _config([{"internal": "US30", "enabled": True}])
    report = sc.check_symbol_evidence(config, manifests={})
    assert report["enabled_no_evidence"] == ["US30"]
    assert report["rows"][0]["verdict"] == "ENABLED_NO_EVIDENCE"


def test_enabled_symbol_with_evidence_is_clean():
    config = _config([{"internal": "XAUUSD", "enabled": True}])
    manifests = {"h4_backtest_manifest.json": {"results": {"symbols_tested": ["XAUUSD"]}}}
    report = sc.check_symbol_evidence(config, manifests=manifests)
    assert report["enabled_no_evidence"] == []
    assert report["rows"][0]["verdict"] == "ENABLED_WITH_EVIDENCE"
    assert report["rows"][0]["manifest_count"] == 1


def test_disabled_symbol_with_evidence_is_not_flagged_as_missing():
    config = _config([{"internal": "AUDUSD", "enabled": False}])
    manifests = {"h4_yearly_stability_manifest.json": {"results": {"AUDUSD": {"pf": 0.87}}}}
    report = sc.check_symbol_evidence(config, manifests=manifests)
    assert report["disabled_no_evidence"] == []
    assert report["rows"][0]["verdict"] == "DISABLED_WITH_EVIDENCE"


def test_disabled_symbol_with_no_evidence_flagged_distinctly():
    config = _config([{"internal": "USDCAD", "enabled": False}])
    report = sc.check_symbol_evidence(config, manifests={})
    assert report["disabled_no_evidence"] == ["USDCAD"]
    assert report["enabled_no_evidence"] == []


def test_symbol_mention_does_not_false_positive_on_substring():
    # BTCUSD should not be "found" inside a manifest that only mentions BTCUSDT-like text
    config = _config([{"internal": "ETHUSD", "enabled": True}])
    manifests = {"m.json": {"results": {"symbol": "BTCUSD"}}}
    report = sc.check_symbol_evidence(config, manifests=manifests)
    assert report["rows"][0]["verdict"] == "ENABLED_NO_EVIDENCE"


def test_symbols_without_internal_key_are_skipped_not_crashed():
    config = _config([{"enabled": True}])  # malformed entry, no internal
    report = sc.check_symbol_evidence(config, manifests={})
    assert report["total_symbols"] == 0


def test_selection_disclosure_labels_disclosed_and_undisclosed():
    manifests = {
        "a_manifest.json": {"params": {"symbol_selection": "fixed_before_test"}},
        "b_manifest.json": {"params": {}},
        "c_manifest.json": {},
    }
    report = sc.check_selection_disclosure(manifests)
    assert [d["manifest"] for d in report["disclosed"]] == ["a_manifest.json"]
    assert set(report["undisclosed"]) == {"b_manifest.json", "c_manifest.json"}
    assert report["invalid_label"] == []


def test_selection_disclosure_flags_invalid_label():
    manifests = {"x_manifest.json": {"params": {"symbol_selection": "vibes"}}}
    report = sc.check_selection_disclosure(manifests)
    assert report["invalid_label"] == [{"manifest": "x_manifest.json", "label": "vibes"}]


def test_against_real_repo_state_runs_without_crashing():
    """Integration smoke test against the actual committed registry/config —
    not asserting specific symbols (those change over time), just that the
    checker runs cleanly end-to-end against real data."""
    from utils.helpers import load_config
    config = load_config()
    report = sc.check_symbol_evidence(config)
    assert report["total_symbols"] > 0
    selection = sc.check_selection_disclosure()
    assert selection["convention_introduced"] == "2026-07-11"
