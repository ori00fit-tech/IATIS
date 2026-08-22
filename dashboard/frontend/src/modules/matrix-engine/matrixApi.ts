import { apiGet, apiPost } from '../../lib/api'

// Hypothesis Discovery Engine, Phase 2B — thin client over
// execution/routes/research_matrix.py. Every type here mirrors that
// module's Pydantic models / storage/research_matrix.py's own row shapes
// exactly, matching mission-center/api.ts's own established convention.
//
// This client (and the Matrix Engine dashboard tab built on it) is a pure
// PROJECTION of the authoritative D1 evidence — it never computes a
// verdict of its own. Every status/p-value/rejection-reason/verdict field
// below was already decided by backtest/matrix_orchestrator.py; the UI
// only displays and sorts it.

// Mirrors backtest.research_matrix.CELL_STATUSES exactly. VALIDATING
// (Phase 3A) is Stage B's own atomic-claim state, mirroring RUNNING's role
// for Stage A — see storage.claim_candidate_cells()'s own docstring.
export const CELL_STATUSES = [
  'QUEUED', 'RUNNING', 'SCREENED', 'CANDIDATE', 'VALIDATING', 'VALIDATED', 'REJECTED', 'INSUFFICIENT_DATA', 'FAILED',
] as const
export type CellStatus = (typeof CELL_STATUSES)[number]

// Mirrors backtest.research_matrix.RISK_PRESET_NAMES exactly.
export const RISK_PRESET_NAMES = ['conservative', 'balanced', 'aggressive'] as const
export type RiskPresetName = (typeof RISK_PRESET_NAMES)[number]

export interface BundleSpec {
  name: string
  timeframes: string[]
  engines: string[]
  indicators: Record<string, unknown>[]
  context_filters: Record<string, unknown>[]
}

export interface MatrixGenerateRequest {
  symbols?: string[] // omitted -> every configured symbol
  bundles: BundleSpec[]
  risk_presets?: RiskPresetName[] // omitted -> all 3
  confluence_overrides?: { min_engines_agreeing?: number; min_informative_weight_share?: number }
  engine_variants?: Record<string, string>
  data_provider?: string
}

export interface MatrixGenerateResponse {
  cells_considered: number
  family_id: string
  planned_n: number
  research_code_commit: string | null
  research_code_dirty: boolean
  inserted: number
  duplicate: number
}

export interface MatrixFamily {
  family_id: string
  planned_n: number
  family_alpha: number
  symbols_json: string | null
  created_at: string
}

export type FamilyStatusBreakdown = Record<CellStatus, number>

export interface FamilyEvidenceSummary {
  family_id: string
  planned_n: number
  family_alpha: number
  bonferroni_alpha: number | null
  symbols: string[]
  created_at: string
  cells_generated: number
  status_breakdown: FamilyStatusBreakdown
  total_requeues: number
}

export interface GroupStatsBucket extends Record<string, number> {
  total: number
}

export interface FamilySummaryResponse {
  family: FamilyEvidenceSummary
  by_symbol: Record<string, GroupStatsBucket>
  by_bundle: Record<string, GroupStatsBucket>
  by_risk_preset: Record<string, GroupStatsBucket>
}

export interface MatrixCell {
  cell_id: string
  family_id: string
  fingerprint: string
  symbol: string
  bundle_json: string
  risk_preset: string
  confluence_overrides_json: string | null
  engine_variants_json: string | null
  data_provider: string | null
  research_code_commit: string | null
  status: CellStatus
  rejection_reason: string | null
  stage_a_mission_id: string | null
  stage_a_trial_number: number | null
  stage_a_metrics_json: string | null
  stage_a_p_value: number | null
  lead_id: string | null
  stage_b_validation_id: string | null
  stage_b_verdict: string | null
  requeue_count: number
  created_at: string
  updated_at: string
}

export interface CellStageEvidence {
  mission_id: string | null
  trial_number: number | null
  metrics: Record<string, unknown> | null
  p_value: number | null
  lead_id: string | null
  trial_detail: Record<string, unknown> | null
}

