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
}))
