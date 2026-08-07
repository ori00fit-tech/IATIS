import { apiGet, apiPost } from '../../lib/api'

export type MacroBenchmarkProfile = 'smoke' | 'standard' | 'deep'

export interface MacroBenchmarkRunRequest {
  profile: MacroBenchmarkProfile
  series?: string[]
  providers?: string[]
  months?: number
  tolerance_pct?: number
}

export interface MacroBenchmarkRunSummary {
  run_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface MacroBenchmarkRunRow {
  id: string
  profile: string
  status: string
  series_json: string
  providers_json: string | null
  months: number | null
  tolerance_pct: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface MacroBenchmarkProgress {
  total_results: number
  fetch_ok: number
  fetch_failed: number
}

export interface MacroBenchmarkRunStatusResponse {
  run_id: string
  run: MacroBenchmarkRunRow | null
  progress: MacroBenchmarkProgress
  job_status: string | null
}

export interface MacroBenchmarkResultRow {
  run_id: string
  provider: string
  series: string
  fetch_ok: number
  error: string | null
  latency_ms: number | null
  observation_count: number
  completeness_score: number | null
  freshness_score: number | null
  timestamp_integrity_score: number | null
  latency_score: number | null
  // Only ever populated for VIX/US10Y/US02Y — the 3 series with a real
  // second source (CBOE+FRED, or FRED+Alpha Vantage). null everywhere
  // else, excluded from the composite via renormalization, never
  // fabricated — see backtest/macro_benchmark.py's module docstring.
  cross_provider_agreement_score: number | null
  composite_score: number | null
  latest_value: number | null
  latest_date: string | null
  detail_json: string | null
  created_at: string
}

export const createMacroBenchmark = (body: MacroBenchmarkRunRequest) =>
  apiPost<{ run_id: string } & MacroBenchmarkRunSummary>('/research/macro-benchmark', body)

export const listMacroBenchmarks = () => apiGet<{ runs: MacroBenchmarkRunSummary[] }>('/research/macro-benchmark')

export const getMacroBenchmark = (runId: string) =>
  apiGet<MacroBenchmarkRunStatusResponse>(`/research/macro-benchmark/${encodeURIComponent(runId)}`)

export const getMacroBenchmarkResults = (runId: string, series?: string, provider?: string) =>
  apiGet<{ run_id: string; results: MacroBenchmarkResultRow[] }>(
    `/research/macro-benchmark/${encodeURIComponent(runId)}/results`,
    { series, provider },
  )

export const cancelMacroBenchmark = (runId: string) =>
  apiPost<MacroBenchmarkRunSummary>(`/research/macro-benchmark/${encodeURIComponent(runId)}/cancel`)
