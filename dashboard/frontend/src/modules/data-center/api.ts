import { apiGet } from '../../lib/api'

export type CacheStatus = 'OK' | 'STALE' | 'GAPS' | 'STARVED' | 'MISSING'

export interface TimeframeStatus {
  bars: number
  provider?: string | null
  last_bar_time: string | null
  age_minutes: number | null
  gap_count_30d: number
  duplicate_count: number
  timezone: string | null
  integrity_score: number
  status: CacheStatus
}

export interface SymbolDataHealth {
  symbol: string
  timeframes: Record<string, TimeframeStatus>
  overall_status: CacheStatus
}

export interface DataHealthResponse {
  checked_at: string
  symbols: SymbolDataHealth[]
  summary: { ok: number; stale: number; gaps: number; starved?: number; missing: number }
}

export const getDataHealth = () => apiGet<DataHealthResponse>('/data-health')

// Cross-provider data-confidence history (core/data_confidence.py, gap
// analysis S1). Reads the STORED check table — polling this never burns
// provider API budget. Monitoring only, never a gate.
export interface DataConfidenceCheck {
  ts: string
  symbol: string
  interval: string
  provider_a: string | null
  provider_b: string | null
  bars_common: number | null
  mean_diff_pct: number | null
  max_diff_pct: number | null
  pct_exceeding: number | null
  verdict: string
}

export interface DataConfidenceHistory {
  checks: DataConfidenceCheck[]
  n: number
  material_disagreements: number
  note: string
}

export function getDataConfidence(): Promise<DataConfidenceHistory> {
  return apiGet<DataConfidenceHistory>('/data-confidence')
}
