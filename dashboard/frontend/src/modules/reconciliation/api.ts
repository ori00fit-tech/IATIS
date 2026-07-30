import { apiGet, apiPost } from '../../lib/api'

// Portfolio Reconciliation (2026-07-30) — thin client over
// execution/routes/outcomes.py's GET /reconciliation (last stored broker-
// vs-internal diff, scheduler-only writer) and POST /reconciliation/repair
// (closes internal-only outcome rows via storage.outcome_tracker.
// reconcile_close_signal — never fabricates a win/loss). This dashboard
// process never calls execution.reconciliation.reconcile() itself: that
// would open a second cTrader session and collide with the scheduler's
// single per-account session slot.

export interface ReconciliationReport {
  status: 'match' | 'mismatch' | 'skipped' | 'none'
  checked_at?: string
  reason?: string
  broker_open?: string[]
  internal_open?: string[]
  broker_only?: string[]
  internal_only?: string[]
  n_broker?: number
  n_internal?: number
}

export interface RepairResult {
  repaired: string[]
  skipped_no_open_signal: string[]
  reason?: string
}

export const getReconciliation = () => apiGet<ReconciliationReport>('/reconciliation')

export const repairReconciliation = () => apiPost<RepairResult>('/reconciliation/repair')

export type RowStatus = 'match' | 'broker_only' | 'internal_only'

export interface ReconciliationRow {
  symbol: string
  onBroker: boolean
  onInternal: boolean
  status: RowStatus
}

/** Symbol | Broker | Internal | Status table rows, derived from the raw
 * broker_open/internal_open sets — pure function, easy to unit-reason. */
export function buildRows(report: ReconciliationReport | null): ReconciliationRow[] {
  if (!report) return []
  const broker = new Set(report.broker_open ?? [])
  const internal = new Set(report.internal_open ?? [])
  const symbols = Array.from(new Set([...broker, ...internal])).sort()
  return symbols.map((symbol) => {
    const onBroker = broker.has(symbol)
    const onInternal = internal.has(symbol)
    return {
      symbol,
      onBroker,
      onInternal,
      status: onBroker && onInternal ? 'match' : onBroker ? 'broker_only' : 'internal_only',
    }
  })
}
