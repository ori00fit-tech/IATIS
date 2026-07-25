import { apiGet, apiPost } from '../../lib/api'

export interface Hypothesis {
  id: string
  title: string
  status: string
  description: string
  last_updated: string
  conclusion?: string
  // false = PASSED status that fails the codified promotion criteria
  // (research/edge_gate.py) — render as untrusted, never as green.
  trusted?: boolean
  sample_size?: number
  win_rate?: number
  p_value?: number
}

export interface TrustAudit {
  criteria: Record<string, number | boolean>
  warnings: string[]
}

export interface ResearchResponse {
  hypothesis_summary: { total: number; passed: number; failed: number; research: number; needs_data: number }
  hypotheses: Hypothesis[]
  trust_audit?: TrustAudit
  latest_backtest: {
    file: string
    generated_at: string
    avg_wr: number
    avg_pf: number
    top_symbols: { symbol: string; win_rate: number; profit_factor: number }[]
  } | null
}

export interface BacktestMetrics {
  trades_closed?: number
  win_rate?: number // stored as a fraction (0..1) in the legacy backtest_*.json format
  profit_factor?: number
  max_drawdown_pct?: number // fraction in legacy format
  total_return_pct?: number // fraction in legacy format
  sharpe_ratio?: number
}

// The /backtest-results endpoint serves two on-disk shapes: the new
// full_pipeline_backtest_*.json (top-level percentage fields) and the legacy
// backtest_*.json fallback (values only under `metrics`, as fractions). Every
// numeric field is therefore optional — see normalizeBacktest() in the module.
export interface BacktestResult {
  file: string
  symbol: string
  period: string
  trades?: number
  win_rate?: number
  profit_factor?: number
  max_drawdown_pct?: number
  total_return_pct?: number
  metrics?: BacktestMetrics
}

export interface BacktestResultsResponse {
  count: number
  results: BacktestResult[]
}

export interface RegimeRow {
  regime: string
  total_decisions: number
  executes: number
  execute_rate: number
  wins: number
  losses: number
  win_rate: number | null
  profit_factor: number | null
  expectancy_usd: number | null
}

export interface MetaAnalysisResponse {
  regime_matrix: { data: RegimeRow[]; note: string }
}

export const getResearch = () => apiGet<ResearchResponse>('/research')
export const getBacktestResults = () => apiGet<BacktestResultsResponse>('/backtest-results')
export const getMetaAnalysis = () => apiGet<MetaAnalysisResponse>('/meta-analysis')

// AI research summary (ai/ai_analyzer.py) — on-demand, phrases the stats
// above in plain English. Sent as the request body so the backend
// doesn't need a third copy of the registry.json / backtest-file
// parsing logic already in /research and /meta-analysis.
export interface AiResearchSummary {
  status: 'ok' | 'disabled' | 'error'
  text: string
  provider: string
  error?: string
}

export const getAiResearchSummary = (body: {
  hypothesis_summary: ResearchResponse['hypothesis_summary']
  latest_backtest: ResearchResponse['latest_backtest']
  regime_matrix: RegimeRow[]
}) => apiPost<AiResearchSummary>('/ai/research-summary', body)

// AI Research Assistant (Backtesting Lab priority #5) — free-form Q&A
// grounded in whatever the caller already has in memory (hypothesis
// registry, KPIs, sweep results, comparison table). The backend never
// re-fetches or re-derives — `context` here IS the entire evidence the
// model sees, so it can only answer, never invent live-decision advice
// (ai/prompts/research_qa.txt enforces that boundary in the prompt itself).
export interface AiResearchAnswer {
  status: 'ok' | 'disabled' | 'error'
  text: string
  provider: string
  error?: string
}

export const askResearchQuestion = (context: unknown, question: string) =>
  apiPost<AiResearchAnswer>('/ai/research-question', { context, question })

// Git-tracked evidence manifests (research/manifest.py, audit item H2):
// each binds a research run to a git commit, config hash, and dataset
// SHA256 fingerprints — the system's auditable evidence trail.
export interface EvidenceManifest {
  file: string
  kind: string
  generated_at: string
  reproducible: boolean
  git_commit: string
  git_dirty: boolean
  decision_timeframe: string | null
  engines_enabled: string[] | null
  note: string | null
  datasets_count: number
  results: Record<string, unknown> | null
}

