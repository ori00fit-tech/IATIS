import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { DataTable, type Column } from '../../components/DataTable'
import { Badge } from '../../components/Badge'
import { getProviderChains, getDataConfidence } from './api'
import { evaluateProviders, reviewChains, RETIRED_PROVIDERS, type ProviderScore } from './scoring'
import {
  createPriceBenchmark, getPriceBenchmark, getPriceBenchmarkResults, cancelPriceBenchmark,
  getPriceBenchmarkHistory,
  type BenchmarkProfile, type BenchmarkResultRow, type ScoreHistoryRow,
} from './priceBenchmarkApi'
import { scoreProvidersFromResults, scoreProvidersByTimeframe, type PriceQualityScore } from './priceQualityScoring'
import { deriveRoutingRecommendations, groupRoutingBySymbol } from './routing'
import { buildEvidenceMatrix, classifyAllFailures } from './evidence'
import { EvidenceChart } from './EvidenceChart'

// Phase 1c — echarts-backed, lazy-loaded like every other echarts consumer
// in this app (BacktestingCharts.tsx's MonthlyReturnsHeatmap/RMultipleHistogram)
// so echarts' JS only downloads when this panel's history section actually
// renders, not on every Provider Eval page load.
const ScoreHistoryChart = lazy(() => import('./ScoreHistoryChart').then((m) => ({ default: m.ScoreHistoryChart })))
import {
  createNewsBenchmark, getNewsBenchmark, getNewsBenchmarkResults, cancelNewsBenchmark,
  type NewsBenchmarkProfile, type NewsBenchmarkResultRow,
} from './newsBenchmarkApi'
import {
  createMacroBenchmark, getMacroBenchmark, getMacroBenchmarkResults, cancelMacroBenchmark,
  type MacroBenchmarkProfile, type MacroBenchmarkResultRow,
} from './macroBenchmarkApi'
import {
  createAnalyticsBenchmark, getAnalyticsBenchmark, getAnalyticsBenchmarkResults, cancelAnalyticsBenchmark,
  type AnalyticsBenchmarkProfile, type AnalyticsBenchmarkResultRow,
} from './analyticsBenchmarkApi'

const POLL_MS = 60_000
const BENCHMARK_POLL_MS = 2_000
// Matches backend/routes/provider_benchmark.py's _BenchmarkRequest default
// (tolerance_pct) — Run controls don't yet expose an override, so every
// run today uses this value; used here only to classify deviation
// severity for the Evidence drill-down chart.
const DEFAULT_TOLERANCE_PCT = 0.05

function scoreColor(s: number): string {
  if (s >= 75) return 'text-green'
  if (s >= 55) return 'text-amber'
  return 'text-red'
}
function scoreBar(s: number): string {
  if (s >= 75) return 'bg-green'
  if (s >= 55) return 'bg-amber'
  return 'bg-red'
}

function fmtAge(iso: string | null): string {
  if (!iso) return 'never'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = (Date.now() - t) / 1000
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172800) return `${(s / 3600).toFixed(0)}h ago`
  return `${(s / 86400).toFixed(0)}d ago`
}

function ProviderRow({ p, rank }: { p: ProviderScore; rank: number }) {
  const b = p.breakdown
  return (
    <div className="border-b border-border last:border-b-0 px-4 py-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-muted text-[0.8em] w-6 shrink-0">#{rank}</span>
        <span className="font-bold text-accent w-28 shrink-0">{p.provider}</span>
        <span
          className={`inline-block w-2 h-2 rounded-full shrink-0 ${p.availability_state === 'up' ? 'bg-green' : p.availability_state === 'down' ? 'bg-red' : 'bg-muted'}`}
          title={p.availability_state === 'up' ? 'usable now (credentials present)' : p.availability_state === 'down' ? 'unavailable — no credentials / dependency' : 'availability not reported by /provider-chains'}
        />
        <div className="flex-1 min-w-[120px] h-2 rounded bg-surface overflow-hidden">
          <div className={`h-full ${scoreBar(p.score)}`} style={{ width: `${p.score}%` }} />
        </div>
        <span className={`font-extrabold w-10 text-right shrink-0 ${scoreColor(p.score)}`}>{p.score}</span>
      </div>
      <div className="flex items-center gap-2 flex-wrap mt-2 pl-9 text-[0.72em]">
        {/* Native decision-TF coverage — the headline "valid data" signal */}
        {['H4', 'D1', 'H1'].map((tf) => {
          const native = p.nativeDecisionTFs.includes(tf)
          return (
            <span
              key={tf}
              className={`px-1.5 py-0.5 rounded border ${native ? 'border-green/40 text-green' : 'border-red/30 text-red/80'}`}
              title={native ? `${tf} served natively` : `${tf} NOT native — would be resampled (decision-poisoning risk)`}
            >
              {tf} {native ? 'native' : 'resampled'}
            </span>
          )
        })}
        {p.inActiveChain ? (
          <span className="text-muted">chains: {p.chainsIn.map((c) => c.cls).join(', ')}</span>
        ) : (
          <span className="text-red/80 border border-red/30 rounded px-1.5 py-0.5" title="Not in any configured chain — delivers no data to the pipeline right now">
            not in any chain
          </span>
        )}
        <span className="text-muted">served {p.usageCount}× · {fmtAge(p.lastUsed)}</span>
        {p.checksInvolving > 0 && (
          <span className={p.disagreements > 0 ? 'text-amber' : 'text-muted'}>
            agreement {p.checksInvolving - p.disagreements}/{p.checksInvolving}
          </span>
        )}
        <span className="text-muted/70" title="score = native TF (40) + availability (20) + chain trust (20) + usage (10) + agreement (10)">
          [{b.native}+{b.availability}+{b.chainTrust}+{b.usage}+{b.agreement}]
        </span>
      </div>
      {p.note && <div className="pl-9 mt-1 text-[0.68em] text-muted/80 italic">{p.note}</div>}
    </div>
  )
}

