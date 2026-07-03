import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Proxy target: local FastAPI dev server (uvicorn execution.api_server:app --reload --port 8000)
const API_PROXY_TARGET = process.env.IATIS_API_URL || 'http://127.0.0.1:8000'

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
  '/symbol-health',
  '/data-health',
  '/ai',
  '/analyze',
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
