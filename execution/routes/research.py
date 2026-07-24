"""
execution/routes/research.py
-------------------------------
Research Center (Mission Control modules 4, 9, 10): manifests, hypothesis
registry, philosophy audit, research integrity checks (leakage guard,
survivorship checker, manifest validator), per-hypothesis drill-down, and
downloadable reports. Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from execution.api_core import _check_auth, _executor, _get_config, logger
from execution.api_shared_helpers import _data_health_snapshot, _load_manifests
# /reports/{kind} calls these other routers' handlers directly as plain
# in-process function calls (not HTTP) to reuse their logic — imported
# here rather than reimplemented. No circular import: none of these
# modules import from execution.routes.research.
from execution.routes.data_providers import provider_chains_endpoint
from execution.routes.forward_review import forward_review_endpoint
from execution.routes.health import system_health_full
from execution.routes.outcomes import get_outcomes

router = APIRouter()


@router.get("/research/manifests")
async def research_manifests(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Git-tracked evidence manifests (research/manifest.py, audit item H2).

    Each manifest binds one research run to the exact git commit, a config
    hash, and per-dataset SHA256 fingerprints. The dashboard renders these
    as the system's auditable evidence trail — including the honest
    `reproducible: false` flag for runs from a dirty working tree.
    """
    _check_auth(x_api_key, iatis_session)
    manifests = _load_manifests()
    return {"count": len(manifests), "manifests": manifests}


