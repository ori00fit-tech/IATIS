import { useEffect, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { AiStatusFrame } from '../../components/AiStatusFrame'
import { DataTable, type Column } from '../../components/DataTable'
import { PriceChart } from '../../components/PriceChart'
import { getDecisions, getOutcomes, getCandles, explainTrade, type DecisionEntry, type DecisionFilters, type OpenSignal, type TradeExplanation } from './api'

const POLL_MS = 18_000
const CHART_POLL_MS = 30_000

// config/symbols.yaml's internal names — the 20 symbols IATIS actually
// trades. Kept as a small static list rather than a new API round-trip;
// this changes about as often as the symbols table itself does.
const CHART_SYMBOLS = [
  'XAUUSD', 'BTCUSD', 'ETHUSD', 'XAGUSD', 'USOIL',
  'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD',
  'EURJPY', 'GBPJPY', 'AUDJPY', 'EURGBP', 'EURCHF',
  'US30', 'NAS100', 'SPX500',
]
const CHART_INTERVALS = ['M15', 'H1', 'H4', 'D1'] as const

function ChartPanel() {
  const { markUnauthenticated } = useAuth()
  const [symbol, setSymbol] = useState('XAUUSD')
  const [interval, setInterval] = useState<(typeof CHART_INTERVALS)[number]>('H4')
  const candles = usePolling(() => getCandles(symbol, interval), CHART_POLL_MS, markUnauthenticated)

  useEffect(() => {
    candles.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, interval])

  const select = 'bg-surface border border-border rounded px-2 py-1.5 text-[0.82em] text-text'

  return (
    <Panel
      title="Price Chart"
      right={candles.data ? `via ${candles.data.provider} · ${candles.data.bars.length} bars` : undefined}
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-surface/40">
        <select className={select} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
          {CHART_SYMBOLS.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select className={select} value={interval} onChange={(e) => setInterval(e.target.value as (typeof CHART_INTERVALS)[number])}>
          {CHART_INTERVALS.map((i) => (
            <option key={i} value={i}>{i}</option>
          ))}
        </select>
        {candles.data?.signal && (
          <Badge tone={candles.data.signal.verdict === 'EXECUTE' ? 'exec' : 'no-trade'}>
            {`Last: ${candles.data.signal.verdict}`}
          </Badge>
        )}
      </div>
      {candles.data && candles.data.bars.length > 0 ? (
        <PriceChart bars={candles.data.bars} signal={candles.data.signal} />
      ) : (
        <Empty>{candles.loading ? 'Loading candles...' : candles.error ? `Error: ${candles.error.message}` : 'No candle data'}</Empty>
      )}
    </Panel>
  )
}

function scoreColor(score: number) {
  return score >= 65 ? 'text-green' : score >= 55 ? 'text-amber' : 'text-red'
}

interface FilterState {
  verdict: string
  symbol: string
  engine: string
  minScore: string
  dateFrom: string
  dateTo: string
  reason: string
  riskRejected: boolean
}

const EMPTY_FILTERS: FilterState = {
  verdict: '',
  symbol: '',
  engine: '',
  minScore: '',
  dateFrom: '',
  dateTo: '',
  reason: '',
  riskRejected: false,
}

function toApiFilters(f: FilterState): DecisionFilters {
  const out: DecisionFilters = {}
  if (f.verdict) out.verdict = f.verdict
  if (f.symbol) out.symbol = f.symbol.toUpperCase()
  if (f.engine) out.engine = f.engine
  if (f.minScore) out.min_score = Number(f.minScore)
  if (f.dateFrom) out.date_from = f.dateFrom
  if (f.dateTo) out.date_to = f.dateTo
  if (f.reason) out.reason = f.reason
  if (f.riskRejected) out.risk_rejected = true
  return out
}

function DecisionFilterBar({
  filters,
  onChange,
  onApply,
  onClear,
}: {
  filters: FilterState
  onChange: (f: FilterState) => void
  onApply: () => void
  onClear: () => void
}) {
  const input = 'bg-surface border border-border rounded px-2 py-1.5 text-[0.82em] text-text placeholder:text-muted'
  return (
    <div className="flex flex-wrap items-end gap-2 px-4 py-3 border-b border-border bg-surface/40">
      <label className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Symbol</span>
        <input className={input} placeholder="EURUSD" value={filters.symbol} onChange={(e) => onChange({ ...filters, symbol: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Verdict</span>
        <select className={input} value={filters.verdict} onChange={(e) => onChange({ ...filters, verdict: e.target.value })}>
          <option value="">All</option>
          <option value="EXECUTE">EXECUTE</option>
          <option value="NO_TRADE">NO_TRADE</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Engine</span>
        <input className={input} placeholder="smc, nnfx..." value={filters.engine} onChange={(e) => onChange({ ...filters, engine: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Min score</span>
        <input className={`${input} w-20`} type="number" min={0} max={100} value={filters.minScore} onChange={(e) => onChange({ ...filters, minScore: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">From</span>
        <input className={input} type="date" value={filters.dateFrom} onChange={(e) => onChange({ ...filters, dateFrom: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">To</span>
        <input className={input} type="date" value={filters.dateTo} onChange={(e) => onChange({ ...filters, dateTo: e.target.value })} />
      </label>
      <label className="flex flex-col gap-1 min-w-[160px]">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Reason contains</span>
        <input className={input} placeholder="e.g. RR too low" value={filters.reason} onChange={(e) => onChange({ ...filters, reason: e.target.value })} />
      </label>
      <label className="flex items-center gap-1.5 pb-1.5 text-[0.78em] text-muted">
        <input type="checkbox" checked={filters.riskRejected} onChange={(e) => onChange({ ...filters, riskRejected: e.target.checked })} />
        Risk-rejected only
      </label>
      <div className="flex gap-2 pb-0.5">
        <button onClick={onApply} className="px-3 py-1.5 text-[0.78em] rounded bg-accent/15 text-accent hover:bg-accent/25">
          Apply
        </button>
        <button onClick={onClear} className="px-3 py-1.5 text-[0.78em] rounded text-muted hover:text-text">
          Clear
        </button>
      </div>
    </div>
  )
}

function DecisionJsonPanel({ decision, onClose }: { decision: DecisionEntry; onClose: () => void }) {
  const prov = decision.report.provenance
  return (
    <Panel
      title={`Decision JSON — ${decision.symbol} @ ${decision.timestamp}`}
      right={<button onClick={onClose} className="text-muted hover:text-text">✕ close</button>}
    >
      {prov && (
        <div className="px-4 py-2.5 border-b border-border text-[0.75em] flex flex-wrap gap-x-5 gap-y-1">
          <span>
            <span className="text-muted">code </span>
            <span className="text-accent font-mono">{prov.git_commit}</span>
          </span>
          <span>
            <span className="text-muted">config </span>
            <span className="text-accent font-mono">{prov.config_hash}</span>
          </span>
          {Object.entries(prov.data_versions).map(([tf, v]) => (
            <span key={tf} title={v.sha256 ? `sha256 ${v.sha256} · ${v.first_ts ?? '?'} → ${v.last_ts ?? '?'}` : v.error}>
              <span className="text-muted">{tf} </span>
              <span className={v.error ? 'text-red' : ''}>
                {v.error ? 'error' : `${v.provider ?? '?'}·${v.row_count ?? '?'} bars`}
              </span>
            </span>
          ))}
        </div>
      )}
      <pre className="p-4 text-[0.78em] overflow-auto max-h-[480px] whitespace-pre-wrap break-words">
        {JSON.stringify(decision, null, 2)}
      </pre>
    </Panel>
  )
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
        <AiStatusFrame
          loading={state.loading}
          fetchError={state.error}
          status={state.data?.status}
          providerError={state.data?.error}
          disabledHint="AI explanations are disabled (set `ai.enabled: true` and an API key to turn this on)."
        >
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
        </AiStatusFrame>
      </div>
    </Panel>
  )
}

export function LiveSignals() {
  const { markUnauthenticated } = useAuth()
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS)
  const [appliedFilters, setAppliedFilters] = useState<FilterState>(EMPTY_FILTERS)
  const decisions = usePolling(() => getDecisions(30, toApiFilters(appliedFilters)), POLL_MS, markUnauthenticated)
  const outcomes = usePolling(() => getOutcomes(20), POLL_MS, markUnauthenticated)
  const [explaining, setExplaining] = useState<DecisionEntry | null>(null)
  const [viewingJson, setViewingJson] = useState<DecisionEntry | null>(null)

  useEffect(() => {
    decisions.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(appliedFilters)])

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
    {
      header: 'JSON',
      render: (d) => (
        <button
          onClick={() => setViewingJson(d)}
          className="text-muted hover:text-text text-[0.85em] underline decoration-dotted"
          title="View the full decision report"
        >
          View
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

      <ChartPanel />

      <Panel
        title="Decision Explorer"
        right={decisions.data ? `${decisions.data.matched} matched / ${decisions.data.total_in_log} logged` : undefined}
      >
        <DecisionFilterBar
          filters={filters}
          onChange={setFilters}
          onApply={() => setAppliedFilters(filters)}
          onClear={() => {
            setFilters(EMPTY_FILTERS)
            setAppliedFilters(EMPTY_FILTERS)
          }}
        />
        {decisions.data && decisions.data.decisions.length > 0 ? (
          <DataTable columns={decisionColumns} rows={decisions.data.decisions} rowKey={(d) => `${d.timestamp}-${d.symbol}`} />
        ) : (
          <Empty>{decisions.loading ? 'Loading...' : 'No decisions match these filters'}</Empty>
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
      {viewingJson && <DecisionJsonPanel decision={viewingJson} onClose={() => setViewingJson(null)} />}
    </div>
  )
}
