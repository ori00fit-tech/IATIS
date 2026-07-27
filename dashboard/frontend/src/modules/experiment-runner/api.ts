import { apiGet, apiPost } from '../../lib/api'

export interface JobDescriptor {
  id: string
  description: string
  category: 'research' | 'ops'
  /** True for jobs (backtest) that reject a run without symbols. */
  requires_symbols?: boolean
}

export interface JobCatalogResponse {
  jobs: JobDescriptor[]
}

export type JobStatus = 'queued' | 'running' | 'finished' | 'failed' | 'timeout' | 'cancelled'

export interface JobSummary {
  job_id: string
  job: string
  status: JobStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface JobDetail extends JobSummary {
  log: string[]
}

export interface JobListResponse {
  jobs: JobSummary[]
}

export const getJobCatalog = () => apiGet<JobCatalogResponse>('/experiments/jobs')
export const getJobList = () => apiGet<JobListResponse>('/experiments')
export const getJobDetail = (jobId: string) => apiGet<JobDetail>(`/experiments/${jobId}`)
export const cancelJob = (jobId: string) => apiPost<JobSummary>(`/experiments/${jobId}/cancel`)

// Backtesting Lab Pro Phase A (2026-07-27) — ad-hoc per-run dataset
// date-range slice + BacktestConfig overrides, forwarded verbatim to
// POST /experiments/run's start/end/risk_overrides fields. Ephemeral,
// this-run-only — never written to config/engines.yaml or config/risk.yaml.
export interface RiskOverrides {
  min_rr?: number
  sl_atr_multiplier?: number
  risk_per_trade?: number
  commission_pips?: number
  slippage_pips?: number
  swap_pips_per_night?: number
  initial_balance?: number
  warmup_bars?: number
  step_bars?: number
}

export interface DateRange {
  start?: string
  end?: string
}

export const runJob = (job: string, symbols?: string[], range?: DateRange, riskOverrides?: RiskOverrides) =>
  apiPost<JobSummary>('/experiments/run', {
    job, symbols, start: range?.start, end: range?.end, risk_overrides: riskOverrides,
  })

// robustness only (Parameter Sweep, 2026-07-25) — server validates params
// against backtest.robustness.SWEEP_PARAMS and requires 1.0 in multipliers;
// this never lets the caller pick a "winning" value, only which points to
// measure.
export const runRobustnessJob = (
  symbols: string[], params?: string[], multipliers?: number[],
  range?: DateRange, riskOverrides?: RiskOverrides,
) =>
  apiPost<JobSummary>('/experiments/run', {
    job: 'robustness', symbols, params, multipliers,
    start: range?.start, end: range?.end, risk_overrides: riskOverrides,
  })
