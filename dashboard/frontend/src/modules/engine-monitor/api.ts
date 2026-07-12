import { apiGet, apiPost } from '../../lib/api'

export interface EngineStat {
  engine: string
  total_votes: number
  neutral_pct: number
  bullish_pct: number
  bearish_pct: number
  agreement_rate: number | null
  avg_score_when_voting: number
}

export interface EngineAttribution {
  engine: string
  matched_trades: number
  wins: number
  losses: number
  win_rate: number | null
  profit_factor: number | 'Infinity' | null
  direction_agreement_pct: number | null
}

export interface AttributionResponse {
  window_seconds: number
  note: string
  total_closed_trades: number
  matched_trades: number
  engines: EngineAttribution[]
}

export interface EngineStatsResponse {
  engine_stats: EngineStat[]
  neutral_rates: { engine: string; total: number; neutral_pct: number }[]
  current_weights: Record<string, number>
  suggested_weights: Record<string, number>
  attribution: AttributionResponse
  note: string
}

export const getEngineStats = () => apiGet<EngineStatsResponse>('/engine-stats')

// AI weight optimizer (ai/dynamic_weights.py) — a distinct, older AI
// feature from ai/ai_analyzer.py's explain/news/macro/research layer.
// Always called dry_run — this dashboard only ever shows suggestions,
// never writes config.yaml itself. Applying a weight change is a
// deliberate config edit, done separately, not a dashboard button click.
export interface AiWeightSuggestion {
  status: 'success' | 'insufficient_data' | 'not_configured' | 'parse_error' | 'error'
  message?: string
  suggested_weights: Record<string, number>
  reasoning?: Record<string, string>
  confidence?: string
  note?: string
  trades_analyzed?: number
}

export const getAiWeightSuggestions = () => apiPost<AiWeightSuggestion>('/ai/optimize-weights?dry_run=true')
