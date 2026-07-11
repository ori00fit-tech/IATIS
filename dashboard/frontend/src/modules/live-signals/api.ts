import { apiGet, apiPost } from '../../lib/api'

export interface PipelineReport {
  symbol: string
  summary: string
  regime: { state: string; confidence: number; volatility: string; trend_strength: number; notes: string[] }
  confluence: {
    score: number
    raw_score: number
    passed: boolean
    fail_reasons: string[]
    vote: { winning_bias: string; agree_count: number; total_engines: number }
  }
  entry_price: number | null
  stop_loss: number | null
  take_profit: number | null
  risk_reward: string | null
}

export interface DecisionEntry {
  timestamp: string
  final_verdict: 'EXECUTE' | 'NO_TRADE'
  symbol: string
  report: PipelineReport
}

export interface DecisionsResponse {
  total_in_log: number
  matched: number
  returned: number
  summary: { total: number; execute: number; no_trade: number; no_trade_reasons: Record<string, number> }
  decisions: DecisionEntry[]
}

export interface DecisionFilters {
  verdict?: string
  symbol?: string
  date_from?: string
  date_to?: string
  engine?: string
  min_score?: number
  risk_rejected?: boolean
  reason?: string
}

export interface OpenSignal {
  signal_id: string
  symbol: string
  direction: string
  entry_price: number | null
  stop_loss: number | null
  take_profit: number | null
  entry_time: string
  cf_score: number
  regime: string | null
}

export interface OutcomesResponse {
  summary: { total_closed: number; wins: number; losses: number; win_rate: number; total_pips: number; open_signals: number }
  open_signals: OpenSignal[]
  recent: OpenSignal[]
}

export const getDecisions = (limit = 20, filters: DecisionFilters = {}) =>
  apiGet<DecisionsResponse>('/decisions', { limit, ...filters })
export const getOutcomes = (limit = 20) => apiGet<OutcomesResponse>('/outcomes', { limit })

// AI explanation layer (ai/ai_analyzer.py) — read-only, explanation only.
// The decision itself was already made by confluence+risk; this never
// changes final_verdict, it only asks a provider to phrase it in plain
// English. See execution/api_server.py's POST /ai/explain-trade.
export interface TradeExplanation {
  status: 'ok' | 'disabled' | 'error'
  summary: string
  pros: string[]
  cons: string[]
  risk_level: string
  confidence: number
  recommendation: string
  market_sentiment: string
  news_risk: string
  explanation: string
  warnings: string[]
  provider: string
  error: string
}

export const explainTrade = (report: PipelineReport) => apiPost<TradeExplanation>('/ai/explain-trade', report)
