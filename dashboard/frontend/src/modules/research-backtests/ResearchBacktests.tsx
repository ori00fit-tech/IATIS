import { useEffect, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { AiStatusFrame } from '../../components/AiStatusFrame'
import { DataTable, type Column } from '../../components/DataTable'
import {
  getResearch,
  getBacktestResults,
  getMetaAnalysis,
  getManifests,
  getAiResearchSummary,
  getHypothesisDetail,
  type Hypothesis,
  type BacktestResult,
  type RegimeRow,
  type EvidenceManifest,
  type AiResearchSummary,
  type HypothesisDetailResponse,
} from './api'
import { getJobDetail, runJob, type JobDetail } from '../experiment-runner/api'

const POLL_MS = 60_000

function pfBadge(pf: number) {
  if (pf >= 1.5) return 'good' as const
  if (pf >= 1.1) return 'marginal' as const
  return 'poor' as const
}

function finiteNum(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

// Collapse both /backtest-results shapes into one set of percentage-scaled
// numbers. New rows carry top-level percentages; legacy rows carry only
// `metrics` as fractions (×100 here). Anything missing stays undefined so the
// table renders '—' instead of throwing on .toFixed of undefined — the exact
// crash the Research & Backtests panel was hitting against legacy files.
function normalizeBacktest(r: BacktestResult) {
  const m = r.metrics ?? {}
  const asPct = (top: unknown, frac: unknown): number | undefined => {
    const t = finiteNum(top)
    if (t !== undefined) return t
    const f = finiteNum(frac)
    return f !== undefined ? f * 100 : undefined
  }
  return {
    trades: finiteNum(r.trades) ?? finiteNum(m.trades_closed),
    winRate: asPct(r.win_rate, m.win_rate),
    profitFactor: finiteNum(r.profit_factor) ?? finiteNum(m.profit_factor),
    maxDrawdownPct: asPct(r.max_drawdown_pct, m.max_drawdown_pct),
    totalReturnPct: asPct(r.total_return_pct, m.total_return_pct),
  }
}

function statusTone(status: string) {
  if (status === 'PASSED') return 'exec' as const
  if (status.includes('FAILED')) return 'no-trade' as const
  return 'neutral' as const
}

function ManifestCard({ m }: { m: EvidenceManifest }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-b border-border last:border-b-0">
      <button onClick={() => setOpen(!open)} className="w-full text-left px-4 py-3 hover:bg-surface/50">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="font-bold text-accent text-[0.85em]">{m.kind}</span>
          <span
            className={`text-[0.68em] font-bold uppercase tracking-[1px] px-1.5 py-0.5 rounded border ${
              m.reproducible ? 'text-green border-green/40' : 'text-red border-red/40'
            }`}
            title={m.reproducible ? 'Bound to a clean git commit — independently re-runnable' : 'Generated from a dirty/unknown git state — not verifiable'}
          >
            {m.reproducible ? 'reproducible' : 'not reproducible'}
          </span>
          {m.decision_timeframe && <span className="text-[0.7em] text-accent2 font-bold">{m.decision_timeframe}</span>}
          <span className="text-muted text-[0.7em] ml-auto">
            {m.generated_at?.slice(0, 10)} · commit {m.git_commit || '?'} · {m.datasets_count} datasets
          </span>
        </div>
      </button>
      {open && (
        <div className="px-4 pb-3 text-[0.78em]">
          {m.engines_enabled && <p className="text-muted mb-1">engines: {m.engines_enabled.join(', ')}</p>}
          {m.note && <p className="text-muted mb-2">{m.note}</p>}
          {m.results && (
            <pre className="bg-surface border border-border rounded p-2 overflow-x-auto text-[0.85em] max-h-64 overflow-y-auto">
              {JSON.stringify(m.results, null, 1)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

function HypothesisDetailPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: HypothesisDetailResponse | null }>({
    loading: true,
    error: null,
    data: null,
  })

  useEffect(() => {
    let cancelled = false
    setState({ loading: true, error: null, data: null })
    getHypothesisDetail(id)
      .then((data) => !cancelled && setState({ loading: false, error: null, data }))
      .catch((err) => !cancelled && setState({ loading: false, error: err instanceof Error ? err.message : String(err), data: null }))
    return () => {
      cancelled = true
    }
  }, [id])

  const hyp = state.data?.hypothesis as Record<string, unknown> | undefined
  // Fields rendered specially below; everything else in the raw dump so
  // nothing in registry.json is ever hidden, just de-duplicated.
  const SPECIAL_KEYS = new Set(['status', 'title', 'last_updated', 'conclusion', 'lesson', 'manifest', 'result_file', 'result_files'])
  const rest = hyp ? Object.fromEntries(Object.entries(hyp).filter(([k]) => !SPECIAL_KEYS.has(k))) : {}

  return (
    <Panel title={`Hypothesis ${id}`} right={<button onClick={onClose} className="text-muted hover:text-text">✕ close</button>}>
      {state.loading ? (
        <Empty>Loading...</Empty>
      ) : state.error ? (
        <Empty>Failed: {state.error}</Empty>
      ) : !hyp ? (
        <Empty>Not found</Empty>
      ) : (
        <div className="p-4 flex flex-col gap-4 text-[0.85em]">
          <div className="flex items-center gap-3 flex-wrap">
            <Badge tone={statusTone(String(hyp.status ?? ''))}>{String(hyp.status ?? 'UNKNOWN')}</Badge>
            <span className="font-bold">{String(hyp.title ?? '')}</span>
            <span className="text-muted text-[0.85em] ml-auto">updated {String(hyp.last_updated ?? '?')}</span>
          </div>
          {typeof hyp.conclusion === 'string' && hyp.conclusion && (
            <div>
              <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1">Conclusion</div>
              <p>{hyp.conclusion}</p>
            </div>
          )}
          {typeof hyp.lesson === 'string' && hyp.lesson && (
            <div>
              <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1">Lesson</div>
              <p>{hyp.lesson}</p>
            </div>
          )}

          <div>
            <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1">
              Linked Manifests {state.data && `(${state.data.manifests.exact.length} exact, ${state.data.manifests.heuristic.length} heuristic)`}
            </div>
            {state.data && (state.data.manifests.exact.length > 0 || state.data.manifests.heuristic.length > 0) ? (
              <div className="border border-border rounded">
                {state.data.manifests.exact.map((m) => (
                  <ManifestCard key={m.file} m={m} />
                ))}
                {state.data.manifests.heuristic.map((m) => (
                  <div key={m.file} className="opacity-70" title="Heuristic match — hypothesis ID found in filename/kind, not a declared link">
                    <ManifestCard m={m} />
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-muted">No linked manifests found.</p>
            )}
          </div>

          {state.data && state.data.result_files.length > 0 && (
            <div>
              <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1">Result Files</div>
              {state.data.result_files.map((rf) => (
                <div key={rf.path} className="font-mono text-[0.85em]">
                  <span className={rf.exists ? 'text-green' : 'text-red'}>{rf.exists ? '✓' : '✗'}</span> {rf.path}
                </div>
              ))}
            </div>
          )}

          {Object.keys(rest).length > 0 && (
            <div>
              <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1">Everything else in registry.json</div>
              <pre className="bg-surface border border-border rounded p-2 overflow-x-auto text-[0.85em] max-h-64 overflow-y-auto">
                {JSON.stringify(rest, null, 1)}
              </pre>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

// Run the real cost-inclusive backtest engine from the dashboard
// (operator request 2026-07-16). Server-side, symbols are validated
// against the configured universe — the argv is never user-shaped.
// IMPORTANT epistemics (the registry's own rule): results are IN-SAMPLE
// scans, not evidence — no decision flows from a dashboard backtest;
// D001/D002 come from forward_review only.
const BT_PRESETS: { label: string; symbols: string[] }[] = [
  { label: 'Carriers', symbols: ['XAUUSD', 'BTCUSD', 'ETHUSD'] },
  { label: 'FX majors', symbols: ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD', 'AUDUSD', 'NZDUSD'] },
]

const BT_BADGE: Record<string, 'exec' | 'no-trade' | 'good' | 'marginal' | 'neutral'> = {
  queued: 'neutral',
  running: 'marginal',
  finished: 'exec',
  failed: 'no-trade',
  timeout: 'no-trade',
}

function BacktestRunner() {
  const [symbolsText, setSymbolsText] = useState('XAUUSD BTCUSD ETHUSD')
  const [starting, setStarting] = useState(false)
  const [job, setJob] = useState<JobDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!job || job.status === 'finished' || job.status === 'failed' || job.status === 'timeout') return
    const id = setInterval(() => {
      getJobDetail(job.job_id).then(setJob).catch(() => {})
    }, 3000)
    return () => clearInterval(id)
  }, [job])

  const run = async () => {
    const symbols = symbolsText.toUpperCase().split(/[\s,]+/).filter(Boolean)
    setStarting(true)
    setError(null)
    try {
      const summary = await runJob('backtest', symbols)
      setJob({ ...summary, log: [] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const busy = starting || job?.status === 'queued' || job?.status === 'running'

  return (
    <Panel title="Run Backtest" right="in-sample scan — never evidence; decisions come from forward_review only">
      <div className="flex flex-col gap-2.5 p-4">
        <div className="flex items-center gap-2 flex-wrap">
          <input
            value={symbolsText}
            onChange={(e) => setSymbolsText(e.target.value)}
            placeholder="XAUUSD BTCUSD ETHUSD"
            className="flex-1 min-w-[240px] px-3 py-1.5 text-[0.82em] rounded border border-border bg-bg text-text font-mono"
          />
          {BT_PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => setSymbolsText(p.symbols.join(' '))}
              className="px-2.5 py-1.5 text-[0.75em] rounded border border-border text-muted hover:text-accent hover:border-accent"
            >
              {p.label}
            </button>
          ))}
          <button
            onClick={run}
            disabled={busy}
            className="px-4 py-1.5 text-[0.78em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50 font-bold"
          >
            {busy ? 'Running…' : 'Run backtest'}
          </button>
          {job && <Badge tone={BT_BADGE[job.status] ?? 'neutral'}>{job.status}</Badge>}
          {error && <span className="text-red text-[0.78em]">{error}</span>}
        </div>
        <p className="text-[0.72em] text-muted">
          Real engine, real measured spreads, gap-aware exits — on local H1 datasets, symbols validated
          server-side against the configured universe. CPU-minutes on the VPS; one backtest at a time.
          Reports land in reports/ (Reports tab).
        </p>
        {job && job.log.length > 0 && (
          <pre className="p-3 bg-bg/60 rounded text-[0.72em] overflow-auto max-h-[280px] whitespace-pre-wrap break-words font-mono">
            {job.log.slice(-120).join('\n')}
          </pre>
        )}
      </div>
    </Panel>
  )
}

export function ResearchBacktests() {
  const { markUnauthenticated } = useAuth()
  const research = usePolling(getResearch, POLL_MS, markUnauthenticated)
  const backtests = usePolling(getBacktestResults, POLL_MS, markUnauthenticated)
  const meta = usePolling(getMetaAnalysis, POLL_MS, markUnauthenticated)
  const manifests = usePolling(getManifests, POLL_MS, markUnauthenticated)

  const hs = research.data?.hypothesis_summary
  const [ai, setAi] = useState<{ loading: boolean; error: string | null; data: AiResearchSummary | null }>({
    loading: false,
    error: null,
    data: null,
  })
  const [drilldown, setDrilldown] = useState<string | null>(null)

  const generateAiSummary = () => {
    if (!research.data) return
    setAi({ loading: true, error: null, data: null })
    getAiResearchSummary({
      hypothesis_summary: research.data.hypothesis_summary,
      latest_backtest: research.data.latest_backtest,
      regime_matrix: meta.data?.regime_matrix.data ?? [],
    })
      .then((data) => setAi({ loading: false, error: null, data }))
      .catch((err) => setAi({ loading: false, error: err instanceof Error ? err.message : String(err), data: null }))
  }

  const hypothesisColumns: Column<Hypothesis>[] = [
    {
      header: 'ID',
      render: (h) => (
        <button onClick={() => setDrilldown(h.id)} className="font-bold text-accent hover:text-accent2 underline decoration-dotted">
          {h.id}
        </button>
      ),
    },
    { header: 'Title', render: (h) => <span title={h.conclusion || undefined}>{h.title}</span> },
    {
      header: 'Status',
      render: (h) =>
        h.status === 'PASSED' && h.trusted === false ? (
          <Badge tone="marginal">PASSED (untrusted)</Badge>
        ) : (
          <Badge tone={statusTone(h.status)}>{h.status}</Badge>
        ),
    },
    { header: 'N', render: (h) => h.sample_size ?? '—', align: 'right' },
    { header: 'Win Rate', render: (h) => (h.win_rate != null ? `${h.win_rate}%` : '—'), align: 'right' },
    { header: 'p-value', render: (h) => h.p_value ?? '—', align: 'right' },
  ]

  const backtestColumns: Column<BacktestResult>[] = [
    { header: 'Symbol', render: (r) => <span className="font-bold text-accent">{r.symbol}</span> },
    { header: 'Trades', render: (r) => normalizeBacktest(r).trades ?? '—', align: 'right' },
    {
      header: 'WR%',
      render: (r) => {
        const wr = normalizeBacktest(r).winRate
        return wr != null ? `${wr.toFixed(1)}%` : '—'
      },
      align: 'right',
    },
    {
      header: 'PF',
      render: (r) => {
        const pf = normalizeBacktest(r).profitFactor
        return pf != null ? <Badge tone={pfBadge(pf)}>{pf.toFixed(2)}</Badge> : <span className="text-muted">—</span>
      },
      align: 'right',
    },
    {
      header: 'DD%',
      render: (r) => {
        const dd = normalizeBacktest(r).maxDrawdownPct
        return dd != null ? <span className="text-red">{dd.toFixed(1)}%</span> : <span className="text-muted">—</span>
      },
      align: 'right',
    },
    {
      header: 'Return%',
      render: (r) => {
        const ret = normalizeBacktest(r).totalReturnPct
        return ret != null ? (
          <span className={ret >= 0 ? 'text-green' : 'text-red'}>{ret.toFixed(1)}%</span>
        ) : (
          <span className="text-muted">—</span>
        )
      },
      align: 'right',
    },
  ]

  const regimeColumns: Column<RegimeRow>[] = [
    { header: 'Regime', render: (r) => <span className="font-bold text-accent">{r.regime}</span> },
    { header: 'Decisions', render: (r) => r.total_decisions, align: 'right' },
    { header: 'Execute Rate', render: (r) => `${r.execute_rate}%`, align: 'right' },
    { header: 'Win Rate', render: (r) => (r.win_rate != null ? `${r.win_rate}%` : '—'), align: 'right' },
    { header: 'PF', render: (r) => r.profit_factor ?? '—', align: 'right' },
    { header: 'Expectancy $', render: (r) => r.expectancy_usd ?? '—', align: 'right' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={hs?.total ?? '—'} label="Hypotheses" color="blue" />
        <KpiCard value={hs?.passed ?? '—'} label="Passed" color="green" />
        <KpiCard value={hs?.failed ?? '—'} label="Failed" color="red" />
        <KpiCard value={hs?.research ?? '—'} label="In Research" color="amber" />
        <KpiCard value={research.data?.latest_backtest?.avg_pf?.toFixed(2) ?? '—'} label="Avg PF (latest BT)" color="purple" />
      </div>

      <BacktestRunner />

      <Panel
        title="AI Research Summary"
        right={
          <button
            onClick={generateAiSummary}
            disabled={ai.loading || !research.data}
            className="text-accent hover:text-accent2 text-[0.78em] disabled:opacity-50"
          >
            {ai.loading ? 'Generating…' : ai.data ? 'Regenerate' : 'Generate'}
          </button>
        }
      >
        <div className="p-4">
          {!ai.data && !ai.loading && !ai.error ? (
            <Empty>On-demand only — phrases the hypothesis registry, latest backtest, and regime matrix below. Click Generate.</Empty>
          ) : (
            <AiStatusFrame loading={ai.loading} fetchError={ai.error} status={ai.data?.status} providerError={ai.data?.error}>
              <p className="text-[0.9em]">{ai.data?.text}</p>
            </AiStatusFrame>
          )}
        </div>
      </Panel>

      <Panel
        title="Evidence Manifests"
        right={manifests.data ? `${manifests.data.count} runs · git-tracked, SHA256-fingerprinted` : undefined}
      >
        {manifests.data && manifests.data.manifests.length > 0 ? (
          <div>
            {manifests.data.manifests.map((m) => (
              <ManifestCard key={m.file} m={m} />
            ))}
          </div>
        ) : (
          <Empty>
            {manifests.loading
              ? 'Loading...'
              : 'No evidence manifests yet — research runs write them to research/results/'}
          </Empty>
        )}
      </Panel>

      {research.data?.trust_audit && research.data.trust_audit.warnings.length > 0 && (
        <Panel
          title="Edge Gate Trust Audit"
          right={`promotion bar: ≥${research.data.trust_audit.criteria.min_trades ?? 300} OOS trades · PF ≥ ${research.data.trust_audit.criteria.min_oos_pf ?? 1.2} · walk-forward · Monte Carlo`}
        >
          <div className="p-4 flex flex-col gap-2">
            {research.data.trust_audit.warnings.map((w, i) => (
              <div key={i} className="text-[0.8em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
                ⚠️ {w}
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Panel title="Hypothesis Registry" right="click an ID for the full drill-down">
        {research.data && research.data.hypotheses.length > 0 ? (
          <DataTable columns={hypothesisColumns} rows={research.data.hypotheses} rowKey={(h) => h.id} />
        ) : (
          <Empty>{research.loading ? 'Loading...' : 'No hypotheses registered yet'}</Empty>
        )}
      </Panel>

      {drilldown && <HypothesisDetailPanel id={drilldown} onClose={() => setDrilldown(null)} />}

      <Panel title="Backtest Results" right={backtests.data ? `${backtests.data.count} runs` : undefined}>
        {backtests.data && backtests.data.results.length > 0 ? (
          <DataTable columns={backtestColumns} rows={backtests.data.results} rowKey={(r) => `${r.file}-${r.symbol}`} />
        ) : (
          <Empty>{backtests.loading ? 'Loading...' : 'No backtest results yet'}</Empty>
        )}
      </Panel>

      <Panel title="Regime Performance Matrix" right={meta.data?.regime_matrix.note}>
        {meta.data && meta.data.regime_matrix.data.length > 0 ? (
          <DataTable columns={regimeColumns} rows={meta.data.regime_matrix.data} rowKey={(r) => r.regime} />
        ) : (
          <Empty>{meta.loading ? 'Loading...' : 'No regime performance data yet'}</Empty>
        )}
      </Panel>
    </div>
  )
}
