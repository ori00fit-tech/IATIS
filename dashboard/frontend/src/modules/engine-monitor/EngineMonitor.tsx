import { useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { getEngineStats, getAiWeightSuggestions, type AiWeightSuggestion } from './api'

const POLL_MS = 45_000

function AiWeightPanel() {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: AiWeightSuggestion | null }>({
    loading: false,
    error: null,
    data: null,
  })

  const generate = () => {
    setState({ loading: true, error: null, data: null })
    getAiWeightSuggestions()
      .then((data) => setState({ loading: false, error: null, data }))
      .catch((err) => setState({ loading: false, error: err instanceof Error ? err.message : String(err), data: null }))
  }

  return (
    <Panel
      title="AI Weight Suggestions (Claude)"
      right={
        <button
          onClick={generate}
          disabled={state.loading}
          className="text-accent hover:text-accent2 text-[0.78em] disabled:opacity-50"
        >
          {state.loading ? 'Analyzing…' : state.data ? 'Regenerate' : 'Generate'}
        </button>
      }
    >
      <div className="p-4">
        {!state.data && !state.loading && !state.error && (
          <Empty>
            On-demand only, suggestions are read-only here — applying a weight change to config.yaml is a
            deliberate separate step, not a dashboard click. Click Generate.
          </Empty>
        )}
        {state.loading && <Empty>Asking Claude to analyze engine performance…</Empty>}
        {state.error && <Empty>Request failed: {state.error}</Empty>}
        {state.data?.status === 'not_configured' && <Empty>ANTHROPIC_API_KEY is not set in the environment.</Empty>}
        {state.data?.status === 'insufficient_data' && <Empty>{state.data.message}</Empty>}
        {(state.data?.status === 'error' || state.data?.status === 'parse_error') && (
          <Empty>{state.data.message ?? 'AI weight optimization failed.'}</Empty>
        )}
        {state.data?.status === 'success' && (
          <div className="flex flex-col gap-3 text-[0.88em]">
            <div className="flex gap-4 text-muted">
              <span>Confidence: {state.data.confidence ?? '—'}</span>
              <span>Trades analyzed: {state.data.trades_analyzed ?? '—'}</span>
            </div>
            <table className="w-full text-left">
              <thead>
                <tr className="text-muted text-[0.85em]">
                  <th className="pb-1">Engine</th>
                  <th className="pb-1 text-right">Suggested</th>
                  <th className="pb-1 pl-3">Reasoning</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(state.data.suggested_weights)
                  .filter(([, w]) => w > 0)
                  .map(([engine, w]) => (
                    <tr key={engine} className="border-t border-border">
                      <td className="py-1.5 font-bold text-accent">{engine}</td>
                      <td className="py-1.5 text-right">{w.toFixed(3)}</td>
                      <td className="py-1.5 pl-3 text-muted">{state.data?.reasoning?.[engine] ?? '—'}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {state.data.note && <p className="text-muted italic">{state.data.note}</p>}
          </div>
        )}
      </div>
    </Panel>
  )
}

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

      <AiWeightPanel />
    </div>
  )
}