@router.get("/research/symbols")
async def research_symbols(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Symbol Manager + Timeframe Selector (Research Workspace, 2026-07-24):
    the FULL symbol universe from config/symbols.yaml, grouped by asset
    class, including disabled/WATCHLIST/RETIRED entries with their
    governance metadata (status/status_reason) — unlike /symbol-health and
    /provider-chains, which only report on the live-enabled subset. Also
    surfaces each provider's native timeframe coverage so the frontend can
    restrict the Timeframe Selector to what a chosen symbol can actually
    serve.
    """
    _check_auth(x_api_key, iatis_session)
    from core.data_providers import DEFAULT_CHAINS, _NATIVE_TF, provider_chain_for, symbol_class

    config = _get_config()
    overrides = config.get("data", {}).get("provider_chains") or {}
    symbols_cfg = config.get("data", {}).get("twelve_data_symbols", [])

    by_asset_class: dict[str, list[dict[str, Any]]] = {}
    for s in symbols_cfg:
        internal = s.get("internal", "")
        entry = {
            "internal": internal,
            "symbol": s.get("symbol", internal),
            "enabled": bool(s.get("enabled", False)),
            "status": s.get("status", "UNKNOWN"),
            "status_reason": s.get("status_reason", ""),
            "status_since": s.get("status_since"),
            "min_score": s.get("min_score"),
            "rr": s.get("rr"),
            "provider_chain": provider_chain_for(internal, overrides) if internal else [],
        }
        asset_class = s.get("asset_class") or symbol_class(internal) or "unknown"
        by_asset_class.setdefault(asset_class, []).append(entry)

    return {
        "asset_classes": by_asset_class,
        "native_timeframes": {p: sorted(tfs) for p, tfs in _NATIVE_TF.items()},
        "chains": {cls: (overrides.get(cls) or chain) for cls, chain in DEFAULT_CHAINS.items()},
    }


@router.get("/research")
async def research_center(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Center — hypothesis status, engine performance, backtest results."""
    _check_auth(x_api_key, iatis_session)
    import json as _json
    from pathlib import Path

    registry_path = Path("research/results/registry.json")
    hypotheses_raw = {}
    if registry_path.exists():
        try:
            hypotheses_raw = _json.loads(registry_path.read_text()).get("hypotheses", {})
        except Exception:
            pass

    # Trust audit: which PASSED entries actually clear the codified
    # promotion criteria (research/edge_gate.py) — the dashboard must never
    # render an under-evidenced PASSED as green.
    try:
        from research.edge_gate import PROMOTION_CRITERIA, audit_passed_hypotheses
        trust_warnings = audit_passed_hypotheses(hypotheses_raw)
        flagged_ids = {w.split(" ", 1)[0] for w in trust_warnings}
        promotion_criteria = PROMOTION_CRITERIA
    except Exception:
        trust_warnings, flagged_ids, promotion_criteria = [], set(), {}

    hypotheses = []
    for h_id, h_data in hypotheses_raw.items():
        entry = {
            "id": h_id,
            "title": h_data.get("title", ""),
            "status": h_data.get("status", "UNKNOWN"),
            "description": h_data.get("description", "")[:120],
            "last_updated": h_data.get("last_updated", ""),
            "conclusion": (h_data.get("conclusion") or h_data.get("lesson") or "")[:300],
            "trusted": h_data.get("status") != "PASSED" or h_id not in flagged_ids,
        }
        # Load result file if exists
        result_file = h_data.get("result_file")
        if result_file:
            rp = Path("research") / result_file
            if rp.exists():
                try:
                    r = _json.loads(rp.read_text())
                    entry["sample_size"] = (r.get("n_fvg_entries") or
                        r.get("qualified_n") or r.get("total_n"))
                    entry["win_rate"] = (r.get("win_rate") or
                        r.get("qualified_win_rate"))
                    entry["p_value"] = r.get("p_value")
                except Exception:
                    pass
        hypotheses.append(entry)

    try:
        from storage.engine_tracker import engine_stats
        stats = engine_stats(min_votes=1)
    except Exception:
        stats = []

    try:
        from storage.outcome_tracker import performance_summary
        outcomes = performance_summary()
    except Exception:
        outcomes = {"total_closed": 0, "win_rate": 0}

    backtest_files = sorted(Path("storage").glob("full_pipeline_backtest_*.json"), reverse=True)
    latest_backtest = None
    if backtest_files:
        try:
            bt = _json.loads(backtest_files[0].read_text())
            valid = [r for r in bt.get("results", [])
                     if not r.get("error") and r.get("trades", 0) >= 10]
            latest_backtest = {
                "file": backtest_files[0].name,
                "generated_at": bt.get("generated_at", ""),
                "summary": bt.get("summary", {}),
                "avg_wr": round(sum(r.get("win_rate",0) for r in valid)/len(valid), 1) if valid else 0,
                "avg_pf": round(sum(r.get("profit_factor",0) for r in valid)/len(valid), 2) if valid else 0,
                "top_symbols": sorted(valid, key=lambda x: x.get("profit_factor",0), reverse=True)[:5],
            }
        except Exception:
            pass

    return {
        "hypothesis_summary": {
            "total": len(hypotheses),
            "passed": sum(1 for h in hypotheses if h["status"] == "PASSED"),
            "failed": sum(1 for h in hypotheses if "FAILED" in h["status"]),
            "research": sum(1 for h in hypotheses if h["status"] == "RESEARCH"),
            "needs_data": sum(1 for h in hypotheses if h["status"] == "NEEDS_MORE_DATA"),
        },
        "hypotheses": hypotheses,
        "trust_audit": {
            "criteria": promotion_criteria,
            "warnings": trust_warnings,
        },
        "engine_performance": stats,
        "outcome_summary": outcomes,
        "latest_backtest": latest_backtest,
    }


@router.get("/philosophy-audit")
async def philosophy_audit_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """System Philosophy Audit — the same 29 checks as
    `python -m scripts.philosophy_audit`, on demand from the dashboard.

    Read-only (SELECTs against the decisions DB). Takes ~10-20s because it
    issues multiple D1 round-trips; the frontend calls it from a button,
    never on a poll."""
    _check_auth(x_api_key, iatis_session)

    def _run() -> dict[str, Any]:
        from scripts.philosophy_audit import run_all
        from storage import d1_client
        # Ensure the audited tables exist (CREATE IF NOT EXISTS) — a fresh
        # DB (or the tests' fake D1) has none until a first decision lands.
        from storage.decision_db import init_db as _init_decisions
        from storage.outcome_tracker import _init_db as _init_outcomes
        _init_decisions()
        _init_outcomes()
        with d1_client.d1_connection() as con:
            checks = run_all(con)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(checks),
                "fail": sum(1 for c in checks if c.status == "FAIL"),
                "warn": sum(1 for c in checks if c.status == "WARN"),
                "pass": sum(1 for c in checks if c.status == "PASS"),
                "info": sum(1 for c in checks if c.status == "INFO"),
            },
            "checks": [
                {"axis": c.axis, "name": c.name, "status": c.status,
                 "detail": c.detail,
                 "evidence": [str(e) for e in c.evidence[:12]]}
                for c in checks
            ],
        }

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_executor, _run)
    except Exception as exc:
        logger.error(f"Philosophy audit failed: {exc}")
        raise HTTPException(status_code=503,
                            detail="Audit unavailable — decisions DB unreachable.")


# ---------------------------------------------------------------------------
# Research Integrity (Mission Control module 9) — on-demand, read-only
# checks alongside the philosophy audit above. Deliberately excludes
# cross-provider diff (scripts/cross_provider_diff.py): that tool makes
# live provider API calls and burns rate-limited quota (see /budget), so
# it belongs in the Experiment Runner (module 5) where a human explicitly
# kicks off a job with visible cost, not a casual dashboard click.
# ---------------------------------------------------------------------------
def _leakage_guard_report() -> dict[str, Any]:
    """Static leakage scan (research/guards/static_scan.py) over every
    research/experiment script. Advisory only, by that module's own
    design — CLEAN or WARNINGS_FOUND, never a hard FAIL; see its
    docstring for why a heuristic AST scan must never claim proof.
    """
    from research.guards.static_scan import scan_paths

    paths: list[Path] = []
    for d in ("research", "scripts"):
        paths.extend(sorted(Path(d).rglob("*.py")))
    paths.extend(sorted(Path(".").glob("run_h*.py")))

    report = scan_paths(paths)
    return {"status": "PASS" if report["verdict"] == "CLEAN" else "WARNING", **report}


