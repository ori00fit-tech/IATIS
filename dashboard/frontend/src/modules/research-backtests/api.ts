import { apiGet } from '../../lib/api'

export interface Hypothesis {
  id: string
  title: string
  status: string
  description: string
  last_updated: string
  sample_size?: number
  win_rate?: number
  p_value?: number
}

export interface ResearchResponse {
  hypothesis_summary: { total: number; passed: number; failed: number; research: number; needs_data: number }
  hypotheses: Hypothesis[]
  latest_backtest: {
    file: string
    generated_at: string
    avg_wr: number
    avg_pf: number
    top_symbols: { symbol: string; win_rate: number; profit_factor: number }[]
  } | null
}

export interface BacktestResult {
  file: string
  symbol: string
  period: string
  trades: number
  win_rate: number
  profit_factor: number
  max_drawdown_pct: number
  total_return_pct: number
}

export interface BacktestResultsResponse {
  count: number
  results: BacktestResult[]
}

export interface RegimeRow {
  regime: string
  total_decisions: number
  executes: number
  execute_rate: number
  wins: number
  losses: number
  win_rate: number | null
  profit_factor: number | null
  expectancy_usd: number | null
}

export interface MetaAnalysisResponse {
  regime_matrix: { data: RegimeRow[]; note: string }
}

export const getResearch = () => apiGet<ResearchResponse>('/research')
export const getBacktestResults = () => apiGet<BacktestResultsResponse>('/backtest-results')
export const getMetaAnalysis = () => apiGet<MetaAnalysisResponse>('/meta-analysis')
