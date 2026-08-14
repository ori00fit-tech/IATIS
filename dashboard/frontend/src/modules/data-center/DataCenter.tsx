import { useEffect, useRef, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import type { CacheStatus, DataConfidenceCheck, WarehouseManifestEntry } from './api'
import {
  getDataConfidence, getDataHealth, getWarehouseManifest,
  runDownloadHistoryJob, runPushToWarehouseJob, DOWNLOAD_TIMEFRAMES,
} from './api'
import { DataTable, type Column } from '../../components/DataTable'
import { getProviderChains } from '../system-audit/api'
import { getJobDetail, runJob, type JobDetail, type JobStatus } from '../experiment-runner/api'
import { getResearchSymbols, type SymbolsResponse } from '../research-backtests/api'
import { reportDownloadUrl } from '../reports/api'

const POLL_MS = 60_000
const JOB_POLL_MS = 3_000

const CELL_CLASS: Record<CacheStatus, string> = {
  OK: 'bg-green/15 text-green',
  STALE: 'bg-amber/15 text-amber',
  GAPS: 'bg-amber/25 text-amber',
  STARVED: 'bg-red/25 text-red',
  MISSING: 'bg-red/15 text-red',
}

const JOB_BADGE: Record<JobStatus, 'exec' | 'no-trade' | 'good' | 'marginal' | 'neutral'> = {
  queued: 'neutral',
  running: 'marginal',
  finished: 'exec',
  failed: 'no-trade',
  timeout: 'no-trade',
  cancelled: 'no-trade',
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

const DATASET_STATUS_CLASS: Record<string, string> = {
  READY: 'bg-green/15 text-green',
  PENDING: 'bg-amber/15 text-amber',
  INCOMPLETE: 'bg-amber/25 text-amber',
  INVALID: 'bg-red/25 text-red',
  EMPTY: 'bg-red/15 text-red',
}

// Simple, self-contained symbol multi-select for the "deepen" action below
// — mirrors ExperimentRunner.tsx's own SymbolMultiSelect pattern (this app
// has no shared popover/combobox primitive; a small purpose-built dropdown
// per screen is the established convention here), not extracted into a
// shared component since ExperimentRunner's version is private to that file.
function DeepenSymbolPicker({
  value, onChange, symbolsData,
}: {
  value: string[]
  onChange: (symbols: string[]) => void
  symbolsData: SymbolsResponse | null
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [open])

  const allEntries = symbolsData ? Object.values(symbolsData.asset_classes).flat() : []
  const filtered = allEntries.filter((s) => s.internal.toLowerCase().includes(search.toLowerCase()))
  const toggle = (sym: string) => onChange(value.includes(sym) ? value.filter((s) => s !== sym) : [...value, sym])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-56 text-left truncate"
      >
        {value.length === 0 ? <span className="text-muted">Select symbol(s)…</span> : value.join(', ')}
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-72 max-h-72 overflow-y-auto bg-panel border border-border rounded shadow-md p-2 flex flex-col gap-2">
          <input
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            className="px-2 py-1 text-[0.78em] rounded border border-border bg-bg text-text"
          />
          <div className="flex flex-col gap-0.5">
            {filtered.map((s) => (
              <label key={s.internal} className="flex items-center gap-2 px-2 py-2 rounded hover:bg-surface cursor-pointer text-[0.78em]">
                <input type="checkbox" checked={value.includes(s.internal)} onChange={() => toggle(s.internal)} />
                <span className="font-mono">{s.internal}</span>
              </label>
            ))}
            {filtered.length === 0 && <span className="text-muted text-[0.78em] px-1.5 py-1">No matches</span>}
          </div>
        </div>
      )}
    </div>
  )
}

