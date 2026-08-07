import { useEffect, useRef, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { DataTable, type Column } from '../../components/DataTable'
import { Badge } from '../../components/Badge'
import { getProviderChains, getDataConfidence } from './api'
import { evaluateProviders, reviewChains, RETIRED_PROVIDERS, type ProviderScore } from './scoring'
import {
  createPriceBenchmark, getPriceBenchmark, getPriceBenchmarkResults, cancelPriceBenchmark,
  type BenchmarkProfile, type BenchmarkResultRow,
} from './priceBenchmarkApi'
import { scoreProvidersFromResults, scoreProvidersByTimeframe, type PriceQualityScore } from './priceQualityScoring'
import { deriveRoutingRecommendations, groupRoutingBySymbol } from './routing'
import { buildEvidenceMatrix, classifyAllFailures } from './evidence'

const POLL_MS = 60_000
const BENCHMARK_POLL_MS = 2_000

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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => stopPolling, [])

  const start = async (profile: BenchmarkProfile) => {
    setStarting(true)
    setError(null)
    setResults(null)
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
                    {evidenceRows.map((row) => (
                      <tr key={row.symbol} className="border-t border-border">
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
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

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
    </div>
  )
}
