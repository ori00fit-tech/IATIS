import { apiGet, apiPost } from '../../lib/api'
import { formatPossiblyInfinite, type PossiblyInfinite } from '../backtesting-charts/api'

export { formatPossiblyInfinite, type PossiblyInfinite }

// AI Research Lab / Mission Center Phase 3 (2026-07-28) — thin client over
// execution/routes/missions.py. Every field here mirrors that module's
// _MissionRequest Pydantic model exactly.

export const SAMPLER_KEYS = ['grid', 'random', 'tpe', 'nsga2'] as const
export const OPTIMIZABLE_METRICS = [
  'profit_factor', 'sharpe_ratio', 'sortino_ratio', 'calmar_ratio',
  'expectancy_r', 'sqn', 'recovery_factor', 'win_rate',
] as const

// Hypothesis Bundles (2026-07-30) — an atomic {timeframes, engines,
// indicators, context_filters} combo, named by the operator. When present
// on a request, REPLACES independent sampling of the 4 flat *_choices
// fields below with one shared index over complete bundles — see
// backtest/optimizer.py's module-level comment on _HYPOTHESIS_IDX_KEY.
export interface HypothesisBundleSpec {
  name: string
  timeframes: string[]
  engines: string[]
  indicators: Record<string, unknown>[]
  context_filters: Record<string, unknown>[]
  // Track C (Phase 4, 2026-08-01) — ad-hoc PriceAction v2/Wyckoff v2
  // selection carried by this one named hypothesis.
  engine_variants?: Record<string, string>
}

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
  context_filter_set_choices: Record<string, unknown>[][]
  // Track C (Phase 4, 2026-08-01) — ad-hoc PriceAction v2/Wyckoff v2
  // selection, index-sampled same as the *_set_choices fields above.
  // Still 100% ephemeral — never activates a variant in config/
  // engines.yaml's live default.
  engine_variant_choices: Record<string, string>[]
  hypothesis_bundle_choices?: HypothesisBundleSpec[]
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

// Edge Discovery (2026-07-31) — mirrors backtest/meta_analysis.py's 3 new
// dataclasses exactly. cross_trial_consensus is a real, computed exact
// binomial sign test over trial-level outcomes — never AI narrative.
// pooled_breakdown/opportunity_candidates derive from ONE flat pooled
// 3-way field (backtest/metrics.py's by_direction_regime_session), so
// every tree level (1-way/2-way/3-way) shows up as its own row here.
export interface ConsensusClaim {
  dimension: 'direction' | 'regime' | 'session'
  metric: 'win_rate' | 'profit_factor'
  dominant_value: string
  other_value: string
  n_trials_compared: number
  trials_favor_dominant: number
  fraction_favor_dominant: number
  p_value: number | null
  confidence_pct: number | null
  // DEPENDENT_TRIALS_LEAD_ONLY (2026-08-02): the mission never varied any
  // entry-signal dimension (only risk/cost params) — every trial ran the
  // same signal stream, so this claim's p_value/confidence_pct are nulled
  // out rather than computed under a known-violated independence
  // assumption. See MetaAnalysisResponse.dependence_warning.
  significance: 'INSUFFICIENT_DATA' | 'SURVIVES_CORRECTION' | 'NOMINAL_ONLY' | 'NOT_SIGNIFICANT' | 'DEPENDENT_TRIALS_LEAD_ONLY'
  claim_text: string
}

export interface PooledBreakdownRow {
  direction: string | null
  regime: string | null
  session: string | null
  level: string
  trades: number
  wins: number
  win_rate: number
  pnl: number
  gross_profit: number
  gross_loss: number
  profit_factor: PossiblyInfinite
}

// Ranked SUGGESTIONS ONLY — never auto-selected, never auto-run. rank_score
// is real, honestly-labeled effect_size x trades, not a fabricated
// "information gain" statistic.
export interface OpportunityCandidate {
  direction: string | null
  regime: string | null
  session: string | null
  level: string
  trades: number
  profit_factor: PossiblyInfinite
  effect_size: PossiblyInfinite
  rank_score: PossiblyInfinite
  label: string
}

// A human-triggered pre-fill request from TopOpportunitiesPanel into
// MissionBuilder's already-built hypothesisMode/ContextFiltersBuilder —
// never auto-submits a mission.
export interface PrefillRequest {
  direction: string | null
  regime: string | null
  session: string | null
  label: string
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
  cross_trial_consensus: ConsensusClaim[]
  pooled_breakdown: PooledBreakdownRow[]
  opportunity_candidates: OpportunityCandidate[]
  note: string
  // Dependence detection (2026-08-02) — true when this mission's search
  // space never varied any entry-signal dimension (timeframes/engines/
  // indicators/context filters/engine variants — only risk/cost params
  // differed across trials). cross_trial_consensus and pooled_breakdown
  // are NOT independent-trial statistics in that case; dependence_warning
  // is the human-readable explanation to render prominently.
  dependence_detected: boolean
  dependence_warning: string | null
}

export const getMetaAnalysis = (missionId: string, opts?: { symbol?: string; top_fraction?: number; n_bins?: number }) =>
  apiGet<MetaAnalysisResponse>(`/research/missions/${encodeURIComponent(missionId)}/meta-analysis`, opts)

// ── Feature Mining / Hypothesis Discovery Phase 1 (2026-07-30) ────────────
// Mirrors backtest/feature_mining.py's dataclasses exactly. Retrospective,
// descriptive, non-ML association analysis — see FeatureMiningPanel's own
// "EXPLORATORY — NOT EVIDENCE" banner. Computed at validation time by
// backtest/mission_validator.py; this endpoint only pools already-stored
// per-symbol results, never runs a new backtest.

export interface FeatureBinResult {
  feature: string
  bin_label: string
  bin_index: number | null
  n_trades: number
  win_rate: number
  mean_r: number
  std_r: number | null
  lift_win_rate: number | null
  p_value: number | null
  significance: 'INSUFFICIENT_DATA' | 'SURVIVES_CORRECTION' | 'NOMINAL_ONLY' | 'NOT_SIGNIFICANT'
}

export interface FeatureAssociation {
  feature: string
  feature_type: 'numeric' | 'categorical'
  n_observed: number
  overall_win_rate: number
  bins: FeatureBinResult[]
  insufficient_data: boolean
  note: string
}

export interface FeatureMiningResponse {
  mission_id: string
  validation_id: string
  n_trades_total: number
  n_features_tested: number
  bonferroni_alpha: number | null
  associations: FeatureAssociation[]
  caveat: string
  insufficient_data: boolean
  note: string
}

export const getFeatureMining = (missionId: string, validationId: string) =>
  apiGet<FeatureMiningResponse>(
    `/research/missions/${encodeURIComponent(missionId)}/feature-mining`,
    { validation_id: validationId },
  )
