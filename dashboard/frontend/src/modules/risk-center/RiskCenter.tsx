import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import {
  getHealthFull,
  getSymbolHealth,
  getReconciliation,
  getOutcomesFull,
  type OutcomeRow,
  type RiskSummary,
  type SymbolHealthEntry,
} from './api'

const POLL_MS = 20_000

// The house risk rules the whole system is built on (CLAUDE.md): every
// carrier setup targets RR >= 2 on an ATR stop. Risk Center measures live
// open positions against that bar rather than restating it as prose.
const MIN_RR = 2
// Win rate below this sample size is noise — same bar Mission Control uses.
const MIN_SIGNIFICANT_N = 30

const isBuy = (dir: string) => dir === 'BUY' || dir === 'BULLISH'

/** Reward:risk implied by the setup's own entry/stop/target. */
function plannedRR(r: OutcomeRow): number | null {
  if (r.entry_price == null || r.stop_loss == null || r.take_profit == null) return null
  const risk = Math.abs(r.entry_price - r.stop_loss)
  if (risk <= 0) return null
  return Math.abs(r.take_profit - r.entry_price) / risk
}

/**
 * Realized R-multiple of a closed trade — recomputed from the row's own
 * entry/stop/exit with the exact formula storage/outcome_tracker.py uses, so
 * this panel and the backend summary can never silently disagree.
 */
function realizedR(r: OutcomeRow): number | null {
  if (r.entry_price == null || r.stop_loss == null || r.exit_price == null) return null
  const risk = Math.abs(r.entry_price - r.stop_loss)
  if (risk <= 0) return null
  const diff = isBuy(r.direction) ? r.exit_price - r.entry_price : r.entry_price - r.exit_price
  return diff / risk
}

function ageLabel(iso: string): string {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = (Date.now() - t) / 1000
  if (s < 90) return `${Math.round(s)}s`
  if (s < 5400) return `${Math.round(s / 60)}m`
  if (s < 172800) return `${(s / 3600).toFixed(1)}h`
  return `${(s / 86400).toFixed(1)}d`
}

function fmtPf(pf: RiskSummary['profit_factor']): string {
  if (pf === null) return '—'
  if (pf === 'Infinity') return '∞'
  return pf.toFixed(2)
}

function pfColor(pf: RiskSummary['profit_factor'], n: number): 'green' | 'amber' | 'red' | 'default' {
  if (pf === null || n < MIN_SIGNIFICANT_N) return 'default'
  if (pf === 'Infinity') return 'green'
  if (pf >= 1.2) return 'green'
  if (pf >= 1.0) return 'amber'
  return 'red'
}

// Realized-R histogram buckets — the shape of the return distribution is the
// honest read on tail risk that a single average hides.
const R_BUCKETS: { label: string; test: (r: number) => boolean; tone: string }[] = [
  { label: '≤ −1R', test: (r) => r <= -1, tone: 'bg-red' },
  { label: '−1…0R', test: (r) => r > -1 && r < 0, tone: 'bg-red/60' },
  { label: '0…1R', test: (r) => r >= 0 && r < 1, tone: 'bg-amber/70' },
  { label: '1…2R', test: (r) => r >= 1 && r < 2, tone: 'bg-green/60' },
  { label: '2…3R', test: (r) => r >= 2 && r < 3, tone: 'bg-green' },
  { label: '≥ 3R', test: (r) => r >= 3, tone: 'bg-accent' },
]

