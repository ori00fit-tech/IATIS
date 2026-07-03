import { useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { getEngineStats } from './api'

const POLL_MS = 45_000

function Bar({ value, max, colorClass }: { value: number; max: number; colorClass: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div className="h-1 bg-border rounded-full mt-1">
      <div className={`h-full rounded-full ${colorClass}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

export function EngineMonitor() {
  const { markUnauthenticated } = useAuth()
  const { data, loading, error } = usePolling(getEngineStats, POLL_MS, markUnauthenticated)
  const [showInactive, setShowInactive] = useState(false)

  if (error) return <Empty>Could not load engine stats</Empty>
  if (!data) return <Empty>{loading ? 'Loading...' : 'No data'}</Empty>

  const activeNames = new Set(data.engine_stats.map((e) => e.engine))
  const inactiveNames = Object.keys(data.current_weights).filter((name) => !activeNames.has(name))
  const maxVotes = Math.max(1, ...data.engine_stats.map((e) => e.total_votes))

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Active Engines" right={`${data.engine_stats.length} voting`}>
        <div className="grid gap-3 p-4 grid-cols-[repeat(auto-fit,minmax(220px,1fr))]">
          {data.engine_stats.map((e) => {
            const current = data.current_weights[e.engine]
            const suggested = data.suggested_weights[e.engine]
            const delta = current != null && suggested != null ? suggested - current : null
            return (
              <div key={e.engine} className="bg-surface border border-border rounded-lg p-3.5">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-bold text-accent uppercase text-[0.85em]">{e.engine}</span>
                  <span className="text-muted text-[0.75em]">{e.total_votes} votes</span>
                </div>
                <div className="text-[0.75em] text-muted flex justify-between mb-0.5">
                  <span>Bullish {e.bullish_pct.toFixed(0)}%</span>
                  <span>Bearish {e.bearish_pct.toFixed(0)}%</span>
                  <span>Neutral {e.neutral_pct.toFixed(0)}%</span>
                </div>
                <Bar value={e.total_votes} max={maxVotes} colorClass="bg-accent" />
                <div className="flex justify-between text-[0.78em] mt-3">
                  <span className="text-muted">Agreement</span>
                  <span>{e.agreement_rate != null ? `${e.agreement_rate.toFixed(0)}%` : '—'}</span>
                </div>
                <div className="flex justify-between text-[0.78em]">
                  <span className="text-muted">Avg score</span>
                  <span>{e.avg_score_when_voting.toFixed(0)}</span>
                </div>
                <div className="flex justify-between text-[0.78em]">
                  <span className="text-muted">Weight (current → suggested)</span>
                  <span>
                    {current?.toFixed(3) ?? '—'} → {suggested?.toFixed(3) ?? '—'}
                    {delta != null && (
                      <span className={delta > 0 ? 'text-green' : delta < 0 ? 'text-red' : 'text-muted'}> ({delta > 0 ? '+' : ''}{delta.toFixed(3)})</span>
                    )}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </Panel>

      {inactiveNames.length > 0 && (
        <Panel title="Inactive Engines" right={
          <button onClick={() => setShowInactive((v) => !v)} className="text-accent cursor-pointer bg-transparent border-none">
            {showInactive ? 'Hide' : `Show ${inactiveNames.length}`}
          </button>
        }>
          {showInactive ? (
            <div className="p-4 flex flex-wrap gap-2">
              {inactiveNames.map((name) => (
                <span key={name} className="text-[0.78em] text-muted bg-surface border border-border rounded px-2.5 py-1">
                  {name} · weight {data.current_weights[name]?.toFixed(3)} · 0 recorded votes
                </span>
              ))}
            </div>
          ) : (
            <Empty>{inactiveNames.length} engine(s) configured but not currently casting votes</Empty>
          )}
        </Panel>
      )}

      <p className="text-[0.72em] text-muted px-1">{data.note}</p>
    </div>
  )
}
