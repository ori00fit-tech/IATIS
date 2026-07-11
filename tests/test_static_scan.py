"""
tests/test_static_scan.py
-----------------------------
research/guards/static_scan.py: heuristic AST scan for leakage-shaped
code patterns. ADVISORY ONLY — asserts on findings/verdicts, never on
anything being "blocked" (the module never blocks by design).
"""
from __future__ import annotations

from research.guards.static_scan import scan_paths, scan_source


def _write(tmp_path, name: str, code: str):
    p = tmp_path / name
    p.write_text(code)
    return p


def test_clean_script_has_no_findings(tmp_path):
    p = _write(tmp_path, "clean.py", "x = df['close'].shift(1)\n")
    report = scan_source(p)
    assert report["verdict"] == "CLEAN"
    assert report["findings"] == []


def test_negative_shift_flagged_high(tmp_path):
    p = _write(tmp_path, "bad.py", "x = df['close'].shift(-1)\n")
    report = scan_source(p)
    assert report["verdict"] == "WARNINGS_FOUND"
    assert report["high_severity_count"] == 1
    assert report["findings"][0]["pattern"] == "NEGATIVE_SHIFT"


def test_positive_shift_not_flagged(tmp_path):
    p = _write(tmp_path, "ok.py", "x = df['close'].shift(1)\n")
    report = scan_source(p)
    assert report["verdict"] == "CLEAN"


def test_negative_diff_flagged(tmp_path):
    p = _write(tmp_path, "bad_diff.py", "x = df['close'].diff(-2)\n")
    report = scan_source(p)
    assert report["findings"][0]["pattern"] == "NEGATIVE_SHIFT"


def test_shift_with_variable_periods_not_guessed_at(tmp_path):
    # Non-literal argument -> can't determine sign statically -> no finding,
    # not a guess. This is the scanner declining to overclaim.
    p = _write(tmp_path, "dynamic.py", "x = df['close'].shift(n)\n")
    report = scan_source(p)
    assert report["verdict"] == "CLEAN"


def test_bfill_method_flagged_high(tmp_path):
    p = _write(tmp_path, "bfill.py", "x = df['close'].bfill()\n")
    report = scan_source(p)
    assert report["findings"][0]["pattern"] == "BACKWARD_FILL"
    assert report["high_severity_count"] == 1


def test_fillna_method_bfill_flagged(tmp_path):
    p = _write(tmp_path, "fillna_bfill.py", "x = df['close'].fillna(method='bfill')\n")
    report = scan_source(p)
    assert report["findings"][0]["pattern"] == "BACKWARD_FILL"


def test_fillna_ffill_not_flagged(tmp_path):
    p = _write(tmp_path, "fillna_ffill.py", "x = df['close'].fillna(method='ffill')\n")
    report = scan_source(p)
    assert report["verdict"] == "CLEAN"


def test_fillna_with_value_not_flagged(tmp_path):
    p = _write(tmp_path, "fillna_value.py", "x = df['close'].fillna(0)\n")
    report = scan_source(p)
    assert report["verdict"] == "CLEAN"


def test_rolling_flagged_info_only(tmp_path):
    p = _write(tmp_path, "rolling.py", "x = df['close'].rolling(20).mean()\n")
    report = scan_source(p)
    assert report["findings"][0]["pattern"] == "UNLAGGED_ROLLING"
    assert report["findings"][0]["severity"] == "INFO"
    assert report["high_severity_count"] == 0
    assert report["info_count"] == 1
    # advisory: still WARNINGS_FOUND, but zero high-severity items
    assert report["verdict"] == "WARNINGS_FOUND"


def test_merge_asof_forward_flagged_high(tmp_path):
    p = _write(tmp_path, "asof.py",
               "pd.merge_asof(a, b, on='ts', direction='forward')\n")
    report = scan_source(p)
    assert report["findings"][0]["pattern"] == "FORWARD_MERGE_ASOF"
    assert report["high_severity_count"] == 1


def test_merge_asof_backward_default_not_flagged(tmp_path):
    p = _write(tmp_path, "asof_ok.py",
               "pd.merge_asof(a, b, on='ts', direction='backward')\n")
    report = scan_source(p)
    assert report["verdict"] == "CLEAN"


def test_merge_asof_no_direction_not_flagged(tmp_path):
    # backward is pandas's default; omitting direction is safe.
    p = _write(tmp_path, "asof_default.py", "pd.merge_asof(a, b, on='ts')\n")
    report = scan_source(p)
    assert report["verdict"] == "CLEAN"


def test_syntax_error_reported_not_raised(tmp_path):
    p = _write(tmp_path, "broken.py", "def f(:\n")
    report = scan_source(p)
    assert report["verdict"] == "SCAN_FAILED"
    assert report["findings"][0]["pattern"] == "SYNTAX_ERROR"


def test_never_raises_advisory_contract(tmp_path):
    # The module's central promise: no exception path for any input file.
    for code in ("x.shift(-1)", "def f(:", "", "x = 1 + 1"):
        p = _write(tmp_path, "any.py", code)
        report = scan_source(p)  # must not raise
        assert "verdict" in report


def test_multiple_findings_in_one_file(tmp_path):
    p = _write(tmp_path, "multi.py",
               "a = df.shift(-1)\nb = df.bfill()\nc = df.rolling(5).mean()\n")
    report = scan_source(p)
    assert len(report["findings"]) == 3
    assert report["high_severity_count"] == 2
    assert report["info_count"] == 1


def test_scan_paths_aggregates_multiple_files(tmp_path):
    clean = _write(tmp_path, "clean.py", "x = 1\n")
    dirty = _write(tmp_path, "dirty.py", "x = df.shift(-1)\n")
    report = scan_paths([clean, dirty])
    assert report["files_scanned"] == 2
    assert report["verdict"] == "WARNINGS_FOUND"
    assert report["total_high_severity"] == 1


def test_scan_paths_all_clean_is_clean():
    report = scan_paths([])
    assert report["verdict"] == "CLEAN"
    assert report["files_scanned"] == 0


def test_manifest_convention_key_is_documented():
    """No functional assertion — this pins the documented manifest
    integration key name so a future rename is a deliberate, visible
    diff rather than a silent drift from what the docstring promises."""
    from research.guards import static_scan
    assert "static_leakage_scan" in static_scan.__doc__
