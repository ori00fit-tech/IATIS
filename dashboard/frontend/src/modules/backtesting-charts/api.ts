import { apiGet } from '../../lib/api'
import { getFileContent } from '../file-explorer/api'

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

// ── Queue Manager run reports (Phase 5/6, 2026-07-24) — completed
// backtest/walk_forward/robustness job outputs, a separate data source
// from the legacy /backtest-results above (in-sample pipeline scans).
// These come from real Queue Manager runs (Experiment Runner tab) and
// carry actual chart-ready series data, not just aggregate metrics.

export type RunReportKind = 'backtest' | 'walk_forward' | 'robustness' | 'chart_data' | 'unknown'

export interface RunReportEntry {
  kind: RunReportKind
  file: string
  size_bytes: number
  readable?: boolean
  error?: string
  generated_utc?: string | null
  evaluated?: number | null
  highlights: Record<string, unknown>
}

export interface RunReportsResponse {
  reports_dir: string
  count: number
  reports: RunReportEntry[]
}

export const getRunReports = () => apiGet<RunReportsResponse>('/research/run-reports')

// backtest/report.py's chart_data sidecar — one per symbol, written
// alongside every HTML backtest report.
// Interactive Chart entry/exit markers + per-trade decision panel
// (2026-07-25) — real per-engine bias/score captured at entry time
// (backtesting.backtest_engine.py's decision snapshot), not synthesized
// client-side. engine_votes/cf_score/regime are null for trades that
// predate that change or came from a Trade built outside run_backtest.
export interface ChartEngineVote {
  engine: string
  bias: string
  score: number
  reasons: string[]
}

export interface ChartTrade {
  trade_id: string
  direction: string
  entry_time: number | null // unix seconds
  exit_time: number | null
  entry_price: number
  exit_price: number | null
  stop_loss: number
  take_profit: number
  pnl_usd: number
  is_win: boolean
  exit_reason: string
  regime: string | null
  cf_score: number | null
  engine_votes: Record<string, ChartEngineVote> | null
}

export interface ChartCandle {
  time: number // unix seconds
  open: number
  high: number
  low: number
  close: number
}

export interface ChartDataFile {
  symbol: string
  timeframe: string
  equity_curve: { x: string; y: number }[]
  monthly_returns: Record<string, number>
  yearly_returns: Record<string, number>
  by_regime: Record<string, unknown>
  by_symbol: Record<string, unknown>
  by_direction: Record<string, unknown>
  by_session: Record<string, unknown>
  candles: ChartCandle[] | null
  trades: ChartTrade[]
  monte_carlo: {
    median_return: number
    p5_return: number
    p95_return: number
    median_max_dd: number
    worst_max_dd: number
    risk_of_ruin: number
    probability_profit: number
  } | null
}

/** Fetches a *_chart_data.json sidecar's full content via the File
 * Explorer's generic, repo-confined /files/read — /research/run-reports
 * only returns lightweight highlights, never the full series. */
export async function getChartDataFile(file: string): Promise<ChartDataFile> {
  const res = await getFileContent(`reports/${file}`)
  if (res.error || res.content == null) {
    throw new Error(res.error ?? `${file}: empty content`)
  }
  return JSON.parse(res.content) as ChartDataFile
}
