import { create } from 'zustand'

export interface ConsoleEntry {
  level: 'error' | 'warn' | 'unhandledrejection'
  message: string
  timestamp: string
}

const MAX_ENTRIES = 200

interface ConsoleCaptureState {
  entries: ConsoleEntry[]
  append: (e: ConsoleEntry) => void
  clear: () => void
}

// No persist middleware (2026-07-26): unlike presetsStore.ts, this is
// deliberately NOT saved across reloads — a stale error from a previous
// page load reappearing here would actively mislead live diagnostics.
export const useConsoleStore = create<ConsoleCaptureState>((set) => ({
  entries: [],
  append: (e) => set((s) => ({ entries: [...s.entries, e].slice(-MAX_ENTRIES) })),
  clear: () => set({ entries: [] }),
}))

function record(level: ConsoleEntry['level'], message: string) {
  useConsoleStore.getState().append({ level, message, timestamp: new Date().toISOString() })
}

/**
 * Installs global capture exactly once. Must be imported early (main.tsx)
 * so errors from the very first paint — including a module ErrorBoundary
 * trip, which already calls console.error (ErrorBoundary.tsx) — are
 * captured even before the BottomPanel's Console tab is ever opened.
 */
export function installConsoleCapture() {
  const originalError = console.error.bind(console)
  console.error = (...args: unknown[]) => {
    record('error', args.map(String).join(' '))
    originalError(...args)
  }
  const originalWarn = console.warn.bind(console)
  console.warn = (...args: unknown[]) => {
    record('warn', args.map(String).join(' '))
    originalWarn(...args)
  }
  window.addEventListener('error', (e) => {
    record('error', e.filename ? `${e.message} (${e.filename}:${e.lineno})` : e.message)
  })
  window.addEventListener('unhandledrejection', (e) => {
    record('unhandledrejection', e.reason instanceof Error ? e.reason.message : String(e.reason))
  })
}
