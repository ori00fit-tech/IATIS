import { useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { getAlerts, type Alert, type AlertSeverity } from './api'

const POLL_MS = 20_000

const SEVERITY_BADGE: Record<AlertSeverity, 'poor' | 'marginal' | 'good'> = {
  error: 'poor',
  warning: 'marginal',
  info: 'good',
}

function categoryLabel(category: string) {
  return category.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function AlertRow({ alert }: { alert: Alert }) {
  const [expanded, setExpanded] = useState(false)
  const hasDetail = alert.detail && Object.keys(alert.detail).length > 0

  return (
    <div className="px-4 py-3 border-b border-border last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <Badge tone={SEVERITY_BADGE[alert.severity]}>{alert.severity.toUpperCase()}</Badge>
          <div className="min-w-0">
            <div className="text-[0.7em] text-muted uppercase tracking-[0.8px] mb-0.5">{categoryLabel(alert.category)}</div>
            <div className="text-[0.88em] text-text">{alert.message}</div>
          </div>
        </div>
        {hasDetail && (
          <button onClick={() => setExpanded((v) => !v)} className="text-muted hover:text-text text-[0.78em] shrink-0 underline decoration-dotted">
            {expanded ? 'hide' : 'detail'}
          </button>
        )}
      </div>
      {expanded && hasDetail && (
        <pre className="mt-2 p-2 bg-surface/60 rounded text-[0.75em] overflow-auto whitespace-pre-wrap break-words">
          {JSON.stringify(alert.detail, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function AlertCenter() {
  const { markUnauthenticated } = useAuth()
  const [filter, setFilter] = useState<AlertSeverity | 'all'>('all')
  const alerts = usePolling(() => getAlerts(), POLL_MS, markUnauthenticated)

  const bySev = alerts.data?.by_severity
  const visible = alerts.data ? alerts.data.alerts.filter((a) => filter === 'all' || a.severity === filter) : []

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={alerts.data?.count ?? '—'} label="Total Alerts" color="blue" />
        <KpiCard value={bySev?.error ?? 0} label="Errors" color="red" />
        <KpiCard value={bySev?.warning ?? 0} label="Warnings" color="amber" />
        <KpiCard value={bySev?.info ?? 0} label="Info" color="default" />
      </div>

      <Panel title="Alert Center" right={alerts.data ? `checked ${alerts.data.checked_at.slice(11, 19)} UTC` : undefined}>
        <div className="flex gap-2 px-4 py-3 border-b border-border bg-surface/40">
          {(['all', 'error', 'warning', 'info'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-3 py-1.5 text-[0.78em] rounded ${filter === s ? 'bg-accent/15 text-accent' : 'text-muted hover:text-text'}`}
            >
              {s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>

        {visible.length > 0 ? (
          visible.map((a, i) => <AlertRow key={i} alert={a} />)
        ) : (
          <Empty>{alerts.loading ? 'Loading...' : 'No alerts — everything nominal'}</Empty>
        )}
      </Panel>
    </div>
  )
}
