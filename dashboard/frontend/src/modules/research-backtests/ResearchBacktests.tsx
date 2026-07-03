import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { getResearch, getBacktestResults, getMetaAnalysis, type Hypothesis, type BacktestResult, type RegimeRow } from './api'

const POLL_MS = 60_000

function pfBadge(pf: number) {
  if (pf >= 1.5) return 'good' as const
  if (pf >= 1.1) return 'marginal' as const
  return 'poor' as const
}

function statusTone(status: string) {
  if (status === 'PASSED') return 'exec' as const
  if (status.includes('FAILED')) return 'no-trade' as const
  return 'neutral' as const
}

export function ResearchBacktests() {
  const { markUnauthenticated } = useAuth()
  const research = usePolling(getResearch, POLL_MS, markUnauthenticated)
  const backtests = usePolling(getBacktestResults, POLL_MS, markUnauthenticated)
  const meta = usePolling(getMetaAnalysis, POLL_MS, markUnauthenticated)

  const hs = research.data?.hypothesis_summary

  const hypothesisColumns: Column<Hypothesis>[] = [
    { header: 'ID', render: (h) => <span className="font-bold text-accent">{h.id}</span> },
    { header: 'Title', render: (h) => h.title },
    { header: 'Status', render: (h) => <Badge tone={statusTone(h.status)}>{h.status}</Badge> },
    { header: 'N', render: (h) => h.sample_size ?? '—', align: 'right' },
    { header: 'Win Rate', render: (h) => (h.win_rate != null ? `${h.win_rate}%` : '—'), align: 'right' },
    { header: 'p-value', render: (h) => h.p_value ?? '—', align: 'right' },
  ]

  const backtestColumns: Column<BacktestResult>[] = [
    { header: 'Symbol', render: (r) => <span className="font-bold text-accent">{r.symbol}</span> },
    { header: 'Trades', render: (r) => r.trades, align: 'right' },
    { header: 'WR%', render: (r) => r.win_rate, align: 'right' },
    { header: 'PF', render: (r) => <Badge tone={pfBadge(r.profit_factor)}>{r.profit_factor.toFixed(2)}</Badge>, align: 'right' },
    { header: 'DD%', render: (r) => <span className="text-red">{r.max_drawdown_pct}%</span>, align: 'right' },
    {
      header: 'Return%',
      render: (r) => <span className={r.total_return_pct >= 0 ? 'text-green' : 'text-red'}>{r.total_return_pct}%</span>,
      align: 'right',
    },
  ]

  const regimeColumns: Column<RegimeRow>[] = [
    { header: 'Regime', render: (r) => <span className="font-bold text-accent">{r.regime}</span> },
    { header: 'Decisions', render: (r) => r.total_decisions, align: 'right' },
    { header: 'Execute Rate', render: (r) => `${r.execute_rate}%`, align: 'right' },
    { header: 'Win Rate', render: (r) => (r.win_rate != null ? `${r.win_rate}%` : '—'), align: 'right' },
    { header: 'PF', render: (r) => r.profit_factor ?? '—', align: 'right' },
    { header: 'Expectancy $', render: (r) => r.expectancy_usd ?? '—', align: 'right' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={hs?.total ?? '—'} label="Hypotheses" color="blue" />
        <KpiCard value={hs?.passed ?? '—'} label="Passed" color="green" />
        <KpiCard value={hs?.failed ?? '—'} label="Failed" color="red" />
        <KpiCard value={hs?.research ?? '—'} label="In Research" color="amber" />
        <KpiCard value={research.data?.latest_backtest?.avg_pf?.toFixed(2) ?? '—'} label="Avg PF (latest BT)" color="purple" />
      </div>

      <Panel title="Hypothesis Registry">
        {research.data && research.data.hypotheses.length > 0 ? (
          <DataTable columns={hypothesisColumns} rows={research.data.hypotheses} rowKey={(h) => h.id} />
        ) : (
          <Empty>{research.loading ? 'Loading...' : 'No hypotheses registered yet'}</Empty>
        )}
      </Panel>

      <Panel title="Backtest Results" right={backtests.data ? `${backtests.data.count} runs` : undefined}>
        {backtests.data && backtests.data.results.length > 0 ? (
          <DataTable columns={backtestColumns} rows={backtests.data.results} rowKey={(r) => `${r.file}-${r.symbol}`} />
        ) : (
          <Empty>{backtests.loading ? 'Loading...' : 'No backtest results yet'}</Empty>
        )}
      </Panel>

      <Panel title="Regime Performance Matrix" right={meta.data?.regime_matrix.note}>
        {meta.data && meta.data.regime_matrix.data.length > 0 ? (
          <DataTable columns={regimeColumns} rows={meta.data.regime_matrix.data} rowKey={(r) => r.regime} />
        ) : (
          <Empty>{meta.loading ? 'Loading...' : 'No regime performance data yet'}</Empty>
        )}
      </Panel>
    </div>
  )
}
