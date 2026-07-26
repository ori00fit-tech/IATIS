import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Proxy target: local FastAPI dev server (uvicorn execution.api_server:app --reload --port 8000)
const API_PROXY_TARGET = process.env.IATIS_API_URL || 'http://127.0.0.1:8000'

// Kept in sync with execution/routes/*.py's top-level path prefixes —
// this list drifted stale after the Mission Control build-out (journal,
// risk-center, execution-quality, alerts, reports, experiments, ops,
// files, forward-demo tabs all added later) added routers this list
// never picked up, silently breaking those tabs under `npm run dev`
// (production is unaffected: the built bundle is served same-origin by
// FastAPI itself, no proxy involved).
const API_PATHS = [
  '/health',
  '/decisions',
  '/budget',
  '/stats',
  '/login',
  '/logout',
  '/experience',
  '/engine-stats',
  '/backtest-results',
  '/research',
  '/meta-analysis',
  '/outcomes',
  '/candles',
  '/symbol-health',
  '/data-health',
  '/ai',
  '/analyze',
  '/alerts',
  '/audit-log',
  '/dashboard',
  '/data-confidence',
  '/execution-quality',
  '/experiments',
  '/files',
  '/forward-review',
  '/journal',
  '/logs',
  '/metrics',
  '/ops',
  '/philosophy-audit',
  '/provider-chains',
  '/reconciliation',
  '/reports',
  '/shadow-book',
]

export default defineConfig(({ mode }) => ({
  // Production build is mounted at /app on the existing FastAPI app.
  base: mode === 'production' ? '/app/' : '/',
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(
      API_PATHS.map((path) => [
        path,
        { target: API_PROXY_TARGET, changeOrigin: true, secure: false },
      ]),
    ),
  },
  build: {
    rolldownOptions: {
      output: {
        // Phase 5 (2026-07-26): this project's vite@8 bundles via Rolldown,
        // not classic Rollup — `manualChunks` is deprecated and (unlike
        // Rollup) function-only, with no equivalent to the `$initial` tag
        // below, so it can't tell eager code apart from code only reachable
        // through a dynamic import(). `codeSplitting.groups` is the current
        // API (verified against node_modules/rolldown's own type defs).
        codeSplitting: {
          groups: [
            // react + react-dom (+ scheduler) — the one vendor chunk that
            // never changes between deploys. Higher priority so it's
            // resolved before the generic `vendor` group below.
            {
              name: 'vendor-react',
              test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/,
              tags: ['$initial'],
              priority: 20,
            },
            // Everything else the always-mounted shell needs eagerly
            // (react-query, framer-motion, cmdk, zustand, the Radix bits
            // Sidebar/BottomPanel/CommandPalette use). `$initial` is the
            // load-bearing guard: it restricts this group to code reachable
            // from the eager entry graph, so `echarts` — already isolated
            // behind Phase 3's component-level React.lazy, only reachable
            // via a dynamic import — can never be swept back into an
            // eagerly-loaded chunk.
            {
              name: 'vendor',
              test: /node_modules[\\/]/,
              tags: ['$initial'],
              priority: 10,
            },
          ],
        },
      },
    },
  },
}))
