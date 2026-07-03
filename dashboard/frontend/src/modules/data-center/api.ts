import { apiGet } from '../../lib/api'

export type CacheStatus = 'OK' | 'STALE' | 'GAPS' | 'MISSING'

export interface TimeframeStatus {
  bars: number
  last_bar_time: string | null
  age_minutes: number | null
  gap_count_30d: number
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
  summary: { ok: number; stale: number; gaps: number; missing: number }
}

export const getDataHealth = () => apiGet<DataHealthResponse>('/data-health')
