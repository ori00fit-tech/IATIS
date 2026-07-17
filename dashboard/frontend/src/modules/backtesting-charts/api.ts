import { apiGet } from '../../lib/api'

// Backtesting Charts is a visualization layer over data other endpoints
// already produce: /backtest-results (per-run metrics + an optional legacy
// equity_curve) and /outcomes (score-bucket calibration). No new computation
// runs server-side and nothing here can influence a decision.

export interface BacktestRun {
  file: string
  symbol: string
  period: string
  trades?: number
  win_rate?: number
  profit_factor?: number
  max_drawdown_pct?: number
  total_return_pct?: number
  // Per-bar balance series — present only for legacy backtest_engine.save()
  // runs; pipeline runs omit it. Down-sampled server-side to <=500 points.
  equity_curve?: number[]
  metrics?: Record<string, number | undefined>
}

export interface BacktestResultsResponse {
  count: number
  results: BacktestRun[]
}

export interface CalibrationBucket {
  bucket: string
  n: number
  wins: number
}

export interface OutcomesCalibration {
  summary: {
    total_closed: number
    calibration: CalibrationBucket[]
    note: string
  }
}

export const getBacktestResults = () => apiGet<BacktestResultsResponse>('/backtest-results')
export const getOutcomesCalibration = () => apiGet<OutcomesCalibration>('/outcomes', { limit: 1 })
