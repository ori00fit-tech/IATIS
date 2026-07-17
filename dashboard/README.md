# IATIS Command Center (dashboard)

A React/TypeScript SPA on top of the existing `execution/api_server.py` FastAPI
app. No separate backend — this frontend calls the same endpoints the system
already exposes, plus one new one (`/data-health`).

## Dev setup

```bash
# Terminal 1 — the real backend, auth bypassed in dev mode
ENV=development API_SERVER_KEY=dev uvicorn execution.api_server:app --reload --port 8000

# Terminal 2 — the frontend, proxies API calls to the backend above
cd dashboard/frontend
npm install
npm run dev
```

Open the printed `localhost` URL. `vite.config.ts`'s dev proxy points at
`http://127.0.0.1:8000` by default; override with `IATIS_API_URL` if the
backend runs elsewhere (e.g. through the deployed Cloudflare tunnel, with a
real `X-API-Key`).

## Production

```bash
cd dashboard/frontend
npm install
npm run build     # → dashboard/frontend/dist
```

`execution/api_server.py` mounts `dashboard/frontend/dist` at `/app` automatically
if the directory exists (see the bottom of that file) — no extra config needed
once `dist/` is built. The existing server-rendered `/dashboard` page is
untouched and keeps working in parallel.

## v1 scope

5 modules, each mapped to real data — see `docs/VISION_v2.md`'s "no future
phase functionality pretending to be complete" rule for why the rest are
roadmap placeholders instead of mock screens.

