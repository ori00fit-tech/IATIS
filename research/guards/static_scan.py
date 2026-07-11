"""
research/guards/static_scan.py
----------------------------------
Static (source-code) leakage scanner — the STATIC half of the Research
Integrity Layer, complementary to research/guards/causal_guard.py's
runtime assertions.

ADVISORY ONLY. Never raises, never blocks, never fails an experiment. This
is a deliberate, stated design decision (see the module-level honesty note
below), not a missing feature.

Why advisory and not a hard gate: this scanner works by walking a script's
AST for a small set of KNOWN leakage-shaped patterns (a negative shift, a
backward-fill, a forward-direction merge_asof, an un-lagged rolling
window). It cannot understand intent. `df["mfe"].shift(-1)` is a real bug
in a live decision function and a completely legitimate line in a
post-hoc metric computed AFTER a backtest's test window has already
closed — the same syntax, opposite verdicts, and no AST pass can tell them
apart without richer context this scanner does not have. A tool that
turned this heuristic into a hard PASS/FAIL gate would either block
correct research (false positives) or, worse, hand out false confidence
on a "PASS" that missed a real leak via a pattern this scanner doesn't
know to look for (false negatives) — exactly the failure mode that
produced the trade-management "+100%" mirage in the first place. A silent
false PASS is more dangerous than no scanner at all, so this module never
produces one: every result is a WARNING for a human to read, never a
verdict to trust blindly.

Detected patterns (each is a heuristic, not a proof):
  - NEGATIVE_SHIFT: `.shift(-N)` / `.diff(-N)` for N > 0 — pulls a future
    row backward. HIGH severity.
  - BACKWARD_FILL: `.bfill()` / `.fillna(method="bfill"/"backfill")` —
    propagates a future value into earlier NaNs. HIGH severity.
  - FORWARD_MERGE_ASOF: `pd.merge_asof(..., direction="forward")` (or
    "nearest") — matches rows at or after the join key by construction.
    HIGH severity.
  - UNLAGGED_ROLLING: any `.rolling(...)` call — INFO severity only. This
    scanner cannot tell whether the result is later shifted before use,
    so it flags every occurrence for human review rather than guessing.

Usage:
    from research.guards.static_scan import scan_source
    report = scan_source("scripts/my_new_experiment.py")
    # report["verdict"] in {"CLEAN", "WARNINGS_FOUND"} — advisory label,
    # attach to a manifest via params["static_leakage_scan"] = report
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

_BFILL_METHODS = {"bfill", "backfill"}


class _Finding:
    __slots__ = ("line", "col", "pattern", "severity", "message")

    def __init__(self, line: int, col: int, pattern: str, severity: str, message: str):
        self.line, self.col, self.pattern, self.severity, self.message = (
            line, col, pattern, severity, message
        )

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line, "col": self.col, "pattern": self.pattern,
                "severity": self.severity, "message": self.message}


def _literal_int(node: ast.AST | None) -> int | None:
    """Best-effort literal-int extraction; returns None for anything not a
    plain int constant (variables, expressions) — those are silently
    skipped rather than guessed at, keeping this a low-noise scanner."""
    if node is None:
        return None
    try:
        val = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None
    return val if isinstance(val, int) else None


def _call_attr_name(node: ast.Call) -> str | None:
    return node.func.attr if isinstance(node.func, ast.Attribute) else None


def _kwarg(node: ast.Call, name: str) -> ast.AST | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


class _LeakageVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[_Finding] = []

    def visit_Call(self, node: ast.Call) -> None:
        method = _call_attr_name(node)

        if method in ("shift", "diff"):
            periods_node = node.args[0] if node.args else _kwarg(node, "periods")
            periods = _literal_int(periods_node)
            if periods is not None and periods < 0:
                self.findings.append(_Finding(
                    node.lineno, node.col_offset, "NEGATIVE_SHIFT", "HIGH",
                    f".{method}({periods}) pulls a future row backward — verify this "
                    f"is not used at decision time (legitimate only in post-hoc metrics "
                    f"computed after the window it references has already closed)."
                ))

        elif method == "bfill":
            self.findings.append(_Finding(
                node.lineno, node.col_offset, "BACKWARD_FILL", "HIGH",
                ".bfill() propagates a future value into earlier NaNs."
            ))

        elif method == "fillna":
            # method= is keyword-only in modern pandas; a positional first
            # arg to fillna is the fill value, not the method, so it's not
            # a leakage-relevant case and is intentionally not scanned.
            method_arg = _kwarg(node, "method")
            method_val = method_arg.value if isinstance(method_arg, ast.Constant) else None
            if method_val in _BFILL_METHODS:
                self.findings.append(_Finding(
                    node.lineno, node.col_offset, "BACKWARD_FILL", "HIGH",
                    f".fillna(method='{method_val}') propagates a future value into "
                    f"earlier NaNs."
                ))

        elif method == "rolling":
            self.findings.append(_Finding(
                node.lineno, node.col_offset, "UNLAGGED_ROLLING", "INFO",
                ".rolling(...) — verify the result is shifted/lagged before being used "
                "at decision time; pandas rolling windows are trailing but INCLUDE the "
                "current row by default, which is only safe if 'current row' means "
                "'already closed' at the moment this value is consumed."
            ))

        # pd.merge_asof(...) is an Attribute call (func = pd.merge_asof), so
        # it's already captured by `method = _call_attr_name(node)` above.
        elif method == "merge_asof":
            direction_arg = _kwarg(node, "direction")
            direction_val = direction_arg.value if isinstance(direction_arg, ast.Constant) else None
            if direction_val in ("forward", "nearest"):
                self.findings.append(_Finding(
                    node.lineno, node.col_offset, "FORWARD_MERGE_ASOF", "HIGH",
                    f"merge_asof(direction='{direction_val}') matches rows at or after "
                    f"the join key — use direction='backward' (the pandas default) for "
                    f"point-in-time joins."
                ))

        self.generic_visit(node)


def scan_source(path: str | Path) -> dict[str, Any]:
    """Scan one Python file for known leakage-shaped patterns. Advisory
    only — see module docstring. Never raises on scan failures other than
    a genuine syntax error in the target file (reported as a finding, not
    an exception, so a caller building a manifest never crashes on this)."""
    path = Path(path)
    try:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return {
            "file": str(path),
            "verdict": "SCAN_FAILED",
            "findings": [{"line": exc.lineno or 0, "col": exc.offset or 0,
                          "pattern": "SYNTAX_ERROR", "severity": "HIGH",
                          "message": f"Could not parse: {exc.msg}"}],
            "high_severity_count": 1,
        }

    visitor = _LeakageVisitor()
    visitor.visit(tree)
    findings = [f.to_dict() for f in visitor.findings]
    high = sum(1 for f in findings if f["severity"] == "HIGH")

    return {
        "file": str(path),
        "verdict": "CLEAN" if not findings else "WARNINGS_FOUND",
        "note": ("Heuristic source scan — a clean result does NOT prove the absence "
                 "of look-ahead; it means no KNOWN pattern was matched. Advisory only, "
                 "never blocks. Read research/guards/static_scan.py's docstring before "
                 "trusting a result either way."),
        "findings": findings,
        "high_severity_count": high,
        "info_count": len(findings) - high,
    }


def scan_paths(paths: list[str | Path]) -> dict[str, Any]:
    """Scan multiple files (e.g. every source file an experiment touches)
    and roll them into one advisory report."""
    reports = [scan_source(p) for p in paths]
    return {
        "files_scanned": len(reports),
        "verdict": "CLEAN" if all(r["verdict"] == "CLEAN" for r in reports) else "WARNINGS_FOUND",
        "total_high_severity": sum(r.get("high_severity_count", 0) for r in reports),
        "reports": reports,
    }
