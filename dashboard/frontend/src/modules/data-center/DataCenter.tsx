import { useEffect, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import type { CacheStatus, DataConfidenceCheck } from './api'
import { getDataConfidence, getDataHealth } from './api'
import { DataTable, type Column } from '../../components/DataTable'
import { getProviderChains } from '../system-audit/api'
import { getJobDetail, runJob, type JobDetail, type JobStatus } from '../experiment-runner/api'
import { reportDownloadUrl } from '../reports/api'

const POLL_MS = 60_000
const JOB_POLL_MS = 3_000

const CELL_CLASS: Record<CacheStatus, string> = {
  OK: 'bg-green/15 text-green',
  STALE: 'bg-amber/15 text-amber',
  GAPS: 'bg-amber/25 text-amber',
  MISSING: 'bg-red/15 text-red',
}

const JOB_BADGE: Record<JobStatus, 'exec' | 'no-trade' | 'good' | 'marginal' | 'neutral'> = {
  queued: 'neutral',
  running: 'marginal',
  finished: 'exec',
  failed: 'no-trade',
  timeout: 'no-trade',
}

function VerifyAndExport() {
  const [starting, setStarting] = useState(false)
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!detail || detail.status === 'finished' || detail.status === 'failed' || detail.status === 'timeout') return
    const id = setInterval(() => {
      getJobDetail(detail.job_id)
        .then(setDetail)
        .catch(() => {})
    }, JOB_POLL_MS)
    return () => clearInterval(id)
  }, [detail])

  const verify = async () => {
    setStarting(true)
    setError(null)
    try {
      const summary = await runJob('verify_data_integrity')
      setDetail({ ...summary, log: [] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 px-4 py-3 border-b border-border bg-surface/40">
      <div className="flex items-center gap-3">
        <button
          onClick={verify}
          disabled={starting || detail?.status === 'queued' || detail?.status === 'running'}
          className="px-3 py-1.5 text-[0.78em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50"
        >
          {detail?.status === 'queued' || detail?.status === 'running' ? 'Verifying…' : 'Verify (scripts.verify_data_integrity)'}
        </button>
        <a href={reportDownloadUrl('data_quality')} className="text-accent hover:text-accent2 text-[0.78em] underline decoration-dotted">
          Export report (.md)
        </a>
        {detail && <Badge tone={JOB_BADGE[detail.status]}>{detail.status}</Badge>}
        {error && <span className="text-red text-[0.78em]">{error}</span>}
      </div>
      {detail && detail.log.length > 0 && (
        <pre className="p-3 bg-bg/60 rounded text-[0.72em] overflow-auto max-h-[200px] whitespace-pre-wrap break-words font-mono">
          {detail.log.join('\n')}
        </pre>
      )}
    </div>
  )
}

export function DataCenter() {
  const { markUnauthenticated } = useAuth()
  const { data, loading, error } = usePolling(getDataHealth, POLL_MS, markUnauthenticated)
  const chains = usePolling(getProviderChains, POLL_MS * 5, markUnauthenticated)
  const confidence = usePolling(getDataConfidence, POLL_MS, markUnauthenticated)

  const timeframeSet = new Set<string>()
  data?.symbols.forEach((s) => Object.keys(s.timeframes).forEach((tf) => timeframeSet.add(tf)))
  const timeframes = Array.from(timeframeSet)

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={data?.summary.ok ?? '—'} label="OK" color="green" />
        <KpiCard value={data?.summary.stale ?? '—'} label="Stale" color="amber" />
        <KpiCard value={data?.summary.gaps ?? '—'} label="Gaps" color="amber" />
        <KpiCard value={data?.summary.missing ?? '—'} label="Missing" color="red" />
      </div>

      <Panel title="Cache Completeness" right={data ? `checked ${new Date(data.checked_at).toLocaleTimeString()}` : undefined}>
        <VerifyAndExport />
        {error ? (
          <Empty>Could not load data health</Empty>
        ) : !data || data.symbols.length === 0 ? (
          <Empty>{loading ? 'Loading...' : 'No symbols configured'}</Empty>
        ) : (
          <table className="w-full border-collapse text-[0.82em]">
            <thead>
              <tr>
                <th className="px-3 py-2 text-left text-muted text-[0.75em] uppercase tracking-[0.8px] bg-surface font-semibold">
                  Symbol
                </th>
                {timeframes.map((tf) => (
                  <th key={tf} className="px-3 py-2 text-center text-muted text-[0.75em] uppercase tracking-[0.8px] bg-surface font-semibold">
                    {tf}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.symbols.map((s) => (
                <tr key={s.symbol} className="border-b border-border last:border-b-0">
                  <td className="px-3 py-2.5 font-bold text-accent">{s.symbol}</td>
                  {timeframes.map((tf) => {
                    const cell = s.timeframes[tf]
                    return (
                      <td key={tf} className="px-2 py-2 text-center">
                        {cell ? (
                          <span
                            className={`inline-block px-2 py-1 rounded text-[0.85em] font-bold ${CELL_CLASS[cell.status]}`}
                            title={
                              cell.last_bar_time
                                ? `last bar: ${cell.last_bar_time} (${cell.age_minutes}m ago) · tz: ${cell.timezone ?? '?'}\n` +
                                  `${cell.gap_count_30d} gaps/30d · ${cell.duplicate_count} duplicate timestamps\n` +
                                  `integrity score: ${cell.integrity_score}/100 (heuristic, not a statistical measure)`
                                : 'no cache file'
                            }
                          >
                            {cell.status}
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="Provider Chains" right="asset-class failover order — first native provider wins">
        {!chains.data ? (
          <Empty>{chains.loading ? 'Loading...' : 'Could not load provider chains'}</Empty>
        ) : (
          <div className="p-4 flex flex-col gap-3">
            {Object.entries(chains.data.chains).map(([cls, providers]) => (
              <div key={cls} className="flex items-center gap-2 flex-wrap">
                <span className="w-20 text-[0.75em] uppercase tracking-[1px] text-muted font-semibold">{cls}</span>
                {providers.map((p, i) => (
                  <span key={p} className="flex items-center gap-2">
                    {i > 0 && <span className="text-muted text-[0.7em]">→</span>}
                    <span
                      className={`px-2 py-0.5 rounded text-[0.75em] font-bold border ${
                        chains.data!.availability[p]
                          ? 'text-green border-green/40 bg-green/10'
                          : 'text-muted border-border bg-surface/50'
                      }`}
                      title={
                        (chains.data!.availability[p] ? 'available' : 'not configured (no credentials)') +
                        ' · native: ' +
                        (chains.data!.native_timeframes[p]?.join(' ') ?? '?') +
                        (chains.data!.recent_usage[p]
                          ? ` · served ${chains.data!.recent_usage[p].count}x recently, last ${chains.data!.recent_usage[p].last_used_at?.slice(0, 19) ?? '?'}`
                          : ' · no recent usage recorded')
                      }
                    >
                      {p}
                    </span>
                  </span>
                ))}
              </div>
            ))}
            <div className="text-[0.7em] text-muted mt-1">
              Greyed providers lack credentials and are skipped instantly. Hover a provider for its native timeframes and how
              often it actually served the last 200 logged decisions — a timeframe no chain member serves natively is
              resampled from the best fetched base.
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Macro / Alt Data Sources" right="CBOE, FRED, CFTC — no live ping, config + local cache freshness only">
        {!chains.data ? (
          <Empty>{chains.loading ? 'Loading...' : 'Could not load'}</Empty>
        ) : (
          <div className="p-4 flex flex-col gap-2">
            {Object.entries(chains.data.macro_sources).map(([name, status]) => (
              <div key={name} className="flex items-center gap-3 text-[0.82em]">
                <span
                  className={`inline-block w-2 h-2 rounded-full shrink-0 ${status.configured ? 'bg-green' : 'bg-muted'}`}
                />
                <span className="font-bold text-accent w-28 shrink-0">{name}</span>
                <span className="text-muted">{status.note}</span>
                {status.last_cached && <span className="text-muted text-[0.85em] ml-auto shrink-0">cached {status.last_cached.slice(0, 19)}</span>}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Cross-Provider Data Confidence"
        right={
          confidence.data
            ? `${confidence.data.n} check(s) · ${confidence.data.material_disagreements} material`
            : undefined
        }
      >
        {confidence.data && confidence.data.checks.length > 0 ? (
          <>
            <DataTable columns={confidenceColumns} rows={confidence.data.checks} rowKey={(c) => `${c.ts}-${c.symbol}`} />
            <p className="px-4 py-2 text-[0.72em] text-muted border-t border-border">{confidence.data.note}</p>
          </>
        ) : (
          <Empty>
            {confidence.loading
              ? 'Loading...'
              : 'No checks recorded yet. Enable features.data_confidence_check in config.yaml — the scheduler then cross-checks ONE symbol per run (round-robin) between the top two providers in its chain (~1-2 extra provider calls per run). Reading this panel never triggers fetches.'}
          </Empty>
        )}
      </Panel>
    </div>
  )
}

const confidenceColumns: Column<DataConfidenceCheck>[] = [
  { header: 'When (UTC)', render: (c) => c.ts.slice(0, 19).replace('T', ' ') },
  { header: 'Symbol', render: (c) => <span className="text-accent font-bold">{c.symbol}</span> },
  { header: 'TF', render: (c) => c.interval },
  { header: 'Providers', render: (c) => `${c.provider_a ?? '?'} vs ${c.provider_b ?? '?'}` },
  { header: 'Bars', render: (c) => c.bars_common ?? '—', align: 'right' },
  { header: 'Mean Δ%', render: (c) => (c.mean_diff_pct != null ? c.mean_diff_pct.toFixed(4) : '—'), align: 'right' },
  { header: 'Max Δ%', render: (c) => (c.max_diff_pct != null ? c.max_diff_pct.toFixed(3) : '—'), align: 'right' },
  {
    header: 'Verdict',
    render: (c) => (
      <Badge tone={c.verdict.startsWith('MATERIAL') ? 'poor' : c.verdict.startsWith('MINOR') ? 'marginal' : 'good'}>
        {c.verdict}
      </Badge>
    ),
  },
]
