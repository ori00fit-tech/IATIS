import type { BenchmarkResultRow } from './priceBenchmarkApi'

// Kept intentionally separate from ./scoring.ts's capability-only score —
// the operator explicitly asked that score/routing/evidence stay three
// distinct responsibilities, never blended into one number. Provider Eval's
// existing "Provider Ranking" panel (capability score) is untouched by this
// module; the Price Quality Benchmark panel is additive.

export interface PriceQualityScore {
  provider: string
  meanComposite: number | null
  meanCompleteness: number | null
  meanCorrectness: number | null
  meanTimestampIntegrity: number | null
  meanOhlcIntegrity: number | null
  meanCrossProviderAgreement: number | null
  meanFreshness: number | null
  meanLatency: number | null
  meanLatencyMs: number | null
  nPoints: number
  nFetchOk: number
  nFetchFailed: number
}

export interface PriceQualityByTimeframe {
  provider: string
  timeframe: string
  meanComposite: number | null
  nPoints: number
}

function mean(values: (number | null)[]): number | null {
  const present = values.filter((v): v is number => v !== null && v !== undefined && !Number.isNaN(v))
  if (present.length === 0) return null
  return Math.round((present.reduce((s, v) => s + v, 0) / present.length) * 100) / 100
}

/** Aggregates raw benchmark result rows (from the latest FINISHED run) into
 * one Price Quality score per provider — the additive pill next to (never
 * merged into) scoring.ts's capability score. */
export function scoreProvidersFromResults(rows: BenchmarkResultRow[]): PriceQualityScore[] {
  const byProvider = new Map<string, BenchmarkResultRow[]>()
  for (const r of rows) {
    const list = byProvider.get(r.provider) ?? []
    list.push(r)
    byProvider.set(r.provider, list)
  }

  const out: PriceQualityScore[] = []
  for (const [provider, providerRows] of byProvider) {
    const okRows = providerRows.filter((r) => r.fetch_ok)
    out.push({
      provider,
      meanComposite: mean(providerRows.map((r) => r.composite_score)),
      meanCompleteness: mean(providerRows.map((r) => r.completeness_score)),
      meanCorrectness: mean(providerRows.map((r) => r.correctness_score)),
      meanTimestampIntegrity: mean(providerRows.map((r) => r.timestamp_integrity_score)),
      meanOhlcIntegrity: mean(providerRows.map((r) => r.ohlc_integrity_score)),
      meanCrossProviderAgreement: mean(providerRows.map((r) => r.cross_provider_agreement_score)),
      meanFreshness: mean(providerRows.map((r) => r.freshness_score)),
      meanLatency: mean(providerRows.map((r) => r.latency_score)),
      meanLatencyMs: mean(okRows.map((r) => r.latency_ms)),
      nPoints: providerRows.length,
      nFetchOk: okRows.length,
      nFetchFailed: providerRows.length - okRows.length,
    })
  }
  return out.sort((a, b) => (b.meanComposite ?? -1) - (a.meanComposite ?? -1) || a.provider.localeCompare(b.provider))
}

/** Per-provider per-timeframe breakdown (feeds the provider-detail
 * expansion: "H1 99.8 / H4 99.7 / D1 99.9"). */
export function scoreProvidersByTimeframe(rows: BenchmarkResultRow[]): PriceQualityByTimeframe[] {
  const key = (provider: string, timeframe: string) => `${provider}::${timeframe}`
  const groups = new Map<string, BenchmarkResultRow[]>()
  for (const r of rows) {
    const k = key(r.provider, r.timeframe)
    const list = groups.get(k) ?? []
    list.push(r)
    groups.set(k, list)
  }
  const out: PriceQualityByTimeframe[] = []
  for (const [, groupRows] of groups) {
    out.push({
      provider: groupRows[0].provider,
      timeframe: groupRows[0].timeframe,
      meanComposite: mean(groupRows.map((r) => r.composite_score)),
      nPoints: groupRows.length,
    })
  }
  return out.sort((a, b) => a.provider.localeCompare(b.provider) || a.timeframe.localeCompare(b.timeframe))
}
