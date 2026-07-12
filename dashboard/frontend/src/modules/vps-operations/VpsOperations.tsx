import { useEffect, useState } from 'react'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { StatusRow } from '../../components/StatusDot'
import { getHealthFull, type HealthFull } from '../mission-control/api'
import { getJobCatalog, getJobDetail, runJob, type JobDescriptor, type JobDetail, type JobStatus } from '../experiment-runner/api'
import { reloadConfig } from './api'

const POLL_MS = 3_000

const STATUS_TONE: Record<JobStatus, 'exec' | 'no-trade' | 'good' | 'marginal' | 'neutral'> = {
  queued: 'neutral',
  running: 'marginal',
  finished: 'exec',
  failed: 'no-trade',
  timeout: 'no-trade',
}

function DiagnosticsPanel() {
  const [data, setData] = useState<HealthFull | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      setData(await getHealthFull())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Panel
      title="Diagnostics / Health Check"
      right={
        <button
          onClick={run}
          disabled={running}
          className="px-3 py-1 text-[0.8em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50"
        >
          {running ? 'Checking…' : data ? 'Re-check' : 'Run diagnostics'}
        </button>
      }
    >
      {error ? (
        <Empty>Failed: {error}</Empty>
      ) : !data ? (
        <Empty>Read-only snapshot of GET /health/full — CPU/RAM/disk, scheduler, DB, calendar, providers, cTrader.</Empty>
      ) : (
        <div>
          <StatusRow label="Overall" state={data.status === 'healthy' ? 'ok' : 'warn'} detail={data.status} />
          {data.system && (
            <StatusRow
              label="System"
              state={data.system.error ? 'err' : 'ok'}
              detail={data.system.error ?? `CPU ${data.system.cpu_pct}% · RAM ${data.system.ram_pct}% · Disk ${data.system.disk_pct}%`}
            />
          )}
          {data.scheduler && (
            <StatusRow
              label="Scheduler"
              state={data.scheduler.status === 'running' ? 'ok' : 'warn'}
              detail={data.scheduler.last_run ?? 'no recent run found'}
            />
          )}
          {data.database && (
            <StatusRow
              label="Database"
              state={data.database.status === 'ok' ? 'ok' : 'err'}
              detail={data.database.error ?? `${data.database.total_decisions ?? 0} decisions`}
            />
          )}
          {data.ctrader && (
            <StatusRow label="cTrader" state={data.ctrader.configured ? 'ok' : 'warn'} detail={data.ctrader.configured ? 'configured' : 'not configured'} />
          )}
          {data.issues.length > 0 && (
            <div className="px-3 py-2 text-[0.78em] text-amber">{data.issues.join(' · ')}</div>
          )}
        </div>
      )}
    </Panel>
  )
}

function ReloadConfigPanel() {
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    setMessage(null)
    try {
      const r = await reloadConfig()
      setMessage(r.message)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Panel
      title="Reload Config"
      right={
        <button
          onClick={run}
          disabled={running}
          className="px-3 py-1 text-[0.8em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50"
        >
          {running ? 'Reloading…' : 'Reload config.yaml'}
        </button>
      }
    >
      <div className="px-4 py-3 text-[0.82em]">
        {error ? <span className="text-red">{error}</span> : message ? <span className="text-green">{message}</span> : (
          <span className="text-muted">
            Clears the in-process config.yaml cache so the next request reloads it from disk. Does not change any threshold/engine/trading value —
            only cache staleness.
          </span>
        )}
      </div>
    </Panel>
  )
}

function BackupPanel() {
  const [job, setJob] = useState<JobDescriptor | null>(null)
  const [starting, setStarting] = useState(false)
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getJobCatalog()
      .then((r) => setJob(r.jobs.find((j) => j.category === 'ops') ?? null))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!detail || detail.status === 'finished' || detail.status === 'failed' || detail.status === 'timeout') return
    const id = setInterval(() => {
      getJobDetail(detail.job_id)
        .then(setDetail)
        .catch(() => {})
    }, POLL_MS)
    return () => clearInterval(id)
  }, [detail])

  const start = async () => {
    if (!job) return
    setStarting(true)
    setError(null)
    try {
      const summary = await runJob(job.id)
      setDetail({ ...summary, log: [] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  return (
    <Panel
      title="Backup"
      right={
        job && (
          <button
            onClick={start}
            disabled={starting || detail?.status === 'queued' || detail?.status === 'running'}
            className="px-3 py-1 text-[0.8em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50"
          >
            {detail?.status === 'queued' || detail?.status === 'running' ? 'Running…' : 'Trigger backup now'}
          </button>
        )
      }
    >
      <div className="px-4 py-3 text-[0.82em] text-muted">{job?.description ?? 'Loading...'}</div>
      {error && <div className="px-4 py-2 text-[0.8em] text-red">{error}</div>}
      {detail && (
        <div className="px-4 pb-4">
          <div className="flex items-center gap-2 mb-2">
            <Badge tone={STATUS_TONE[detail.status]}>{detail.status}</Badge>
            {detail.returncode !== null && <span className="text-muted text-[0.78em]">exit {detail.returncode}</span>}
          </div>
          {detail.log.length > 0 && (
            <pre className="p-3 bg-surface/60 rounded text-[0.75em] overflow-auto max-h-[300px] whitespace-pre-wrap break-words font-mono">
              {detail.log.join('\n')}
            </pre>
          )}
        </div>
      )}
    </Panel>
  )
}

export function VpsOperations() {
  return (
    <div className="flex flex-col gap-4">
      <DiagnosticsPanel />
      <ReloadConfigPanel />
      <BackupPanel />
    </div>
  )
}
