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

export interface BacktestResult {
  file: string
  symbol: string
  period: string
  trades: number
  win_rate: number
  profit_factor: number
  max_drawdown_pct: number
  total_return_pct: number
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
