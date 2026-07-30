import { apiGet, apiPost } from '../../lib/api'

// AI Research Lab / Mission Center Phase 3 (2026-07-28) — thin client over
// execution/routes/missions.py. Every field here mirrors that module's
// _MissionRequest Pydantic model exactly.

export const SAMPLER_KEYS = ['grid', 'random', 'tpe', 'nsga2'] as const
export const OPTIMIZABLE_METRICS = [
  'profit_factor', 'sharpe_ratio', 'sortino_ratio', 'calmar_ratio',
  'expectancy_r', 'sqn', 'recovery_factor', 'win_rate',
] as const

export interface MissionRequest {
  name?: string
  symbols: string[]
  sampler: (typeof SAMPLER_KEYS)[number]
  n_trials_per_symbol: number
  objective_metric: (typeof OPTIMIZABLE_METRICS)[number]
  min_trades: number
  seed: number
  start?: string
  end?: string
  timeframes_choices: string[][]
  engine_set_choices: string[][]
  indicator_set_choices: Record<string, unknown>[][]
  risk_param_ranges: Record<string, [number, number]>
  risk_param_grid: Record<string, number[]>
  oos_holdout_fraction?: number
  max_wall_clock_seconds?: number
}

export interface MissionCreateResponse {
  mission_id: string
  job_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface MissionSummary {
  job_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface MissionRow {
  id: string
  name: string
  status: string
  sampler: string
  objective_metric: string
  symbols_json: string
  n_trials_per_symbol: number
  min_trades: number
  seed: number
  search_space_json: string
  config_json: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface MissionProgress {
  by_symbol: Record<string, Record<string, number>>
  total: number
}

export interface MissionStatusResponse {
  mission_id: string
  mission: MissionRow | null
  progress: MissionProgress
  job_status: string | null
}

export interface MissionTrial {
  mission_id: string
  trial_number: number
  symbol: string
  state: 'COMPLETE' | 'PRUNED' | 'FAIL'
  objective_value: number | null
  params_json: string
  metrics_json: string | null
  trades: number
  error: string | null
  started_at: string
  finished_at: string
}

export interface MissionLeaderboardResponse {
  mission_id: string
  symbol: string | null
  trials: MissionTrial[]
}

export const createMission = (body: MissionRequest) =>
  apiPost<MissionCreateResponse>('/research/missions', body)

export const listMissions = () => apiGet<{ missions: MissionSummary[] }>('/research/missions')

export const getMissionStatus = (missionId: string) =>
  apiGet<MissionStatusResponse>(`/research/missions/${encodeURIComponent(missionId)}`)

export const getMissionLeaderboard = (missionId: string, symbol?: string) =>
  apiGet<MissionLeaderboardResponse>(`/research/missions/${encodeURIComponent(missionId)}/leaderboard`, symbol ? { symbol } : undefined)

export const cancelMission = (missionId: string) =>
  apiPost<MissionSummary & { mission_id: string }>(`/research/missions/${encodeURIComponent(missionId)}/cancel`)

// ── Phase 3 (2026-07-30): Multi-Stage Validation + Meta-Analysis ──────────
// Mirrors execution/routes/missions.py's _ValidationRequest + backtest/
// meta_analysis.py's MetaAnalysisResult exactly. A validation's verdict
// (NO_EDGE/WEAK_LEAD/STRONG_LEAD) is a LEAD, never registry.json evidence
// — see backtest/mission_validator.py's module docstring.

export const VERDICTS = ['NO_EDGE', 'WEAK_LEAD', 'STRONG_LEAD'] as const
export type Verdict = (typeof VERDICTS)[number]

// Mirrors backtest.robustness.DEFAULT_MULTIPLIERS/SWEEP_PARAMS.
export const RB_DEFAULT_MULTIPLIERS = [0.5, 0.8, 1.0, 1.2, 1.5]
export const RB_PARAMS = ['sl_atr_multiplier', 'commission_pips', 'slippage_pips', 'min_rr'] as const

export interface ValidationRequest {
  trial_number: number
  trial_symbol: string
  validation_symbols: string[]
  start?: string
  end?: string
  wf_windows?: number
  wf_min_trades_per_window?: number
  wf_warmup_bars?: number
  rb_multipliers?: number[]
  rb_params?: string[]
  rb_min_trades?: number
  mc_n_simulations?: number
  mc_seed?: number
}

export interface ValidationCreateResponse {
  validation_id: string
  job_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface ValidationRow {
  id: string
  mission_id: string
  trial_number: number
  trial_symbol: string
  status: string
  validation_symbols_json: string
  objective_metric: string
  criteria_json: string
  overall_verdict: Verdict | null
  passing_symbols: number | null
  total_symbols: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface CriteriaEntry {
  actual: unknown
  threshold: unknown
  passed: boolean
}

export interface ValidationResultRow {
  validation_id: string
  symbol: string
  passed: number
  metrics_json: string | null
  monte_carlo_json: string | null
  walk_forward_json: string | null
  robustness_json: string | null
  criteria_breakdown_json: string
  error: string | null
  started_at: string
  finished_at: string
}

export interface ValidationDetailResponse {
  validation_id: string
  validation: ValidationRow | null
  results: ValidationResultRow[]
  job_status: string | null
}

export const createValidation = (missionId: string, body: ValidationRequest) =>
  apiPost<ValidationCreateResponse>(`/research/missions/${encodeURIComponent(missionId)}/validate`, body)

export const listValidations = (missionId: string) =>
  apiGet<{ mission_id: string; validations: ValidationRow[] }>(
    `/research/missions/${encodeURIComponent(missionId)}/validations`)

export const getValidation = (missionId: string, validationId: string) =>
  apiGet<ValidationDetailResponse>(
    `/research/missions/${encodeURIComponent(missionId)}/validations/${encodeURIComponent(validationId)}`)

export interface DimensionFrequency {
  dimension: 'engine' | 'timeframe'
  value: string
  top_count: number
  top_fraction: number
  all_count: number
  all_fraction: number
  lift: number | null
}

export interface ConsensusBin {
  bin_lo: number
  bin_hi: number
  n_trials: number
  mean_objective: number | null
  mean_trades: number | null
}

export interface ConsensusBand {
  risk_param: string
  bins: ConsensusBin[]
  shape: 'PLATEAU' | 'SPIKE' | 'INCONCLUSIVE'
}

export interface MetaAnalysisResponse {
  mission_id: string
  symbol: string | null
  sampler: string
  n_total_trials: number
  n_complete_trials: number
  top_fraction_used: number
  top_n: number
  insufficient_data: boolean
  engine_frequencies: DimensionFrequency[]
  timeframe_frequencies: DimensionFrequency[]
  consensus_bands: ConsensusBand[]
  note: string
}

export const getMetaAnalysis = (missionId: string, opts?: { symbol?: string; top_fraction?: number; n_bins?: number }) =>
  apiGet<MetaAnalysisResponse>(`/research/missions/${encodeURIComponent(missionId)}/meta-analysis`, opts)
