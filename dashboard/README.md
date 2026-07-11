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
| Mission Control | `/health`, `/health/full`, `/budget`, `/symbol-health` | system status, CPU/RAM/disk, credits |
| Live Signals | `/decisions`, `/outcomes` | recent pipeline decisions + open paper signals |
| Data Center | `/data-health` (new) | OHLCV cache completeness per symbol/timeframe |
| Engine Monitor | `/engine-stats` | per-engine vote/accuracy stats, current vs. suggested weights |
| Research & Backtests | `/research`, `/backtest-results`, `/meta-analysis` | hypothesis registry, backtest runs, regime matrix |
| Live Logs | `/logs`, `/logs/sources` | whitelisted journalctl/file log tail — no arbitrary shell access, see Mission Control module 13 |
| File Explorer | `/files/tree`, `/files/read`, `/files/download`, `/files/search`, `/files/diff` | read-only, path-confined to the repo root, secret-shaped paths denylisted server-side, see Mission Control module 11 |
| Alert Center | `/alerts` | aggregates signals already computed by other endpoints (scheduler status, provider config, data health, manifests, forward decision rules) — no new data source, see Mission Control module 14 |
| Forward Demo | `/outcomes` (extended with profit_factor/avg_r_multiple), `/forward-review`, `/shadow-book` | D001/D002 pre-registered forward decision rule progress + the shadow-book counterfactual gate ledger, see Mission Control module 6 |

All polling-based (15–60s depending on module) — no WebSocket in v1; see
`.claude/plans/glittery-drifting-lerdorf.md` for the full architecture
rationale.
