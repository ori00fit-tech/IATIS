import { useEffect, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { AiStatusFrame } from '../../components/AiStatusFrame'
import { getDecisions, explainTrade, type DecisionEntry, type TradeExplanation } from './api'

const POLL_MS = 30_000

function verdictTone(v: string) {
  return v === 'EXECUTE' ? 'exec' : 'no-trade'
}

function timeAgo(iso: string): string {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const s = (Date.now() - t) / 1000
  if (s < 90) return `${Math.round(s)}s ago`
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172800) return `${(s / 3600).toFixed(1)}h ago`
  return `${(s / 86400).toFixed(1)}d ago`
}

// Aggregate "why NO_TRADE" across the feed — the decision center's headline
// read on what the system is actually rejecting on.
function NoTradeReasons({ reasons }: { reasons: Record<string, number> }) {
  const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1])
  if (entries.length === 0) return <Empty>No NO_TRADE reasons in this window</Empty>
  const max = Math.max(...entries.map(([, n]) => n), 1)
  return (
    <div className="p-4 flex flex-col gap-2">
      {entries.map(([reason, n]) => (
        <div key={reason} className="flex items-center gap-2 text-[0.8em]">
          <span className="w-40 shrink-0 truncate text-muted" title={reason}>{reason}</span>
          <div className="flex-1 h-4 bg-surface rounded overflow-hidden">
            <div className="h-full bg-red/60" style={{ width: `${(n / max) * 100}%` }} />
          </div>
          <span className="w-8 text-right tabular-nums shrink-0">{n}</span>
        </div>
      ))}
    </div>
  )
}

