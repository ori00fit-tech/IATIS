import { useState } from 'react'
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
  type Hypothesis,
  type BacktestResult,
  type RegimeRow,
  type EvidenceManifest,
  type AiResearchSummary,
} from './api'

const POLL_MS = 60_000

function pfBadge(pf: number) {
  if (pf >= 1.5) return 'good' as const
  if (pf >= 1.1) return 'marginal' as const
  return 'poor' as const
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
    { header: 'ID', render: (h) => <span className="font-bold text-accent">{h.id}</span> },
    { header: 'Title', render: (h) => h.title },
    { header: 'Status', render: (h) => <Badge tone={statusTone(h.status)}>{h.status}</Badge> },
    { header: 'N', render: (h) => h.sample_size ?? '—', align: 'right' },
    { header: 'Win Rate', render: (h) => (h.win_rate != null ? `${h.win_rate}%` : '—'), align: 'right' },
    { header: 'p-value', render: (h) => h.p_value ?? '—', align: 'right' },
  ]

  const backtestColumns: Column<BacktestResult>[] = [
    { header: 'Symbol', render: (r) => <span className="font-bold text-accent">{r.symbol}</span> },
    { header: 'Trades', render: (r) => r.trades, align: 'right' },
    { header: 'WR%', render: (r) => r.win_rate, align: 'right' },
    { header: 'PF', render: (r) => <Badge tone={pfBadge(r.profit_factor)}>{r.profit_factor.toFixed(2)}</Badge>, align: 'right' },
    { header: 'DD%', render: (r) => <span className="text-red">{r.max_drawdown_pct}%</span>, align: 'right' },
    {
      header: 'Return%',
      render: (r) => <span className={r.total_return_pct >= 0 ? 'text-green' : 'text-red'}>{r.total_return_pct}%</span>,
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

      <Panel title="Hypothesis Registry">
        {research.data && research.data.hypotheses.length > 0 ? (
          <DataTable columns={hypothesisColumns} rows={research.data.hypotheses} rowKey={(h) => h.id} />
        ) : (
          <Empty>{research.loading ? 'Loading...' : 'No hypotheses registered yet'}</Empty>
        )}
      </Panel>

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
