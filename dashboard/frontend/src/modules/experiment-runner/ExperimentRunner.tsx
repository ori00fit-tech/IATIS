import { useEffect, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { getJobCatalog, getJobList, getJobDetail, runJob, type JobCatalogResponse, type JobSummary, type JobDetail, type JobStatus } from './api'

const POLL_MS = 3_000

const STATUS_TONE: Record<JobStatus, 'exec' | 'no-trade' | 'good' | 'marginal' | 'neutral'> = {
  queued: 'neutral',
  running: 'marginal',
  finished: 'exec',
  failed: 'no-trade',
  timeout: 'no-trade',
}

export function ExperimentRunner() {
  const { markUnauthenticated } = useAuth()
  const [catalog, setCatalog] = useState<JobCatalogResponse | null>(null)
  const [starting, setStarting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)
  const [detail, setDetail] = useState<JobDetail | null>(null)

  const jobs = usePolling(() => getJobList(), POLL_MS, markUnauthenticated)

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

  const start = async (jobId: string) => {
    setStarting(jobId)
    setError(null)
    try {
      const summary = await runJob(jobId)
      jobs.refetch()
      setViewing(summary.job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(null)
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
        <button onClick={() => setViewing(j.job_id)} className="text-accent hover:text-accent2 text-[0.85em] underline decoration-dotted">
          View
        </button>
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
            {catalog.jobs.map((job) => {
              const isRunning = runningJobNames.has(job.id)
              return (
                <div key={job.id} className="px-4 py-3 flex items-center justify-between gap-4">
                  <div>
                    <div className="text-[0.85em] font-bold text-text">{job.id}</div>
                    <div className="text-[0.78em] text-muted">{job.description}</div>
                  </div>
                  <button
                    onClick={() => start(job.id)}
                    disabled={isRunning || starting === job.id}
                    className="px-3 py-1.5 text-[0.78em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50 disabled:cursor-wait shrink-0"
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
            <div className="flex items-center gap-2">
              <Badge tone={STATUS_TONE[detail.status]}>{detail.status}</Badge>
              <button onClick={() => setViewing(null)} className="text-muted hover:text-text">
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
