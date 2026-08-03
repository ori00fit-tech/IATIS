"""
tests/test_direction_symmetry.py
------------------------------------
Forensic System Audit Phase 1, item B (2026-08-02) — tests for
research/diagnostics/direction_symmetry.py's AST-based, advisory-only
scanner.
"""
from __future__ import annotations

from pathlib import Path

from research.diagnostics.direction_symmetry import (
    DIRECTIONAL_TOKENS,
    run_direction_symmetry_audit,
    scan_file,
)


def _write(tmp_path: Path, source: str) -> Path:
    p = tmp_path / "synthetic_engine.py"
    p.write_text(source)
    return p


# ── Synthetic cases ──────────────────────────────────────────────────────

def test_mirrored_branches_produce_zero_findings(tmp_path):
    source = """
def decide(bias):
    if bias == Bias.BULLISH:
        score = 30
    elif bias == Bias.BEARISH:
        score = 30
    return score
"""
    findings = scan_file(_write(tmp_path, source))
    assert findings == []


def test_missing_mirror_flags_one_sided_branch(tmp_path):
    source = """
def decide(bias):
    if bias == Bias.BULLISH:
        score = 30
    return score
"""
    findings = scan_file(_write(tmp_path, source))
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "MISSING_MIRROR"
    assert f.token == "BULLISH"
    assert f.severity == "MEDIUM"
    assert f.function == "decide"


def test_asymmetric_constant_flags_differing_magnitudes(tmp_path):
    source = """
def decide(bias):
    if bias == Bias.BULLISH:
        score = 30
    elif bias == Bias.BEARISH:
        score = 50
    return score
"""
    findings = scan_file(_write(tmp_path, source))
    kinds = [f.kind for f in findings]
    assert "ASYMMETRIC_CONSTANT" in kinds
    asym = [f for f in findings if f.kind == "ASYMMETRIC_CONSTANT"][0]
    assert asym.severity == "INFO"
    assert asym.token == "BULLISH"


def test_buy_sell_string_tokens_also_detected(tmp_path):
    source = """
def decide(direction):
    if direction == "BUY":
        pass
"""
    findings = scan_file(_write(tmp_path, source))
    assert len(findings) == 1
    assert findings[0].token == "BUY"


def test_no_directional_tokens_at_all_produces_zero_findings(tmp_path):
    source = """
def unrelated(x):
    return x * 2
"""
    findings = scan_file(_write(tmp_path, source))
    assert findings == []


def test_malformed_python_file_does_not_raise(tmp_path):
    p = tmp_path / "broken.py"
    p.write_text("def f(:\n    this is not python")
    findings = scan_file(p)
    assert findings == []


def test_directional_tokens_are_two_complete_mirror_pairs():
    assert DIRECTIONAL_TOKENS == {
        "BULLISH": "BEARISH", "BEARISH": "BULLISH",
        "BUY": "SELL", "SELL": "BUY",
    }


# ── Real-repo regression anchor ──────────────────────────────────────────
# Proves the heuristic doesn't false-positive against code this session
# already manually verified symmetric by direct quote comparison.

def test_price_action_engine_has_no_missing_mirror_findings():
    root = Path(__file__).resolve().parents[1]
    findings = scan_file(root / "engines" / "price_action_engine.py")
    missing_mirrors = [f for f in findings if f.kind == "MISSING_MIRROR"]
    assert missing_mirrors == []


def test_voting_system_has_no_missing_mirror_findings():
    root = Path(__file__).resolve().parents[1]
    findings = scan_file(root / "confluence" / "voting_system.py")
    missing_mirrors = [f for f in findings if f.kind == "MISSING_MIRROR"]
    assert missing_mirrors == []


# ── Smoke test across every real engine file ─────────────────────────────

def test_run_direction_symmetry_audit_scans_all_real_engines_without_crashing():
    report = run_direction_symmetry_audit()
    assert len(report.files_scanned) >= 10
    assert any("engines/" in f for f in report.files_scanned)
    assert any("confluence/" in f for f in report.files_scanned)
    assert any("risk/" in f for f in report.files_scanned)
    # No assertions on which findings the 6 previously-unaudited engines
    # (ict, quant, wyckoff, divergence, sentiment, macro) produce — that's
    # the actual point of running the tool for real, not something to
    # pre-decide in a test.
    for f in report.findings:
        assert f.kind in ("MISSING_MIRROR", "ASYMMETRIC_CONSTANT")
        assert f.severity in ("MEDIUM", "INFO")
    assert "advisory" in report.caveat.lower()


def test_report_to_dict_is_json_serializable():
    import json

    report = run_direction_symmetry_audit()
    json.dumps(report.to_dict())
