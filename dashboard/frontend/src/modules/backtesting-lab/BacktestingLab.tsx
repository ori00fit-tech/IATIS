import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useApiQuery } from '../../lib/useApiQuery'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { AiResearchAssistant } from '../../components/AiResearchAssistant'
import {
  getResearchDatasets,
  getResearchSymbols,
  getResearchEngines,
  getResearchIndicators,
  getResearch,
  getValidationConfig,
  getJobCatalog,
  runJob,
  runRobustnessJob,
  getJobDetail,
  type DatasetEntry,
  type SymbolEntry,
  type EngineEntry,
  type IndicatorEntry,
  type JobDescriptor,
  type JobDetail,
} from './api'

const POLL_MS = 60_000

// ─────────────────────────────────────────────────────────────────────────
// Backtesting Lab (Research Workbench, 2026-07-25) — a step-by-step
// experiment builder, distinct from the Research & Backtests monitoring
// dashboard: that tab shows results, this one assembles a run. Every step
// is wired to a REAL endpoint already backing the monitoring tab — this
// is a different UI shape over identical data, not new backend surface,
// with ONE exception: the Strategy/Indicators/Timeframes steps are
// deliberately READ-ONLY. There is no backend support today for a
// per-run engine-weight or indicator-parameter override, and building
// fake controls that don't affect the actual run would be dishonest UI —
// CLAUDE.md also forbids changing engine weights outside a new
// pre-registered hypothesis, so "read-only, labeled why" is correct
// either way, not just a shortcut.
// ─────────────────────────────────────────────────────────────────────────

const STEPS = ['Dataset', 'Symbols', 'Timeframes', 'Strategy', 'Indicators', 'Hypotheses', 'Execution', 'Results'] as const
type StepIndex = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7

interface WizardState {
  selectedSymbols: string[]
  jobId: string // 'backtest' | 'walk_forward' | 'robustness' | 'hypothesis_H0xx'
}

function Stepper({ current, onJump, maxReached }: { current: StepIndex; onJump: (i: StepIndex) => void; maxReached: StepIndex }) {
  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-1">
      {STEPS.map((label, i) => {
        const idx = i as StepIndex
        const reachable = idx <= maxReached
        const active = idx === current
        return (
          <div key={label} className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => reachable && onJump(idx)}
              disabled={!reachable}
              className={`relative flex items-center gap-2 px-3 py-1.5 rounded-full text-[0.78em] font-bold border transition-colors ${
                active
                  ? 'border-accent text-accent'
                  : reachable
                    ? 'border-border text-text hover:border-accent/50 hover:text-accent'
                    : 'border-border text-muted/50 cursor-not-allowed'
              }`}
            >
              {active && (
                <motion.span
                  layoutId="stepper-active-pill"
                  className="absolute inset-0 rounded-full bg-accent/10"
                  transition={{ duration: 0.15, ease: 'easeInOut' }}
                />
              )}
              <span
                className={`relative z-10 flex items-center justify-center w-4 h-4 rounded-full text-[0.85em] ${active ? 'bg-accent text-bg' : 'bg-border text-muted'}`}
              >
                {i + 1}
              </span>
              <span className="relative z-10">{label}</span>
            </button>
            {i < STEPS.length - 1 && <span className="text-muted text-[0.8em]">→</span>}
          </div>
        )
      })}
    </div>
  )
}

// ── Step 1: Dataset Explorer ────────────────────────────────────────────
function DatasetStep() {
  const { markUnauthenticated } = useAuth()
  const datasets = useApiQuery(['research-datasets'], getResearchDatasets, POLL_MS, markUnauthenticated)

  const columns: Column<DatasetEntry>[] = [
    { header: 'Symbol', render: (d) => <span className="font-bold text-accent">{d.symbol}</span> },
    { header: 'Timeframe', render: (d) => <Badge tone="neutral">{d.timeframe}</Badge> },
    { header: 'Rows', render: (d) => d.rows ?? '—', align: 'right' },
    { header: 'Range', render: (d) => (d.start && d.end ? <span className="text-muted text-[0.85em]">{d.start.slice(0, 10)} → {d.end.slice(0, 10)}</span> : '—') },
    { header: 'Readable', render: (d) => (d.readable ? <Badge tone="exec">yes</Badge> : <Badge tone="no-trade" >{d.error?.slice(0, 40) ?? 'no'}</Badge>) },
  ]

  return (
    <Panel title="Dataset Explorer" right={datasets.data ? `${datasets.data.count} local datasets · ${datasets.data.data_dir}` : undefined}>
      {datasets.data && datasets.data.datasets.length > 0 ? (
        <DataTable columns={columns} rows={datasets.data.datasets} rowKey={(d) => d.file} />
      ) : (
        <Empty>
          {datasets.loading
            ? 'Loading…'
            : 'No local historical CSVs found — run scripts/download_all_symbols.py on the VPS to populate data/.'}
        </Empty>
      )}
      <div className="px-4 pb-4 text-[0.78em] text-muted">
        This previews what's actually on disk for backtesting — live/provider data availability is a separate concern
        (see the Data Center and Provider Eval tabs). Every symbol below can be selected in the next step.
      </div>
    </Panel>
  )
}

