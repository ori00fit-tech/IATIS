import { apiGet, apiPost } from '../../lib/api'

export type EngineBenchmarkProfile = 'smoke' | 'standard' | 'deep'

export interface EngineBenchmarkRunRequest {
  profile: EngineBenchmarkProfile
  symbols?: string[]
  engines?: string[]
  start?: string
  end?: string
}

export interface EngineBenchmarkRunSummary {
  run_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface EngineBenchmarkRunRow {
  id: string
  profile: string
  status: string
  symbols_json: string
  engines_json: string
  start_date: string | null
  end_date: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface EngineBenchmarkProgress {
  total_results: number
  run_ok: number
  run_failed: number
}

export interface EngineBenchmarkRunStatusResponse {
  run_id: string
  run: EngineBenchmarkRunRow | null
  progress: EngineBenchmarkProgress
  job_status: string | null
}

// Deliberately NO composite/quality score anywhere in this shape — see
// backtest/engine_benchmark.py's own module docstring on why this is
// not a ranking tool. Every field below is a raw, real backtest KPI.
export interface EngineBenchmarkResultRow {
  run_id: string
  engine: string
  symbol: string
  run_ok: number
  error: string | null
  total_trades: number
  win_rate: number | null
  profit_factor: number | null
  sharpe_ratio: number | null
  sortino_ratio: number | null
  max_drawdown: number | null
  expectancy_r: number | null
  expectancy: number | null
  bars_used: number
  data_start: string | null
  data_end: string | null
  created_at: string
}

export const createEngineBenchmark = (body: EngineBenchmarkRunRequest) =>
  apiPost<{ run_id: string } & EngineBenchmarkRunSummary>('/research/engine-benchmark', body)

export const listEngineBenchmarks = () => apiGet<{ runs: EngineBenchmarkRunSummary[] }>('/research/engine-benchmark')

export const getEngineBenchmark = (runId: string) =>
  apiGet<EngineBenchmarkRunStatusResponse>(`/research/engine-benchmark/${encodeURIComponent(runId)}`)

export const getEngineBenchmarkResults = (runId: string, symbol?: string, engine?: string) =>
  apiGet<{ run_id: string; results: EngineBenchmarkResultRow[] }>(
    `/research/engine-benchmark/${encodeURIComponent(runId)}/results`,
    { symbol, engine },
  )

export const cancelEngineBenchmark = (runId: string) =>
  apiPost<EngineBenchmarkRunSummary>(`/research/engine-benchmark/${encodeURIComponent(runId)}/cancel`)
