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

// ── Phase 1b: Evidence drill-down chart data ──────────────────────────
// Real per-bar (close, consensus_close, diff_pct) tuples computed server-
// side (backtest.price_benchmark.build_evidence_series), capped to the
// most recent ~100 bars — a drill-down view, never the main decision
// surface (tables/scores stay primary per the operator's own direction).

export interface EvidenceSeriesPoint {
  ts: string
  close: number
  consensus_close: number | null
  diff_pct: number | null
  exceeds_tolerance: boolean
}

export type DeviationSeverity = 'LOW' | 'MEDIUM' | 'HIGH'

export interface ProviderEvidenceSeries {
  provider: string
  points: EvidenceSeriesPoint[]
}

export function parseEvidenceSeries(row: BenchmarkResultRow): EvidenceSeriesPoint[] {
  if (!row.evidence_series_json) return []
  try {
    const parsed = JSON.parse(row.evidence_series_json)
    return Array.isArray(parsed) ? (parsed as EvidenceSeriesPoint[]) : []
  } catch {
    return []
  }
}

/** Every provider's evidence series for one (symbol, timeframe) pair —
 * the exact scope an Evidence drill-down chart overlays. */
export function buildEvidenceChartSeries(
  rows: BenchmarkResultRow[], symbol: string, timeframe: string,
): ProviderEvidenceSeries[] {
  return rows
    .filter((r) => r.symbol === symbol && r.timeframe === timeframe && r.fetch_ok)
    .map((r) => ({ provider: r.provider, points: parseEvidenceSeries(r) }))
    .filter((s) => s.points.length > 0)
}

/** Severity heuristic, documented not hidden: >=2x tolerance is MEDIUM,
 * >=5x tolerance is HIGH — multiples of the run's own configured
 * tolerance_pct rather than an arbitrary fixed percentage, so it scales
 * with whatever tolerance the operator actually set for that run. */
export function classifyDeviationSeverity(diffPct: number, tolerancePct: number): DeviationSeverity {
  if (diffPct >= tolerancePct * 5) return 'HIGH'
  if (diffPct >= tolerancePct * 2) return 'MEDIUM'
  return 'LOW'
}

export interface DeviationEvent {
  ts: string
  provider: string
  providerClose: number
  consensusClose: number
  diffPct: number
  severity: DeviationSeverity
}

/** The "DEVIATION DETECTED" event list a chart's marker track surfaces —
 * one entry per bar a provider's own evidence series flagged as exceeding
 * tolerance against the run's median consensus. */
export function buildDeviationEvents(series: ProviderEvidenceSeries[], tolerancePct: number): DeviationEvent[] {
  const events: DeviationEvent[] = []
  for (const s of series) {
    for (const p of s.points) {
      if (!p.exceeds_tolerance || p.diff_pct === null || p.consensus_close === null) continue
      events.push({
        ts: p.ts,
        provider: s.provider,
        providerClose: p.close,
        consensusClose: p.consensus_close,
        diffPct: p.diff_pct,
        severity: classifyDeviationSeverity(p.diff_pct, tolerancePct),
      })
    }
  }
  return events.sort((a, b) => a.ts.localeCompare(b.ts))
}

export function formatDeviationEvent(e: DeviationEvent): string {
  return `DEVIATION DETECTED — ${e.provider} ${e.providerClose} vs consensus ${e.consensusClose} — ${e.diffPct.toFixed(4)}% — ${e.severity}`
}