// ── Step 2: Symbol Picker ───────────────────────────────────────────────
function SymbolsStep({ state, setState }: { state: WizardState; setState: (s: WizardState) => void }) {
  const { markUnauthenticated } = useAuth()
  const symbols = useApiQuery(['research-symbols'], getResearchSymbols, POLL_MS, markUnauthenticated)
  const [search, setSearch] = useState('')

  const allEntries: SymbolEntry[] = symbols.data ? Object.values(symbols.data.asset_classes).flat() : []
  const filtered = allEntries.filter((s) => s.internal.toLowerCase().includes(search.toLowerCase()))

  const toggle = (sym: string) => {
    setState({
      ...state,
      selectedSymbols: state.selectedSymbols.includes(sym)
        ? state.selectedSymbols.filter((s) => s !== sym)
        : [...state.selectedSymbols, sym],
    })
  }

  const selectAssetClass = (ac: string) => {
    if (!symbols.data) return
    const syms = symbols.data.asset_classes[ac]?.map((s) => s.internal) ?? []
    setState({ ...state, selectedSymbols: Array.from(new Set([...state.selectedSymbols, ...syms])) })
  }

  const clear = () => setState({ ...state, selectedSymbols: [] })

  return (
    <Panel
      title="Symbol Picker"
      right={symbols.data ? `${allEntries.length} symbols in universe` : undefined}
    >
      <div className="p-4 flex flex-col gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            className="flex-1 min-w-[180px] px-3 py-1.5 text-[0.82em] rounded border border-border bg-bg text-text font-mono"
          />
          {symbols.data &&
            Object.keys(symbols.data.asset_classes)
              .sort()
              .map((ac) => (
                <button
                  key={ac}
                  onClick={() => selectAssetClass(ac)}
                  className="px-2.5 py-1.5 text-[0.75em] rounded border border-border text-muted hover:text-accent hover:border-accent"
                >
                  Select All {ac}
                </button>
              ))}
          {state.selectedSymbols.length > 0 && (
            <button onClick={clear} className="px-2.5 py-1.5 text-[0.75em] rounded border border-red/40 text-red hover:bg-red/10">
              Clear
            </button>
          )}
        </div>

        <div className="flex flex-wrap gap-1.5 max-h-[220px] overflow-y-auto">
          {filtered.map((s) => {
            const selected = state.selectedSymbols.includes(s.internal)
            return (
              <button
                key={s.internal}
                onClick={() => toggle(s.internal)}
                title={s.status_reason}
                className={`px-2.5 py-1 rounded text-[0.78em] font-mono border ${
                  selected
                    ? 'border-accent bg-accent/15 text-accent'
                    : s.enabled
                      ? 'border-border text-text hover:border-accent/50'
                      : 'border-border text-muted hover:border-accent/50'
                }`}
              >
                {s.internal}
              </button>
            )
          })}
          {filtered.length === 0 && <span className="text-muted text-[0.82em]">{symbols.loading ? 'Loading…' : 'No match'}</span>}
        </div>

        <div>
          <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-1.5">Selected ({state.selectedSymbols.length})</div>
          {state.selectedSymbols.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              <AnimatePresence initial={false}>
                {state.selectedSymbols.map((sym) => (
                  <motion.span
                    key={sym}
                    layout
                    initial={{ opacity: 0, scale: 0.85 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.85 }}
                    transition={{ duration: 0.12, ease: 'easeInOut' }}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-accent/10 border border-accent/30 text-accent text-[0.78em] font-mono"
                  >
                    {sym}
                    <button onClick={() => toggle(sym)} className="hover:text-red">
                      ✕
                    </button>
                  </motion.span>
                ))}
              </AnimatePresence>
            </div>
          ) : (
            <Empty>No symbols selected yet — pick at least one to continue.</Empty>
          )}
        </div>
      </div>
    </Panel>
  )
}