def _survivorship_report() -> dict[str, Any]:
    """Symbol-evidence + selection-disclosure gate
    (research/survivorship_checker.py) — matches that module's own
    return-code convention: an enabled symbol with zero committed
    evidence is a FAIL, everything else advisory-only WARNING/PASS.
    """
    from research.survivorship_checker import check_selection_disclosure, check_symbol_evidence

    config = _get_config()
    symbol_report = check_symbol_evidence(config)
    selection_report = check_selection_disclosure()
    if symbol_report["enabled_no_evidence"]:
        status = "FAIL"
    elif (symbol_report["disabled_no_evidence"] or selection_report["undisclosed"]
          or selection_report["invalid_label"]):
        status = "WARNING"
    else:
        status = "PASS"
    return {"status": status, "symbol_evidence": symbol_report, "selection_disclosure": selection_report}


def _manifest_validator_report() -> dict[str, Any]:
    """Which evidence manifests are reproducible=false — reuses
    _load_manifests() (also backing /research/manifests and /alerts) so
    this never drifts from what those already show.
    """
    manifests = _load_manifests()
    non_reproducible = [m for m in manifests if m.get("reproducible") is False]
    return {
        "status": "WARNING" if non_reproducible else "PASS",
        "total": len(manifests),
        "reproducible_count": len(manifests) - len(non_reproducible),
        "non_reproducible": [
            {"file": m["file"], "kind": m.get("kind"), "git_dirty": m.get("git_dirty")}
            for m in non_reproducible
        ],
    }