function DeepenAction({ onPushed }: { onPushed: () => void }) {
  const { markUnauthenticated } = useAuth()
  const symbolsCatalog = usePolling(getResearchSymbols, POLL_MS, markUnauthenticated)
  const [symbols, setSymbols] = useState<string[]>([])
  const [timeframe, setTimeframe] = useState<string>('H1')
  const [years, setYears] = useState<number>(2)
  const [downloadJob, setDownloadJob] = useState<JobDetail | null>(null)
  const [pushJob, setPushJob] = useState<JobDetail | null>(null)
  const [starting, setStarting] = useState<'download' | 'push' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!downloadJob || downloadJob.status === 'finished' || downloadJob.status === 'failed' || downloadJob.status === 'timeout' || downloadJob.status === 'cancelled') return
    const id = setInterval(() => { getJobDetail(downloadJob.job_id).then(setDownloadJob).catch(() => {}) }, JOB_POLL_MS)
    return () => clearInterval(id)
  }, [downloadJob])

  useEffect(() => {
    if (!pushJob || pushJob.status === 'finished' || pushJob.status === 'failed' || pushJob.status === 'timeout' || pushJob.status === 'cancelled') return
    const id = setInterval(() => {
      getJobDetail(pushJob.job_id).then((d) => {
        setPushJob(d)
        if (d.status === 'finished') onPushed()
      }).catch(() => {})
    }, JOB_POLL_MS)
    return () => clearInterval(id)
  }, [pushJob, onPushed])

  const download = async () => {
    if (symbols.length === 0) return
    setStarting('download')
    setError(null)
    try {
      const summary = await runDownloadHistoryJob(symbols, timeframe, years)
      setDownloadJob({ ...summary, log: [] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(null)
    }
  }

  const push = async () => {
    if (symbols.length === 0) return
    setStarting('push')
    setError(null)
    try {
      const summary = await runPushToWarehouseJob(symbols)
      setPushJob({ ...summary, log: [] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(null)
    }
  }

  const downloadRunning = downloadJob?.status === 'queued' || downloadJob?.status === 'running'
  const pushRunning = pushJob?.status === 'queued' || pushJob?.status === 'running'

  return (
    <div className="flex flex-col gap-2 px-4 py-3 border-b border-border bg-surface/40">
      <div className="flex items-center gap-2 flex-wrap">
        <DeepenSymbolPicker value={symbols} onChange={setSymbols} symbolsData={symbolsCatalog.data} />
        <select
          value={timeframe}
          onChange={(e) => setTimeframe(e.target.value)}
          className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text"
        >
          {DOWNLOAD_TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
        </select>
        <input
          type="number" min={0.1} max={20} step={0.5} value={years}
          onChange={(e) => setYears(Number(e.target.value))}
          title="years of history to fetch"
          className="w-20 bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text"
        />
        <span className="text-muted text-[0.75em]">years</span>
        <button
          onClick={download}
          disabled={symbols.length === 0 || starting === 'download' || downloadRunning}
          className="px-3 py-1.5 text-[0.78em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50"
        >
          {downloadRunning ? 'Downloading…' : '1. Download (Dukascopy)'}
        </button>
        <button
          onClick={push}
          disabled={symbols.length === 0 || starting === 'push' || pushRunning}
          className="px-3 py-1.5 text-[0.78em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50"
        >
          {pushRunning ? 'Pushing…' : '2. Push to warehouse'}
        </button>
        {downloadJob && <Badge tone={JOB_BADGE[downloadJob.status]}>{`download: ${downloadJob.status}`}</Badge>}
        {pushJob && <Badge tone={JOB_BADGE[pushJob.status]}>{`push: ${pushJob.status}`}</Badge>}
        {error && <span className="text-red text-[0.78em]">{error}</span>}
      </div>
      <p className="text-[0.7em] text-muted">
        Dukascopy only — free, credential-free historical feed. cTrader enforces a single-session-per-account
        limit and would race the live scheduler's own connection, so cTrader-sourced deepening stays a VPS
        CLI-only operation (stop the scheduler first). Download writes a local CSV; Push writes it into the
        D1 warehouse (native H4/D1 files are preferred over resampling H1) — run Download, then Push.
      </p>
      {(downloadJob?.log.length ?? 0) > 0 && (
        <pre className="p-3 bg-bg/60 rounded text-[0.72em] overflow-auto max-h-[160px] whitespace-pre-wrap break-words font-mono">
          {downloadJob!.log.join('\n')}
        </pre>
      )}
      {(pushJob?.log.length ?? 0) > 0 && (
        <pre className="p-3 bg-bg/60 rounded text-[0.72em] overflow-auto max-h-[160px] whitespace-pre-wrap break-words font-mono">
          {pushJob!.log.join('\n')}
        </pre>
      )}
    </div>
  )
}

const warehouseColumns: Column<WarehouseManifestEntry>[] = [
  { header: 'Symbol', render: (d) => <span className="text-accent font-bold">{d.symbol}</span> },
  { header: 'TF', render: (d) => d.timeframe },
  {
    header: 'Status',
    render: (d) => (
      <span className={`inline-block px-2 py-1 rounded text-[0.85em] font-bold ${DATASET_STATUS_CLASS[d.status] ?? ''}`}>
        {d.status}
      </span>
    ),
  },
  { header: 'Rows', render: (d) => d.row_count.toLocaleString(), align: 'right' },
  { header: 'Coverage', render: (d) => (d.coverage_pct != null ? `${d.coverage_pct.toFixed(1)}%` : '—'), align: 'right' },
  {
    header: 'Source',
    render: (d) => (
      <span title={d.native ? 'genuinely native download' : 'derived by resampling H1'}>
        {d.source ?? '—'} {d.native ? '' : '(resampled)'}
      </span>
    ),
  },
  { header: 'Last updated', render: (d) => d.last_updated?.slice(0, 19).replace('T', ' ') ?? '—' },
]

function WarehousePanel() {
  const { markUnauthenticated } = useAuth()
  const warehouse = usePolling(getWarehouseManifest, POLL_MS, markUnauthenticated)

  return (
    <Panel
      title="Trusted Data Center — Warehouse"
      right={warehouse.data ? `${warehouse.data.datasets.length} dataset(s) · checked ${new Date(warehouse.data.checked_at).toLocaleTimeString()}` : undefined}
    >
      <DeepenAction onPushed={() => warehouse.refetch()} />
      {warehouse.error ? (
        <Empty>Could not load warehouse manifest</Empty>
      ) : !warehouse.data || warehouse.data.datasets.length === 0 ? (
        <Empty>{warehouse.loading ? 'Loading...' : 'Nothing pushed yet — deepen a symbol above, then push it to the warehouse.'}</Empty>
      ) : (
        <DataTable columns={warehouseColumns} rows={warehouse.data.datasets} rowKey={(d) => `${d.symbol}-${d.timeframe}`} />
      )}
    </Panel>
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
        <KpiCard value={data?.summary.starved ?? '—'} label="Starved" color="red" />
        <KpiCard value={data?.summary.missing ?? '—'} label="Missing" color="red" />
      </div>

      <WarehousePanel />

      <Panel
        title="Live Feed Health"
        right={
          data
            ? `from decision provenance · checked ${new Date(data.checked_at).toLocaleTimeString()}`
            : undefined
        }
      >
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
                                ? `provider: ${cell.provider ?? '?'} · ${cell.bars} bars\n` +
                                  `last bar: ${cell.last_bar_time} (decision ${cell.age_minutes}m ago)\n` +
                                  `STARVED = below engine minimums (210 decision-TF / 50 D1 bars) — the silent-degradation class`
                                : 'no provenance-carrying decision yet for this symbol'
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
