import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { getDecisions, getOutcomes, type DecisionEntry, type OpenSignal } from './api'

const POLL_MS = 18_000

function scoreColor(score: number) {
  return score >= 65 ? 'text-green' : score >= 55 ? 'text-amber' : 'text-red'
}

export function LiveSignals() {
  const { markUnauthenticated } = useAuth()
  const decisions = usePolling(() => getDecisions(30), POLL_MS, markUnauthenticated)
  const outcomes = usePolling(() => getOutcomes(20), POLL_MS, markUnauthenticated)

  const s = decisions.data?.summary
  const total = s?.total ?? 0
  const exec = s?.execute ?? 0
  const execRate = total > 0 ? `${((exec / total) * 100).toFixed(1)}%` : '—'

  const decisionColumns: Column<DecisionEntry>[] = [
    { header: 'Time', render: (d) => <span className="text-muted">{d.timestamp.slice(11, 19)}</span> },
    { header: 'Symbol', render: (d) => <span className="font-bold text-accent">{d.symbol}</span> },
    { header: 'Verdict', render: (d) => <Badge tone={d.final_verdict === 'EXECUTE' ? 'exec' : 'no-trade'}>{d.final_verdict}</Badge> },
    { header: 'Regime', render: (d) => d.report?.regime?.state ?? '—' },
    {
      header: 'Score',
      render: (d) => <span className={`font-bold ${scoreColor(d.report?.confluence?.score ?? 0)}`}>{(d.report?.confluence?.score ?? 0).toFixed(0)}</span>,
      align: 'right',
    },
    {
      header: 'Summary',
      render: (d) => (
        <span className="text-muted text-[0.92em] block max-w-[280px] truncate" title={d.report?.summary}>
          {d.report?.summary ?? ''}
        </span>
      ),
    },
  ]

  const openColumns: Column<OpenSignal>[] = [
    { header: 'Signal', render: (o) => <span className="text-muted text-[0.85em]">{o.signal_id}</span> },
    { header: 'Symbol', render: (o) => <span className="font-bold text-accent">{o.symbol}</span> },
    {
      header: 'Direction',
      render: (o) => <span className={`font-bold ${o.direction === 'BULLISH' || o.direction === 'BUY' ? 'text-green' : 'text-red'}`}>{o.direction}</span>,
    },
    { header: 'Entry', render: (o) => o.entry_price ?? '—', align: 'right' },
    { header: 'Score', render: (o) => <span className={scoreColor(o.cf_score)}>{o.cf_score}</span>, align: 'right' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={total} label="Total Decisions" color="blue" />
        <KpiCard value={exec} label="EXECUTE" color="green" />
        <KpiCard value={total - exec} label="NO_TRADE" color="default" />
        <KpiCard value={execRate} label="Execute Rate" color="purple" />
        <KpiCard value={outcomes.data?.summary.open_signals ?? '—'} label="Open Signals" color="amber" />
        <KpiCard value={outcomes.data?.summary.win_rate != null ? `${outcomes.data.summary.win_rate.toFixed(1)}%` : '—'} label="Win Rate" color="green" />
      </div>

      <Panel title="Recent Decisions" right={decisions.data ? `${decisions.data.total_in_log} logged` : undefined}>
        {decisions.data && decisions.data.decisions.length > 0 ? (
          <DataTable columns={decisionColumns} rows={decisions.data.decisions} rowKey={(d) => `${d.timestamp}-${d.symbol}`} />
        ) : (
          <Empty>{decisions.loading ? 'Loading...' : 'No decisions logged yet'}</Empty>
        )}
      </Panel>

      <Panel title="Open Signals" right="Paper trading">
        {outcomes.data && outcomes.data.open_signals.length > 0 ? (
          <DataTable columns={openColumns} rows={outcomes.data.open_signals} rowKey={(o) => o.signal_id} />
        ) : (
          <Empty>{outcomes.loading ? 'Loading...' : 'No open signals'}</Empty>
        )}
      </Panel>
    </div>
  )
}
