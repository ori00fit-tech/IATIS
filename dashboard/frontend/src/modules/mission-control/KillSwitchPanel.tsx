import { useState } from 'react'
import { Panel } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { activateKillSwitch, deactivateKillSwitch, getKillSwitchStatus } from './api'

const POLL_MS = 10_000

/**
 * Operational kill switch — RTS 6 Art.12 / PRA SS5/18 "kill functionality"
 * style manual halt on NEW order submission. Mounted at the very top of
 * Mission Control as the single most safety-critical control on the
 * dashboard.
 *
 * Scope, stated in the UI itself so it is never mistaken for more than it
 * is: this blocks scheduler.py's EXECUTE branch only. IATIS only ever
 * places protected market orders with SL/TP already attached — no
 * pending/limit order queue exists to "cancel" — and this control does
 * NOT force-close already-open positions (a separate, materially riskier
 * action, deliberately not built here).
 */
export function KillSwitchPanel() {
  const { markUnauthenticated } = useAuth()
  const status = usePolling(getKillSwitchStatus, POLL_MS, markUnauthenticated)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const state = status.data

  const activate = async () => {
    if (!reason.trim()) {
      setError('A reason is required to activate the kill switch.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await activateKillSwitch(reason.trim())
      setReason('')
      await status.refetch()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const deactivate = async () => {
    setBusy(true)
    setError(null)
    try {
      await deactivateKillSwitch()
      await status.refetch()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const active = state?.active ?? false

  return (
    <Panel
      title="Kill Switch"
      right={
        state ? (
          <Badge tone={active ? 'no-trade' : 'good'}>{active ? 'ACTIVE — new orders blocked' : 'inactive'}</Badge>
        ) : undefined
      }
    >
      <div className="p-4 flex flex-col gap-3">
        <p className="text-[0.78em] text-muted">
          Immediate, manual halt on new order submission. Blocks scheduler.py's EXECUTE branch before any broker
          call is made — every EXECUTE signal is still analyzed and logged, just not sent to the broker. Does not
          cancel pending orders (IATIS places protected market orders only — there is nothing to cancel) and does
          not close already-open positions. Never auto-clears — only an explicit Deactivate below turns it back off.
        </p>

        {active && state && (
          <div className="text-[0.8em] bg-red/10 text-red rounded px-3 py-2 flex flex-col gap-0.5">
            <span className="font-bold">Reason: {state.reason}</span>
            <span className="text-muted">
              Activated {state.activated_at} by {state.activated_by}
            </span>
          </div>
        )}

        {error && <div className="text-[0.78em] text-red">{error}</div>}

        {active ? (
          <button
            onClick={deactivate}
            disabled={busy}
            className="min-h-11 px-4 py-2 rounded border border-red/40 text-red font-bold text-[0.85em] hover:bg-red/10 disabled:opacity-50 self-start"
          >
            {busy ? 'Deactivating…' : 'Deactivate — resume order submission'}
          </button>
        ) : (
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Reason for halting (required)"
              className="flex-1 min-h-11 px-3 py-2 rounded border border-border bg-bg text-text text-[0.85em]"
            />
            <button
              onClick={activate}
              disabled={busy}
              className="min-h-11 px-4 py-2 rounded border border-red/60 bg-red/10 text-red font-bold text-[0.85em] hover:bg-red/20 disabled:opacity-50"
            >
              {busy ? 'Activating…' : 'Activate kill switch'}
            </button>
          </div>
        )}
      </div>
    </Panel>
  )
}
