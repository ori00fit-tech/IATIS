import { useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import {
  buildRows, getReconciliation, repairReconciliation,
  type ReconciliationRow, type RepairResult,
} from './api'

const POLL_MS = 15_000

function statusBadge(status: ReconciliationRow['status']) {
  if (status === 'match') return <Badge tone="exec">match</Badge>
  if (status === 'broker_only') return <Badge tone="poor">broker only</Badge>
  return <Badge tone="marginal">internal only</Badge>
}

const columns: Column<ReconciliationRow>[] = [
  { header: 'Symbol', render: (r) => <span className="text-accent font-bold">{r.symbol}</span> },
  { header: 'Broker', render: (r) => (r.onBroker ? '✅ open' : '—'), align: 'right' },
  { header: 'Internal', render: (r) => (r.onInternal ? '✅ open' : '—'), align: 'right' },
  { header: 'Status', render: (r) => statusBadge(r.status) },
]

export function Reconciliation() {
  const { markUnauthenticated } = useAuth()
  const report = usePolling(getReconciliation, POLL_MS, markUnauthenticated)
  const [repairing, setRepairing] = useState(false)
  const [repairResult, setRepairResult] = useState<RepairResult | null>(null)
  const [repairError, setRepairError] = useState<string | null>(null)

  const d = report.data
  const rows = buildRows(d ?? null)
  const status = d?.status ?? 'none'

  const forceRefresh = () => report.refetch()

  const repair = async () => {
    setRepairing(true)
    setRepairError(null)
    setRepairResult(null)
    try {
      const result = await repairReconciliation()
      setRepairResult(result)
      report.refetch()
    } catch (e) {
      setRepairError(e instanceof Error ? e.message : String(e))
    } finally {
      setRepairing(false)
    }
  }

  const hasMismatch = status === 'mismatch'
  const hasInternalOnly = (d?.internal_only ?? []).length > 0

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(160px,1fr))]">
        <KpiCard
          value={status.toUpperCase()}
          label="Reconciliation Status"
          color={status === 'match' ? 'green' : status === 'mismatch' ? 'red' : 'default'}
        />
        <KpiCard value={d?.n_broker ?? '—'} label="Broker Open Positions" color="blue" />
        <KpiCard value={d?.n_internal ?? '—'} label="Internal Open Signals" color="purple" />
        <KpiCard value={(d?.broker_only ?? []).length} label="Broker-Only (missed fills)" color={((d?.broker_only ?? []).length > 0) ? 'red' : 'default'} />
        <KpiCard value={(d?.internal_only ?? []).length} label="Internal-Only (stale rows)" color={hasInternalOnly ? 'amber' : 'default'} />
      </div>

      <Panel
        title="Portfolio Reconciliation"
        right={
          <div className="flex items-center gap-2">
            <button
              onClick={forceRefresh}
              className="text-[0.7em] border border-border rounded px-2 py-1 text-muted hover:text-accent hover:border-accent/50"
            >
              Force Refresh
            </button>
            <button
              onClick={repair}
              disabled={repairing || !hasInternalOnly}
              title={
                hasInternalOnly
                  ? 'Close internal-only rows the broker no longer reports open — never fabricates win/loss'
                  : 'No internal-only rows to repair'
              }
              className="text-[0.7em] border border-amber/40 rounded px-2 py-1 text-amber hover:bg-amber/10 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {repairing ? 'Repairing…' : 'Repair Tracker'}
            </button>
          </div>
        }
      >
        {d?.reason && status !== 'match' && (
          <div className="px-4 pt-3 text-[0.78em] text-muted">{d.reason}</div>
        )}
        {rows.length > 0 ? (
          <DataTable columns={columns} rows={rows} rowKey={(r) => r.symbol} />
        ) : (
          <Empty>
            {status === 'none'
              ? 'No reconciliation checked yet — the scheduler stores a result on the next tick once cTrader is live (ctrader_enabled + not dry_run).'
              : status === 'skipped'
                ? 'Skipped — broker execution is not live (paper/dry-run mode).'
                : 'Both sides report zero open positions.'}
          </Empty>
        )}
        {hasMismatch && (
          <div className="px-4 py-3 border-t border-border text-[0.75em] text-muted">
            Auto-repair runs automatically on every scheduler tick that finds a mismatch
            (never fabricates win/loss — see storage/outcome_tracker.reconcile_close_signal).
            Use <span className="text-amber font-bold">Repair Tracker</span> to close the gap
            immediately instead of waiting for the next tick.
          </div>
        )}
        {repairResult && (
          <div className="px-4 py-3 border-t border-border text-[0.78em]">
            {repairResult.repaired.length > 0 ? (
              <span className="text-green">Closed {repairResult.repaired.length} stale signal(s): {repairResult.repaired.join(', ')}</span>
            ) : (
              <span className="text-muted">{repairResult.reason ?? 'Nothing to repair.'}</span>
            )}
            {repairResult.skipped_no_open_signal.length > 0 && (
              <span className="text-muted"> — no open internal signal found for: {repairResult.skipped_no_open_signal.join(', ')}</span>
            )}
          </div>
        )}
        {repairError && (
          <div className="px-4 py-3 border-t border-border text-[0.78em] text-red">{repairError}</div>
        )}
      </Panel>
    </div>
  )
}