function RDistribution({ rows }: { rows: OutcomeRow[] }) {
  const rs = rows
    .filter((r) => r.outcome !== 'open')
    .map(realizedR)
    .filter((r): r is number => r != null)
  if (rs.length === 0) return <Empty>No closed trades with a computable R-multiple yet</Empty>
  const counts = R_BUCKETS.map((b) => rs.filter((r) => b.test(r)).length)
  const max = Math.max(...counts, 1)
  const avg = rs.reduce((a, b) => a + b, 0) / rs.length
  return (
    <div className="p-4 flex flex-col gap-2">
      <div className="flex items-baseline gap-3 mb-1">
        <span className="text-[0.78em] text-muted">n = {rs.length} closed</span>
        <span className="text-[0.82em]">
          mean <b className={avg >= 0 ? 'text-green' : 'text-red'}>{avg >= 0 ? '+' : ''}{avg.toFixed(2)}R</b>
        </span>
      </div>
      {R_BUCKETS.map((b, i) => (
        <div key={b.label} className="flex items-center gap-2 text-[0.78em]">
          <span className="w-14 text-right text-muted shrink-0">{b.label}</span>
          <div className="flex-1 h-4 bg-surface rounded overflow-hidden">
            <div className={`h-full ${b.tone}`} style={{ width: `${(counts[i] / max) * 100}%` }} />
          </div>
          <span className="w-8 text-right tabular-nums shrink-0">{counts[i]}</span>
        </div>
      ))}
    </div>
  )
}

