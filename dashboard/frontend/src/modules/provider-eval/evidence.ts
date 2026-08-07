import type { BenchmarkResultRow } from './priceBenchmarkApi'

// Evidence Matrix pivot + real failure-classification summaries — built
// from the actual stored error/reason text on each result row, never a
// fabricated category. Directly motivated by the live Dukascopy 500 this
// session hit: a single endpoint/timeframe-boundary quirk must never
// collapse a provider's score to a blank zero without saying WHY.

export interface EvidenceMatrixCell {
  provider: string
  timeframe: string
  fetch_ok: boolean
  composite_score: number | null
  error: string | null
}

export interface EvidenceMatrixRow {
  symbol: string
  providers: Record<string, EvidenceMatrixCell[]> // provider -> cells (one per timeframe)
}

export function buildEvidenceMatrix(rows: BenchmarkResultRow[]): EvidenceMatrixRow[] {
  const bySymbol = new Map<string, BenchmarkResultRow[]>()
  for (const r of rows) {
    const list = bySymbol.get(r.symbol) ?? []
    list.push(r)
    bySymbol.set(r.symbol, list)
  }
  const out: EvidenceMatrixRow[] = []
  for (const [symbol, symbolRows] of bySymbol) {
    const providers: Record<string, EvidenceMatrixCell[]> = {}
    for (const r of symbolRows) {
      const cell: EvidenceMatrixCell = {
        provider: r.provider,
        timeframe: r.timeframe,
        fetch_ok: !!r.fetch_ok,
        composite_score: r.composite_score,
        error: r.error,
      }
      providers[r.provider] = [...(providers[r.provider] ?? []), cell]
    }
    out.push({ symbol, providers })
  }
  return out.sort((a, b) => a.symbol.localeCompare(b.symbol))
}

export interface FailureClassification {
  category: string
  impact: string
}

/** Real, evidence-derived classification from what actually failed and
 * what still succeeded — never a fabricated bucket. A single-timeframe
 * fetch error (e.g. one interval rejected by a provider's API) is
 * distinguished from a total, every-timeframe failure so a provider whose
 * live feed is otherwise healthy is never blanket-penalized for one
 * endpoint quirk (see this session's own Dukascopy period-alignment bug). */
export function classifyFailure(provider: string, rows: BenchmarkResultRow[]): FailureClassification | null {
  const providerRows = rows.filter((r) => r.provider === provider)
  const failed = providerRows.filter((r) => !r.fetch_ok)
  if (failed.length === 0) return null
  const ok = providerRows.filter((r) => r.fetch_ok)

  const timeframesFailed = [...new Set(failed.map((r) => r.timeframe))]
  const timeframesOk = [...new Set(ok.map((r) => r.timeframe))]
  const errorSample = failed[0].error ?? 'no error text recorded'

  if (ok.length === 0) {
    return {
      category: 'TOTAL FETCH FAILURE',
      impact: `${provider} returned no usable data for any of the ${timeframesFailed.length} benchmarked timeframe(s) — "${errorSample}"`,
    }
  }
  return {
    category: 'PARTIAL / INTERVAL-SPECIFIC CONSTRAINT',
    impact: `${provider} failed on ${timeframesFailed.join(', ')} but succeeded on ${timeframesOk.join(', ')} — "${errorSample}". Treat as a degraded endpoint on the failed timeframe(s), not a wholesale provider outage.`,
  }
}

export function classifyAllFailures(rows: BenchmarkResultRow[]): Record<string, FailureClassification> {
  const providers = [...new Set(rows.map((r) => r.provider))]
  const out: Record<string, FailureClassification> = {}
  for (const p of providers) {
    const c = classifyFailure(p, rows)
    if (c) out[p] = c
  }
  return out
}