// ── Step 3: Timeframe Matrix (informational — see module docstring) ─────
function TimeframesStep() {
  const { markUnauthenticated } = useAuth()
  const symbols = useApiQuery(['research-symbols'], getResearchSymbols, POLL_MS, markUnauthenticated)

  // Only providers actually referenced by a live asset-class chain — e.g.
  // yahoo_finance still has a _NATIVE_TF entry server-side (kept for
  // offline research/tests) but was REMOVED from every live chain
  // (CLAUDE.md, 2026-07-16, measured wrong instruments/fake H4 resample).
  // Showing it undifferentiated next to real options here would silently
  // relitigate that removal for anyone using this picker.
  const activeProviders = symbols.data ? new Set(Object.values(symbols.data.chains).flat()) : new Set<string>()

  return (
    <Panel title="Timeframe Matrix">
      <div className="p-4 flex flex-col gap-3">
        <div className="text-[0.82em] bg-amber/10 border border-amber/30 text-amber rounded px-3 py-2">
          Fixed by config.yaml, not selectable per-run: every live decision uses the same H1 base / H4-D1 confirmation
          triplet across all symbols. Changing this mid-sample resets the forward-evidence counter (CLAUDE.md rule 6).
          Shown below: which timeframes each provider actually serves natively — providers in at least one live chain
          only (see /provider-chains for the full chain order).
        </div>
        {symbols.data ? (
          <div className="flex flex-col gap-2">
            {Object.entries(symbols.data.native_timeframes)
              .filter(([provider]) => activeProviders.has(provider))
              .map(([provider, tfs]) => (
              <div key={provider} className="flex items-center gap-2 flex-wrap text-[0.82em]">
                <span className="w-28 shrink-0 font-bold text-accent">{provider}</span>
                {tfs.map((tf) => (
                  <Badge key={tf} tone="neutral">
                    {tf}
                  </Badge>
                ))}
              </div>
            ))}
          </div>
        ) : (
          <Empty>{symbols.loading ? 'Loading…' : 'No provider coverage data'}</Empty>
        )}
      </div>
    </Panel>
  )
}