export function RiskCenter() {
  const { markUnauthenticated } = useAuth()
  const healthFull = usePolling(getHealthFull, POLL_MS, markUnauthenticated)
  const outcomes = usePolling(getOutcomesFull, POLL_MS, markUnauthenticated)
  const symbolHealth = usePolling(getSymbolHealth, POLL_MS, markUnauthenticated)
  const reconciliation = usePolling(getReconciliation, POLL_MS * 2, markUnauthenticated)

  const exp = healthFull.data?.exposure_estimate
  const s = outcomes.data?.summary
  const open = outcomes.data?.open_signals ?? []
  const util = exp?.utilization_pct ?? 0

  // Live rule compliance — derived, not asserted.
  const rrViolations = open.filter((r) => {
    const rr = plannedRR(r)
    return rr != null && rr < MIN_RR
  })
  const noStop = open.filter((r) => r.stop_loss == null)
  const rMultRule = s?.avg_r_multiple

  // Portfolio heat (spec §6, synthesizable slice): a flat book has no heat;
  // a one-directional book concentrates correlated risk. Derived from the open
  // book only — a correlation heatmap / risk budgets need new backend (§5).
  const longs = open.filter((r) => isBuy(r.direction)).length
  const shorts = open.length - longs
  const netBias = open.length === 0 ? 'FLAT' : longs > shorts ? 'NET LONG' : shorts > longs ? 'NET SHORT' : 'BALANCED'
  const dirConcentration = open.length ? (Math.abs(longs - shorts) / open.length) * 100 : 0
  const bySymbol = open.reduce<Record<string, number>>((acc, r) => {
    acc[r.symbol] = (acc[r.symbol] ?? 0) + 1
    return acc
  }, {})
  const topSymbol = Object.entries(bySymbol).sort((a, b) => b[1] - a[1])[0]

  const openColumns: Column<OutcomeRow>[] = [
    { header: 'Symbol', render: (r) => <span className="font-bold text-accent">{r.symbol}</span> },
    {
      header: 'Dir',
      render: (r) => <Badge tone={isBuy(r.direction) ? 'good' : 'poor'}>{isBuy(r.direction) ? 'LONG' : 'SHORT'}</Badge>,
    },
    { header: 'Entry', render: (r) => (r.entry_price != null ? r.entry_price : '—'), align: 'right' },
    { header: 'Stop', render: (r) => (r.stop_loss != null ? <span className="text-red">{r.stop_loss}</span> : <span className="text-red font-bold">none</span>), align: 'right' },
    { header: 'Target', render: (r) => (r.take_profit != null ? <span className="text-green">{r.take_profit}</span> : '—'), align: 'right' },
    {
      header: 'RR',
      render: (r) => {
        const rr = plannedRR(r)
        if (rr == null) return <span className="text-muted">—</span>
        return <span className={rr >= MIN_RR ? 'text-green' : 'text-red font-bold'} title={rr < MIN_RR ? `below house rule RR ≥ ${MIN_RR}` : undefined}>{rr.toFixed(2)}</span>
      },
      align: 'right',
    },
    { header: 'Regime', render: (r) => <span className="text-muted text-[0.9em]">{r.regime ?? '—'}</span> },
    { header: 'Age', render: (r) => <span className="text-muted">{ageLabel(r.entry_time)}</span>, align: 'right' },
  ]

  const symbolColumns: Column<SymbolHealthEntry>[] = [
    { header: 'Symbol', render: (r) => <span className="font-bold text-accent">{r.symbol}</span> },
    {
      header: 'Status',
      render: (r) => (
        <span className={`font-bold ${r.status === 'HEALTHY' ? 'text-green' : r.status === 'CAUTION' ? 'text-amber' : 'text-red'}`}>{r.status}</span>
      ),
    },
    {
      header: 'Size ×',
      render: (r) => {
        const m = r.position_multiplier
        return <span className={m < 1 ? 'text-amber font-bold' : 'text-text'} title="Fractional position multiplier applied by the risk layer">{m.toFixed(2)}×</span>
      },
      align: 'right',
    },
    {
      header: 'Streak',
      render: (r) => (r.consecutive_losses > 0 ? <span className="text-red">−{r.consecutive_losses}</span> : <span className="text-muted">0</span>),
      align: 'right',
    },
    { header: 'PF', render: (r) => (r.profit_factor != null ? r.profit_factor.toFixed(2) : '—'), align: 'right' },
  ]

  const rules = [
    { ok: rrViolations.length === 0, label: `Every open position targets RR ≥ ${MIN_RR}`, detail: rrViolations.length ? `${rrViolations.length} below bar: ${rrViolations.map((r) => r.symbol).join(', ')}` : `${open.length} open` },
    { ok: noStop.length === 0, label: 'Every open position has a hard stop', detail: noStop.length ? `${noStop.length} missing a stop` : 'all stops set' },
    { ok: util < 100, label: 'Exposure within configured cap', detail: exp ? `${util.toFixed(0)}% of cap (upper-bound est.)` : 'no estimate' },
    { ok: rMultRule == null || rMultRule >= 0, label: 'Realized avg R-multiple non-negative', detail: rMultRule == null ? 'no closed trades yet' : `${rMultRule >= 0 ? '+' : ''}${rMultRule.toFixed(2)}R` },
  ]

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.78em] text-muted">
        Monitoring only — Risk Center never gates or sizes a trade. Exposure is the upper-bound estimate from
        <code className="text-accent2"> /health/full</code>; R-multiples are recomputed from each row's own entry/stop/exit.
      </p>

      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard
          value={exp ? `${exp.estimated_pct.toFixed(1)}%` : '—'}
          label="Est. Exposure"
          color={util >= 90 ? 'red' : util >= 50 ? 'amber' : 'green'}
        />
        <KpiCard value={exp ? `${util.toFixed(0)}%` : '—'} label="Cap Utilization" color={util >= 90 ? 'red' : util >= 50 ? 'amber' : 'default'} />
        <KpiCard value={exp?.open_positions ?? open.length} label="Open Positions" color="blue" />
        <KpiCard value={fmtPf(s?.profit_factor ?? null)} label="Profit Factor" color={pfColor(s?.profit_factor ?? null, s?.total_closed ?? 0)} />
        <KpiCard
          value={s?.avg_r_multiple != null ? `${s.avg_r_multiple >= 0 ? '+' : ''}${s.avg_r_multiple.toFixed(2)}R` : '—'}
          label="Avg R-Multiple"
          color={s?.avg_r_multiple == null ? 'default' : s.avg_r_multiple >= 0 ? 'green' : 'red'}
        />
        <KpiCard
          value={s ? `${s.win_rate.toFixed(0)}%` : '—'}
          label="Win Rate"
          color={s == null || s.total_closed < MIN_SIGNIFICANT_N ? 'default' : s.win_rate >= 50 ? 'green' : 'amber'}
        />
        <KpiCard value={rrViolations.length === 0 ? 'PASS' : `×${rrViolations.length}`} label="RR ≥ 2 Compliance" color={rrViolations.length === 0 ? 'green' : 'red'} />
      </div>

      {exp && (
        <div className="flex items-center gap-3 px-3.5 py-2.5 rounded-md border border-border bg-surface text-[0.8em]" title={exp.note}>
          <span className="text-muted uppercase text-[0.72em] tracking-[0.8px] shrink-0">Exposure vs cap</span>
          <div className="flex-1 h-2 rounded-full bg-bg border border-border overflow-hidden max-w-[420px]">
            <div className={`h-full ${util >= 90 ? 'bg-red' : util >= 50 ? 'bg-amber' : 'bg-green'}`} style={{ width: `${Math.min(100, util)}%` }} />
          </div>
          <span className="font-bold shrink-0">{exp.estimated_pct.toFixed(1)}% / {exp.max_exposure_pct.toFixed(1)}%</span>
        </div>
      )}

      <div className="flex items-center gap-4 flex-wrap px-3.5 py-2.5 rounded-md border border-border bg-surface text-[0.8em]">
        <span className="text-muted uppercase text-[0.72em] tracking-[0.8px] shrink-0">Portfolio Heat</span>
        <span>
          Bias{' '}
          <b className={netBias === 'FLAT' || netBias === 'BALANCED' ? 'text-muted' : netBias === 'NET LONG' ? 'text-green' : 'text-red'}>
            {netBias}
          </b>{' '}
          <span className="text-muted">({longs}L / {shorts}S)</span>
        </span>
        <span title="Share of the open book pointing one way — high = correlated directional risk">
          Directional concentration{' '}
          <b className={dirConcentration >= 80 && open.length > 1 ? 'text-red' : dirConcentration >= 50 && open.length > 1 ? 'text-amber' : 'text-text'}>
            {dirConcentration.toFixed(0)}%
          </b>
        </span>
        {topSymbol && topSymbol[1] > 1 && (
          <span className="text-amber">
            {topSymbol[1]}× stacked on <b>{topSymbol[0]}</b>
          </span>
        )}
        <span className="text-muted text-[0.85em] ml-auto shrink-0">correlation heatmap + risk budgets: pending backend (§5/§6)</span>
      </div>

      <div className="grid grid-cols-2 gap-4 max-[900px]:grid-cols-1">
        <Panel title="Risk Rule Compliance" right="live, derived from open book">
          <div className="flex flex-col">
            {rules.map((r) => (
              <div key={r.label} className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-border last:border-b-0 text-[0.82em]">
                <div className="flex items-center gap-2 min-w-0">
                  <span className={r.ok ? 'text-green' : 'text-red'}>{r.ok ? '✓' : '✕'}</span>
                  <span className="truncate">{r.label}</span>
                </div>
                <span className={`shrink-0 text-[0.92em] ${r.ok ? 'text-muted' : 'text-red'}`}>{r.detail}</span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Realized R-Multiple Distribution" right="from closed outcomes">
          {outcomes.data ? <RDistribution rows={outcomes.data.recent} /> : <Empty>{outcomes.loading ? 'Loading…' : 'No outcome data'}</Empty>}
        </Panel>
      </div>

      <Panel title="Open Risk" right={`${open.length} position${open.length === 1 ? '' : 's'} at risk`}>
        {open.length > 0 ? (
          <DataTable columns={openColumns} rows={open} rowKey={(r) => r.signal_id} />
        ) : (
          <Empty>{outcomes.loading ? 'Loading…' : 'No open positions — book is flat'}</Empty>
        )}
      </Panel>

      <Panel
        title="Per-Symbol Risk Scaling"
        right={symbolHealth.data ? `${symbolHealth.data.paused} paused · ${symbolHealth.data.caution} caution` : undefined}
      >
        {symbolHealth.data && symbolHealth.data.symbols.length > 0 ? (
          <DataTable columns={symbolColumns} rows={symbolHealth.data.symbols} rowKey={(r) => r.symbol} />
        ) : (
          <Empty>{symbolHealth.loading ? 'Loading…' : 'No symbol health data yet'}</Empty>
        )}
      </Panel>

      {reconciliation.data && reconciliation.data.status === 'mismatch' && (
        <div className="px-3.5 py-2.5 rounded-md border border-red/40 bg-red/5 text-[0.8em] text-red">
          ⚠ Position reconciliation mismatch — broker and internal books disagree
          {reconciliation.data.broker_only?.length ? ` · broker-only: ${reconciliation.data.broker_only.join(', ')}` : ''}
          {reconciliation.data.internal_only?.length ? ` · internal-only: ${reconciliation.data.internal_only.join(', ')}` : ''}
        </div>
      )}
    </div>
  )
}
