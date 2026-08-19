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
// (download_history, push_to_warehouse) run via the shared experiment-
// runner job engine. `download_history` supports three providers.
// Dukascopy/Twelve Data are free/credential-free and always safe to
// trigger ad-hoc. cTrader is also offered here, but the backend
// (execution/routes/experiments.py's _guard_ctrader_download()) rejects
// the request with 409 unless iatis-scheduler is confirmed inactive —
// a live cTrader session enforces a single-session-per-account limit
// and would otherwise race the scheduler's own connection. That 409
// surfaces through the same inline error path as any other failure, no
// special-casing needed here. MT5/dukascopy_jforex remain unavailable
// from this UI entirely.
export interface DownloadProviderSpec {
  id: 'dukascopy' | 'twelve_data' | 'ctrader'
  label: string
  timeframes: readonly string[]
  supportsYears: boolean
}

export const DOWNLOAD_PROVIDERS: readonly DownloadProviderSpec[] = [
  { id: 'dukascopy', label: 'Dukascopy', timeframes: ['M15', 'H1', 'H4', 'D1'], supportsYears: true },
  { id: 'twelve_data', label: 'Twelve Data', timeframes: ['M15', 'H1'], supportsYears: false },
  { id: 'ctrader', label: 'cTrader', timeframes: ['M15', 'H1'], supportsYears: true },
]

export const runDownloadHistoryJob = (
  symbols: string[],
  timeframe: string,
  years: number | undefined,
  provider: DownloadProviderSpec['id'],
) =>
  apiPost<JobSummary>('/experiments/run', {
    job: 'download_history',
    symbols,
    timeframe,
    provider,
    ...(years !== undefined ? { years } : {}),
  })

export const runPushToWarehouseJob = (symbols: string[]) =>
  apiPost<JobSummary>('/experiments/run', { job: 'push_to_warehouse', symbols })
