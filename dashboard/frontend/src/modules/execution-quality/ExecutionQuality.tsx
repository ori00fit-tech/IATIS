import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { fmtPips, getExecutionQuality, type RecentFill, type SlippageBucket } from './api'

const POLL_MS = 30_000

type BucketRow = SlippageBucket & { name: string }

// The one comparison this whole tab exists for: is realized slippage
// inside the cost the backtest already paid for? Sustained mean above
// the assumption = the backtested edge is overstated by the difference.
function slippageVerdict(
  mean: number | undefined,
  assumption: number,
): { tone: 'good' | 'marginal' | 'poor'; label: string } {
  if (mean === undefined || mean === null) return { tone: 'marginal', label: 'no data' }
  if (mean <= assumption) return { tone: 'good', label: 'within assumption' }
  if (mean <= assumption * 2) return { tone: 'marginal', label: 'above assumption' }
  return { tone: 'poor', label: 'far above assumption' }
}

function bucketColumns(assumption: number, nameHeader: string): Column<BucketRow>[] {
  return [
    { header: nameHeader, render: (b) => <span className="text-accent font-bold">{b.name}</span> },
    { header: 'Fills', render: (b) => b.n, align: 'right' },
    { header: 'Mean (pips)', render: (b) => fmtPips(b.mean_slippage_pips), align: 'right' },
    { header: 'Median', render: (b) => fmtPips(b.median_slippage_pips), align: 'right' },
    { header: 'P90', render: (b) => fmtPips(b.p90_slippage_pips), align: 'right' },
    { header: 'Worst', render: (b) => fmtPips(b.worst_slippage_pips), align: 'right' },
    { header: 'Mean R cost', render: (b) => (b.mean_slippage_r != null ? b.mean_slippage_r.toFixed(4) : '—'), align: 'right' },
    {
      header: 'vs assumption',
      render: (b) => {
        const v = slippageVerdict(b.mean_slippage_pips, assumption)
        return <Badge tone={v.tone}>{v.label}</Badge>
      },
    },
  ]
}

function toRows(buckets: Record<string, SlippageBucket>): BucketRow[] {
  return Object.entries(buckets).map(([name, b]) => ({ name, ...b }))
}

export function ExecutionQuality() {
  const { markUnauthenticated } = useAuth()
  const report = usePolling(() => getExecutionQuality(), POLL_MS, markUnauthenticated)

  const d = report.data
  const overall = d?.overall
  const assumption = d?.backtest_assumption_pips ?? 0.5
  const verdict = slippageVerdict(overall?.mean_slippage_pips, assumption)

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(150px,1fr))]">
        <KpiCard value={overall?.n ?? '—'} label="Real Fills Recorded" color="blue" />
        <KpiCard
          value={overall?.n ? fmtPips(overall.mean_slippage_pips) : '—'}
          label="Mean Slippage (pips)"
          color={verdict.tone === 'good' ? 'green' : verdict.tone === 'marginal' ? 'amber' : 'red'}
        />
        <KpiCard value={fmtPips(assumption).replace('+', '')} label="Backtest Assumption (pips)" color="purple" />
        <KpiCard
          value={overall?.mean_slippage_r != null ? overall.mean_slippage_r.toFixed(4) : '—'}
          label="Mean Cost per Trade (R)"
          color="default"
        />
        <KpiCard value={overall?.n ? fmtPips(overall.worst_slippage_pips) : '—'} label="Worst Fill (pips)" color="amber" />
      </div>

      <Panel title="By Symbol" right={overall?.n ? undefined : 'awaiting first real broker fill'}>
        {d && Object.keys(d.by_symbol).length > 0 ? (
          <DataTable columns={bucketColumns(assumption, 'Symbol')} rows={toRows(d.by_symbol)} rowKey={(b) => b.name} />
        ) : (
          <Empty>
            {report.loading
              ? 'Loading...'
              : 'No real fills yet — the TCA ledger records every non-dry-run broker fill automatically. Dry-run signals are excluded (their slippage is zero by construction).'}
          </Empty>
        )}
      </Panel>

      <Panel title="By Session" right="microstructure as measurement — never a gate">
        {d && Object.keys(d.by_session).length > 0 ? (
          <DataTable columns={bucketColumns(assumption, 'Session')} rows={toRows(d.by_session)} rowKey={(b) => b.name} />
        ) : (
          <Empty>{report.loading ? 'Loading...' : 'Session breakdown appears with the first fills.'}</Empty>
        )}
      </Panel>

      <Panel title="Recent Fills" right={d?.recent?.length ? `last ${d.recent.length}` : undefined}>
        {d && d.recent && d.recent.length > 0 ? (
          <DataTable columns={fillColumns} rows={d.recent} rowKey={(f) => `${f.ts}-${f.trade_id ?? f.symbol}`} />
        ) : (
          <Empty>{report.loading ? 'Loading...' : 'Every real broker fill lands here with its intended-vs-fill shortfall.'}</Empty>
        )}
      </Panel>

      {d?.note && <p className="text-[0.75em] text-muted px-1">{d.note}</p>}
    </div>
  )
}

const fillColumns: Column<RecentFill>[] = [
  { header: 'When (UTC)', render: (f) => f.ts.slice(0, 19).replace('T', ' ') },
  { header: 'Symbol', render: (f) => <span className="text-accent font-bold">{f.symbol}</span> },
  {
    header: 'Side',
    render: (f) => <Badge tone={f.direction === 'BUY' ? 'good' : 'marginal'}>{f.direction}</Badge>,
  },
  { header: 'Session', render: (f) => f.session ?? '—' },
  { header: 'Intended', render: (f) => f.intended_price, align: 'right' },
  { header: 'Fill', render: (f) => f.fill_price, align: 'right' },
  {
    header: 'Slippage (pips)',
    render: (f) => (
      <span className={f.slippage_pips > 0 ? 'text-red' : f.slippage_pips < 0 ? 'text-green' : 'text-muted'}>
        {fmtPips(f.slippage_pips)}
      </span>
    ),
    align: 'right',
  },
  { header: 'R cost', render: (f) => (f.slippage_r != null ? f.slippage_r.toFixed(4) : '—'), align: 'right' },
  { header: 'Trade', render: (f) => f.trade_id ?? '—' },
]
