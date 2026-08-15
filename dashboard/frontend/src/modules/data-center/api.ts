import { apiGet, apiPost } from '../../lib/api'
import {
  getJobDetail, cancelJob, type JobDetail, type JobStatus, type JobSummary,
} from '../experiment-runner/api'

export { getJobDetail, cancelJob }
export type { JobDetail, JobStatus, JobSummary }

export type CacheStatus = 'OK' | 'STALE' | 'GAPS' | 'STARVED' | 'MISSING'

export interface TimeframeStatus {
  bars: number
  provider?: string | null
  last_bar_time: string | null
  age_minutes: number | null
  gap_count_30d: number
  duplicate_count: number
  timezone: string | null
  integrity_score: number
  status: CacheStatus
}

export interface SymbolDataHealth {
  symbol: string
  timeframes: Record<string, TimeframeStatus>
  overall_status: CacheStatus
}

export interface DataHealthResponse {
  checked_at: string
  symbols: SymbolDataHealth[]
  summary: { ok: number; stale: number; gaps: number; starved?: number; missing: number }
}

export const getDataHealth = () => apiGet<DataHealthResponse>('/data-health')

// Cross-provider data-confidence history (core/data_confidence.py, gap
// analysis S1). Reads the STORED check table — polling this never burns
// provider API budget. Monitoring only, never a gate.
export interface DataConfidenceCheck {
  ts: string
  symbol: string
  interval: string
  provider_a: string | null
  provider_b: string | null
  bars_common: number | null
  mean_diff_pct: number | null
  max_diff_pct: number | null
  pct_exceeding: number | null
  verdict: string
}

export interface DataConfidenceHistory {
  checks: DataConfidenceCheck[]
  n: number
  material_disagreements: number
  note: string
}

export function getDataConfidence(): Promise<DataConfidenceHistory> {
  return apiGet<DataConfidenceHistory>('/data-confidence')
}

// Trusted Data Center warehouse inspector (storage/market_bars.py) — a
// DIFFERENT concept from DataHealthResponse above (live-decision-pipeline
// cache health): this is the D1 dataset_manifest table, what's actually
// been deepened/pushed via scripts/push_bars_to_d1.py and is READY for
// Mission Center/backtests to read.
export type DatasetStatus = 'PENDING' | 'READY' | 'INCOMPLETE' | 'INVALID' | 'EMPTY'

export interface WarehouseManifestEntry {
  symbol: string
  timeframe: string
  source: string | null
  start_ts: number | null
  end_ts: number | null
  row_count: number
  expected_rows: number | null
  coverage_pct: number | null
  gap_count: number | null
  duplicate_count: number | null
  invalid_ohlc_count: number | null
  status: DatasetStatus
  error: string | null
  last_updated: string | null
  /** true = a genuinely native file (never derived by resampling H1). */
  native: boolean
}

export interface WarehouseManifestResponse {
  datasets: WarehouseManifestEntry[]
  checked_at: string
}

export const getWarehouseManifest = () => apiGet<WarehouseManifestResponse>('/warehouse-manifest')

// Data Center "Deepen a symbol" action — two whitelisted jobs
// (download_history via Dukascopy, push_to_warehouse) run via the shared
// experiment-runner job engine. Dukascopy only, never cTrader — see
// execution/routes/experiments.py's _JOB_COMMANDS comment for why.
export const DOWNLOAD_TIMEFRAMES = ['M15', 'H1', 'H4', 'D1'] as const

export const runDownloadHistoryJob = (symbols: string[], timeframe: string, years: number) =>
  apiPost<JobSummary>('/experiments/run', { job: 'download_history', symbols, timeframe, years })

export const runPushToWarehouseJob = (symbols: string[]) =>
  apiPost<JobSummary>('/experiments/run', { job: 'push_to_warehouse', symbols })
