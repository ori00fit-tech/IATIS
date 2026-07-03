import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { StatusRow } from '../../components/StatusDot'
import { DataTable, type Column } from '../../components/DataTable'
import { getHealth, getHealthFull, getBudget, getSymbolHealth, type SymbolHealthEntry } from './api'

const POLL_MS = 15_000

function pct(n: number | undefined | null) {
  return n === undefined || n === null ? '—' : `${n.toFixed(0)}%`
}

export function MissionControl() {
  const { markUnauthenticated } = useAuth()
  const health = usePolling(getHealth, POLL_MS, markUnauthenticated)
  const healthFull = usePolling(getHealthFull, POLL_MS, markUnauthenticated)
  const budget = usePolling(getBudget, POLL_MS, markUnauthenticated)
  const symbolHealth = usePolling(getSymbolHealth, POLL_MS, markUnauthenticated)

  const hf = healthFull.data
  const creditsColor = (budget.data?.remaining_today ?? 0) > 400 ? 'green' : (budget.data?.remaining_today ?? 0) > 100 ? 'amber' : 'red'

  const symbolColumns: Column<SymbolHealthEntry>[] = [
    { header: 'Symbol', render: (s) => <span className="font-bold text-accent">{s.symbol}</span> },
    { header: 'SHI', render: (s) => s.shi_score, align: 'right' },
    {
      header: 'Status',
      render: (s) => (
        <span
          className={`font-bold ${s.status === 'HEALTHY' ? 'text-green' : s.status === 'CAUTION' ? 'text-amber' : 'text-red'}`}
        >
          {s.status}
        </span>
      ),
    },
    { header: 'Win Rate', render: (s) => (s.win_rate != null ? `${s.win_rate.toFixed(1)}%` : '—'), align: 'right' },
    { header: 'Trades', render: (s) => <span className="text-muted">{s.trades_count}</span>, align: 'right' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className={`flex items-center gap-2 px-3.5 py-2 rounded-md border text-[0.78em] ${
        hf?.status === 'degraded' ? 'border-red/40 bg-red/5' : 'border-border bg-surface'
      }`}>
        <span className={`inline-block w-2 h-2 rounded-full ${hf?.status === 'degraded' ? 'bg-red' : 'bg-green'}`} />
        <span>
          {healthFull.loading ? 'Checking system health...' : hf?.status === 'degraded' ? `Degraded — ${hf.issues.join('; ')}` : `All systems nominal · v${health.data?.version ?? '?'}`}
        </span>
      </div>

      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={pct(hf?.system?.cpu_pct)} label="CPU" color={hf && hf.system && hf.system.cpu_pct > 80 ? 'red' : 'default'} />
        <KpiCard value={pct(hf?.system?.ram_pct)} label="RAM" color={hf && hf.system && hf.system.ram_pct > 85 ? 'red' : 'default'} />
        <KpiCard value={pct(hf?.system?.disk_pct)} label="Disk" color={hf && hf.system && hf.system.disk_pct > 80 ? 'red' : 'default'} />
        <KpiCard value={hf?.system?.uptime_hours != null ? `${hf.system.uptime_hours}h` : '—'} label="Uptime" color="purple" />
        <KpiCard value={budget.data?.remaining_today ?? '—'} label="API Credits" color={creditsColor} />
        <KpiCard
          value={symbolHealth.data ? `${symbolHealth.data.healthy}/${symbolHealth.data.total}` : '—'}
          label="Symbols Healthy"
          color="blue"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 max-[768px]:grid-cols-1">
        <Panel title="System Status">
          <StatusRow label="API Server" state={health.error ? 'err' : 'ok'} detail={health.data?.status} />
          <StatusRow
            label="Scheduler"
            state={hf?.scheduler?.status === 'running' ? 'ok' : hf?.scheduler?.status === 'unknown' ? 'warn' : 'err'}
            detail={hf?.scheduler?.last_run ?? 'no run seen'}
          />
          <StatusRow
            label="Database"
            state={hf?.database?.status === 'ok' ? 'ok' : 'err'}
            detail={hf?.database ? `${hf.database.total_decisions ?? 0} decisions` : undefined}
          />
          <StatusRow
            label="News Calendar"
            state={hf?.calendar?.status === 'ok' ? 'ok' : 'warn'}
            detail={hf?.calendar?.event_count != null ? `${hf.calendar.event_count} events` : hf?.calendar?.note}
          />
          <StatusRow
            label="cTrader"
            state={hf?.ctrader?.configured ? 'ok' : 'warn'}
            detail={hf?.ctrader?.environment}
          />
          {hf?.data_providers &&
            Object.entries(hf.data_providers).map(([name, status]) => (
              <StatusRow key={name} label={name} state={status.includes('configured') || status === 'always_available' ? 'ok' : 'warn'} detail={status} />
            ))}
        </Panel>

        <Panel title="Symbol Health" right={symbolHealth.data ? `${symbolHealth.data.caution} caution · ${symbolHealth.data.paused} paused` : undefined}>
          {symbolHealth.data && symbolHealth.data.symbols.length > 0 ? (
            <DataTable columns={symbolColumns} rows={symbolHealth.data.symbols} rowKey={(s) => s.symbol} />
          ) : (
            <Empty>{symbolHealth.loading ? 'Loading...' : 'No symbol health data yet'}</Empty>
          )}
        </Panel>
      </div>
    </div>
  )
}
