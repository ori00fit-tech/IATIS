import { useEffect, useRef, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { DataTable, type Column } from '../../components/DataTable'
import {
  createEngineBenchmark,
  getEngineBenchmark,
  getEngineBenchmarkResults,
  cancelEngineBenchmark,
  type EngineBenchmarkProfile,
  type EngineBenchmarkResultRow,
} from './engineBenchmarkApi'
import { getEngineStats, type EngineAttribution } from '../engine-monitor/api'

const POLL_MS = 2_000
const ATTRIBUTION_POLL_MS = 45_000

function formatPct(value: number | null): string {
  return value != null ? `${(value * 100).toFixed(1)}%` : '—'
}

function formatNum(value: number | null, digits = 2): string {
  if (value === null) return '—'
  if (!Number.isFinite(value)) return value > 0 ? '∞' : '-∞'
  return value.toFixed(digits)
}

function formatPf(value: number | 'Infinity' | null): string {
  if (value === null) return '—'
  if (value === 'Infinity') return '∞'
  return value.toFixed(2)
}

// ── Live Trade Attribution — reuses the EXISTING /engine-stats endpoint,
// no new backend needed. A DELIBERATELY distinct data source from the
// standalone backtest results below: this is real live/paper trading
// history, approximately joined to engine votes by time-proximity (see
// storage/engine_tracker.py's own "APPROXIMATE" note), not a controlled
// ablation experiment.
function LiveAttributionPanel() {
  const { markUnauthenticated } = useAuth()
  const { data } = usePolling(getEngineStats, ATTRIBUTION_POLL_MS, markUnauthenticated)
  const engines: EngineAttribution[] = data?.attribution.engines ?? []

  const columns: Column<EngineAttribution>[] = [
    { header: 'Engine', render: (e) => <span className="font-bold text-accent">{e.engine}</span> },
    { header: 'Matched trades', render: (e) => e.matched_trades, align: 'right' },
    { header: 'W / L', render: (e) => `${e.wins} / ${e.losses}`, align: 'right' },
    { header: 'Win rate', render: (e) => (e.win_rate != null ? `${e.win_rate.toFixed(1)}%` : '—'), align: 'right' },
    { header: 'Profit factor', render: (e) => formatPf(e.profit_factor), align: 'right' },
    {
      header: 'Direction agreement',
      render: (e) => (e.direction_agreement_pct != null ? `${e.direction_agreement_pct.toFixed(0)}%` : '—'),
      align: 'right',
    },
  ]

  return (
    <Panel
      title="Live Trade Attribution"
      right={data ? `${data.attribution.matched_trades}/${data.attribution.total_closed_trades} closed trades matched` : ''}
    >
      <p className="px-4 py-2 text-[0.75em] text-muted border-b border-border">
        Real live/paper trading history — each engine credited with the win/loss of every closed trade it voted near
        in time (approximate, time-proximity join — see the note below). A completely different data source from the
        standalone backtest table above: this reflects the live multi-engine ensemble's actual outcomes, not an
        isolated single-engine simulation.
      </p>
      {data && <p className="px-4 py-1.5 text-[0.7em] text-muted/70 border-b border-border">{data.attribution.note}</p>}
      {engines.length > 0 ? (
        <DataTable columns={columns} rows={engines} rowKey={(e) => e.engine} />
      ) : (
        <Empty>No matched trades yet — needs closed outcomes with engine votes recorded nearby in time.</Empty>
      )}
    </Panel>
  )
}

// ── Standalone-engine-ablation backtest benchmark ───────────────────

function BacktestBenchmarkPanel() {
  const [runId, setRunId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<string | null>(null)
  const [results, setResults] = useState<EngineBenchmarkResultRow[] | null>(null)
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

  const start = async (profile: EngineBenchmarkProfile) => {
    setStarting(true)
    setError(null)
    setResults(null)
    try {
      const res = await createEngineBenchmark({ profile })
      setRunId(res.run_id)
      setJobStatus(res.status)
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const status = await getEngineBenchmark(res.run_id)
          setJobStatus(status.job_status)
          if (status.job_status && !['queued', 'running'].includes(status.job_status)) {
            stopPolling()
            const r = await getEngineBenchmarkResults(res.run_id)
            setResults(r.results)
          }
        } catch (e) {
          stopPolling()
          setError(e instanceof Error ? e.message : String(e))
        }
      }, POLL_MS)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!runId) return
    try {
      await cancelEngineBenchmark(runId)
    } catch {
      // best-effort — the poll loop above will surface the real terminal state
    }
  }

  const running = jobStatus === 'queued' || jobStatus === 'running'

  // Deliberately NO default sort and NO "best engine" highlight — every
  // column is independently sortable (a mechanical numeric order, not a
  // ranking judgment) and the table always starts in run order. See
  // backtest/engine_benchmark.py's own module docstring for why this
  // benchmark is never allowed to rank or auto-select an engine.
  const columns: Column<EngineBenchmarkResultRow>[] = [
    { header: 'Engine', render: (r) => <span className="font-bold text-accent">{r.engine}</span> },
    { header: 'Symbol', render: (r) => r.symbol },
    {
      header: 'Trades',
      align: 'right',
      accessorFn: (r) => r.total_trades,
      render: (r) => (r.run_ok ? r.total_trades : <span className="text-red">FAIL</span>),
    },
    { header: 'Win Rate', align: 'right', accessorFn: (r) => r.win_rate ?? -1, render: (r) => formatPct(r.win_rate) },
    { header: 'Profit Factor', align: 'right', accessorFn: (r) => r.profit_factor ?? -1, render: (r) => formatNum(r.profit_factor) },
    { header: 'Sharpe', align: 'right', accessorFn: (r) => r.sharpe_ratio ?? -999, render: (r) => formatNum(r.sharpe_ratio) },
    { header: 'Max DD %', align: 'right', accessorFn: (r) => r.max_drawdown ?? -1, render: (r) => formatNum(r.max_drawdown, 1) },
    { header: 'Expectancy (R)', align: 'right', accessorFn: (r) => r.expectancy_r ?? -999, render: (r) => formatNum(r.expectancy_r, 3) },
    {
      header: 'Status',
      render: (r) => (r.run_ok ? <span className="text-muted">ok</span> : <span className="text-red">{r.error ?? 'failed'}</span>),
    },
  ]

  return (
    <Panel title="Standalone-Engine Ablation Backtest" right="advisory — never writes config.yaml or config/engines.yaml">
      <div className="p-4 flex flex-col gap-4">
        <div className="rounded border border-amber/40 bg-amber/10 px-3 py-2.5 text-[0.78em] text-amber leading-relaxed">
          <strong>EXPLORATORY — NOT EVIDENCE, AND NOT A RANKING.</strong> Each row is a real backtest of ONE engine run
          completely alone, with the live confluence quorum (normally 2+ engines must agree) overridden to 1 — the
          only way a single engine can ever produce a trade at all. This measures &ldquo;how does this engine&rsquo;s
          own signal perform in isolation,&rdquo; a different question from &ldquo;how does it contribute to the live
          multi-engine ensemble,&rdquo; and results here are <strong>not directly comparable</strong> to live/paper
          performance. This table never sorts to a &ldquo;best engine&rdquo; by default and no score here feeds any
          promotion decision — enabling, disabling, or re-weighting an engine still requires a pre-registered
          hypothesis run through Mission Center&rsquo;s own Validation pipeline. See CLAUDE.md&rsquo;s dead list:
          &ldquo;Enabling more engines (any)&rdquo; — every addition dilutes; subset selection is universe-dependent
          noise.
        </div>

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
          <DataTable columns={columns} rows={results} rowKey={(r) => `${r.engine}:${r.symbol}`} />
        )}
      </div>
    </Panel>
  )
}

export function EngineBenchmark() {
  return (
    <div className="flex flex-col gap-4">
      <BacktestBenchmarkPanel />
      <LiveAttributionPanel />
    </div>
  )
}
