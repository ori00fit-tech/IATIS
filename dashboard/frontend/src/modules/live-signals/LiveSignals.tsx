import { useEffect, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { getDecisions, getOutcomes, explainTrade, type DecisionEntry, type OpenSignal, type TradeExplanation } from './api'

const POLL_MS = 18_000

function scoreColor(score: number) {
  return score >= 65 ? 'text-green' : score >= 55 ? 'text-amber' : 'text-red'
}

function AIExplanationPanel({ decision, onClose }: { decision: DecisionEntry; onClose: () => void }) {
  const [state, setState] = useState<{ loading: boolean; data: TradeExplanation | null; error: string | null }>({
    loading: true,
    data: null,
    error: null,
  })

  useEffect(() => {
    let cancelled = false
    setState({ loading: true, data: null, error: null })
    explainTrade(decision.report)
      .then((data) => {
        if (!cancelled) setState({ loading: false, data, error: null })
      })
      .catch((err) => {
        if (!cancelled) setState({ loading: false, data: null, error: err instanceof Error ? err.message : String(err) })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decision.symbol, decision.timestamp])

  return (
    <Panel title={`AI Explanation — ${decision.symbol}`} right={<button onClick={onClose} className="text-muted hover:text-text">✕ close</button>}>
      <div className="p-4">
        {state.loading && <Empty>Asking the AI provider…</Empty>}
        {state.error && <Empty>Request failed: {state.error}</Empty>}
        {state.data?.status === 'disabled' && (
          <Empty>AI explanations are disabled (set `ai.enabled: true` and an API key to turn this on).</Empty>
        )}
        {state.data?.status === 'error' && <Empty>Provider error: {state.data.error}</Empty>}
        {state.data?.status === 'ok' && (
          <div className="flex flex-col gap-3 text-[0.9em]">
            <p>{state.data.explanation || state.data.summary}</p>
            <div className="flex gap-3 flex-wrap">
              <Badge tone={state.data.market_sentiment?.toLowerCase() === 'bullish' ? 'exec' : state.data.market_sentiment?.toLowerCase() === 'bearish' ? 'no-trade' : 'neutral'}>
                {state.data.market_sentiment || 'NEUTRAL'}
              </Badge>
              <Badge tone={state.data.risk_level === 'HIGH' ? 'poor' : state.data.risk_level === 'MEDIUM' ? 'marginal' : 'good'}>
                {`Risk: ${state.data.risk_level}`}
              </Badge>
              <span className="text-muted">Confidence: {state.data.confidence}%</span>
              <span className="text-muted">via {state.data.provider}</span>
            </div>
            {state.data.pros.length > 0 && (
              <div>
                <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1">Pros</div>
                <ul className="list-disc pl-5 text-green">{state.data.pros.map((p, i) => <li key={i}>{p}</li>)}</ul>
              </div>
            )}
            {state.data.cons.length > 0 && (
              <div>
                <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1">Cons</div>
                <ul className="list-disc pl-5 text-red">{state.data.cons.map((c, i) => <li key={i}>{c}</li>)}</ul>
              </div>
            )}
            {state.data.warnings.length > 0 && (
              <div className="text-amber">
                {state.data.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
              </div>
            )}
            {state.data.recommendation && <p className="text-muted italic">{state.data.recommendation}</p>}
          </div>
        )}
      </div>
    </Panel>
  )
}

export function LiveSignals() {
  const { markUnauthenticated } = useAuth()
  const decisions = usePolling(() => getDecisions(30), POLL_MS, markUnauthenticated)
  const outcomes = usePolling(() => getOutcomes(20), POLL_MS, markUnauthenticated)
  const [explaining, setExplaining] = useState<DecisionEntry | null>(null)

  const s = decisions.data?.summary
  const total = s?.total ?? 0
  const exec = s?.execute ?? 0
  const execRate = total > 0 ? `${((exec / total) * 100).toFixed(1)}%` : '—'

  const decisionColumns: Column<DecisionEntry>[] = [
    { header: 'Time', render: (d) => <span className="text-muted">{d.timestamp.slice(11, 19)}</span> },
    { header: 'Symbol', render: (d) => <span className="font-bold text-accent">{d.symbol}</span> },
    { header: 'Verdict', render: (d) => <Badge tone={d.final_verdict === 'EXECUTE' ? 'exec' : 'no-trade'}>{d.final_verdict}</Badge> },
    { header: 'Regime', render: (d) => d.report?.regime?.state ?? '—' },
    {
      header: 'Score',
      render: (d) => <span className={`font-bold ${scoreColor(d.report?.confluence?.score ?? 0)}`}>{(d.report?.confluence?.score ?? 0).toFixed(0)}</span>,
      align: 'right',
    },
    {
      header: 'Summary',
      render: (d) => (
        <span className="text-muted text-[0.92em] block max-w-[280px] truncate" title={d.report?.summary}>
          {d.report?.summary ?? ''}
        </span>
      ),
    },
    {
      header: 'AI',
      render: (d) => (
        <button
          onClick={() => setExplaining(d)}
          className="text-accent hover:text-accent2 text-[0.85em] underline decoration-dotted"
          title="Ask the AI provider to explain this decision in plain English"
        >
          Explain
        </button>
      ),
      align: 'right',
    },
  ]

  const openColumns: Column<OpenSignal>[] = [
    { header: 'Signal', render: (o) => <span className="text-muted text-[0.85em]">{o.signal_id}</span> },
    { header: 'Symbol', render: (o) => <span className="font-bold text-accent">{o.symbol}</span> },
    {
      header: 'Direction',
      render: (o) => <span className={`font-bold ${o.direction === 'BULLISH' || o.direction === 'BUY' ? 'text-green' : 'text-red'}`}>{o.direction}</span>,
    },
    { header: 'Entry', render: (o) => o.entry_price ?? '—', align: 'right' },
    { header: 'Score', render: (o) => <span className={scoreColor(o.cf_score)}>{o.cf_score}</span>, align: 'right' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={total} label="Total Decisions" color="blue" />
        <KpiCard value={exec} label="EXECUTE" color="green" />
        <KpiCard value={total - exec} label="NO_TRADE" color="default" />
        <KpiCard value={execRate} label="Execute Rate" color="purple" />
        <KpiCard value={outcomes.data?.summary.open_signals ?? '—'} label="Open Signals" color="amber" />
        <KpiCard value={outcomes.data?.summary.win_rate != null ? `${outcomes.data.summary.win_rate.toFixed(1)}%` : '—'} label="Win Rate" color="green" />
      </div>

      <Panel title="Recent Decisions" right={decisions.data ? `${decisions.data.total_in_log} logged` : undefined}>
        {decisions.data && decisions.data.decisions.length > 0 ? (
          <DataTable columns={decisionColumns} rows={decisions.data.decisions} rowKey={(d) => `${d.timestamp}-${d.symbol}`} />
        ) : (
          <Empty>{decisions.loading ? 'Loading...' : 'No decisions logged yet'}</Empty>
        )}
      </Panel>

      <Panel title="Open Signals" right="Paper trading">
        {outcomes.data && outcomes.data.open_signals.length > 0 ? (
          <DataTable columns={openColumns} rows={outcomes.data.open_signals} rowKey={(o) => o.signal_id} />
        ) : (
          <Empty>{outcomes.loading ? 'Loading...' : 'No open signals'}</Empty>
        )}
      </Panel>

      {explaining && <AIExplanationPanel decision={explaining} onClose={() => setExplaining(null)} />}
    </div>
  )
}