export interface ManifestsResponse {
  count: number
  manifests: EvidenceManifest[]
}

export const getManifests = () => apiGet<ManifestsResponse>('/research/manifests')

// Research Center drill-down (module 4) — full registry.json entry for
// one hypothesis plus its linked manifests and result files.
export interface HypothesisDetailResponse {
  id: string
  hypothesis: Record<string, unknown>
  manifests: { exact: EvidenceManifest[]; heuristic: EvidenceManifest[] }
  result_files: { path: string; exists: boolean }[]
}

export const getHypothesisDetail = (id: string) => apiGet<HypothesisDetailResponse>(`/research/${encodeURIComponent(id)}`)

// ── Research Workspace, Phases 2-6 (2026-07-24/25) ──────────────────────────

// Symbol Manager: the FULL symbol universe (including disabled/WATCHLIST/
// RETIRED entries /symbol-health and /provider-chains omit), grouped by
// asset class.
export interface SymbolEntry {
  internal: string
  symbol: string
  enabled: boolean
  status: string
  status_reason: string
  status_since: string | null
  min_score: number | null
  rr: number | null
  provider_chain: string[]
}

export interface SymbolsResponse {
  asset_classes: Record<string, SymbolEntry[]>
  native_timeframes: Record<string, string[]>
  chains: Record<string, string[]>
}

export const getResearchSymbols = () => apiGet<SymbolsResponse>('/research/symbols')

// Engine Selector: activation state is read-only here — toggling an
// engine needs a new pre-registered hypothesis (CLAUDE.md), not a
// dashboard click, so there is no corresponding POST.
export interface EngineEntry {
  name: string
  enabled: boolean
  prod4: boolean
  weight: number | null
  version: string | null
}

export interface EnginesResponse {
  engines: EngineEntry[]
  smc_full_spec: boolean
  crypto_positioning_modulator: boolean
}

export const getResearchEngines = () => apiGet<EnginesResponse>('/research/engines')

// Technical Indicator catalog: what's actually implemented, not a
// selector that changes anything.
export interface IndicatorEntry {
  id: string
  name: string
  category: string
  description: string
  default_params: Record<string, unknown>
  source: string
  used_by: string[]
}

export interface IndicatorsResponse {
  count: number
  categories: Record<string, IndicatorEntry[]>
  indicators: IndicatorEntry[]
}

export const getResearchIndicators = () => apiGet<IndicatorsResponse>('/research/indicators')

// Dashboard Summary: one fast landing-page aggregation, same pattern
// /health/full uses for the ops dashboard.
export interface DashboardSummaryResponse {
  generated_at: string
  hypotheses: { total: number; by_status: Record<string, number> }
  symbols: { total: number; enabled: number; by_asset_class: Record<string, number> }
  engines: { total: number; enabled: number }
  evidence: { manifests_total: number; manifests_reproducible: number; note: string }
  forward_review: { rules_total: number; rules_triggered: number }
  run_reports: { total: number; by_kind: Record<string, number> }
}

export const getDashboardSummary = () => apiGet<DashboardSummaryResponse>('/research/dashboard-summary')

// Walk-Forward / Monte Carlo / Robustness real defaults + the codified
// promotion bar (research/edge_gate.py PROMOTION_CRITERIA) — what a
// Parameter Sweep / Grid Search UI configures against, not invented
// client-side.
export interface ValidationConfigResponse {
  walk_forward: { n_windows: number; min_pf: number; min_trades_per_window: number; warmup_bars: number; methodology: string }
  monte_carlo: { n_simulations: number; ruin_threshold: number; note: string }
  robustness: { params: string[]; multipliers: number[]; min_trades: number; stable_band_pct: number; methodology: string }
  promotion_criteria: Record<string, number | boolean>
}

export const getValidationConfig = () => apiGet<ValidationConfigResponse>('/research/validation-config')
