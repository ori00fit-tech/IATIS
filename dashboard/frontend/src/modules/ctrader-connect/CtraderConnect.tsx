import { useEffect, useState } from 'react'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { useApiQuery } from '../../lib/useApiQuery'
import { useAuth } from '../../lib/auth'
import { getCtraderStatus, connectCtrader } from './api'

const POLL_MS = 15000

function formatDuration(seconds: number): string {
  const abs = Math.abs(seconds)
  const d = Math.floor(abs / 86400)
  const h = Math.floor((abs % 86400) / 3600)
  const label = d > 0 ? `${d}d ${h}h` : `${h}h`
  return seconds < 0 ? `${label} ago` : label
}

export function CtraderConnect() {
  const { markUnauthenticated } = useAuth()
  const statusQuery = useApiQuery(['ctrader-status'], getCtraderStatus, POLL_MS, markUnauthenticated)
  const [banner, setBanner] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  useEffect(() => {
    const hash = window.location.hash
    if (hash.includes('ctrader_connected=1')) {
      setBanner({ kind: 'success', text: 'Connected. Restart iatis-scheduler to activate the new token.' })
      history.replaceState(null, '', window.location.pathname + window.location.search + '#/ctrader-connect')
    } else if (hash.includes('ctrader_error=')) {
      const err = new URLSearchParams(hash.split('?')[1] ?? hash.split('=').slice(1).join('=')).get('ctrader_error')
        ?? hash.split('ctrader_error=')[1]?.split('&')[0]
      setBanner({ kind: 'error', text: `Connection failed: ${err ?? 'unknown error'}` })
      history.replaceState(null, '', window.location.pathname + window.location.search + '#/ctrader-connect')
    }
  }, [])

  if (!statusQuery.data) return <Panel title="cTrader Connection"><Empty>Loading…</Empty></Panel>
  const s = statusQuery.data

  return (
    <div className="flex flex-col gap-4 p-4">
      <Panel title="cTrader Connection" right="OAuth 2.0 web flow — credentials only, never the trading path">
        <div className="p-4 flex flex-col gap-4">
          {banner && (
            <div className={`text-[0.8em] ${banner.kind === 'success' ? 'text-green' : 'text-red'}`}>
              {banner.text}
            </div>
          )}

          <div className="flex items-center gap-2 text-[0.85em]">
            <Badge tone={s.configured ? 'good' : 'poor'}>{s.configured ? 'Connected' : 'Not connected'}</Badge>
            <span className="text-muted">env: {s.environment}</span>
          </div>

          <div className="flex flex-col gap-1 text-[0.8em] text-muted">
            <span>Account ID: {s.account_id ?? '—'}</span>
            <span>Refresh token on file: {s.has_refresh_token ? 'yes' : 'no'}</span>
            <span>
              Token expires:{' '}
              {s.expires_in_seconds != null ? formatDuration(s.expires_in_seconds) : 'unknown (not tracked)'}
            </span>
            {s.needs_reauthorization && (
              <span className="text-red">Token expired and no refresh token available — re-authorize below.</span>
            )}
          </div>

          <div>
            <button
              onClick={connectCtrader}
              className="px-4 py-2 rounded bg-accent text-bg font-bold text-[0.82em]"
            >
              {s.configured ? 'Re-authorize cTrader' : 'Connect cTrader'}
            </button>
          </div>
        </div>
      </Panel>
    </div>
  )
}