function qualityScoreColor(s: number | null): string {
  if (s === null) return 'text-muted'
  if (s >= 90) return 'text-green'
  if (s >= 70) return 'text-amber'
  return 'text-red'
}

function PriceBenchmarkPanel() {
  const [runId, setRunId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<string | null>(null)
  const [results, setResults] = useState<BenchmarkResultRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null)
  const [evidenceTarget, setEvidenceTarget] = useState<{ symbol: string; timeframe: string } | null>(null)
  const [history, setHistory] = useState<ScoreHistoryRow[] | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const loadHistory = () => {
    getPriceBenchmarkHistory()
      .then((r) => setHistory(r.history))
      .catch((e) => setHistoryError(e instanceof Error ? e.message : String(e)))
  }

  // Phase 1c — loaded once on mount (independent of any specific run) and
  // refreshed whenever a run this panel itself started reaches a terminal
  // state, so a freshly-finished run's own point appears without a reload.
  useEffect(() => {
    loadHistory()
    return stopPolling
  }, [])

  const start = async (profile: BenchmarkProfile) => {
    setStarting(true)
    setError(null)
    setResults(null)
    setEvidenceTarget(null)
    try {
      const res = await createPriceBenchmark({ profile })
      setRunId(res.run_id)
      setJobStatus(res.status)
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const status = await getPriceBenchmark(res.run_id)
          setJobStatus(status.job_status)
          if (status.job_status && !['queued', 'running'].includes(status.job_status)) {
            stopPolling()
            const r = await getPriceBenchmarkResults(res.run_id)
            setResults(r.results)
            loadHistory()
          }
        } catch (e) {
          stopPolling()
          setError(e instanceof Error ? e.message : String(e))
        }
      }, BENCHMARK_POLL_MS)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!runId) return
    try {
      await cancelPriceBenchmark(runId)
    } catch {
      // best-effort — the poll loop above will surface the real terminal state
    }
  }

  const running = jobStatus === 'queued' || jobStatus === 'running'
  const scores: PriceQualityScore[] = results ? scoreProvidersFromResults(results) : []
  const byTf = results ? scoreProvidersByTimeframe(results) : []
  const evidenceRows = results ? buildEvidenceMatrix(results) : []
  const failures = results ? classifyAllFailures(results) : {}
  const routingRecs = results ? deriveRoutingRecommendations(results) : []
  const routingBySymbol = groupRoutingBySymbol(routingRecs)

  const rankingColumns: Column<PriceQualityScore>[] = [
    { header: 'Provider', render: (r) => <span className="font-bold text-accent">{r.provider}</span> },
    {
      header: 'Price Quality',
      align: 'right',
      accessorFn: (r) => r.meanComposite ?? -1,
      render: (r) => (
        <span className={`font-extrabold ${qualityScoreColor(r.meanComposite)}`}>
          {r.meanComposite ?? '—'}
        </span>
      ),
    },
    { header: 'Completeness', align: 'right', render: (r) => r.meanCompleteness ?? '—' },
    { header: 'Correctness', align: 'right', render: (r) => r.meanCorrectness ?? '—' },
    { header: 'Timestamp Integrity', align: 'right', render: (r) => r.meanTimestampIntegrity ?? '—' },
    { header: 'OHLC Integrity', align: 'right', render: (r) => r.meanOhlcIntegrity ?? '—' },
    { header: 'Cross-Provider Agreement', align: 'right', render: (r) => r.meanCrossProviderAgreement ?? '—' },
    { header: 'Freshness', align: 'right', render: (r) => r.meanFreshness ?? '—' },
    { header: 'Latency', align: 'right', render: (r) => (r.meanLatencyMs !== null ? `${r.meanLatencyMs}ms` : '—') },
    {
      header: 'Fetches',
      align: 'right',
      render: (r) => (
        <span className={r.nFetchFailed > 0 ? 'text-amber' : 'text-muted'}>
          {r.nFetchOk}/{r.nPoints} ok
        </span>
      ),
    },
  ]

  return (
    <Panel title="Price Quality Benchmark" right="advisory — measures the feed, never changes a chain">
      <div className="p-4 flex flex-col gap-4">
        <p className="text-[0.78em] text-muted">
          Tests every FX/metals/crypto/indices provider against the SAME symbol/timeframe window and scores real,
          per-fetch data-quality dimensions (completeness, per-field correctness vs. a MEDIAN CONSENSUS across every
          provider fetched this run, timestamp-boundary integrity, OHLC structural integrity, cross-provider
          agreement, freshness, latency) — never blind trust of any single "ground truth" provider. Kept fully
          separate from the capability score above. This is a measurement layer only: it never writes to{' '}
          <code className="text-accent2">config.yaml</code>'s provider chains.
        </p>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => start('smoke')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.8em] font-bold hover:bg-accent/10 disabled:opacity-50"
          >
            Run Smoke
          </button>
          <button
            onClick={() => start('standard')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Standard
          </button>
          <button
            onClick={() => start('deep')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Deep
          </button>
          {running && (
            <>
              <span className="text-[0.78em] text-muted">
                {jobStatus} — run {runId}
              </span>
              <button
                onClick={cancel}
                className="px-2.5 py-1 rounded border border-red/40 text-red text-[0.75em] hover:bg-red/10"
              >
                Cancel
              </button>
            </>
          )}
          {!running && jobStatus && jobStatus !== 'queued' && jobStatus !== 'running' && (
            <span className="text-[0.78em] text-muted">
              last run: {jobStatus}
            </span>
          )}
        </div>

        {error && <div className="text-[0.8em] text-red">{error}</div>}

        <div>
          <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">
            Score History <span className="normal-case text-muted/70">(across every finished run, real longitudinal trend)</span>
          </div>
          {historyError && <div className="text-[0.8em] text-red">{historyError}</div>}
          {!historyError && history && (
            <Suspense fallback={<Empty>Loading chart…</Empty>}>
              <ScoreHistoryChart history={history} />
            </Suspense>
          )}
        </div>

        {results && results.length === 0 && <Empty>Run finished with zero results.</Empty>}

        {scores.length > 0 && (
          <div className="flex flex-col gap-3">
            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Provider Ranking</div>
              <DataTable columns={rankingColumns} rows={scores} rowKey={(r) => r.provider} />
            </div>

            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Per-Timeframe Breakdown</div>
              <div className="flex flex-col gap-1">
                {scores.map((s) => {
                  const rows = byTf.filter((t) => t.provider === s.provider)
                  const failure = failures[s.provider]
                  return (
                    <div key={s.provider} className="border border-border rounded-md">
                      <button
                        onClick={() => setExpandedProvider(expandedProvider === s.provider ? null : s.provider)}
                        className="w-full flex items-center justify-between px-3 py-2 text-left text-[0.8em]"
                      >
                        <span className="font-bold text-accent">{s.provider}</span>
                        <span className="text-muted">
                          {rows.map((r) => `${r.timeframe} ${r.meanComposite ?? '—'}`).join(' / ')}
                        </span>
                      </button>
                      {expandedProvider === s.provider && failure && (
                        <div className="px-3 pb-2.5 text-[0.75em]">
                          <Badge tone="marginal">{failure.category}</Badge>
                          <div className="mt-1.5 text-muted">{failure.impact}</div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Evidence Matrix</div>
              <div className="overflow-x-auto">
                <table className="w-full text-[0.78em] border-collapse">
                  <thead>
                    <tr>
                      <th className="text-left px-2 py-1.5 text-muted uppercase tracking-[0.5px] text-[0.85em]">Symbol</th>
                      {scores.map((s) => (
                        <th key={s.provider} className="text-left px-2 py-1.5 text-muted uppercase tracking-[0.5px] text-[0.85em]">
                          {s.provider}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {evidenceRows.map((row) => {
                      const timeframesForSymbol = [...new Set(
                        (results ?? []).filter((r) => r.symbol === row.symbol).map((r) => r.timeframe),
                      )].sort()
                      return (
                        <tr
                          key={row.symbol}
                          className="border-t border-border cursor-pointer hover:bg-accent/[0.04]"
                          onClick={() => timeframesForSymbol[0] && setEvidenceTarget({ symbol: row.symbol, timeframe: timeframesForSymbol[0] })}
                          title="Click to open the Evidence drill-down chart for this symbol"
                        >
                          <td className="px-2 py-1.5 font-bold text-accent">{row.symbol}</td>
                          {scores.map((s) => {
                            const cells = row.providers[s.provider] ?? []
                            if (cells.length === 0) return <td key={s.provider} className="px-2 py-1.5 text-muted/60">—</td>
                            const anyFail = cells.some((c) => !c.fetch_ok)
                            return (
                              <td key={s.provider} className={`px-2 py-1.5 ${anyFail ? 'text-red' : 'text-text'}`}>
                                {cells.map((c) => (c.fetch_ok ? (c.composite_score ?? '—') : 'FAIL')).join('/')}
                              </td>
                            )
                          })}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {evidenceTarget && results && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[0.78em]">
                  <span className="text-muted">Evidence timeframe:</span>
                  {[...new Set(results.filter((r) => r.symbol === evidenceTarget.symbol).map((r) => r.timeframe))]
                    .sort()
                    .map((tf) => (
                      <button
                        key={tf}
                        onClick={() => setEvidenceTarget({ symbol: evidenceTarget.symbol, timeframe: tf })}
                        className={`px-2 py-1 rounded border text-[0.9em] ${
                          tf === evidenceTarget.timeframe ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:bg-surface'
                        }`}
                      >
                        {tf}
                      </button>
                    ))}
                  <button onClick={() => setEvidenceTarget(null)} className="ml-auto text-muted hover:text-text">
                    ✕ close
                  </button>
                </div>
                <EvidenceChart
                  symbol={evidenceTarget.symbol}
                  timeframe={evidenceTarget.timeframe}
                  results={results}
                  tolerancePct={DEFAULT_TOLERANCE_PCT}
                />
              </div>
            )}

            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">
                Routing Recommendation <span className="normal-case text-muted/70">(advisory only — never auto-applied)</span>
              </div>
              <div className="flex flex-col gap-2">
                {[...routingBySymbol.entries()].map(([symbol, recs]) => (
                  <div key={symbol} className="flex items-center gap-2 flex-wrap text-[0.78em] border border-border rounded-md px-3 py-2">
                    <span className="font-bold text-accent w-20 shrink-0">{symbol}</span>
                    {recs.map((r) => (
                      <span key={r.role} className="flex items-center gap-1">
                        <Badge tone={r.role === 'PRIMARY' ? 'good' : r.role === 'BACKUP' ? 'marginal' : 'neutral'}>{r.role}</Badge>
                        <span className="text-text">{r.provider}</span>
                        <span className="text-muted/70">({r.compositeScore ?? '—'})</span>
                      </span>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}

function NewsBenchmarkPanel() {
  const [runId, setRunId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<string | null>(null)
  const [results, setResults] = useState<NewsBenchmarkResultRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => stopPolling, [])

  const start = async (profile: NewsBenchmarkProfile) => {
    setStarting(true)
    setError(null)
    setResults(null)
    try {
      const res = await createNewsBenchmark({ profile })
      setRunId(res.run_id)
      setJobStatus(res.status)
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const status = await getNewsBenchmark(res.run_id)
          setJobStatus(status.job_status)
          if (status.job_status && !['queued', 'running'].includes(status.job_status)) {
            stopPolling()
            const r = await getNewsBenchmarkResults(res.run_id)
            setResults(r.results)
          }
        } catch (e) {
          stopPolling()
          setError(e instanceof Error ? e.message : String(e))
        }
      }, BENCHMARK_POLL_MS)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!runId) return
    try {
      await cancelNewsBenchmark(runId)
    } catch {
      // best-effort — the poll loop above will surface the real terminal state
    }
  }

  const running = jobStatus === 'queued' || jobStatus === 'running'
  const providers = results ? [...new Set(results.map((r) => r.provider))].sort() : []
  const symbols = results ? [...new Set(results.map((r) => r.symbol))].sort() : []

  const rankingColumns: Column<string>[] = [
    { header: 'Provider', render: (p) => <span className="font-bold text-accent">{p}</span> },
    {
      header: 'News Quality',
      align: 'right',
      accessorFn: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.composite_score !== null)
        return rows.length ? rows.reduce((s, r) => s + (r.composite_score ?? 0), 0) / rows.length : -1
      },
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.composite_score !== null)
        const mean = rows.length ? Math.round((rows.reduce((s, r) => s + (r.composite_score ?? 0), 0) / rows.length) * 100) / 100 : null
        return <span className={`font-extrabold ${qualityScoreColor(mean)}`}>{mean ?? '—'}</span>
      },
    },
    {
      header: 'Coverage',
      align: 'right',
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.coverage_score !== null)
        return rows.length ? `${rows.filter((r) => r.coverage_score === 100).length}/${rows.length}` : '—'
      },
    },
    {
      header: 'Sentiment Available',
      align: 'right',
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.fetch_ok);
        if (rows.length === 0) return '—'
        return rows.some((r) => r.sentiment_availability_score !== null) ? 'yes' : 'no'
      },
    },
    {
      header: 'Fetches',
      align: 'right',
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p)
        const ok = rows.filter((r) => r.fetch_ok).length
        return (
          <span className={ok < rows.length ? 'text-amber' : 'text-muted'}>
            {ok}/{rows.length} ok
          </span>
        )
      },
    },
  ]

  return (
    <Panel title="News Quality Benchmark" right="advisory — measures the feed, never influences H021 or any live decision">
      <div className="p-4 flex flex-col gap-4">
        <p className="text-[0.78em] text-muted">
          Tests MarketAux (real per-article, per-entity sentiment) and Finnhub (a category-wide feed, best-effort
          keyword-matched to a symbol — no sentiment field on its free tier) for coverage, source diversity,
          duplicate-headline rate, freshness, latency, sentiment availability, and cross-provider COVERAGE agreement
          (do both providers agree there was real news activity — the honest news-equivalent of a numeric consensus;
          there is no ground-truth headline to score correctness against). Measurement layer only — never writes to{' '}
          <code className="text-accent2">config.yaml</code> and never feeds H021's own pre-registered process.
        </p>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => start('smoke')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.8em] font-bold hover:bg-accent/10 disabled:opacity-50"
          >
            Run Smoke
          </button>
          <button
            onClick={() => start('standard')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Standard
          </button>
          <button
            onClick={() => start('deep')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Deep
          </button>
          {running && (
            <>
              <span className="text-[0.78em] text-muted">
                {jobStatus} — run {runId}
              </span>
              <button
                onClick={cancel}
                className="px-2.5 py-1 rounded border border-red/40 text-red text-[0.75em] hover:bg-red/10"
              >
                Cancel
              </button>
            </>
          )}
          {!running && jobStatus && jobStatus !== 'queued' && jobStatus !== 'running' && (
            <span className="text-[0.78em] text-muted">last run: {jobStatus}</span>
          )}
        </div>

        {error && <div className="text-[0.8em] text-red">{error}</div>}

        {results && results.length === 0 && <Empty>Run finished with zero results.</Empty>}

        {providers.length > 0 && (
          <div className="flex flex-col gap-3">
            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Provider Ranking</div>
              <DataTable columns={rankingColumns} rows={providers} rowKey={(p) => p} />
            </div>

            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Evidence Matrix</div>
              <div className="overflow-x-auto">
                <table className="w-full text-[0.78em] border-collapse">
                  <thead>
                    <tr>
                      <th className="text-left px-2 py-1.5 text-muted uppercase tracking-[0.5px] text-[0.85em]">Symbol</th>
                      {providers.map((p) => (
                        <th key={p} className="text-left px-2 py-1.5 text-muted uppercase tracking-[0.5px] text-[0.85em]">
                          {p}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {symbols.map((symbol) => (
                      <tr key={symbol} className="border-t border-border">
                        <td className="px-2 py-1.5 font-bold text-accent">{symbol}</td>
                        {providers.map((p) => {
                          const row = (results ?? []).find((r) => r.symbol === symbol && r.provider === p)
                          if (!row) return <td key={p} className="px-2 py-1.5 text-muted/60">—</td>
                          return (
                            <td key={p} className={`px-2 py-1.5 ${row.fetch_ok ? 'text-text' : 'text-red'}`}>
                              {row.fetch_ok ? `${row.composite_score ?? '—'} (${row.article_count} articles)` : (row.error ?? 'FAIL')}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}

function MacroBenchmarkPanel() {
  const [runId, setRunId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<string | null>(null)
  const [results, setResults] = useState<MacroBenchmarkResultRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => stopPolling, [])

  const start = async (profile: MacroBenchmarkProfile) => {
    setStarting(true)
    setError(null)
    setResults(null)
    try {
      const res = await createMacroBenchmark({ profile })
      setRunId(res.run_id)
      setJobStatus(res.status)
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const status = await getMacroBenchmark(res.run_id)
          setJobStatus(status.job_status)
          if (status.job_status && !['queued', 'running'].includes(status.job_status)) {
            stopPolling()
            const r = await getMacroBenchmarkResults(res.run_id)
            setResults(r.results)
          }
        } catch (e) {
          stopPolling()
          setError(e instanceof Error ? e.message : String(e))
        }
      }, BENCHMARK_POLL_MS)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!runId) return
    try {
      await cancelMacroBenchmark(runId)
    } catch {
      // best-effort — the poll loop above will surface the real terminal state
    }
  }

  const running = jobStatus === 'queued' || jobStatus === 'running'
  const providers = results ? [...new Set(results.map((r) => r.provider))].sort() : []
  const series = results ? [...new Set(results.map((r) => r.series))].sort() : []

  const rankingColumns: Column<string>[] = [
    { header: 'Provider', render: (p) => <span className="font-bold text-accent">{p}</span> },
    {
      header: 'Macro Quality',
      align: 'right',
      accessorFn: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.composite_score !== null)
        return rows.length ? rows.reduce((s, r) => s + (r.composite_score ?? 0), 0) / rows.length : -1
      },
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.composite_score !== null)
        const mean = rows.length ? Math.round((rows.reduce((s, r) => s + (r.composite_score ?? 0), 0) / rows.length) * 100) / 100 : null
        return <span className={`font-extrabold ${qualityScoreColor(mean)}`}>{mean ?? '—'}</span>
      },
    },
    {
      header: 'Completeness',
      align: 'right',
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.completeness_score !== null)
        if (rows.length === 0) return '—'
        const mean = Math.round((rows.reduce((s, r) => s + (r.completeness_score ?? 0), 0) / rows.length) * 100) / 100
        return <span className={qualityScoreColor(mean)}>{mean}</span>
      },
    },
    {
      header: 'Cross-Provider Agreement',
      align: 'right',
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p && r.cross_provider_agreement_score !== null)
        if (rows.length === 0) return <span className="text-muted/60">n/a — single source</span>
        const mean = Math.round((rows.reduce((s, r) => s + (r.cross_provider_agreement_score ?? 0), 0) / rows.length) * 100) / 100
        return <span className={qualityScoreColor(mean)}>{mean}</span>
      },
    },
    {
      header: 'Fetches',
      align: 'right',
      render: (p) => {
        const rows = (results ?? []).filter((r) => r.provider === p)
        const ok = rows.filter((r) => r.fetch_ok).length
        return (
          <span className={ok < rows.length ? 'text-amber' : 'text-muted'}>
            {ok}/{rows.length} ok
          </span>
        )
      },
    },
  ]

  return (
    <Panel title="Macro Quality Benchmark" right="advisory — never touches the live Macro engine or config.yaml">
      <div className="p-4 flex flex-col gap-4">
        <p className="text-[0.78em] text-muted">
          Tests FRED, CBOE, and Alpha Vantage against a fixed catalog of macro series (VIX, DXY, yields, credit
          spread, Fed balance sheet, CPI, GDP, ...) for completeness, freshness, timestamp integrity, and latency.
          Almost every series has exactly one real provider (FRED) — cross-provider agreement is only ever populated
          for the 3 series with a genuine second source: VIX (CBOE vs FRED) and US10Y/US02Y (FRED vs Alpha Vantage
          Treasury Yield). Measurement layer only — never calls or influences{' '}
          <code className="text-accent2">core.alt_data_loader.load_macro_snapshot()</code> (the live Macro engine's
          own source) or <code className="text-accent2">config.yaml</code>.
        </p>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => start('smoke')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.8em] font-bold hover:bg-accent/10 disabled:opacity-50"
          >
            Run Smoke
          </button>
          <button
            onClick={() => start('standard')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Standard
          </button>
          <button
            onClick={() => start('deep')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Deep
          </button>
          {running && (
            <>
              <span className="text-[0.78em] text-muted">
                {jobStatus} — run {runId}
              </span>
              <button
                onClick={cancel}
                className="px-2.5 py-1 rounded border border-red/40 text-red text-[0.75em] hover:bg-red/10"
              >
                Cancel
              </button>
            </>
          )}
          {!running && jobStatus && jobStatus !== 'queued' && jobStatus !== 'running' && (
            <span className="text-[0.78em] text-muted">last run: {jobStatus}</span>
          )}
        </div>

        {error && <div className="text-[0.8em] text-red">{error}</div>}

        {results && results.length === 0 && <Empty>Run finished with zero results.</Empty>}

        {providers.length > 0 && (
          <div className="flex flex-col gap-3">
            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Provider Ranking</div>
              <DataTable columns={rankingColumns} rows={providers} rowKey={(p) => p} />
            </div>

            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Evidence Matrix</div>
              <div className="overflow-x-auto">
                <table className="w-full text-[0.78em] border-collapse">
                  <thead>
                    <tr>
                      <th className="text-left px-2 py-1.5 text-muted uppercase tracking-[0.5px] text-[0.85em]">Series</th>
                      {providers.map((p) => (
                        <th key={p} className="text-left px-2 py-1.5 text-muted uppercase tracking-[0.5px] text-[0.85em]">
                          {p}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {series.map((s) => (
                      <tr key={s} className="border-t border-border">
                        <td className="px-2 py-1.5 font-bold text-accent">{s}</td>
                        {providers.map((p) => {
                          const row = (results ?? []).find((r) => r.series === s && r.provider === p)
                          if (!row) return <td key={p} className="px-2 py-1.5 text-muted/60">—</td>
                          return (
                            <td key={p} className={`px-2 py-1.5 ${row.fetch_ok ? 'text-text' : 'text-red'}`}>
                              {row.fetch_ok
                                ? `${row.composite_score ?? '—'} (${row.latest_value ?? '—'} as of ${row.latest_date?.slice(0, 10) ?? '—'})`
                                : (row.error ?? 'FAIL')}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}

function AnalyticsBenchmarkPanel() {
  const [runId, setRunId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<string | null>(null)
  const [results, setResults] = useState<AnalyticsBenchmarkResultRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => stopPolling, [])

  const start = async (profile: AnalyticsBenchmarkProfile) => {
    setStarting(true)
    setError(null)
    setResults(null)
    try {
      const res = await createAnalyticsBenchmark({ profile })
      setRunId(res.run_id)
      setJobStatus(res.status)
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const status = await getAnalyticsBenchmark(res.run_id)
          setJobStatus(status.job_status)
          if (status.job_status && !['queued', 'running'].includes(status.job_status)) {
            stopPolling()
            const r = await getAnalyticsBenchmarkResults(res.run_id)
            setResults(r.results)
          }
        } catch (e) {
          stopPolling()
          setError(e instanceof Error ? e.message : String(e))
        }
      }, BENCHMARK_POLL_MS)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!runId) return
    try {
      await cancelAnalyticsBenchmark(runId)
    } catch {
      // best-effort — the poll loop above will surface the real terminal state
    }
  }

  const running = jobStatus === 'queued' || jobStatus === 'running'

  const meanOf = (key: 'determinism_score' | 'coverage_score' | 'freshness_score' | 'latency_score'): number | null => {
    const rows = (results ?? []).filter((r) => r[key] !== null)
    if (rows.length === 0) return null
    return Math.round((rows.reduce((s, r) => s + (r[key] ?? 0), 0) / rows.length) * 100) / 100
  }
  const meanDeterminism = meanOf('determinism_score')
  const meanCoverage = meanOf('coverage_score')
  const meanFreshness = meanOf('freshness_score')
  const meanLatency = meanOf('latency_score')

  const symbolColumns: Column<AnalyticsBenchmarkResultRow>[] = [
    { header: 'Symbol', render: (r) => <span className="font-bold text-accent">{r.symbol}</span> },
    { header: 'Articles', align: 'right', render: (r) => (r.fetch_ok ? r.article_count : '—') },
    {
      header: 'Determinism',
      align: 'right',
      render: (r) => (r.determinism_score === null ? <span className="text-muted/60">n/a — no overlap</span>
        : <span className={qualityScoreColor(r.determinism_score)}>{r.determinism_score}</span>),
    },
    {
      header: 'Composite',
      align: 'right',
      accessorFn: (r) => r.composite_score ?? -1,
      render: (r) => (r.composite_score === null ? '—' : <span className={`font-extrabold ${qualityScoreColor(r.composite_score)}`}>{r.composite_score}</span>),
    },
    {
      header: 'Status',
      render: (r) => (r.fetch_ok ? <span className="text-green">ok</span> : <span className="text-red">{r.error ?? 'FAIL'}</span>),
    },
  ]

  return (
    <Panel title="Analytics Reproducibility Benchmark" right="advisory — reproducibility only, no predictive claim">
      <div className="p-4 flex flex-col gap-4">
        <p className="text-[0.78em] text-muted">
          Scores MarketAux's sentiment API on <b>determinism</b> — the same query, repeated seconds later, must
          return the same sentiment value for the same underlying article — plus coverage, freshness, and latency.
          Deliberately single-provider (TAAPI's documented free-tier rate limit rules it out — it rejects a second
          call made seconds after the first) and deliberately has <b>no predictive or subsequent-outcome-tracking
          dimension</b>: "does this sentiment predict price moves" is a trading hypothesis, not a provider benchmark,
          and belongs in Mission Center's own pre-registered-hypothesis pipeline if ever pursued. Measurement layer
          only — never writes to <code className="text-accent2">config.yaml</code>.
        </p>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => start('smoke')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.8em] font-bold hover:bg-accent/10 disabled:opacity-50"
          >
            Run Smoke
          </button>
          <button
            onClick={() => start('standard')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Standard
          </button>
          <button
            onClick={() => start('deep')}
            disabled={starting || running}
            className="px-3 py-1.5 rounded border border-border text-text text-[0.8em] hover:bg-surface disabled:opacity-50"
          >
            Run Deep
          </button>
          {running && (
            <>
              <span className="text-[0.78em] text-muted">
                {jobStatus} — run {runId}
              </span>
              <button
                onClick={cancel}
                className="px-2.5 py-1 rounded border border-red/40 text-red text-[0.75em] hover:bg-red/10"
              >
                Cancel
              </button>
            </>
          )}
          {!running && jobStatus && jobStatus !== 'queued' && jobStatus !== 'running' && (
            <span className="text-[0.78em] text-muted">last run: {jobStatus}</span>
          )}
        </div>

        {error && <div className="text-[0.8em] text-red">{error}</div>}

        {results && results.length === 0 && <Empty>Run finished with zero results.</Empty>}

        {results && results.length > 0 && (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-4 gap-3">
              {[
                ['Determinism', meanDeterminism], ['Coverage', meanCoverage],
                ['Freshness', meanFreshness], ['Latency', meanLatency],
              ].map(([label, value]) => (
                <div key={label as string} className="border border-border rounded-md p-3 text-center">
                  <div className="text-[0.68em] text-muted uppercase tracking-[1px] mb-1">{label}</div>
                  <div className={`text-[1.4em] font-extrabold ${qualityScoreColor(value as number | null)}`}>
                    {value === null ? '—' : value}
                  </div>
                </div>
              ))}
            </div>

            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">Per-Symbol Results</div>
              <DataTable columns={symbolColumns} rows={results} rowKey={(r) => r.symbol} />
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}

export function ProviderEval() {
  const { markUnauthenticated } = useAuth()
  const chains = usePolling(getProviderChains, POLL_MS, markUnauthenticated)
  const confidence = usePolling(getDataConfidence, POLL_MS, markUnauthenticated)

  if (!chains.data) {
    return (
      <Panel title="Provider Evaluation">
        <Empty>{chains.loading ? 'Loading provider chains…' : 'No provider data available'}</Empty>
      </Panel>
    )
  }

  const ranked = evaluateProviders(chains.data, confidence.data)
  const reviews = reviewChains(chains.data, ranked)
  const best = ranked[0]

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.78em] text-muted">
        Ranks every data provider for its ability to deliver <b>valid</b> data to the pipeline. Native decision-timeframe
        candles dominate the score — a resampled or wrong-instrument bar silently poisons decisions, which is worse than a
        missing one (CLAUDE.md). Synthesis over <code className="text-accent2">/provider-chains</code> and{' '}
        <code className="text-accent2">/data-confidence</code>; it never changes a chain.
      </p>

      {best && (
        <div className="px-3.5 py-2.5 rounded-md border border-green/30 bg-green/5 text-[0.82em]">
          Top provider: <b className="text-green">{best.provider}</b> ({best.score}/100) — native{' '}
          {best.nativeDecisionTFs.join('/') || 'none'} · in {best.chainsIn.length} chain(s)
        </div>
      )}

      <Panel title="Provider Ranking" right={`${ranked.length} providers · best data first`}>
        <div>
          {ranked.map((p, i) => (
            <ProviderRow key={p.provider} p={p} rank={i + 1} />
          ))}
        </div>
        {RETIRED_PROVIDERS.size > 0 && (
          <div className="px-4 py-2 border-t border-border text-[0.7em] text-muted">
            Retired (untrusted, excluded): {[...RETIRED_PROVIDERS].join(', ')} — removed from all price chains and
            replaced by CBOE/FRED in the macro layer.
          </div>
        )}
      </Panel>

      <Panel title="Chain Order Review" right="advisory — configured order encodes measured reliability">
        <div className="p-4 flex flex-col gap-3">
          <p className="text-[0.74em] text-muted">
            Each configured chain re-sorted by score. A divergence is a prompt to investigate — not an instruction to
            re-order. The live order is authoritative and changed only in <code className="text-accent2">config.yaml</code>{' '}
            by an operator.
          </p>
          {reviews.map((r) => (
            <div key={r.cls} className="border border-border rounded-md p-3 text-[0.8em]">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="font-bold text-accent uppercase text-[0.8em] tracking-[1px]">{r.cls}</span>
                {r.differs ? (
                  <span className="text-[0.7em] text-amber border border-amber/40 rounded px-1.5 py-0.5">differs</span>
                ) : (
                  <span className="text-[0.7em] text-green border border-green/40 rounded px-1.5 py-0.5">aligned</span>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <div className="flex gap-2">
                  <span className="text-muted w-20 shrink-0">configured</span>
                  <span className="font-mono text-[0.92em]">{r.current.join(' → ')}</span>
                </div>
                {r.differs && (
                  <div className="flex gap-2">
                    <span className="text-muted w-20 shrink-0">by score</span>
                    <span className="font-mono text-[0.92em] text-amber">{r.suggested.join(' → ')}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <PriceBenchmarkPanel />
      <NewsBenchmarkPanel />
      <MacroBenchmarkPanel />
      <AnalyticsBenchmarkPanel />
    </div>
  )
}
