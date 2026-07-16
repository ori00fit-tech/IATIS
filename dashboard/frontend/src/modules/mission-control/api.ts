import { apiGet, apiGetText } from '../../lib/api'

export interface Health {
  status: string
  version: string
  timestamp: string
  twelve_data_credits_remaining: number | null
  decision_timeframe: string | null
}

// Paper-trading evidence tracker (audit Phase 5): live outcomes are the
// only path to a defensible edge claim — this makes the counter visible.
export interface OutcomesSummary {
  summary: {
    total_closed: number
    wins: number
    losses: number
    win_rate: number
    total_pips: number
    open_signals: number
  }
  open_signals: unknown[]
}

export interface HealthFull {
  status: 'healthy' | 'degraded'
  issues: string[]
  checked_at: string
  system?: {
    cpu_pct: number
    ram_pct: number
    disk_pct: number
    swap_pct: number
    load_1m: number | null
    load_5m: number | null
    load_15m: number | null
    uptime_hours: number
    error?: string
  }
  scheduler?: { last_run: string | null; last_execute_count: number; status: string }
  // kind: "daemon" (inactive = down) vs "timer" (inactive = normal idle
  // state between scheduled runs) — healthy is already computed per-kind
  // server-side, the frontend never needs to re-derive it.
  services?: Record<string, { status: string; kind: 'daemon' | 'timer'; healthy: boolean }>
  database?: { status: string; total_decisions?: number; last_24h?: number; error?: string }
  calendar?: { status: string; fetched_at?: string; event_count?: number; note?: string }
  outcome_tracker?: { status: string; total_closed?: number; win_rate?: number; open_signals?: number }
  // Upper-bound estimate, not the live risk-engine figure — see the
  // `note` field and execution/api_server.py's /health/full docstring
  // for why the real number is unreachable from this process.
  exposure_estimate?: {
    open_positions: number
    estimated_pct: number
    max_exposure_pct: number
    utilization_pct: number | null
    note: string
  }
  data_providers?: Record<string, string>
  ctrader?: { configured: boolean; account_id: string; environment: string }
}

export interface Budget {
  max_per_day: number
  used_today: number
  remaining_today: number
  percent_used: number
}

export interface SymbolHealthEntry {
  symbol: string
  shi_score: number
  status: 'HEALTHY' | 'CAUTION' | 'PAUSED'
  win_rate: number | null
  profit_factor: number | null
  trades_count: number
  consecutive_losses: number
  position_multiplier: number
  last_updated: string
  reason: string
  // false when shi_score is the neutral default (< 5 trades), not a real
  // measurement — status/position_multiplier are unchanged either way
  // (scheduler.py trading logic), this only tells the dashboard whether
  // the badge is measuring something yet.
  has_sufficient_data: boolean
}

export interface SymbolHealthResponse {
  total: number
  healthy: number
  caution: number
  paused: number
  symbols: SymbolHealthEntry[]
}

export const getHealth = () => apiGet<Health>('/health')
export const getHealthFull = () => apiGet<HealthFull>('/health/full')
export const getBudget = () => apiGet<Budget>('/budget')
export const getSymbolHealth = () => apiGet<SymbolHealthResponse>('/symbol-health')
export const getOutcomes = () => apiGet<OutcomesSummary>('/outcomes')

// AI briefing (ai/ai_analyzer.py) — explanation/reporting only, fetched
// on demand (not polled: unlike the widgets above these hit an external
// LLM provider, even though the backend caches each for 20-60min).
export interface AiNewsAnalysis {
  status: 'ok' | 'disabled' | 'error'
  sentiment: string
  impact: 'LOW' | 'MEDIUM' | 'HIGH'
  affected_symbols: string[]
  duration: string
  confidence: number
  summary: string
  provider: string
  error: string
}

export interface AiMacroAnalysis {
  status: 'ok' | 'disabled' | 'error'
  summary: string
  risk_on_off: 'RISK_ON' | 'RISK_OFF' | 'NEUTRAL'
  dxy_bias: string
  key_drivers: string[]
  confidence: number
  provider: string
  error: string
}

export interface AiDailyReport {
  status: 'ok' | 'disabled' | 'error'
  text: string
  provider: string
  error?: string
}

export const getAiNewsAnalysis = () => apiGet<AiNewsAnalysis>('/ai/news-analysis')
export const getAiMacroAnalysis = () => apiGet<AiMacroAnalysis>('/ai/macro-analysis')
export const getAiDailyReport = () => apiGet<AiDailyReport>('/ai/daily-report')

// Market Health (gap analysis A4 — monitoring only, never a gate):
// parsed from the Prometheus-text GET /metrics exposition
// (execution/metrics.py). One 'name value' pair per non-comment line.
export interface MarketHealth {
  d1Up: boolean
  d1LatencySeconds: number | null
  lastDecisionAgeSeconds: number | null
  decisionsTotal: number | null
  executeTotal: number | null
  openOutcomes: number | null
  fillsTotal: number | null
  schemaVersion: number | null
  schemaLatest: number | null
}

export async function getMarketHealth(): Promise<MarketHealth> {
  const text = await apiGetText('/metrics')
  const values: Record<string, number> = {}
  for (const line of text.split('\n')) {
    if (!line || line.startsWith('#')) continue
    const idx = line.lastIndexOf(' ')
    if (idx <= 0) continue
    const v = Number(line.slice(idx + 1))
    if (!Number.isNaN(v)) values[line.slice(0, idx)] = v
  }
  const get = (name: string): number | null => (name in values ? values[name] : null)
  return {
    d1Up: get('iatis_d1_up') === 1,
    d1LatencySeconds: get('iatis_d1_latency_seconds'),
    lastDecisionAgeSeconds: get('iatis_last_decision_age_seconds'),
    decisionsTotal: get('iatis_decisions_total'),
    executeTotal: get('iatis_execute_decisions_total'),
    openOutcomes: get('iatis_open_outcomes'),
    fillsTotal: get('iatis_fills_total'),
    schemaVersion: get('iatis_schema_version'),
    schemaLatest: get('iatis_schema_version_latest'),
  }
}

// Broker-vs-internal position reconciliation (gap analysis M3). Read-only:
// the scheduler (which owns the single cTrader session) stores a result
// every tick; this endpoint only reads the latest stored row.
export interface ReconciliationResult {
  status: 'match' | 'mismatch' | 'skipped' | 'none'
  checked_at?: string
  reason?: string | null
  broker_only?: string[]
  internal_only?: string[]
  n_broker?: number | null
  n_internal?: number | null
}

export function getReconciliation(): Promise<ReconciliationResult> {
  return apiGet<ReconciliationResult>('/reconciliation')
}
