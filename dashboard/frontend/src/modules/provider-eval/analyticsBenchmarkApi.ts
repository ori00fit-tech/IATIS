import { apiGet, apiPost } from '../../lib/api'

export type AnalyticsBenchmarkProfile = 'smoke' | 'standard' | 'deep'

export interface AnalyticsBenchmarkRunRequest {
  profile: AnalyticsBenchmarkProfile
  symbols?: string[]
  providers?: string[]
  hours_back?: number
  limit?: number
}

export interface AnalyticsBenchmarkRunSummary {
  run_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface AnalyticsBenchmarkRunRow {
  id: string
  profile: string
  status: string
  symbols_json: string
  providers_json: string
  hours_back: number
  article_limit: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface AnalyticsBenchmarkProgress {
  total_results: number
  fetch_ok: number
  fetch_failed: number
}

export interface AnalyticsBenchmarkRunStatusResponse {
  run_id: string
  run: AnalyticsBenchmarkRunRow | null
  progress: AnalyticsBenchmarkProgress
  job_status: string | null
}

export interface AnalyticsBenchmarkResultRow {
  run_id: string
  provider: string
  symbol: string
  fetch_ok: number
  error: string | null
  latency_ms: number | null
  article_count: number
  coverage_score: number | null
  // The star metric this phase was scoped for: does the SAME query,
  // repeated seconds later, return the same sentiment value for the
  // same underlying article. null (not fabricated) when there was zero
  // article overlap between the two fetches to compare.
  determinism_score: number | null
  freshness_score: number | null
  latency_score: number | null
  composite_score: number | null
  detail_json: string | null
  created_at: string
}

export const createAnalyticsBenchmark = (body: AnalyticsBenchmarkRunRequest) =>
  apiPost<{ run_id: string } & AnalyticsBenchmarkRunSummary>('/research/analytics-benchmark', body)

export const listAnalyticsBenchmarks = () =>
  apiGet<{ runs: AnalyticsBenchmarkRunSummary[] }>('/research/analytics-benchmark')

export const getAnalyticsBenchmark = (runId: string) =>
  apiGet<AnalyticsBenchmarkRunStatusResponse>(`/research/analytics-benchmark/${encodeURIComponent(runId)}`)

export const getAnalyticsBenchmarkResults = (runId: string, symbol?: string, provider?: string) =>
  apiGet<{ run_id: string; results: AnalyticsBenchmarkResultRow[] }>(
    `/research/analytics-benchmark/${encodeURIComponent(runId)}/results`,
    { symbol, provider },
  )

export const cancelAnalyticsBenchmark = (runId: string) =>
  apiPost<AnalyticsBenchmarkRunSummary>(`/research/analytics-benchmark/${encodeURIComponent(runId)}/cancel`)
