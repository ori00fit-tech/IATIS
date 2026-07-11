"""
tests/test_generate_research_report.py
------------------------------------------
scripts/generate_research_report.py — mechanical registry+manifest ->
Markdown aggregation. Audit follow-up, 2026-07-11.
"""
from __future__ import annotations

from scripts.generate_research_report import (
    build_hypotheses_table, build_manifest_table, build_report, load_manifests, load_registry,
)


def test_build_hypotheses_table_sorts_by_status_then_id():
    hyps = {
        "H002": {"status": "FAILED", "title": "b", "last_updated": "2026-01-01"},
        "H001": {"status": "PASSED", "title": "a", "last_updated": "2026-01-01"},
    }
    table = build_hypotheses_table(hyps)
    lines = table.splitlines()
    # PASSED (rank 0) must appear before FAILED (rank 5)
    passed_line = next(l for l in lines if "H001" in l)
    failed_line = next(l for l in lines if "H002" in l)
    assert lines.index(passed_line) < lines.index(failed_line)


def test_build_hypotheses_table_escapes_pipe_in_title():
    hyps = {"H001": {"status": "RESEARCH", "title": "a | b", "last_updated": ""}}
    table = build_hypotheses_table(hyps)
    assert "a \\| b" in table


def test_build_manifest_table_marks_non_reproducible():
    manifests = {
        "x_manifest.json": {
            "kind": "test_kind", "generated_at": "2026-01-01T00:00:00Z",
            "git": {"commit": "abcdef1234567890"}, "reproducible": False,
        }
    }
    table = build_manifest_table(manifests)
    assert "NO" in table
    assert "abcdef12" in table  # short commit


def test_build_report_counts_statuses_correctly():
    registry = {"hypotheses": {
        "H001": {"status": "FAILED", "title": "a", "last_updated": ""},
        "H002": {"status": "FAILED", "title": "b", "last_updated": ""},
        "H003": {"status": "PASSED", "title": "c", "last_updated": ""},
    }}
    report = build_report(registry, manifests={})
    assert "| FAILED | 2 |" in report
    assert "| PASSED | 1 |" in report
    assert "Hypotheses (3)" in report


def test_build_report_counts_reproducible_manifests():
    manifests = {
        "a_manifest.json": {"kind": "k", "generated_at": "", "git": {}, "reproducible": True},
        "b_manifest.json": {"kind": "k", "generated_at": "", "git": {}, "reproducible": False},
    }
    report = build_report({"hypotheses": {}}, manifests)
    assert "2 total, 1 reproducible, 1 NOT reproducible" in report


def test_against_real_repo_state_produces_nonempty_report():
    """Integration smoke test: run against the actual committed registry
    and manifests, not asserting exact content (it changes over time), just
    that it runs cleanly and includes every real hypothesis."""
    registry = load_registry()
    manifests = load_manifests()
    report = build_report(registry, manifests)
    assert "H019" in report  # this session's pre-registered hypothesis
    assert "H009" in report
    assert len(manifests) > 0
    assert "IATIS Research Snapshot" in report
