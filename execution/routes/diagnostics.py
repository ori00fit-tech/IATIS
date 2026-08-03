"""
execution/routes/diagnostics.py
-----------------------------------
Forensic System Audit Phase 1, item B (2026-08-02) — seed of a real
Diagnostics API surface, kept as its own small router rather than
bloated into missions.py or research.py.

GET /research/diagnostics/direction-symmetry runs research/diagnostics/
direction_symmetry.py fresh on every request — cheap (AST-parses ~25-30
files, no D1/network access), stateless by design, matching research/
guards/static_scan.py's own statelessness. Two path segments after
/research/, so it cannot collide with research.py's GET /research/
{hypothesis_id} catch-all (which only matches a single segment).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Header

from execution.api_core import _check_auth

router = APIRouter()


@router.get("/research/diagnostics/direction-symmetry")
async def diagnostics_direction_symmetry(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from backtest.metrics import json_safe
    from research.diagnostics.direction_symmetry import run_direction_symmetry_audit

    report = run_direction_symmetry_audit()
    return json_safe(report.to_dict())
