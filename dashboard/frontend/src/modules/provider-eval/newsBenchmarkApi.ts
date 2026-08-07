import { apiGet, apiPost } from '../../lib/api'

export type NewsBenchmarkProfile = 'smoke' | 'standard' | 'deep'

export interface NewsBenchmarkRunRequest {
  profile: NewsBenchmarkProfile
  symbols?: string[]
  providers?: string[]
  hours_back?: number
  limit?: number
}

export interface NewsBenchmarkRunSummary {
  run_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface NewsBenchmarkRunRow {
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

export interface NewsBenchmarkProgress {
  total_results: number
  fetch_ok: number
  fetch_failed: number
}

export interface NewsBenchmarkRunStatusResponse {
  run_id: string
  run: NewsBenchmarkRunRow | null
  progress: NewsBenchmarkProgress
  job_status: string | null
}

export interface NewsBenchmarkResultRow {
  run_id: string
  provider: string
  symbol: string
  fetch_ok: number
  error: string | null
  latency_ms: number | null
  article_count: number
  coverage_score: number | null
  source_diversity_score: number | null
  duplicate_rate_score: number | null
  freshness_score: number | null
  latency_score: number | null
  // MarketAux-only (real per-article sentiment); always null for Finnhub
  // — Finnhub's free /news has no sentiment field, never fabricated.
  sentiment_availability_score: number | null
  // Presence/absence consensus across providers — not a numeric
  // correctness measure (there is no ground truth for a headline).
  cross_provider_coverage_agreement_score: number | null
  composite_score: number | null
  mean_sentiment: number | null
  detail_json: string | null
  created_at: string
}

export const createNewsBenchmark = (body: NewsBenchmarkRunRequest) =>
  apiPost<{ run_id: string } & NewsBenchmarkRunSummary>('/research/news-benchmark', body)

export const listNewsBenchmarks = () => apiGet<{ runs: NewsBenchmarkRunSummary[] }>('/research/news-benchmark')

export const getNewsBenchmark = (runId: string) =>
  apiGet<NewsBenchmarkRunStatusResponse>(`/research/news-benchmark/${encodeURIComponent(runId)}`)

export const getNewsBenchmarkResults = (runId: string, symbol?: string, provider?: string) =>
  apiGet<{ run_id: string; results: NewsBenchmarkResultRow[] }>(
    `/research/news-benchmark/${encodeURIComponent(runId)}/results`,
    { symbol, provider },
  )

export const cancelNewsBenchmark = (runId: string) =>
  apiPost<NewsBenchmarkRunSummary>(`/research/news-benchmark/${encodeURIComponent(runId)}/cancel`)