@router.get("/research/integrity")
async def research_integrity(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Integrity — leakage guard, survivorship checker, and
    manifest validator, on demand. Read-only, no network calls, never
    modifies research evidence. See module docstring above for what's
    deliberately excluded and why.
    """
    _check_auth(x_api_key, iatis_session)

    def _run() -> dict[str, Any]:
        checks: dict[str, Any] = {}
        for name, fn in (
            ("leakage_guard", _leakage_guard_report),
            ("survivorship", _survivorship_report),
            ("manifest_validator", _manifest_validator_report),
        ):
            try:
                checks[name] = fn()
            except Exception as exc:
                checks[name] = {"status": "ERROR", "error": str(exc)[:300]}

        statuses = {c.get("status") for c in checks.values()}
        overall = (
            "FAIL" if "FAIL" in statuses else
            "ERROR" if "ERROR" in statuses else
            "WARNING" if "WARNING" in statuses else
            "PASS"
        )
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "overall": overall, "checks": checks}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run)


@router.get("/research/{hypothesis_id}")
async def research_hypothesis_detail(
    hypothesis_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Center drill-down (module 4) — the complete registry.json
    entry for one hypothesis (untruncated, unlike /research's summary
    list) plus every manifest linked to it and its declared result
    file(s).

    Manifest linking uses two sources, kept separate and labeled rather
    than merged into one list pretending to be equally certain:
      - "exact": the hypothesis's own `manifest` field in registry.json
        (a real field some hypotheses declare — H008c, H015, etc. — the
        authoritative link where it exists).
      - "heuristic": any other manifest whose filename or `kind` contains
        the hypothesis ID as a case-insensitive substring. A guess, not
        a fact — many manifest kinds (crypto_volume_experiment,
        ctrader_spread_measurement) don't embed a hypothesis ID at all.

    MUST stay registered after /research/manifests and /research/integrity
    (both literal paths) — Starlette/FastAPI match routes in registration
    order, so a path-param route registered earlier would silently shadow
    them (hit exactly this bug once while building this route; pinned by
    tests/test_api_contract.py::test_research_hypothesis_detail_route_does_not_shadow_literal_routes).
    """
    _check_auth(x_api_key, iatis_session)
    import json as _json

    registry_path = Path("research/results/registry.json")
    if not registry_path.exists():
        raise HTTPException(status_code=404, detail="Registry not found.")
    hypotheses_raw = _json.loads(registry_path.read_text()).get("hypotheses", {})
    hyp = hypotheses_raw.get(hypothesis_id)
    if hyp is None:
        raise HTTPException(status_code=404, detail=f"Hypothesis '{hypothesis_id}' not found.")

    manifests = _load_manifests()
    declared_manifest = hyp.get("manifest")
    declared_name = Path(declared_manifest).name if declared_manifest else None

    exact_links, heuristic_links = [], []
    needle = hypothesis_id.lower()
    for m in manifests:
        if declared_name and m["file"] == declared_name:
            exact_links.append(m)
        elif needle in m["file"].lower() or (m.get("kind") and needle in str(m["kind"]).lower()):
            heuristic_links.append(m)

    # Result file(s) — path + existence check only. Never dumps arbitrary
    # file content through this endpoint; that's File Explorer's job.
    result_paths: list[str] = []
    if isinstance(hyp.get("result_file"), str):
        result_paths.append(hyp["result_file"])
    result_files_field = hyp.get("result_files")
    if isinstance(result_files_field, dict):
        result_paths.extend(v for v in result_files_field.values() if isinstance(v, str))

    return {
        "id": hypothesis_id,
        "hypothesis": hyp,
        "manifests": {"exact": exact_links, "heuristic": heuristic_links},
        "result_files": [
            {"path": p, "exists": (Path("research") / p).exists()}
            for p in result_paths
        ],
    }


# ---------------------------------------------------------------------------
# Reports (Mission Control module 10) — on-demand snapshots assembled from
# data other endpoints already compute; never a second implementation of
# the same numbers. Markdown or JSON only — no PDF dependency exists in
# this project's requirements.txt, and we don't claim functionality that
# isn't real (docs/VISION_v2.md's "no future phase functionality
# pretending to be complete" rule).
# ---------------------------------------------------------------------------
_REPORT_TITLES: dict[str, str] = {
    "research": "IATIS Research Report",
    "manifest_summary": "IATIS Manifest Summary",
    "system": "IATIS System Health Report",
    "provider": "IATIS Data Provider Report",
    "forward": "IATIS Forward Demo Report",
    "data_quality": "IATIS Data Quality Report",
}


def _dict_to_md(title: str, data: dict[str, Any], generated_at: str) -> str:
    """Generic dict → Markdown for report kinds without a dedicated table
    formatter (system/provider/forward): a titled doc with the exact data
    as a JSON block. Honest about being a snapshot, not hand-formatted
    prose — good enough for an operator to read or paste elsewhere."""
    import json as _json

    return "\n".join([
        f"# {title}", "", f"Generated {generated_at}.", "",
        "```json", _json.dumps(data, indent=2, default=str), "```", "",
    ])


def _build_manifest_summary_md(manifests: dict[str, dict]) -> str:
    from scripts.generate_research_report import build_manifest_table

    n_total = len(manifests)
    n_repro = sum(1 for m in manifests.values() if m.get("reproducible"))
    return "\n".join([
        "# IATIS Manifest Summary", "",
        f"Generated {datetime.now(timezone.utc).isoformat()}.", "",
        f"{n_total} manifests, {n_repro} reproducible, {n_total - n_repro} NOT reproducible.", "",
        build_manifest_table(manifests), "",
    ])


@router.get("/reports/{kind}")
async def generate_report(
    kind: str,
    format: str = Query(default="md", pattern="^(md|json)$"),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> Any:
    _check_auth(x_api_key, iatis_session)
    if kind not in _REPORT_TITLES:
        raise HTTPException(status_code=404, detail=f"Unknown report kind '{kind}'. Choose from: {sorted(_REPORT_TITLES)}")

    generated_at = datetime.now(timezone.utc).isoformat()
    title = _REPORT_TITLES[kind]

    if kind == "research":
        from scripts.generate_research_report import build_report, load_manifests, load_registry
        registry = load_registry()
        manifests = load_manifests()
        markdown = build_report(registry, manifests)
        data: dict[str, Any] = {"registry": registry, "manifests": manifests}
    elif kind == "manifest_summary":
        from scripts.generate_research_report import load_manifests
        manifests = load_manifests()
        data = {"manifests": manifests}
        markdown = _build_manifest_summary_md(manifests)
    elif kind == "system":
        data = await system_health_full(x_api_key, iatis_session)
        markdown = _dict_to_md(title, data, generated_at)
    elif kind == "provider":
        data = await provider_chains_endpoint(x_api_key, iatis_session)
        markdown = _dict_to_md(title, data, generated_at)
    elif kind == "data_quality":
        data = _data_health_snapshot()
        markdown = _dict_to_md(title, data, generated_at)
    else:  # "forward"
        data = {
            "forward_review": await forward_review_endpoint(x_api_key, iatis_session),
            "outcomes_summary": (await get_outcomes(x_api_key, iatis_session))["summary"],
        }
        markdown = _dict_to_md(title, data, generated_at)

    if format == "json":
        return {"kind": kind, "title": title, "generated_at": generated_at, "data": data}
    return PlainTextResponse(
        markdown, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="iatis_{kind}_report.md"'},
    )
