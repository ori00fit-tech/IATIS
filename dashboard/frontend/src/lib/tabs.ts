export interface TabDef {
  id: string
  label: string
  /** One-line purpose, shown in the command palette. */
  hint: string
  /** Short glyph shown in the palette / nav for fast visual scanning. */
  glyph: string
}

export const TABS: readonly TabDef[] = [
  { id: 'mission-control', label: 'Mission Control', hint: 'System health, credits, paper-trading evidence', glyph: '◎' },
  { id: 'live-signals', label: 'Live Signals', hint: 'Recent pipeline decisions + open paper signals', glyph: '⚡' },
  { id: 'forward-demo', label: 'Forward Demo', hint: 'D001/D002 forward rules + shadow-book gate ledger', glyph: '▮' },
  { id: 'risk-center', label: 'Risk Center', hint: 'Exposure, RR compliance, R-distribution, per-symbol risk', glyph: '⛨' },
  { id: 'execution-quality', label: 'Execution Quality', hint: 'Real fills, slippage, TCA', glyph: '◈' },
  { id: 'data-center', label: 'Data Center', hint: 'OHLCV cache completeness, provider chains', glyph: '⛁' },
  { id: 'engine-monitor', label: 'Engine Monitor', hint: 'Per-engine votes, accuracy, weights', glyph: '⚙' },
  { id: 'research', label: 'Research & Backtests', hint: 'Hypothesis registry, backtest runs, regime matrix', glyph: '⌕' },
  { id: 'backtesting-charts', label: 'Backtesting Charts', hint: 'Equity curve, per-symbol comparison, score calibration', glyph: '📈' },
  { id: 'system-audit', label: 'System Audit', hint: 'Philosophy audit + research integrity checks', glyph: '✓' },
  { id: 'logs', label: 'Live Logs', hint: 'Whitelisted journalctl / file log tail', glyph: '≣' },
  { id: 'files', label: 'File Explorer', hint: 'Read-only, repo-confined file browser', glyph: '⁋' },
  { id: 'alerts', label: 'Alert Center', hint: 'Aggregated signals from other endpoints', glyph: '⚑' },
  { id: 'reports', label: 'Reports', hint: 'Markdown/JSON snapshots of computed state', glyph: '❏' },
  { id: 'experiments', label: 'Experiment Runner', hint: 'Whitelisted subprocess jobs', glyph: '⏵' },
  { id: 'ops', label: 'VPS Operations', hint: 'Config reload, diagnostics, backups', glyph: '⎈' },
  { id: 'roadmap', label: 'Roadmap', hint: 'Planned modules and phases', glyph: '⌗' },
] as const

export type TabId = (typeof TABS)[number]['id']

const IDS = new Set(TABS.map((t) => t.id))
export function isTabId(value: string): value is TabId {
  return IDS.has(value)
}
