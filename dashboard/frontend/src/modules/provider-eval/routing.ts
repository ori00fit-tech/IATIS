import type { BenchmarkResultRow } from './priceBenchmarkApi'

// Purely advisory, client-side-only derivation from already-fetched
// benchmark results — never calls any endpoint, never touches config.yaml.
// Satisfies the "domain-specific routing, not one global best provider"
// request by grouping PER SYMBOL (a finer grain than per-asset-class,
// derivable directly from the already-fetched result rows without a
// separate symbol->asset_class lookup call) rather than by the coarser
// asset-class buckets /provider-chains reports — still never a single
// global "best provider" answer, and strictly more specific.

export type RoutingRole = 'PRIMARY' | 'BACKUP' | 'VERIFY'

export interface RoutingRecommendation {
  symbol: string
  role: RoutingRole
  provider: string
  compositeScore: number | null
  meanLatencyMs: number | null
}

function mean(values: number[]): number | null {
  if (values.length === 0) return null
  return Math.round((values.reduce((s, v) => s + v, 0) / values.length) * 100) / 100
}

/** Ranks every provider that produced at least one successful fetch for a
 * symbol (across all its benchmarked timeframes) by mean composite score,
 * tie-broken by lower mean latency, and labels the top 3 PRIMARY/BACKUP/
 * VERIFY. Never auto-applied — purely a display recommendation. */
export function deriveRoutingRecommendations(rows: BenchmarkResultRow[]): RoutingRecommendation[] {
  const bySymbol = new Map<string, Map<string, BenchmarkResultRow[]>>()
  for (const r of rows) {
    if (!r.fetch_ok) continue
    const providers = bySymbol.get(r.symbol) ?? new Map<string, BenchmarkResultRow[]>()
    const providerRows = providers.get(r.provider) ?? []
    providerRows.push(r)
    providers.set(r.provider, providerRows)
    bySymbol.set(r.symbol, providers)
  }

  const roles: RoutingRole[] = ['PRIMARY', 'BACKUP', 'VERIFY']
  const out: RoutingRecommendation[] = []
  for (const [symbol, providers] of bySymbol) {
    const ranked = [...providers.entries()]
      .map(([provider, providerRows]) => ({
        provider,
        compositeScore: mean(providerRows.map((r) => r.composite_score).filter((v): v is number => v !== null)),
        meanLatencyMs: mean(providerRows.map((r) => r.latency_ms).filter((v): v is number => v !== null)),
      }))
      .sort(
        (a, b) =>
          (b.compositeScore ?? -1) - (a.compositeScore ?? -1) ||
          (a.meanLatencyMs ?? Infinity) - (b.meanLatencyMs ?? Infinity),
      )
    ranked.slice(0, 3).forEach((entry, i) => {
      out.push({ symbol, role: roles[i], ...entry })
    })
  }
  return out.sort((a, b) => a.symbol.localeCompare(b.symbol) || roles.indexOf(a.role) - roles.indexOf(b.role))
}

/** Groups routing recommendations by symbol for rendering. */
export function groupRoutingBySymbol(recs: RoutingRecommendation[]): Map<string, RoutingRecommendation[]> {
  const out = new Map<string, RoutingRecommendation[]>()
  for (const r of recs) {
    const list = out.get(r.symbol) ?? []
    list.push(r)
    out.set(r.symbol, list)
  }
  return out
}
