export interface TabDef {
  id: string
  label: string
  /** One-line purpose, shown in the command palette. */
  hint: string
  /** Short glyph shown in the palette / nav for fast visual scanning. */
  glyph: string
  /** Sidebar grouping (Phase 1 institutional-redesign shell) — purely
   * additive metadata; id/label/hint/glyph and every existing consumer
   * (CommandPalette, useHashTab) are unaffected by this field. */
  section: string
}

/** Sidebar section display order — Sidebar.tsx iterates this, not object
 * insertion order, so re-ordering sections never requires touching TABS. */
export const SECTION_ORDER = [
  'Overview',
  'Live Ops',
  'Research & Backtests',
  'Data & Providers',
  'Engines & AI',
  'System & Audit',
  'Meta',
] as const

export const TABS: readonly TabDef[] = [
  { id: 'mission-control', label: 'Mission Control', hint: 'System health, credits, paper-trading evidence', glyph: '◎', section: 'Overview' },
  { id: 'live-signals', label: 'Live Signals', hint: 'Recent pipeline decisions + open paper signals', glyph: '⚡', section: 'Overview' },
  { id: 'alerts', label: 'Alert Center', hint: 'Aggregated signals from other endpoints', glyph: '⚑', section: 'Overview' },
  { id: 'forward-demo', label: 'Forward Demo', hint: 'D001/D002 forward rules + shadow-book gate ledger', glyph: '▮', section: 'Live Ops' },
  { id: 'journal', label: 'Trade Journal', hint: 'Every paper trade in detail: R, engines, notes, tags, equity curve', glyph: '✎', section: 'Live Ops' },
  { id: 'risk-center', label: 'Risk Center', hint: 'Exposure, RR compliance, R-distribution, per-symbol risk', glyph: '⛨', section: 'Live Ops' },
  { id: 'execution-quality', label: 'Execution Quality', hint: 'Real fills, slippage, TCA', glyph: '◈', section: 'Live Ops' },
  { id: 'research', label: 'Research & Backtests', hint: 'Hypothesis registry, backtest runs, regime matrix', glyph: '⌕', section: 'Research & Backtests' },
  { id: 'backtesting-lab', label: 'Backtesting Lab', hint: 'Step-by-step experiment builder — dataset, symbols, strategy, execution', glyph: '🧪', section: 'Research & Backtests' },
  { id: 'backtesting-charts', label: 'Backtesting Charts', hint: 'Equity curve, per-symbol comparison, score calibration', glyph: '📈', section: 'Research & Backtests' },
  { id: 'experiments', label: 'Experiment Runner', hint: 'Whitelisted subprocess jobs', glyph: '⏵', section: 'Research & Backtests' },
  { id: 'mission-center', label: 'Mission Center', hint: 'AI Research Lab — Optuna-sampled search missions, exploratory only', glyph: '◭', section: 'Research & Backtests' },
  { id: 'data-center', label: 'Data Center', hint: 'OHLCV cache completeness, provider chains', glyph: '⛁', section: 'Data & Providers' },
  { id: 'provider-eval', label: 'Provider Eval', hint: 'Rank data providers for best valid data + advisory chain order', glyph: '⇅', section: 'Data & Providers' },
  { id: 'engine-monitor', label: 'Engine Monitor', hint: 'Per-engine votes, accuracy, weights', glyph: '⚙', section: 'Engines & AI' },
  { id: 'ai-decision-center', label: 'AI Decision Center', hint: 'Decision anatomy + explain-only AI narration', glyph: '❋', section: 'Engines & AI' },
  { id: 'ai-settings', label: 'AI Settings', hint: 'Provider/model selection for the explanation layer — never the decision path', glyph: '⚗', section: 'Engines & AI' },
  { id: 'system-audit', label: 'System Audit', hint: 'Philosophy audit + research integrity checks', glyph: '✓', section: 'System & Audit' },
  { id: 'logs', label: 'Live Logs', hint: 'Whitelisted journalctl / file log tail', glyph: '≣', section: 'System & Audit' },
  { id: 'files', label: 'File Explorer', hint: 'Read-only, repo-confined file browser', glyph: '⁋', section: 'System & Audit' },
  { id: 'reports', label: 'Reports', hint: 'Markdown/JSON snapshots of computed state', glyph: '❏', section: 'System & Audit' },
  { id: 'ops', label: 'VPS Operations', hint: 'Config reload, diagnostics, backups', glyph: '⎈', section: 'System & Audit' },
  { id: 'roadmap', label: 'Roadmap', hint: 'Planned modules and phases', glyph: '⌗', section: 'Meta' },
] as const

export type TabId = (typeof TABS)[number]['id']

const IDS = new Set(TABS.map((t) => t.id))
export function isTabId(value: string): value is TabId {
  return IDS.has(value)
}
