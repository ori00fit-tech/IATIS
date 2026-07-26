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

// Per-category performance split (backtest/metrics.py:280-309) — every key
// present in one of these dicts has trades > 0 by construction (win_rate is
// only ever added there once a category has at least one trade), so these
// fields are safe as required, not optional.
export interface BreakdownBucket {
  trades: number
  wins: number
  win_rate: number
  pnl: number
}

export interface ChartDataFile {
  symbol: string
  timeframe: string
  equity_curve: { x: string; y: number }[]
  monthly_returns: Record<string, number>
  yearly_returns: Record<string, number>
  by_regime: Record<string, BreakdownBucket>
  by_symbol: Record<string, unknown>
  by_direction: Record<string, BreakdownBucket>
  by_session: Record<string, BreakdownBucket>
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

// Parameter Sweep full detail (2026-07-25) — backtest/robustness.py's
// complete per-point output, not the lightweight verdict-only highlights
// /research/run-reports returns. Every point is here; there is no
// "winner" field anywhere in this shape by design.
// profit_factor is Infinity whenever a point has zero losing trades — a
// real, correct value (backtest/metrics.py's own definition), not a bug.
// The backend sanitizes it to the string "Infinity"/"-Infinity"/"NaN"
// before serializing (bare Infinity is not valid JSON), so this is a
// real union, not defensive typing for a case that can't happen.
export type PossiblyInfinite = number | 'Infinity' | '-Infinity' | 'NaN'

export function formatPossiblyInfinite(v: PossiblyInfinite, digits = 2): string {
  if (v === 'Infinity') return '∞'
  if (v === '-Infinity') return '-∞'
  if (v === 'NaN') return '—'
  return v.toFixed(digits)
}

export interface RobustnessSweepPoint {
  multiplier: number
  value: number
  trades: number
  profit_factor: PossiblyInfinite
  win_rate: number
  max_drawdown_pct: number
  sufficient: boolean
}

export interface RobustnessSweep {
  param: string
  baseline_value: number
  baseline_pf: PossiblyInfinite
  verdict: string
  points: RobustnessSweepPoint[]
}

export interface RobustnessSymbolResult {
  min_trades: number
  multipliers: number[]
  sweeps: RobustnessSweep[]
}

export interface RobustnessReportFile {
  generated_utc: string
  all_params_stable: number
  evaluated: number
  note: string
  engine_overrides: Record<string, unknown>
  symbols: Record<string, RobustnessSymbolResult>
}

export async function getRobustnessReportFile(file: string): Promise<RobustnessReportFile> {
  const res = await getFileContent(`reports/${file}`)
  if (res.error || res.content == null) {
    throw new Error(res.error ?? `${file}: empty content`)
  }
  return JSON.parse(res.content) as RobustnessReportFile
}