// Phase 2C -- backtest.matrix_evidence._decode_validation_result()'s exact
// shape: the Stage B per-symbol research_mission_validation_results row.
// Every field here is diagnostic-only, per that table's own DDL comments --
// none of them ever participated in `passed`/the Stage B verdict already
// carried on CellStageBEvidence.verdict/validation_detail above.
export interface ValidationResultEvidence {
  symbol: string | null
  passed: boolean
  metrics: Record<string, unknown> | null
  monte_carlo: Record<string, unknown> | null
  walk_forward: Record<string, unknown> | null
  robustness: Record<string, unknown> | null
  criteria_breakdown: Record<string, unknown> | null
  significance: Record<string, unknown> | null
  regime_robustness: Record<string, unknown> | null
  stability: Record<string, unknown> | null
  cost_stress: Record<string, unknown> | null
  discovery_score: Record<string, unknown> | null
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export interface CellStageBEvidence {
  validation_id: string | null
  verdict: string | null
  validation_detail: Record<string, unknown> | null
  validation_result: ValidationResultEvidence | null
}

export interface CellEvidence {
  cell_id: string
  family_id: string | null
  fingerprint: string | null
  symbol: string | null
  bundle: Record<string, unknown> | null
  risk_preset: string | null
  confluence_overrides: Record<string, unknown> | null
  engine_variants: Record<string, unknown> | null
  data_provider: string | null
  research_code_commit: string | null
  status: string | null
  rejection_reason: string | null
  requeue_count: number
  created_at: string | null
  updated_at: string | null
  stage_a: CellStageEvidence
  stage_b: CellStageBEvidence
}

// ── Phase 2C -- Evidence Comparison (NOT a leaderboard) ────────────────────
//
// GET /research/matrix/cells/compare never computes a ranking, score, or
// verdict -- see backtest/matrix_evidence.py's own NON-NEGOTIABLE rule.
// `provenance` is nothing more than identity-equality checks (same family?
// same commit? same data provider? same hypothesis lineage?) over columns
// each cell already carries. The UI built on this type must never collapse
// these flags into a single sort order implying one cell is "better."

export type ComparedCell = ({ found: true } & CellEvidence) | { found: false; cell_id: string }

export interface CompareProvenance {
  cell_count: number
  family_ids: string[]
  same_family: boolean
  commits: string[]
  same_commit: boolean
  data_providers: string[]
  same_data_provider: boolean
  same_hypothesis_lineage: boolean
  lineage_key: { symbol: string; bundle: string; risk_preset: string } | null
}

export interface CompareResponse {
  cells: ComparedCell[]
  count: number
  provenance: CompareProvenance | null
}

export interface MatrixRun {
  run_id: string
  family_id: string | null
  status: string
  batch_size: number
  cells_claimed: number
  cells_screened: number
  cells_promoted: number
  cells_validated: number
  matrix_significance_json: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface MatrixRunBatchRequest {
  family_id: string
  batch_size?: number
  stage_b_batch_size?: number
  data_dir?: string
  start?: string
  end?: string
  max_wall_clock_seconds?: number
}

export interface JobSummary {
  run_id: string
  job: string
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  returncode: number | null
  log_lines: number
}

export interface MatrixRunStatusResponse {
  run: MatrixRun | null
  job: JobSummary | null
}

// ── families ────────────────────────────────────────────────────────────

export const generateMatrixCells = (body: MatrixGenerateRequest) =>
  apiPost<MatrixGenerateResponse>('/research/matrix/generate', body)

export const listMatrixFamilies = (limit = 50) =>
  apiGet<{ families: MatrixFamily[]; count: number }>('/research/matrix/families', { limit })

export const getMatrixFamily = (familyId: string) =>
  apiGet<MatrixFamily>(`/research/matrix/families/${encodeURIComponent(familyId)}`)

export const getMatrixFamilySummary = (familyId: string) =>
  apiGet<FamilySummaryResponse>(`/research/matrix/families/${encodeURIComponent(familyId)}/summary`)

// ── cells ───────────────────────────────────────────────────────────────

export const listMatrixCells = (params: { familyId?: string; status?: CellStatus; symbol?: string; limit?: number } = {}) =>
  apiGet<{ cells: MatrixCell[]; count: number }>('/research/matrix/cells', {
    family_id: params.familyId, status: params.status, symbol: params.symbol, limit: params.limit,
  })

export const getMatrixCell = (cellId: string) =>
  apiGet<MatrixCell>(`/research/matrix/cells/${encodeURIComponent(cellId)}`)

export const getMatrixCellEvidence = (cellId: string) =>
  apiGet<CellEvidence>(`/research/matrix/cells/${encodeURIComponent(cellId)}/evidence`)

// Mirrors execution.routes.research_matrix's own _MIN/_MAX_COMPARED_CELLS.
export const MIN_COMPARED_CELLS = 2
export const MAX_COMPARED_CELLS = 10

export const compareMatrixCells = (cellIds: string[]) =>
  apiGet<CompareResponse>('/research/matrix/cells/compare', { cell_ids: cellIds.join(',') })

// ── runs (batches) ─────────────────────────────────────────────────────

export const runMatrixBatch = (body: MatrixRunBatchRequest) =>
  apiPost<{ run_id: string } & JobSummary>('/research/matrix/run-batch', body)

export const listMatrixRuns = (familyId?: string, limit = 50) =>
  apiGet<{ runs: MatrixRun[]; count: number }>('/research/matrix/runs', { family_id: familyId, limit })

export const getMatrixRun = (runId: string) =>
  apiGet<MatrixRunStatusResponse>(`/research/matrix/runs/${encodeURIComponent(runId)}`)

// ── AI Research Planner (Phase 3B) ─────────────────────────────────────────
//
// AI is a PLANNER here, never a JUDGE — see execution/routes/matrix_ai.py's
// own module docstring. Every recommendation is persisted as DRAFT; only a
// human review action (reviewMatrixAIRecommendation) can ever move it to
// APPROVED/REJECTED, and even an APPROVED recommendation does not, by
// itself, generate a single real Matrix cell — that still requires the
// operator to separately submit proposed_next_cells through the existing
// generateMatrixCells() above. This module computes/decides nothing; it
// only calls the API and renders whatever comes back.

export const RECOMMENDATION_STATUSES = ['DRAFT', 'APPROVED', 'REJECTED'] as const
export type RecommendationStatus = (typeof RECOMMENDATION_STATUSES)[number]

export interface ProposedCell {
  symbol: string
  bundle_name: string
  timeframes: string[]
  engines: string[]
  risk_preset: string
  rationale: string
}

export interface MatrixAIProposeRequest {
  family_ids?: string[]
  cell_ids?: string[]
  focus_hint?: string
  provider?: string
  model?: string
}

// The AI CALL's own status (ok/error/disabled) -- distinct from a
// persisted recommendation's review status (DRAFT/APPROVED/REJECTED).
export interface MatrixAIProposeResponse {
  status: 'ok' | 'error' | 'disabled'
  error?: string
  provider: string
  recommendation_id?: string
  evidence_snapshot_hash?: string
  reasoning_summary?: string
  coverage_gaps?: string[]
  proposed_next_cells?: ProposedCell[]
  distinct_from_dead_list?: string
  priority?: string
}

export interface MatrixAIRecommendation {
  recommendation_id: string
  provider: string
  model: string
  input_family_ids_json: string
  input_cell_ids_json: string | null
  evidence_snapshot_json: string
  evidence_snapshot_hash: string
  constraints_used_json: string
  focus_hint: string | null
  reasoning_summary: string
  coverage_gaps_json: string | null
  proposed_next_cells_json: string
  distinct_from_dead_list: string | null
  priority: string | null
  status: RecommendationStatus
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  created_at: string
}

export interface MatrixAIReviewRequest {
  status: 'APPROVED' | 'REJECTED'
  reviewed_by?: string
  review_note?: string
}

export const proposeMatrixAIRecommendation = (body: MatrixAIProposeRequest) =>
  apiPost<MatrixAIProposeResponse>('/research/matrix/ai/propose', body)

export const listMatrixAIRecommendations = (params: { status?: RecommendationStatus; limit?: number } = {}) =>
  apiGet<{ recommendations: MatrixAIRecommendation[]; count: number }>('/research/matrix/ai/recommendations', {
    status: params.status, limit: params.limit,
  })

export const getMatrixAIRecommendation = (recommendationId: string) =>
  apiGet<MatrixAIRecommendation>(`/research/matrix/ai/recommendations/${encodeURIComponent(recommendationId)}`)

export const reviewMatrixAIRecommendation = (recommendationId: string, body: MatrixAIReviewRequest) =>
  apiPost<MatrixAIRecommendation>(`/research/matrix/ai/recommendations/${encodeURIComponent(recommendationId)}/review`, body)