| Module | Endpoint(s) | Notes |
|---|---|---|
| Mission Control | `/health`, `/health/full` (now includes swap, load average, real per-service systemd status — module 1), `/budget`, `/symbol-health`, `/data-health` | system status, CPU/RAM/disk/swap/load, credits — topped by the **Executive Overview** (v0.6 §1): six deterministic 0–100 composite scores (System Health, Data Quality, Risk Status live; Decision Quality + Research Integrity on-demand since those audits are expensive; Production Readiness = weakest-link), each deep-linking to the tab that explains it, plus a Research Status card. The evidence tile is now the full **Evidence Progress** panel (v0.6 §2): win rate with a 95% Wilson confidence interval, PF/expectancy/avg-R/realized-max-drawdown, and a sufficiency band — every figure greyed under a "data collection" banner while closed < 30 |
| Live Signals | `/decisions`, `/outcomes` | recent pipeline decisions + open paper signals |
| Risk Center *(v0.6.0)* | `/health/full`, `/outcomes`, `/symbol-health`, `/reconciliation` | exposure vs cap, live RR≥2 compliance, realized R-multiple distribution, per-symbol risk scaling, plus a **Portfolio Heat** read (v0.6 §6: net directional bias, directional concentration, symbol stacking). Correlation heatmap + risk budgets are deferred (need new backend). Monitoring only — never gates or sizes |
| AI Decision Center *(v0.6.0)* | `/decisions`, `/ai/explain-trade` | decision anatomy (regime, confluence vote, fail-reasons, provenance) + explain-only AI narration per VISION_v2 — never generates or alters a signal. Now tops with a **Decision Quality rollup** (v0.6 §5): avg confluence, avg meta-confidence, quorum quality (informative-weight share), avg risk sizing, and provenance coverage across the window |
| Backtesting Charts *(v0.6.0)* | `/backtest-results` (now passes through legacy `equity_curve`), `/outcomes` | SVG equity curve + drawdown, switchable per-symbol metric comparison, score-calibration curve |
| Data Center | `/data-health`, `/provider-chains` (now includes `recent_usage` from decisions.jsonl and `macro_sources` for CBOE/FRED/CFTC/Alternative.me — module 2) | OHLCV cache completeness per symbol/timeframe |
| Engine Monitor | `/engine-stats` (now includes `attribution`: approximate per-engine PF/WR, time-window-joined to closed outcomes since there's no shared foreign key — see module 8) | per-engine vote/accuracy stats, current vs. suggested weights |
| Research & Backtests | `/research`, `/research/{hypothesis_id}` (drill-down — module 4), `/backtest-results`, `/meta-analysis` | hypothesis registry, backtest runs, regime matrix |
| Live Logs | `/logs`, `/logs/sources` | whitelisted journalctl/file log tail — no arbitrary shell access, see Mission Control module 13 |
| File Explorer | `/files/tree`, `/files/read`, `/files/download`, `/files/search`, `/files/diff` | read-only, path-confined to the repo root, secret-shaped paths denylisted server-side, see Mission Control module 11 |
| Alert Center | `/alerts` | aggregates signals already computed by other endpoints (scheduler status, provider config, data health, manifests, forward decision rules) — no new data source, see Mission Control module 14 |
| Forward Demo | `/outcomes` (extended with profit_factor/avg_r_multiple), `/forward-review`, `/shadow-book` | D001/D002 pre-registered forward decision rule progress + the shadow-book counterfactual gate ledger, see Mission Control module 6 |
| Research Integrity | `/research/integrity` | leakage guard (static scan), survivorship checker, manifest validator — button-triggered next to the philosophy audit on the System Audit tab. Cross-provider diff deliberately excluded (burns provider API quota; belongs in the future Experiment Runner). See Mission Control module 9 |
| Reports | `/reports/{kind}` (research, manifest_summary, system, provider, forward) | Markdown download or JSON view of a snapshot assembled from data other endpoints already compute — no PDF (no dependency for it exists), see Mission Control module 10 |
| Experiment Runner | `/experiments/jobs`, `/experiments/run`, `/experiments`, `/experiments/{job_id}` | whitelisted subprocess jobs only (fixed argv, never shell=True). Deliberately narrow: only `verify_data_integrity` and `forward_review` (local/fast/no network) — long-running or provider-API-spending jobs are NOT wired up; widening the whitelist is an operator decision, see execution/api_server.py's module docstring and MISSION_CONTROL_AUDIT.md. See Mission Control module 5 |
| VPS Operations | `/ops/reload-config` + reuses `/health/full` (diagnostics) and the Experiment Runner's `backup_d1` job (category `ops`) | Deliberately excludes restarting iatis-api/iatis-scheduler — stays SSH-only until an operator explicitly asks for it. See Mission Control module 12 |
| Security / Audit Log | `/audit-log` | Real audit trail (login, job trigger, config reload, outcome close) with masked actor — never the raw API key or full session id. Role-based access control is a deliberately scoped-out gap, not built (today's auth is a single shared key). See Mission Control module 15 |

All polling-based (15–60s depending on module) — no WebSocket in v1; see
`.claude/plans/glittery-drifting-lerdorf.md` for the full architecture
rationale.

## Diagnostic error taxonomy (v0.6 cross-cutting)

Every AI-backed panel (news, macro, daily report, trade explanation, research
summary, AI Decision Center) routes failures through a shared classifier
(`lib/diagnostics.ts`) and renders a typed code + "where to look" hint instead
of a bare string, via `components/DiagnosticError.tsx` inside `AiStatusFrame`.
Codes: `PROVIDER_UNAVAILABLE`, `AUTH_FAILED`, `RATE_LIMITED`, `TIMEOUT`,
`BAD_FORMAT`, `AI_PROVIDER_ERROR`. Classification is pure and client-side —
the frontend knows the panel is AI-side and `ApiError.message` carries the HTTP
status — so no backend change is required (an explicit server `error_code`, if
ever added, takes precedence).

## Shell hardening (command-center layer)

Cross-cutting improvements to the console frame itself, independent of any
one module:

- **Per-module error isolation** (`components/ErrorBoundary.tsx`): a
  render-time throw in one module shows a recoverable fallback in that panel
  only — it no longer white-screens the whole console. The boundary resets
  automatically when you switch tabs.
- **Visibility-aware polling** (`lib/usePolling.ts`): every poller pauses
  while the browser tab is backgrounded and fires one catch-up fetch on
  refocus. This stops ~26 always-on pollers from spending the budgeted API
  credits Mission Control tracks while nobody is watching. The header shows a
  **Live / Paused** pill reflecting the current state.
- **Deep-linkable, restorable tabs** (`lib/useHashTab.ts`): the active module
  lives in the URL hash (`#/engine-monitor`) and mirrors to localStorage —
  shareable links, working back/forward, and the console reopens where you
  left it instead of always on Mission Control.
- **Command palette** (`components/CommandPalette.tsx`): `⌘K` / `Ctrl-K`
  opens a fuzzy jump-to-module switch over all 15 modules — arrow keys +
  Enter, never leaving the keyboard. Tab definitions are centralized in
  `lib/tabs.ts`.