function Explanation({ decision }: { decision: DecisionEntry }) {
  const [state, setState] = useState<{ loading: boolean; data: TradeExplanation | null; error: string | null }>({
    loading: false,
    data: null,
    error: null,
  })
  // Reset when the selected decision changes — the previous explanation is stale.
  useEffect(() => setState({ loading: false, data: null, error: null }), [decision.symbol, decision.timestamp])

  const run = () => {
    setState({ loading: true, data: null, error: null })
    explainTrade(decision.report)
      .then((data) => setState({ loading: false, data, error: null }))
      .catch((err) => setState({ loading: false, data: null, error: err instanceof Error ? err.message : String(err) }))
  }

  if (!state.data && !state.loading && !state.error) {
    return (
      <button onClick={run} className="text-accent hover:text-accent2 text-[0.82em] border border-accent/40 rounded px-3 py-1.5">
        Explain this decision with AI
      </button>
    )
  }
  return (
    <AiStatusFrame
      loading={state.loading}
      fetchError={state.error}
      status={state.data?.status}
      providerError={state.data?.error}
      disabledHint="AI explanations are disabled (set `ai.enabled: true` and an API key to turn this on)."
    >
      {state.data?.status === 'ok' && (
        <div className="flex flex-col gap-3 text-[0.88em]">
          <p>{state.data.explanation || state.data.summary}</p>
          <div className="flex gap-2 flex-wrap items-center">
            <Badge tone={state.data.risk_level === 'HIGH' ? 'poor' : state.data.risk_level === 'MEDIUM' ? 'marginal' : 'good'}>
              {`Risk: ${state.data.risk_level}`}
            </Badge>
            <span className="text-muted text-[0.9em]">confidence {state.data.confidence}% · via {state.data.provider}</span>
          </div>
          {state.data.pros.length > 0 && (
            <div>
              <div className="text-muted uppercase text-[0.68em] tracking-[1px] mb-1">Supporting</div>
              <ul className="list-disc pl-5 text-green">{state.data.pros.map((p, i) => <li key={i}>{p}</li>)}</ul>
            </div>
          )}
          {state.data.cons.length > 0 && (
            <div>
              <div className="text-muted uppercase text-[0.68em] tracking-[1px] mb-1">Against</div>
              <ul className="list-disc pl-5 text-red">{state.data.cons.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </div>
          )}
          {state.data.warnings.length > 0 && <div className="text-amber">{state.data.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}</div>}
          <p className="text-muted text-[0.78em] italic">
            Explanation only — this narration never changed the decision above, which was made by the deterministic pipeline.
          </p>
        </div>
      )}
    </AiStatusFrame>
  )
}

function DecisionAnatomy({ decision }: { decision: DecisionEntry }) {
  const r = decision.report
  const cf = r.confluence
  const reg = r.regime
  const agreePct = cf.vote.total_engines > 0 ? (cf.vote.agree_count / cf.vote.total_engines) * 100 : 0
  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-[1.1em] font-bold text-accent">{decision.symbol}</span>
        <Badge tone={verdictTone(decision.final_verdict)}>{decision.final_verdict}</Badge>
        <span className="text-muted text-[0.78em]">{timeAgo(decision.timestamp)}</span>
      </div>

      <p className="text-[0.88em]">{r.summary}</p>

      {/* Regime */}
      <div className="grid grid-cols-2 gap-3 max-[600px]:grid-cols-1">
        <div className="border border-border rounded-md p-3">
          <div className="text-muted uppercase text-[0.66em] tracking-[1px] mb-2">Regime</div>
          <div className="flex items-baseline gap-2">
            <span className="font-bold text-accent2">{reg.state}</span>
            <span className="text-muted text-[0.8em]">conf {(reg.confidence * 100).toFixed(0)}%</span>
          </div>
          <div className="text-[0.78em] text-muted mt-1">vol {reg.volatility} · trend {(reg.trend_strength * 100).toFixed(0)}%</div>
        </div>
        {/* Confluence vote */}
        <div className="border border-border rounded-md p-3">
          <div className="text-muted uppercase text-[0.66em] tracking-[1px] mb-2">Confluence</div>
          <div className="flex items-baseline gap-2">
            <span className={`font-bold ${cf.passed ? 'text-green' : 'text-red'}`}>{cf.score.toFixed(0)}</span>
            <span className="text-muted text-[0.8em]">raw {cf.raw_score.toFixed(0)} · {cf.passed ? 'passed' : 'blocked'}</span>
          </div>
          <div className="text-[0.78em] text-muted mt-1">
            {cf.vote.winning_bias} · {cf.vote.agree_count}/{cf.vote.total_engines} engines ({agreePct.toFixed(0)}%)
          </div>
          <div className="mt-1.5 h-1.5 rounded bg-surface overflow-hidden">
            <div className={`h-full ${cf.passed ? 'bg-green' : 'bg-red'}`} style={{ width: `${Math.min(100, agreePct)}%` }} />
          </div>
        </div>
      </div>

      {/* Why NO_TRADE (fail reasons) */}
      {cf.fail_reasons.length > 0 && (
        <div className="border border-red/30 bg-red/5 rounded-md p-3">
          <div className="text-red uppercase text-[0.66em] tracking-[1px] mb-1.5">Blocked by</div>
          <ul className="list-disc pl-5 text-[0.82em] text-red flex flex-col gap-0.5">
            {cf.fail_reasons.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </div>
      )}

      {/* Levels */}
      {(r.entry_price != null || r.stop_loss != null) && (
        <div className="flex gap-4 flex-wrap text-[0.82em]">
          <span>Entry <b className="text-accent">{r.entry_price ?? '—'}</b></span>
          <span>Stop <b className="text-red">{r.stop_loss ?? '—'}</b></span>
          <span>Target <b className="text-green">{r.take_profit ?? '—'}</b></span>
          <span>RR <b>{r.risk_reward ?? '—'}</b></span>
        </div>
      )}

      {/* Provenance */}
      {r.provenance && (
        <div className="text-[0.72em] text-muted border-t border-border pt-2">
          <span className="uppercase tracking-[1px] mr-2">Provenance</span>
          commit <code className="text-accent2">{r.provenance.git_commit?.slice(0, 8) || '?'}</code> · config{' '}
          <code className="text-accent2">{r.provenance.config_hash?.slice(0, 8) || '?'}</code> ·{' '}
          {Object.keys(r.provenance.data_versions ?? {}).length} data sources fingerprinted
        </div>
      )}

      {/* AI explanation (explain-only) */}
      <div className="border-t border-border pt-3">
        <Explanation decision={decision} />
      </div>
    </div>
  )
}

export function AiDecisionCenter() {
  const { markUnauthenticated } = useAuth()
  const decisions = usePolling(() => getDecisions(40), POLL_MS, markUnauthenticated)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)

  const feed = decisions.data?.decisions ?? []
  const summary = decisions.data?.summary
  const keyOf = (d: DecisionEntry) => `${d.timestamp}-${d.symbol}`
  const selected = feed.find((d) => keyOf(d) === selectedKey) ?? feed[0] ?? null
  const executeRate = summary && summary.total > 0 ? (summary.execute / summary.total) * 100 : 0

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.78em] text-muted">
        Explain-only decision transparency (VISION_v2): the pipeline decides deterministically; this tab deconstructs each
        decision and, on demand, narrates it. The AI layer never generates or alters a signal.
      </p>

      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={summary?.total ?? '—'} label="Decisions (window)" color="blue" />
        <KpiCard value={summary?.execute ?? '—'} label="Execute" color="green" />
        <KpiCard value={summary?.no_trade ?? '—'} label="No-Trade" color="amber" />
        <KpiCard value={summary ? `${executeRate.toFixed(0)}%` : '—'} label="Execute Rate" color={executeRate >= 20 ? 'green' : 'default'} />
      </div>

      <Panel title="Why NO_TRADE — reason breakdown" right="aggregated across the window">
        {summary ? <NoTradeReasons reasons={summary.no_trade_reasons ?? {}} /> : <Empty>{decisions.loading ? 'Loading…' : 'No data'}</Empty>}
      </Panel>

      <div className="grid grid-cols-[minmax(220px,300px)_1fr] gap-4 max-[820px]:grid-cols-1">
        <Panel title="Recent Decisions" right={`${feed.length}`}>
          {feed.length > 0 ? (
            <div className="max-h-[520px] overflow-y-auto">
              {feed.map((d) => {
                const k = keyOf(d)
                const active = selected && keyOf(selected) === k
                return (
                  <button
                    key={k}
                    onClick={() => setSelectedKey(k)}
                    className={`w-full text-left px-3 py-2.5 border-b border-border flex items-center justify-between gap-2 ${active ? 'bg-accent/10' : 'hover:bg-surface/50'}`}
                  >
                    <span className="flex flex-col min-w-0">
                      <span className="font-bold text-accent text-[0.82em]">{d.symbol}</span>
                      <span className="text-muted text-[0.68em]">{timeAgo(d.timestamp)}</span>
                    </span>
                    <span className="flex items-center gap-2 shrink-0">
                      <span className="text-muted text-[0.72em] tabular-nums">{d.report.confluence.score.toFixed(0)}</span>
                      <span className={`text-[0.62em] font-bold px-1.5 py-0.5 rounded ${d.final_verdict === 'EXECUTE' ? 'bg-green/15 text-green' : 'bg-red/10 text-red'}`}>
                        {d.final_verdict === 'EXECUTE' ? 'EXEC' : 'NO'}
                      </span>
                    </span>
                  </button>
                )
              })}
            </div>
          ) : (
            <Empty>{decisions.loading ? 'Loading…' : 'No decisions in the log yet'}</Empty>
          )}
        </Panel>

        <Panel title="Decision Anatomy">
          {selected ? <DecisionAnatomy decision={selected} /> : <Empty>Select a decision</Empty>}
        </Panel>
      </div>
    </div>
  )
}
