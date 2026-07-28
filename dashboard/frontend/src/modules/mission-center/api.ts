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