// ── Step 4: Strategy (Engine Selector, read-only) ───────────────────────
function StrategyStep() {
  const { markUnauthenticated } = useAuth()
  const engines = useApiQuery(['research-engines'], getResearchEngines, POLL_MS, markUnauthenticated)

  return (
    <Panel title="Strategy" right="read-only — a new hypothesis is required to change engine activation or weights">
      <div className="p-4 flex flex-col gap-3">
        <div className="grid gap-2.5 grid-cols-[repeat(auto-fill,minmax(200px,1fr))]">
          {engines.data?.engines.map((e: EngineEntry) => (
            <div
              key={e.name}
              className={`rounded-lg border px-3.5 py-3 flex flex-col gap-1.5 ${
                e.enabled ? 'border-accent/40 bg-accent/[0.04]' : 'border-border bg-surface/40 opacity-60'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-bold text-text">{e.name}</span>
                {e.enabled ? <Badge tone="exec">ON</Badge> : <Badge tone="neutral">OFF</Badge>}
              </div>
              <div className="flex items-center gap-2 text-[0.78em] text-muted">
                <span>weight {e.weight != null ? e.weight.toFixed(4) : '—'}</span>
                {e.prod4 && <Badge tone="good">frozen</Badge>}
              </div>
            </div>
          ))}
        </div>
        {!engines.data && <Empty>{engines.loading ? 'Loading…' : 'No engine data'}</Empty>}
      </div>
    </Panel>
  )
}

// ── Step 5: Indicators (catalog, read-only) ─────────────────────────────
function IndicatorsStep() {
  const { markUnauthenticated } = useAuth()
  const indicators = useApiQuery(['research-indicators'], getResearchIndicators, POLL_MS, markUnauthenticated)

  return (
    <Panel title="Indicators" right="catalog of what each engine already computes — not an editor">
      <div className="p-4 grid gap-2.5 grid-cols-[repeat(auto-fill,minmax(220px,1fr))]">
        {indicators.data?.indicators.map((i: IndicatorEntry) => (
          <div key={i.id} className="rounded-lg border border-border bg-surface/40 px-3.5 py-3 flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className="font-bold text-accent text-[0.88em]">{i.name}</span>
              <Badge tone="neutral">{i.category}</Badge>
            </div>
            <span className="text-muted text-[0.78em]">{i.description}</span>
          </div>
        ))}
        {!indicators.data && <Empty>{indicators.loading ? 'Loading…' : 'No indicator catalog'}</Empty>}
      </div>
    </Panel>
  )
}

// ── Step 6: Hypotheses ───────────────────────────────────────────────────
function HypothesesStep({ state, setState }: { state: WizardState; setState: (s: WizardState) => void }) {
  const { markUnauthenticated } = useAuth()
  const research = useApiQuery(['research-registry'], getResearch, POLL_MS, markUnauthenticated)
  const catalog = useApiQuery(['job-catalog'], getJobCatalog, POLL_MS, markUnauthenticated)

  const hypothesisJobs = (catalog.data?.jobs ?? []).filter((j: JobDescriptor) => j.id.startsWith('hypothesis_'))
  const registryById = new Map((research.data?.hypotheses ?? []).map((h) => [h.id, h]))

  const options: { id: string; label: string; description: string }[] = [
    { id: 'backtest', label: 'Standalone Backtest', description: 'Cost-inclusive scan on the symbols picked above — an exploratory in-sample run, not evidence.' },
    { id: 'walk_forward', label: 'Walk-Forward', description: 'Disjoint chronological OOS window consistency test on the symbols picked above.' },
    { id: 'robustness', label: 'Robustness Sweep', description: 'Parameter-sensitivity screen on the symbols picked above — reports every point, never picks a winner.' },
    ...hypothesisJobs.map((j: JobDescriptor) => {
      const hid = j.id.replace('hypothesis_', '')
      const reg = registryById.get(hid)
      return {
        id: j.id,
        label: `${hid}${reg ? ` — ${reg.title}` : ''}`,
        description: j.description,
      }
    }),
  ]

  return (
    <Panel title="Hypotheses" right="pick what this run actually executes">
      <div className="p-4 flex flex-col gap-2">
        {options.map((opt) => (
          <button
            key={opt.id}
            onClick={() => setState({ ...state, jobId: opt.id })}
            className={`text-left px-3.5 py-2.5 rounded-lg border transition-colors ${
              state.jobId === opt.id ? 'border-accent bg-accent/10' : 'border-border hover:border-accent/40'
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="font-bold text-text text-[0.88em]">{opt.label}</span>
              {opt.id.startsWith('hypothesis_') && registryById.get(opt.id.replace('hypothesis_', ''))?.status && (
                <Badge tone={statusToneFor(registryById.get(opt.id.replace('hypothesis_', ''))!.status)}>
                  {registryById.get(opt.id.replace('hypothesis_', ''))!.status}
                </Badge>
              )}
            </div>
            <div className="text-muted text-[0.78em] mt-0.5">{opt.description}</div>
          </button>
        ))}
      </div>
    </Panel>
  )
}

function statusToneFor(status: string): 'exec' | 'no-trade' | 'neutral' {
  if (status === 'PASSED') return 'exec'
  if (status.includes('FAILED')) return 'no-trade'
  return 'neutral'
}

// ── Step 7: Execution ────────────────────────────────────────────────────
// Parameter Sweep config (2026-07-25, Backtesting Lab priority #4) — only
// shown for the robustness job. Deliberately NO "pick the best value"
// control anywhere: the operator chooses which parameters and which
// multipliers to MEASURE, never which one wins. Matches the guardrail
// backtest/robustness.py's own module docstring commits to (STABLE/
// SENSITIVE/INSUFFICIENT verdicts, never an auto-selected winner) — see
// the Backtesting Charts tab for how the resulting per-point sweep table
// is displayed after the run.
function SweepConfig({
  params, multipliers, onParamsChange, onMultipliersChange,
}: {
  params: string[]
  multipliers: number[]
  onParamsChange: (p: string[]) => void
  onMultipliersChange: (m: number[]) => void
}) {
  const { markUnauthenticated } = useAuth()
  const validation = useApiQuery(['validation-config'], getValidationConfig, POLL_MS, markUnauthenticated)
  const allParams = validation.data?.robustness.params ?? params
  const [multipliersText, setMultipliersText] = useState(multipliers.join(', '))

  const toggleParam = (p: string) => {
    onParamsChange(params.includes(p) ? params.filter((x) => x !== p) : [...params, p])
  }

  const applyMultipliers = () => {
    const parsed = multipliersText
      .split(',')
      .map((s) => Number.parseFloat(s.trim()))
      .filter((n) => Number.isFinite(n) && n > 0)
    onMultipliersChange(parsed)
  }

  return (
    <div className="flex flex-col gap-2.5 p-3 rounded-lg border border-panel-border bg-panel shadow-md">
      <div className="text-[0.72em] text-muted uppercase tracking-[1px]">Parameter Sweep — which points to measure, never which one wins</div>
      <div className="flex flex-wrap gap-1.5">
        {allParams.map((p) => (
          <button
            key={p}
            onClick={() => toggleParam(p)}
            className={`px-2.5 py-1 rounded text-[0.78em] font-mono border ${
              params.includes(p) ? 'border-accent bg-accent/15 text-accent' : 'border-border text-muted hover:border-accent/50'
            }`}
          >
            {p}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[0.78em] text-muted">multipliers (1.0 required as baseline):</span>
        <input
          value={multipliersText}
          onChange={(e) => setMultipliersText(e.target.value)}
          onBlur={applyMultipliers}
          placeholder="0.5, 0.8, 1.0, 1.2, 1.5"
          className="px-2.5 py-1 text-[0.78em] rounded border border-border bg-bg text-text font-mono w-64"
        />
      </div>
      {!multipliers.includes(1.0) && (
        <span className="text-amber text-[0.75em]">1.0 is missing — add it back before running (the server rejects a sweep with no baseline).</span>
      )}
    </div>
  )
}

function ExecutionStep({
  state,
  job,
  setJob,
  onFinished,
}: {
  state: WizardState
  job: JobDetail | null
  setJob: (j: JobDetail | null) => void
  onFinished: () => void
}) {
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requiresSymbols = ['backtest', 'walk_forward', 'robustness'].includes(state.jobId)
  const isRobustness = state.jobId === 'robustness'

  const { markUnauthenticated } = useAuth()
  const validation = useApiQuery(['validation-config'], getValidationConfig, POLL_MS, markUnauthenticated)
  const [sweepParams, setSweepParams] = useState<string[]>([])
  const [sweepMultipliers, setSweepMultipliers] = useState<number[]>([])
  useEffect(() => {
    if (validation.data && sweepParams.length === 0) setSweepParams(validation.data.robustness.params)
    if (validation.data && sweepMultipliers.length === 0) setSweepMultipliers(validation.data.robustness.multipliers)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [validation.data])

  useEffect(() => {
    if (!job || job.status === 'finished' || job.status === 'failed' || job.status === 'timeout' || job.status === 'cancelled') return
    const id = setInterval(() => {
      getJobDetail(job.job_id).then(setJob).catch(() => {})
    }, 3000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job])

  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      const summary = isRobustness
        ? await runRobustnessJob(state.selectedSymbols, sweepParams.length > 0 ? sweepParams : undefined, sweepMultipliers.length > 0 ? sweepMultipliers : undefined)
        : await runJob(state.jobId, requiresSymbols ? state.selectedSymbols : undefined)
      setJob({ ...summary, log: [] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const terminal = job && ['finished', 'failed', 'timeout', 'cancelled'].includes(job.status)
  useEffect(() => {
    if (terminal) onFinished()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terminal])

  const sweepInvalid = isRobustness && (sweepParams.length === 0 || !sweepMultipliers.includes(1.0))

  return (
    <Panel title="Execution" right={`job: ${state.jobId}`}>
      <div className="p-4 flex flex-col gap-3">
        <div className="text-[0.82em] text-muted">
          Symbols: {state.selectedSymbols.length > 0 ? state.selectedSymbols.join(', ') : requiresSymbols ? 'none selected' : 'n/a — fixed method'}
        </div>
        {isRobustness && (
          <SweepConfig params={sweepParams} multipliers={sweepMultipliers} onParamsChange={setSweepParams} onMultipliersChange={setSweepMultipliers} />
        )}
        <button
          onClick={start}
          disabled={starting || (job != null && !terminal) || (requiresSymbols && state.selectedSymbols.length === 0) || sweepInvalid}
          title={sweepInvalid ? 'Pick at least one parameter and keep 1.0 in the multipliers' : undefined}
          className="self-start px-4 py-1.5 text-[0.82em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50 font-bold"
        >
          {starting ? 'Starting…' : job && !terminal ? 'Running…' : 'Run'}
        </button>
        {error && <span className="text-red text-[0.8em]">{error}</span>}
        {job && (
          <div className="flex items-center gap-2">
            <Badge tone={job.status === 'finished' ? 'exec' : job.status === 'running' || job.status === 'queued' ? 'marginal' : 'no-trade'}>{job.status}</Badge>
            {job.returncode != null && <span className="text-muted text-[0.8em]">exit {job.returncode}</span>}
          </div>
        )}
        {job && job.log.length > 0 && (
          <pre className="p-3 bg-panel shadow-sm border border-panel-border rounded-lg text-[0.72em] overflow-auto max-h-[280px] whitespace-pre-wrap break-words font-mono">
            {job.log.slice(-150).join('\n')}
          </pre>
        )}
      </div>
    </Panel>
  )
}

// ── Step 8: Results ──────────────────────────────────────────────────────
function ResultsStep({ state, job }: { state: WizardState; job: JobDetail | null }) {
  const context = job
    ? {
        job_id: job.job_id,
        job_kind: state.jobId,
        symbols: state.selectedSymbols,
        status: job.status,
        returncode: job.returncode ?? null,
        log_tail: job.log.slice(-60),
      }
    : null

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Results">
        <div className="p-4 flex flex-col gap-3 text-[0.85em]">
          <p className="text-muted">
            The full Result Explorer (equity curves, walk-forward/robustness verdicts, Monte Carlo) lives in the
            Backtesting Charts tab — it reads the same reports/ output this run just wrote.
          </p>
          <a href="#/backtesting-charts" className="self-start px-4 py-1.5 text-[0.82em] rounded border border-accent text-accent hover:bg-accent/10 font-bold">
            Open Backtesting Charts →
          </a>
        </div>
      </Panel>
      <AiResearchAssistant
        context={context}
        emptyHint="Run a job in the Execution step first — the assistant answers from that run's own status and log, nothing else."
        examples={['Did this run finish successfully?', 'Summarize what happened in this run.', 'Were there any errors in the log?']}
      />
    </div>
  )
}

export function BacktestingLab() {
  const [step, setStep] = useState<StepIndex>(0)
  const [maxReached, setMaxReached] = useState<StepIndex>(0)
  const [state, setState] = useState<WizardState>({ selectedSymbols: [], jobId: 'backtest' })
  const [job, setJob] = useState<JobDetail | null>(null)

  const goTo = (i: StepIndex) => {
    setStep(i)
    if (i > maxReached) setMaxReached(i)
  }
  const next = () => goTo(Math.min(7, step + 1) as StepIndex)
  const back = () => goTo(Math.max(0, step - 1) as StepIndex)

  const requiresSymbols = ['backtest', 'walk_forward', 'robustness'].includes(state.jobId)
  const canAdvanceFromSymbols = step !== 1 || state.selectedSymbols.length > 0 || !requiresSymbols

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.78em] text-muted">
        Research Workbench — assembles a real Queue Manager run step by step. Distinct from the Research &amp;
        Backtests tab, which is a monitoring dashboard over the same data.
      </p>

      <Panel title="Backtesting Lab">
        <div className="p-4">
          <Stepper current={step} onJump={goTo} maxReached={maxReached} />
        </div>
      </Panel>

      {step === 0 && <DatasetStep />}
      {step === 1 && <SymbolsStep state={state} setState={setState} />}
      {step === 2 && <TimeframesStep />}
      {step === 3 && <StrategyStep />}
      {step === 4 && <IndicatorsStep />}
      {step === 5 && <HypothesesStep state={state} setState={setState} />}
      {step === 6 && (
        <ExecutionStep
          state={state}
          job={job}
          setJob={setJob}
          onFinished={() => setMaxReached((m) => (m < 7 ? (7 as StepIndex) : m))}
        />
      )}
      {step === 7 && <ResultsStep state={state} job={job} />}

      <div className="flex items-center justify-between">
        <button
          onClick={back}
          disabled={step === 0}
          className="px-4 py-1.5 text-[0.82em] rounded border border-border text-muted hover:text-text hover:border-accent/50 disabled:opacity-40"
        >
          ← Back
        </button>
        <button
          onClick={next}
          disabled={step === 7 || !canAdvanceFromSymbols}
          title={!canAdvanceFromSymbols ? 'Select at least one symbol first' : undefined}
          className="px-4 py-1.5 text-[0.82em] rounded border border-accent text-accent hover:bg-accent/10 disabled:opacity-40 font-bold"
        >
          Next →
        </button>
      </div>
    </div>
  )
}
