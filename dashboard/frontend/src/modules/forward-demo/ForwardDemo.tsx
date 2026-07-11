import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { getOutcomes } from '../live-signals/api'
import { getForwardReview, getShadowBook, formatMetric, type ForwardRule, type ShadowGate } from './api'

const POLL_MS = 20_000

function ruleBadgeTone(rule: ForwardRule): 'no-trade' | 'good' | 'neutral' {
  if (rule.triggered) return 'no-trade'
  if (rule.sufficient_n) return 'good'
  return 'neutral'
}

function ruleBadgeLabel(rule: ForwardRule): string {
  if (rule.triggered) return 'VERDICT REACHED'
  if (rule.sufficient_n) return 'evaluated — not triggered'
  return `n=${rule.n}/${rule.min_n}`
}

function RuleCard({ rule }: { rule: ForwardRule }) {
  return (
    <div className="px-4 py-3 border-b border-border last:border-b-0">
      <div className="flex items-center justify-between gap-3 mb-1.5">
        <span className="font-bold text-accent text-[0.85em]">{rule.rule_id}</span>
        <Badge tone={ruleBadgeTone(rule)}>{ruleBadgeLabel(rule)}</Badge>
      </div>
      <p className="text-[0.82em] text-muted mb-2">{rule.statement}</p>
      <div className="h-1.5 bg-surface rounded-full overflow-hidden mb-1.5">
        <div
          className={`h-full ${rule.triggered ? 'bg-red' : 'bg-accent'}`}
          style={{ width: `${Math.min(100, rule.progress_pct ?? 0)}%` }}
        />
      </div>
      <div className="text-[0.78em] text-muted">
        {rule.bucket} {rule.metric} = {formatMetric(rule.current_value, 3)} (needs {rule.op} {rule.threshold}) · n={rule.n}/{rule.min_n}
      </div>
    </div>
  )
}

const gateColumns: Column<ShadowGate>[] = [
  { header: 'Gate', render: (g) => <span className="text-accent">{g.primary_gate ?? '—'}</span> },
  { header: 'Closed', render: (g) => g.n_closed, align: 'right' },
  { header: 'Wins', render: (g) => g.wins, align: 'right' },
  { header: 'Avg R', render: (g) => (g.avg_r != null ? g.avg_r.toFixed(3) : '—'), align: 'right' },
  { header: 'Total R', render: (g) => (g.total_r != null ? g.total_r.toFixed(2) : '—'), align: 'right' },
  {
    header: 'Verdict',
    render: (g) => (
      <Badge tone={g.verdict === 'saving losses' ? 'good' : g.verdict === 'rejecting profit' ? 'poor' : 'neutral'}>{g.verdict}</Badge>
    ),
  },
]

export function ForwardDemo() {
  const { markUnauthenticated } = useAuth()
  const outcomes = usePolling(() => getOutcomes(5), POLL_MS, markUnauthenticated)
  const forward = usePolling(() => getForwardReview(), POLL_MS, markUnauthenticated)
  const shadow = usePolling(() => getShadowBook(), POLL_MS, markUnauthenticated)

  const s = outcomes.data?.summary

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={s?.total_closed ?? '—'} label="Closed Trades" color="blue" />
        <KpiCard value={s?.open_signals ?? '—'} label="Open Trades" color="amber" />
        <KpiCard value={s ? formatMetric(s.profit_factor) : '—'} label="Profit Factor" color="green" />
        <KpiCard value={s?.win_rate != null ? `${s.win_rate.toFixed(1)}%` : '—'} label="Win Rate" color="purple" />
        <KpiCard value={s?.avg_r_multiple != null ? `${s.avg_r_multiple.toFixed(2)}R` : '—'} label="Avg R" color="default" />
      </div>

      <Panel
        title="Pre-Registered Forward Decision Rules"
        right={forward.data ? `checked ${forward.data.checked_at.slice(11, 19)} UTC` : undefined}
      >
        {forward.data && forward.data.rules.length > 0 ? (
          forward.data.rules.map((r) => <RuleCard key={r.rule_id} rule={r} />)
        ) : (
          <Empty>{forward.loading ? 'Loading...' : 'No pre-registered forward decision rules found'}</Empty>
        )}
      </Panel>

      <Panel title="Shadow Book" right={shadow.data ? `${shadow.data.open} open shadow signals` : undefined}>
        {shadow.data?.note && <p className="px-4 py-2 text-[0.78em] text-muted border-b border-border">{shadow.data.note}</p>}
        {shadow.data && shadow.data.gates.length > 0 ? (
          <DataTable columns={gateColumns} rows={shadow.data.gates} rowKey={(g) => g.primary_gate ?? 'unknown'} />
        ) : (
          <Empty>{shadow.loading ? 'Loading...' : 'No closed shadow signals yet'}</Empty>
        )}
      </Panel>
    </div>
  )
}
