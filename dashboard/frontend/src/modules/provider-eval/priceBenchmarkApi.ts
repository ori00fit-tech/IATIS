import { apiGet, apiPost } from '../../lib/api'

export type BenchmarkProfile = 'smoke' | 'standard' | 'deep'

export interface BenchmarkRunRequest {
  profile: BenchmarkProfile
  symbols?: string[]
  timeframes?: string[]
  providers?: string[]
  outputsize?: number
  tolerance_pct?: number
}

export interface BenchmarkRunSummary {
  run_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface BenchmarkRunRow {
  id: string
  profile: string
  status: string
  symbols_json: string
  timeframes_json: string
  providers_json: string | null
  outputsize: number
  tolerance_pct: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface BenchmarkProgress {
  total_results: number
  fetch_ok: number
  fetch_failed: number
}

export interface BenchmarkRunStatusResponse {
  run_id: string
  run: BenchmarkRunRow | null
  progress: BenchmarkProgress
  job_status: string | null
}

export interface BenchmarkResultRow {
  run_id: string
  provider: string
  symbol: string
  timeframe: string
  fetch_ok: number
  error: string | null
  latency_ms: number | null
  bars_fetched: number
  completeness_score: number | null
  correctness_score: number | null
  timestamp_integrity_score: number | null
  ohlc_integrity_score: number | null
  ohlc_integrity_reason: string | null
  spread_quality_score: number | null
  cross_provider_agreement_score: number | null
  freshness_score: number | null
  latency_score: number | null
  composite_score: number | null
  detail_json: string | null
  // Phase 1b: capped (close, consensus_close, diff_pct) tuples for the
  // Evidence drill-down chart, JSON-encoded — null when the provider's
  // bars never overlapped the run's consensus (see build_evidence_series).
  evidence_series_json: string | null
  created_at: string
}

export const createPriceBenchmark = (body: BenchmarkRunRequest) =>
  apiPost<{ run_id: string } & BenchmarkRunSummary>('/research/provider-benchmark', body)

export const listPriceBenchmarks = () => apiGet<{ runs: BenchmarkRunSummary[] }>('/research/provider-benchmark')

export const getPriceBenchmark = (runId: string) =>
  apiGet<BenchmarkRunStatusResponse>(`/research/provider-benchmark/${encodeURIComponent(runId)}`)

export const getPriceBenchmarkResults = (runId: string, symbol?: string, provider?: string) =>
  apiGet<{ run_id: string; results: BenchmarkResultRow[] }>(
    `/research/provider-benchmark/${encodeURIComponent(runId)}/results`,
    { symbol, provider },
  )

export const cancelPriceBenchmark = (runId: string) =>
  apiPost<BenchmarkRunSummary>(`/research/provider-benchmark/${encodeURIComponent(runId)}/cancel`)
