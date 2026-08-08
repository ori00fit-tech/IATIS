import { apiGet } from '../../lib/api'

// Provider Benchmark & Data Quality Lab — Phase 5. Read-only synthesis
// over the four already-shipped benchmark domains (Price/News/Macro/
// Analytics) — no new fetch/scoring engine of its own.

export type ScorecardDomain = 'price' | 'news' | 'macro' | 'analytics'
export const SCORECARD_DOMAINS: ScorecardDomain[] = ['price', 'news', 'macro', 'analytics']

export interface ScorecardProviderRow {
  provider: string
  mean_composite_score: number | null
  fetch_ok_ratio: number
  n_items: number
}

export interface ScorecardDomainSummary {
  available: boolean
  run_id: string | null
  profile: string | null
  finished_at: string | null
  providers: ScorecardProviderRow[]
}

export interface ProviderScorecardResponse {
  domains: Record<ScorecardDomain, ScorecardDomainSummary>
}

export const getProviderScorecard = () => apiGet<ProviderScorecardResponse>('/research/provider-scorecard')

export interface BestProviderRanking {
  provider: string
  rank: number
  available: boolean
  composite_score: number | null
  fetch_ok: boolean
  error: string | null
}

export interface BestProviderResponse {
  domain: ScorecardDomain
  item: Record<string, string>
  run_id: string | null
  profile: string | null
  finished_at: string | null
  available: boolean
  best: BestProviderRanking | null
  ranking: BestProviderRanking[]
  note?: string
}

export const getBestProvider = (params: {
  domain: ScorecardDomain
  symbol?: string
  series?: string
  timeframe?: string
}) => apiGet<BestProviderResponse>('/research/best-provider', params)
