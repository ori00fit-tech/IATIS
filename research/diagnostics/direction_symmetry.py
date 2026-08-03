"""
research/diagnostics/direction_symmetry.py
--------------------------------------------
Forensic System Audit Phase 1, item B (2026-08-02) — a static, AST-based
scanner for one-sided directional (BULLISH/BEARISH, BUY/SELL) branches in
engines/*.py, confluence/*.py, and risk/*.py.

ADVISORY ONLY. Never raises, never blocks, never fails a build or a test —
same design posture as research/guards/static_scan.py's leakage scanner.
A finding here is a LEAD for a human code reviewer, not proof of a bug.

Why this is a NEW, distinct check from what already exists: this session's
own backtest/meta_analysis.py already compares BUY-vs-SELL *statistical
outcomes* (win_rate/PF) via a real binomial-sign-test — that answers "does
the SYSTEM behave symmetrically in practice." This module answers a
different, complementary question: "does the CODE contain a branch for one
direction with no mirrored branch for the other, or two mirrored branches
that assign visibly different magnitudes." Neither check can substitute
for the other — a code asymmetry might never manifest in a given dataset's
statistics, and a statistical asymmetry might be a real market effect with
perfectly symmetric code underneath it.

Verified, by direct manual read this session, that the core entry/SL/TP/
close-price formulas in backtesting/backtest_engine.py and
confluence/voting_system.py's tally_votes() are symmetric — this scanner's
own regression test (tests/test_direction_symmetry.py) asserts it produces
ZERO MISSING_MIRROR findings against those already-audited files, so a
false-positive regression there would be caught immediately.

Two heuristics, each best-effort:
  - MISSING_MIRROR (MEDIUM): a function references one side of a
    directional token family (e.g. Bias.BULLISH or the string "BUY")
    somewhere in its own body, but never the mirror side anywhere in that
    same body.
  - ASYMMETRIC_CONSTANT (INFO): an `if <token>: ... elif <mirror token>:
    ...` pair where the same variable is assigned a numeric literal in
    both branches, but the two literals differ in magnitude. Often
    intentional (e.g. a deliberately asymmetric conviction weight) —
    always worth a human look, never a verdict.

Usage:
    from research.diagnostics.direction_symmetry import run_direction_symmetry_audit
    report = run_direction_symmetry_audit()
    # report.findings — never filtered to "the important ones", every
    # finding is reported, matching this project's own "report everything,
    # never auto-select a winner/verdict" convention.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DIRECTIONAL_TOKENS: dict[str, str] = {
    "BULLISH": "BEARISH", "BEARISH": "BULLISH",
    "BUY": "SELL", "SELL": "BUY",
}

_SCAN_GLOBS: tuple[str, ...] = ("engines/*.py", "confluence/*.py", "risk/*.py")

_CAVEAT = (
    "Static, heuristic, advisory-only — never blocks a build, never fails a "
    "test. A finding here is a LEAD for a human code reviewer, not proof of "
    "a bug. Verified-symmetric formulas already manually audited this "
    "session (backtest_engine.py entry/SL/TP/close, confluence/"
    "voting_system.py tally_votes, backtest/runner.py rr_actual) correctly "
    "produce zero MISSING_MIRROR findings — see this module's own "
    "regression test."
)


@dataclass(frozen=True)
class SymmetryFinding:
    file: str
    line: int
    function: str
    kind: str            # MISSING_MIRROR | ASYMMETRIC_CONSTANT
    token: str
    detail: str
    severity: str         # MEDIUM | INFO

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file, "line": self.line, "function": self.function,
            "kind": self.kind, "token": self.token, "detail": self.detail,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class DirectionSymmetryReport:
    generated_at: str
    files_scanned: list[str]
    findings: list[SymmetryFinding] = field(default_factory=list)
    caveat: str = _CAVEAT

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "files_scanned": self.files_scanned,
            "findings": [f.to_dict() for f in self.findings],
            "caveat": self.caveat,
        }


def _token_in_node(node: ast.AST) -> str | None:
    """A directional token if `node` is a Bias.BULLISH/BEARISH-style
    attribute access or a "BUY"/"SELL" string literal, else None."""
    if isinstance(node, ast.Attribute) and node.attr in DIRECTIONAL_TOKENS:
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in DIRECTIONAL_TOKENS:
        return node.value
    return None


def _find_token_in_subtree(node: ast.AST) -> str | None:
    for sub in ast.walk(node):
        tok = _token_in_node(sub)
        if tok:
            return tok
    return None


def _numeric_const(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _numeric_const(node.operand)
        return -inner if inner is not None else None
    return None


def _assigned_numeric_constants(stmts: list[ast.stmt]) -> dict[str, float]:
    """Best-effort: simple `x = N` / `x += N` top-level assignments in a
    statement list, mapped target-name -> numeric literal. Anything more
    complex (a computed RHS, a tuple target, an attribute target) is
    skipped, never guessed at."""
    out: dict[str, float] = {}
    for stmt in stmts:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            val = _numeric_const(stmt.value)
            if val is not None:
                out[stmt.targets[0].id] = val
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            val = _numeric_const(stmt.value)
            if val is not None:
                out[stmt.target.id] = val
    return out


def _scan_function(path: Path, func: ast.FunctionDef) -> list[SymmetryFinding]:
    findings: list[SymmetryFinding] = []
    body_nodes = list(ast.walk(func))

    # MISSING_MIRROR: per token family, both sides must appear somewhere
    # in this function's own body, or it's flagged.
    family_tokens_seen: dict[tuple[str, str], set[str]] = {}
    for n in body_nodes:
        tok = _token_in_node(n)
        if tok:
            family_key = tuple(sorted((tok, DIRECTIONAL_TOKENS[tok])))
            family_tokens_seen.setdefault(family_key, set()).add(tok)

    for seen in family_tokens_seen.values():
        if len(seen) == 1:
            token = next(iter(seen))
            mirror = DIRECTIONAL_TOKENS[token]
            findings.append(SymmetryFinding(
                file=str(path), line=func.lineno, function=func.name,
                kind="MISSING_MIRROR", token=token,
                detail=f"Function references {token!r} but never its mirror {mirror!r} anywhere in its own body.",
                severity="MEDIUM",
            ))

    # ASYMMETRIC_CONSTANT: best-effort if/elif token-mirror pairs.
    for n in body_nodes:
        if not isinstance(n, ast.If):
            continue
        tok = _find_token_in_subtree(n.test)
        if not tok:
            continue
        mirror = DIRECTIONAL_TOKENS[tok]
        if len(n.orelse) == 1 and isinstance(n.orelse[0], ast.If):
            elif_node = n.orelse[0]
            elif_tok = _find_token_in_subtree(elif_node.test)
            if elif_tok != mirror:
                continue
            left_vals = _assigned_numeric_constants(n.body)
            right_vals = _assigned_numeric_constants(elif_node.body)
            for var, lval in left_vals.items():
                rval = right_vals.get(var)
                if rval is not None and abs(rval) != abs(lval):
                    findings.append(SymmetryFinding(
                        file=str(path), line=n.lineno, function=func.name,
                        kind="ASYMMETRIC_CONSTANT", token=tok,
                        detail=(
                            f"{tok!r} branch sets {var}={lval!r}, mirrored {mirror!r} branch "
                            f"sets {var}={rval!r} — often intentional, worth a human look."
                        ),
                        severity="INFO",
                    ))
    return findings


def scan_file(path: Path) -> list[SymmetryFinding]:
    try:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return []

    findings: list[SymmetryFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            findings.extend(_scan_function(path, node))
    return findings


def run_direction_symmetry_audit(repo_root: Path | None = None) -> DirectionSymmetryReport:
    root = repo_root or Path(__file__).resolve().parents[2]
    files: list[Path] = []
    for pattern in _SCAN_GLOBS:
        for p in sorted(root.glob(pattern)):
            if p.name == "__init__.py" or p.name.startswith("test_"):
                continue
            files.append(p)

    findings: list[SymmetryFinding] = []
    for f in files:
        findings.extend(scan_file(f))

    return DirectionSymmetryReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        files_scanned=[str(f.relative_to(root)) for f in files],
        findings=findings,
    )


if __name__ == "__main__":
    import json

    report = run_direction_symmetry_audit()
    out_dir = Path(__file__).resolve().parents[2] / "reports" / "forensic"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"DIRECTION_SYMMETRY_{stamp}.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2))
    print(f"Direction symmetry audit: {len(report.findings)} finding(s) across "
          f"{len(report.files_scanned)} file(s). Written to {out_path}")
