# Mission Control Dashboard — Implementation Audit

Date: 2026-07-11
Scope: audit the existing `dashboard/` (React SPA) + `execution/api_server.py`
(FastAPI backend) against the 15-module Mission Control spec, before any
implementation, per this repo's "audit first, never duplicate working code"
rule (`CLAUDE.md`, `docs/VISION_v2.md`'s "no future phase functionality
pretending to be complete" rule).

## Orientation

- The web server is **`execution/api_server.py`** (not `main.py`, which is
  the trading-pipeline entry point imported by the scheduler and by the API
  server's `/analyze` route).
- The frontend is a React/TS SPA at `dashboard/frontend/src/`, built to
  `dashboard/frontend/dist` and mounted at `/app` by the API server. A
  separate legacy server-rendered page (`GET /dashboard`) still runs in
  parallel by design (`dashboard/README.md`) — not duplication to remove,
  it's an intentional fallback.
- v1 shipped 5 real modules (Mission Control, Live Signals, Data Center,
  Engine Monitor, Research & Backtests) + a bonus System Audit tab, all
  polling-based (no WebSocket/SSE anywhere in the backend). A **Roadmap tab**
  already lists 10 placeholder modules — this is the team's own prior
  prioritization signal and roughly maps onto this spec's gaps.
- Auth: single shared `API_SERVER_KEY`, `hmac.compare_digest`, HttpOnly
  session cookies persisted to `storage/sessions.json` (chmod 600). **No
  roles** — one key grants full access.
- Storage: Cloudflare D1 (via Worker proxy, `storage/d1_client.py`) is the
  only queryable backend; a few things stay local by design (append-only
  `storage/decisions.jsonl`, `storage/calendar_cache.json`,
  `storage/sessions.json`, cached OHLCV CSVs).
- Test harness: `tests/conftest.py` gives every test a hermetic fake D1
  (in-memory SQLite standing in for the Worker), strips real credentials,
  and blocks real network I/O. `tests/test_api_contract.py` is the pattern
  for new endpoint tests — assert 401 without auth, assert response shape
  with auth, against the fake D1.

## Module-by-module status

| # | Module | Status | Key files |
|---|---|---|---|
| 1 | System Health | PARTIAL | `api_server.py:/health/full`, `MissionControl.tsx` — has CPU/RAM/disk/uptime; missing swap, load avg, real per-service systemd status (scheduler status is log-text-mined, not `systemctl`), worker concept |
| 2 | Data Providers | PARTIAL | `/provider-chains`, `core/data_providers.py`, `DataCenter.tsx` — shows fallback chains + configured booleans; missing latency, last-update, errors, history, FRED/CBOE/CFTC/Alternative.me status |
| 3 | Data Quality | PARTIAL | `/data-health`, `DataCenter.tsx`, `scripts/verify_data_integrity.py` (unwired), `scripts/cross_provider_diff.py` (unwired) — missing duplicate detection, timezone, numeric integrity score, and ALL action buttons (verify/rebuild/compare/export) |
| 4 | Research Center | PARTIAL | `/research`, `/research/manifests`, `registry.json`, `ResearchBacktests.tsx` — statuses match spec; hypotheses and manifests are two separate unlinked lists, no per-hypothesis drill-down, no experiment-history timeline |
| 5 | Experiment Runner | **MISSING** | scripts exist individually (`walk_forward_validation.py`, `engine_subset_search.py`, `forward_review.py`, `cross_provider_diff.py`, `verify_data_integrity.py`, `generate_research_report.py`, `research/guards/*`, `research/survivorship_checker.py`) but zero API wiring, no queue/progress/logs |
| 6 | Forward Demo | PARTIAL | `/outcomes`, `/shadow-book` (unused by frontend), `scripts/forward_review.py` (CLI-only) — no PF in `/outcomes`, D001/D002 rule status has zero API exposure |
| 7 | Decision Explorer | PARTIAL | `/decisions` (only `limit`+`verdict` filters), `LiveSignals.tsx` — no date/symbol/engine/reason/confluence/risk-rejection filters, no raw-JSON viewer |
| 8 | Engine Analytics | PARTIAL | `/engine-stats`, `EngineMonitor.tsx` — has votes/agreement/weights; no per-engine PF/WR, no correlation, no historical evolution chart |
| 9 | Research Integrity | PARTIAL | `/philosophy-audit` fully wired; leakage guard, cross-provider diff, survivorship checker, manifest validator all backend-only |
| 10 | Reports | PARTIAL | AI text blurbs only (`/ai/daily-report`, `/ai/research-summary`); `scripts/generate_research_report.py` unwired; no markdown/pdf/json export anywhere |
| 11 | File Explorer | **MISSING** | nothing exists |
| 12 | VPS Operations | **MISSING** | ops scripts exist (`backup_d1.py`, `deploy_vps.sh`, `watchdog.py`) but SSH/cron-only, no API triggers |
| 13 | Live Logs | **MISSING** | flagged in `RoadmapGrid.tsx` as "planned v2"; no SSE/WS, no search |
| 14 | Alert Center | **MISSING** | Telegram push exists but nothing surfaced in-dashboard; flagged in roadmap |
| 15 | Security | PARTIAL | single-key auth, no roles, no per-action audit log; "no arbitrary execution" currently true only because no execution surface exists yet (modules 5/12) |

Full endpoint inventory, script inventory, and per-module gap detail: see
the audit performed at the start of this work (superseded by this file;
kept in session history, not duplicated here to avoid drift).

## Technical debt / duplication observed

- `GET /shadow-book` — fully implemented, zero frontend consumers. Reuse
  when building Forward Demo module 6 upgrade instead of rebuilding.
- Hypothesis↔manifest linkage in Research Center is implicit string
  matching, not a real key — fix when building module 4/9 drill-downs
  rather than adding a third parallel lookup.
- Legacy `GET /dashboard` HTML page duplicates routes the SPA also calls —
  intentional per `dashboard/README.md`, leave alone.

## Reuse opportunities (do NOT rebuild)

- All read-only stats endpoints (`/engine-stats`, `/outcomes`,
  `/research`, `/data-health`, `/health/full`) — extend response shape
  in place rather than adding parallel v2 endpoints.
- `tests/test_api_contract.py` pattern (401-without-auth +
  shape-with-auth against fake D1) — follow for every new endpoint.
- `storage/shadow_book.py`, `scripts/forward_review.py` D001/D002 logic —
  wrap with a thin read endpoint, don't reimplement the rule evaluation.
- Frontend `components/` (`Panel`, `DataTable`, `Badge`, `KpiCard`,
  `StatusDot`), `lib/usePolling.ts`, `lib/api.ts` — every new module tab
  should compose these, not introduce new primitives.

## Constraints carried into every module below

- **Never** modify engines, trading logic, hypotheses, or research
  evidence — dashboard is observer/operator only.
- **No mock data** — a module either shows real data or stays a Roadmap
  placeholder (`docs/VISION_v2.md` rule, already enforced by the team).
  This audit only "promotes" a Roadmap item once real wiring lands.
  ..
- **No arbitrary shell / command execution** — modules 5 and 12 need a
  hardcoded whitelist of scripts/operations, subprocess with fixed argv
  (no shell=True, no string interpolation from user input), and output
  capture, never free-form commands.
- File Explorer (11) must hard-deny secret-shaped paths (`.env*`,
  `*.pem`, `*credentials*`, `*token*`, anything under `storage/sessions*`)
  at the backend, not just hide them in the UI.
- One module per commit; run backend tests (`pytest tests/`) + frontend
  build/lint (`npm run build`, `npm run lint`) before each commit.

## Recommended implementation order

Ordered by (value × low risk × how much existing wiring it reuses),
front-loading fully-missing modules the team already flagged as wanted:

1. **Decision Explorer filters + JSON viewer** (7) — small, closes a
   partial gap, exercises the loop end-to-end.
2. **Live Logs** (13) — read-only, already on the roadmap, real
   operator value (reduces SSH).
3. **File Explorer** (11) — read-only, with a strict secret-path
   denylist enforced server-side.
4. **Alert Center** (14) — aggregates signals already available from
   existing endpoints (`/health/full`, `/provider-chains`, `/research`,
   `/outcomes`) into one feed; no new data sources needed yet.
5. **Forward Demo upgrade** (6) — surface D001/D002 rule status + PF +
   wire the already-built `/shadow-book` into the frontend.
6. **Research Integrity wiring** (9) — expose leakage guard,
   cross-provider diff, survivorship checker as on-demand checks next to
   the existing philosophy audit.
7. **Reports** (10) — wrap `generate_research_report.py` + new
   system/provider/forward report generators behind a download endpoint.
8. **Experiment Runner** (5) — whitelisted job queue; biggest single
   module, do after the smaller ones establish the "whitelisted
   subprocess" pattern (reused by module 12).
9. **VPS Operations** (12) — restart/backup/diagnostics, whitelisted,
   reuses the Experiment Runner's job-execution primitive.
10. **Engine Analytics upgrade** (8) — per-engine PF/WR needs joining
    engine votes to outcomes; medium effort.
11. **Data Quality actions** (3) — wire verify/rebuild/compare/export to
    existing `scripts/verify_data_integrity.py` /
    `scripts/cross_provider_diff.py`.
12. **Data Providers telemetry** (2) — latency/last-update/history needs
    a small persistent counter, touches every provider call site.
13. **System Health completion** (1) — swap, load avg, real per-service
    systemd status.
14. **Research Center drill-down** (4) — fix the hypothesis↔manifest
    link properly, unify into one view.
15. **Security / RBAC** (15) — roles are a bigger architecture change
    (multi-user), do last once the action surface (5, 12) that roles
    would actually gate exists; add the per-action audit log alongside
    module 5/12 instead of waiting.

This file will be updated as each module lands (what was audited, reused,
removed, added — per iteration, in commit messages) rather than restating
the full audit each time.
