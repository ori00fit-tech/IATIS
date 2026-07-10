import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import type { CacheStatus } from './api'
import { getDataHealth } from './api'
import { getProviderChains } from '../system-audit/api'

const POLL_MS = 60_000

const CELL_CLASS: Record<CacheStatus, string> = {
  OK: 'bg-green/15 text-green',
  STALE: 'bg-amber/15 text-amber',
  GAPS: 'bg-amber/25 text-amber',
  MISSING: 'bg-red/15 text-red',
}

export function DataCenter() {
  const { markUnauthenticated } = useAuth()
  const { data, loading, error } = usePolling(getDataHealth, POLL_MS, markUnauthenticated)
  const chains = usePolling(getProviderChains, POLL_MS * 5, markUnauthenticated)

  const timeframeSet = new Set<string>()
  data?.symbols.forEach((s) => Object.keys(s.timeframes).forEach((tf) => timeframeSet.add(tf)))
  const timeframes = Array.from(timeframeSet)

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={data?.summary.ok ?? '—'} label="OK" color="green" />
        <KpiCard value={data?.summary.stale ?? '—'} label="Stale" color="amber" />
        <KpiCard value={data?.summary.gaps ?? '—'} label="Gaps" color="amber" />
        <KpiCard value={data?.summary.missing ?? '—'} label="Missing" color="red" />
      </div>

      <Panel title="Cache Completeness" right={data ? `checked ${new Date(data.checked_at).toLocaleTimeString()}` : undefined}>
        {error ? (
          <Empty>Could not load data health</Empty>
        ) : !data || data.symbols.length === 0 ? (
          <Empty>{loading ? 'Loading...' : 'No symbols configured'}</Empty>
        ) : (
          <table className="w-full border-collapse text-[0.82em]">
            <thead>
              <tr>
                <th className="px-3 py-2 text-left text-muted text-[0.75em] uppercase tracking-[0.8px] bg-surface font-semibold">
                  Symbol
                </th>
                {timeframes.map((tf) => (
                  <th key={tf} className="px-3 py-2 text-center text-muted text-[0.75em] uppercase tracking-[0.8px] bg-surface font-semibold">
                    {tf}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.symbols.map((s) => (
                <tr key={s.symbol} className="border-b border-border last:border-b-0">
                  <td className="px-3 py-2.5 font-bold text-accent">{s.symbol}</td>
                  {timeframes.map((tf) => {
                    const cell = s.timeframes[tf]
                    return (
                      <td key={tf} className="px-2 py-2 text-center">
                        {cell ? (
                          <span
                            className={`inline-block px-2 py-1 rounded text-[0.85em] font-bold ${CELL_CLASS[cell.status]}`}
                            title={cell.last_bar_time ? `last bar: ${cell.last_bar_time} (${cell.age_minutes}m ago), ${cell.gap_count_30d} gaps/30d` : 'no cache file'}
                          >
                            {cell.status}
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="Provider Chains" right="asset-class failover order — first native provider wins">
        {!chains.data ? (
          <Empty>{chains.loading ? 'Loading...' : 'Could not load provider chains'}</Empty>
        ) : (
          <div className="p-4 flex flex-col gap-3">
            {Object.entries(chains.data.chains).map(([cls, providers]) => (
              <div key={cls} className="flex items-center gap-2 flex-wrap">
                <span className="w-20 text-[0.75em] uppercase tracking-[1px] text-muted font-semibold">{cls}</span>
                {providers.map((p, i) => (
                  <span key={p} className="flex items-center gap-2">
                    {i > 0 && <span className="text-muted text-[0.7em]">→</span>}
                    <span
                      className={`px-2 py-0.5 rounded text-[0.75em] font-bold border ${
                        chains.data!.availability[p]
                          ? 'text-green border-green/40 bg-green/10'
                          : 'text-muted border-border bg-surface/50'
                      }`}
                      title={
                        (chains.data!.availability[p] ? 'available' : 'not configured (no credentials)') +
                        ' · native: ' +
                        (chains.data!.native_timeframes[p]?.join(' ') ?? '?')
                      }
                    >
                      {p}
                    </span>
                  </span>
                ))}
              </div>
            ))}
            <div className="text-[0.7em] text-muted mt-1">
              Greyed providers lack credentials and are skipped instantly. Hover a provider for its native timeframes —
              a timeframe no chain member serves natively is resampled from the best fetched base.
            </div>
          </div>
        )}
      </Panel>
    </div>
  )
}
