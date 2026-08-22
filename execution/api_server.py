"""
execution/api_server.py
---------------------------
FastAPI HTTP server — composition root.

This module used to contain the entire API (3,530 lines, ~70 endpoints) —
flagged as a monolith across three consecutive audits
(docs/PRODUCTION_AUDIT_2026-07.md, docs/INSTITUTIONAL_GAP_ANALYSIS_2026-07.md,
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1). It is now a thin
composition root: `app` and shared infrastructure (auth, config cache,
session store, symbol validators) live in execution/api_core.py; helpers
consumed by more than one router live in execution/api_shared_helpers.py;
every endpoint lives in its own module under execution/routes/, included
below in the SAME relative order the original file registered them in
(preserving Starlette's first-match-wins route resolution for the
path-param routes that sit alongside literal siblings within one module —
e.g. /research/{hypothesis_id} after /research/manifests and
/research/integrity; no path-param route collides across module
boundaries — verified before this split).

uvicorn execution.api_server:app still works unchanged — `app` is
re-exported here.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from execution.api_core import app, logger  # noqa: F401 (logger re-exported for tests)

# Re-exported for backward compatibility: the pre-split test suite reaches
# into `execution.api_server`'s internals extensively (monkeypatch and
# direct attribute assignment). Most of these are read-only imports in
# this module's own namespace, which is fine for READS but NOT for tests
# that assign/monkeypatch them expecting to affect the real owning
# module's behavior — those specific cases were found empirically (by
# running the full suite after this split) and are called out in each
# affected test file's own comments, pointing at the real module.
from execution.api_core import (  # noqa: F401
    _ENV,
    _REPO_ROOT,
    _active_sessions,
    _check_auth,
    _config_cache,
    _config_lock,
    _docs_url,
    _executor,
    _get_config,
    _load_sessions,
    _reset_config_cache,
    _save_sessions,
    _validate_candle_symbol,
    _validate_symbol,
)
from execution.api_shared_helpers import (  # noqa: F401
    _LOG_UNITS,
    _UNIT_KIND,
    _data_health_snapshot,
    _forward_rule_alerts,
    _forward_rule_progress,
    _load_manifests,
    _scheduler_status,
    _systemd_service_status,
)
from execution.routes.data_providers import (  # noqa: F401
    _macro_source_status,
    _provider_usage_from_decisions,
)
from execution.routes.experiments import (  # noqa: F401
    _Job,
    _RunJobRequest,
    _configured_symbol_universe,
    _job_executor,
    _job_summary,
    _jobs,
    _jobs_lock,
    _run_job,
)
from execution.routes.files import (  # noqa: F401
    _is_denied_path,
    _resolve_safe_path,
)
from execution.routes.research import (  # noqa: F401
    _build_manifest_summary_md,
    _dict_to_md,
    _leakage_guard_report,
    _manifest_validator_report,
    _survivorship_report,
)

from execution.routes import (
    ai,
    alerts,
    analytics_benchmark,
    analyze,
    auth,
    ctrader_auth,
    data_providers,
    dashboard_legacy,
    diagnostics,
    engine_benchmark,
    experience,
    experiments,
    files,
    forward_review,
    health,
    journal,
    kill_switch,
    logs,
    macro_benchmark,
    missions,
    news_benchmark,
    outcomes,
    post_trade,
    provider_benchmark,
    provider_scorecard,
    research,
    research_matrix,
)

# Included in the same relative order their endpoints first appeared in
# the pre-split file — see this module's docstring on why order is
# preserved even though no cross-module path-template collision exists.
#
# EXCEPTION (2026-07-28): missions.router is deliberately included BEFORE
# research.router, breaking strict chronological order — GET
# /research/missions (one path segment after /research/) would otherwise
# be swallowed by research.py's own GET /research/{hypothesis_id} catch-
# all (registered first = wins under Starlette's registration-order
# matching; hypothesis_id="missions" resolves to nothing and 404s).
# /research/missions/{mission_id} (two segments) never collided — only
# the exact one-segment /research/missions path did. Confirmed by a real
# failing test before this reorder (tests/test_missions.py).
app.include_router(health.router)
app.include_router(kill_switch.router)
app.include_router(analyze.router)
app.include_router(auth.router)
app.include_router(ctrader_auth.router)
app.include_router(dashboard_legacy.router)
app.include_router(experience.router)
app.include_router(missions.router)
# Hypothesis Discovery Engine Phase 1 (2026-08-XX) — same collision
# reasoning as missions.router above: /research/matrix/... registers
# before research.router's own GET /research/{hypothesis_id} catch-all.
app.include_router(research_matrix.router)
app.include_router(diagnostics.router)
# Provider Benchmark & Data Quality Lab Phase 1 (2026-08-08) — same
# collision reasoning as missions.router above: GET
# /research/provider-benchmark (one path segment after /research/) would
# otherwise be swallowed by research.py's own GET /research/{hypothesis_id}
# catch-all if registered after it.
app.include_router(provider_benchmark.router)
# Provider Benchmark & Data Quality Lab Phase 2 (2026-08-08) — identical
# collision reasoning: GET /research/news-benchmark must register before
# research.router's catch-all.
app.include_router(news_benchmark.router)
# Provider Benchmark & Data Quality Lab Phase 3 (2026-08-XX) — identical
# collision reasoning: GET /research/macro-benchmark must register before
# research.router's catch-all.
app.include_router(macro_benchmark.router)
# Provider Benchmark & Data Quality Lab Phase 4 (2026-08-XX) — identical
# collision reasoning: GET /research/analytics-benchmark must register
# before research.router's catch-all.
app.include_router(analytics_benchmark.router)
# Provider Benchmark & Data Quality Lab Phase 5 (2026-08-XX) — identical
# collision reasoning: GET /research/provider-scorecard and GET
# /research/best-provider must register before research.router's
# catch-all.
app.include_router(provider_scorecard.router)
# Engine Benchmark (2026-08-09) — identical collision reasoning: GET
# /research/engine-benchmark must register before research.router's
# catch-all.
app.include_router(engine_benchmark.router)
app.include_router(research.router)
app.include_router(experiments.router)
app.include_router(data_providers.router)
app.include_router(outcomes.router)
app.include_router(post_trade.router)
app.include_router(journal.router)
app.include_router(logs.router)
app.include_router(files.router)
app.include_router(forward_review.router)
app.include_router(alerts.router)
app.include_router(ai.router)

# ---------------------------------------------------------------------------
# Command Center SPA — built frontend, mounted only if the build exists.
# `cd dashboard/frontend && npm install && npm run build` produces dist/.
# ---------------------------------------------------------------------------
_DASHBOARD_DIST = Path("dashboard/frontend/dist")
if _DASHBOARD_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=_DASHBOARD_DIST, html=True), name="dashboard_spa")
    logger.info("Command Center SPA mounted at /app")
else:
    logger.info("dashboard/frontend/dist not found — /app not mounted (run npm run build)")
