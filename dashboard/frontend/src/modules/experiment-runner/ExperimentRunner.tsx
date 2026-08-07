import { useEffect, useRef, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { getJobCatalog, getJobList, getJobDetail, runJob, cancelJob, type JobCatalogResponse, type JobSummary, type JobDetail, type JobStatus } from './api'
import { getResearchSymbols, type SymbolsResponse } from '../research-backtests/api'

const POLL_MS = 3_000

const STATUS_TONE: Record<JobStatus, 'exec' | 'no-trade' | 'good' | 'marginal' | 'neutral'> = {
  queued: 'neutral',
  running: 'marginal',
  finished: 'exec',
  failed: 'no-trade',
  timeout: 'no-trade',
  cancelled: 'no-trade',
}

// Symbol picker for the backtest/walk_forward/robustness job rows below
// (2026-07-26) — replaces a free-text "type your symbols" input with real,
// currently-configured symbols from getResearchSymbols(), the same source
// BacktestingLab.tsx's SymbolsStep uses. Self-contained (no new dependency
// — this app has no popover/combobox primitive yet, and one purpose-built
// dropdown for this single screen doesn't warrant adding one).
function SymbolMultiSelect({
  value,
  onChange,
  symbolsData,
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
        {value.length === 0 ? <span className="text-muted">Select symbols…</span> : value.join(', ')}
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

export function ExperimentRunner() {
  const { markUnauthenticated } = useAuth()
  const [catalog, setCatalog] = useState<JobCatalogResponse | null>(null)
  const [starting, setStarting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [cancelling, setCancelling] = useState<string | null>(null)

  const jobs = usePolling(() => getJobList(), POLL_MS, markUnauthenticated)
  const symbolsCatalog = usePolling(getResearchSymbols, POLL_MS, markUnauthenticated)

  useEffect(() => {
    getJobCatalog()
      .then(setCatalog)
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!viewing) return
    let cancelled = false
    const tick = () => {
      getJobDetail(viewing)
        .then((d) => !cancelled && setDetail(d))
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [viewing])

  const runningJobNames = new Set((jobs.data?.jobs ?? []).filter((j) => j.status === 'queued' || j.status === 'running').map((j) => j.job))
  // Per-job symbols text for jobs flagged requires_symbols (backtest).
  const [symbolsInput, setSymbolsInput] = useState<Record<string, string[]>>({})

  const start = async (jobId: string, symbols?: string[]) => {
    setStarting(jobId)
    setError(null)
    try {
      const summary = await runJob(jobId, symbols)
      jobs.refetch()
      setViewing(summary.job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(null)
    }
  }

  const cancel = async (runId: string) => {
    setCancelling(runId)
    setError(null)
    try {
      const summary = await cancelJob(runId)
      jobs.refetch()
      if (viewing === runId) setDetail((d) => (d ? { ...d, status: summary.status } : d))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCancelling(null)
    }
  }

  const columns: Column<JobSummary>[] = [
    { header: 'Job', render: (j) => <span className="font-bold text-accent">{j.job}</span> },
    { header: 'Status', render: (j) => <Badge tone={STATUS_TONE[j.status]}>{j.status}</Badge> },
    { header: 'Started', render: (j) => <span className="text-muted">{j.started_at ? j.started_at.slice(11, 19) : '—'}</span> },
    { header: 'Finished', render: (j) => <span className="text-muted">{j.finished_at ? j.finished_at.slice(11, 19) : '—'}</span> },
    { header: 'Exit code', render: (j) => j.returncode ?? '—', align: 'right' },
    {
      header: 'Log',
      render: (j) => (
        <div className="flex items-center gap-3 justify-end">
          {(j.status === 'queued' || j.status === 'running') && (
            <button
              onClick={() => cancel(j.job_id)}
              disabled={cancelling === j.job_id}
              className="text-red hover:text-red/80 text-[0.85em] underline decoration-dotted disabled:opacity-50 px-2 py-1.5"
            >
              {cancelling === j.job_id ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          <button onClick={() => setViewing(j.job_id)} className="text-accent hover:text-accent2 text-[0.85em] underline decoration-dotted px-2 py-1.5">
            View
          </button>
        </div>
      ),
      align: 'right',
    },
  ]

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Available Jobs" right="whitelisted only — no arbitrary shell">
        {error && <div className="px-4 py-2 text-[0.8em] text-red border-b border-border">{error}</div>}
        {catalog && catalog.jobs.length > 0 ? (
          <div className="divide-y divide-border">
            {catalog.jobs
              .filter((j) => j.category === 'research')
              .map((job) => {
              const isRunning = runningJobNames.has(job.id)
              const symbols = symbolsInput[job.id] ?? []
              const needsSymbols = !!job.requires_symbols
              return (
                <div key={job.id} className="px-4 py-3 flex items-center justify-between gap-4 flex-wrap">
                  <div className="min-w-[200px] flex-1">
                    <div className="text-[0.85em] font-bold text-text">{job.id}</div>
                    <div className="text-[0.78em] text-muted">{job.description}</div>
                  </div>
                  {needsSymbols && (
                    <SymbolMultiSelect
                      value={symbols}
                      onChange={(syms) => setSymbolsInput((m) => ({ ...m, [job.id]: syms }))}
                      symbolsData={symbolsCatalog.data}
                    />
                  )}
                  <button
                    onClick={() => start(job.id, needsSymbols ? symbols : undefined)}
                    disabled={isRunning || starting === job.id || (needsSymbols && symbols.length === 0)}
                    title={needsSymbols && symbols.length === 0 ? 'Enter at least one symbol first' : undefined}
                    className="px-3 py-1.5 text-[0.78em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
                  >
                    {isRunning ? 'Running…' : starting === job.id ? 'Starting…' : 'Run'}
                  </button>
                </div>
              )
            })}
          </div>
        ) : (
          <Empty>Loading job catalog...</Empty>
        )}
      </Panel>

      <Panel title="Job History" right={jobs.data ? `${jobs.data.jobs.length} runs` : undefined}>
        {jobs.data && jobs.data.jobs.length > 0 ? (
          <DataTable columns={columns} rows={jobs.data.jobs} rowKey={(j) => j.job_id} />
        ) : (
          <Empty>{jobs.loading ? 'Loading...' : 'No jobs run yet'}</Empty>
        )}
      </Panel>

      {viewing && detail && (
        <Panel
          title={`Log — ${detail.job} (${detail.job_id})`}
          right={
            <div className="flex items-center gap-3">
              <Badge tone={STATUS_TONE[detail.status]}>{detail.status}</Badge>
              {(detail.status === 'queued' || detail.status === 'running') && (
                <button
                  onClick={() => cancel(detail.job_id)}
                  disabled={cancelling === detail.job_id}
                  className="text-red hover:text-red/80 text-[0.85em] underline decoration-dotted disabled:opacity-50 px-2 py-1.5"
                >
                  {cancelling === detail.job_id ? 'Cancelling…' : 'Cancel'}
                </button>
              )}
              <button onClick={() => setViewing(null)} className="text-muted hover:text-text px-2 py-1.5">
                ✕ close
              </button>
            </div>
          }
        >
          {detail.log.length > 0 ? (
            <pre className="p-4 text-[0.78em] leading-relaxed overflow-auto max-h-[500px] whitespace-pre-wrap break-words font-mono">
              {detail.log.join('\n')}
            </pre>
          ) : (
            <Empty>No output yet</Empty>
          )}
        </Panel>
      )}
    </div>
  )
}
