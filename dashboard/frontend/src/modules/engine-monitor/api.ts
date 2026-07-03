import { apiGet } from '../../lib/api'

export interface EngineStat {
  engine: string
  total_votes: number
  neutral_pct: number
  bullish_pct: number
  bearish_pct: number
  agreement_rate: number | null
  avg_score_when_voting: number
}

export interface EngineStatsResponse {
  engine_stats: EngineStat[]
  neutral_rates: { engine: string; total: number; neutral_pct: number }[]
  current_weights: Record<string, number>
  suggested_weights: Record<string, number>
  note: string
}

export const getEngineStats = () => apiGet<EngineStatsResponse>('/engine-stats')
