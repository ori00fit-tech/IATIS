import { useEffect, useState } from 'react'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { KpiCard } from '../../components/KpiCard'
import { DataTable, type Column } from '../../components/DataTable'
import { useApiQuery } from '../../lib/useApiQuery'
import { useAuth } from '../../lib/auth'
import { ENGINE_KEYS, SUPPORTED_TIMEFRAMES, FILTER_MODES } from '../backtesting-lab/BacktestingLab'
import { getResearchSymbols, saveHypothesisDraft, type SymbolsResponse } from '../research-backtests/api'
import {
  createMission, listMissions, getMissionStatus, getMissionLeaderboard, cancelMission,
  createValidation, listValidations, getValidation, getMetaAnalysis,
  SAMPLER_KEYS, OPTIMIZABLE_METRICS,
  type MissionRequest, type MissionSummary, type MissionStatusResponse, type MissionTrial,
  type CriteriaEntry, type DimensionFrequency, type Verdict,
} from './api'

const POLL_MS = 4000

// AI Research Lab / Mission Center Phase 3 (2026-07-28)
//
// Every mission this UI can launch is EXPLORATORY, never evidence — see
// the significance banner rendered from each leaderboard response and
// CLAUDE.md rule 1. Nothing here ever writes to research/results/
// registry.json; "Propose as hypothesis draft" only ever calls the
// existing, unmodified POST /ai/save-hypothesis-draft (Phase 4d),
// exactly like the AI Copilot panel already does.

// Small purpose-built dropdown, same "one purpose-built component per
// screen, not a shared Combobox primitive" decision already made for
// ExperimentRunner.tsx's own SymbolMultiSelect — duplicated here
// deliberately rather than extracted, per that component's own comment.
function SymbolMultiSelect({
  value, onChange, symbolsData,
}: {
  value: string[]
  onChange: (symbols: string[]) => void
  symbolsData: SymbolsResponse | null
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const allEntries = symbolsData ? Object.values(symbolsData.asset_classes).flat() : []
  const filtered = allEntries.filter((s) => s.internal.toLowerCase().includes(search.toLowerCase()))
  const toggle = (sym: string) => onChange(value.includes(sym) ? value.filter((s) => s !== sym) : [...value, sym])

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-56 text-left truncate"
      >
        {value.length === 0 ? <span className="text-muted">Select symbols…</span> : value.join(', ')}
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-72 max-h-72 overflow-y-auto bg-panel border border-border rounded shadow-md p-2 flex flex-col gap-2">
          <input
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            className="px-2 py-1 text-[0.78em] rounded border border-border bg-bg text-text"
          />
          <div className="flex flex-col gap-0.5">
            {filtered.map((s) => (
              <label key={s.internal} className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-surface cursor-pointer text-[0.78em]">
                <input type="checkbox" checked={value.includes(s.internal)} onChange={() => toggle(s.internal)} />
                <span className="font-mono">{s.internal}</span>
              </label>
            ))}
            {filtered.length === 0 && <span className="text-muted text-[0.78em] px-1.5 py-1">No matches</span>}
          </div>
        </div>
      )}
    </div>
  )
}

function MultiCheckbox({ options, value, onToggle }: { options: readonly string[]; value: string[]; onToggle: (v: string) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((opt) => (
        <label key={opt} className={`flex items-center gap-1.5 px-2 py-1 rounded border text-[0.75em] cursor-pointer ${
          value.includes(opt) ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted'
        }`}>
          <input type="checkbox" checked={value.includes(opt)} onChange={() => onToggle(opt)} className="hidden" />
          {opt}
        </label>
      ))}
    </div>
  )
}

const DEFAULT_RISK_RANGE_FIELDS = ['sl_atr_multiplier', 'min_rr', 'risk_per_trade'] as const

// Context Filters (2026-07-30) — mirrors confluence/context_filters.py's
// CONTEXT_KEYS + each dimension's own _ALL_* option set exactly.
const CONTEXT_KEYS = ['session', 'day_of_week', 'volatility_regime', 'market_regime', 'direction'] as const
type ContextKey = (typeof CONTEXT_KEYS)[number]

const CONTEXT_OPTIONS: Record<ContextKey, { label: string; value: string | number }[]> = {
  session: ['Asia', 'London', 'NewYork', 'Overlap'].map((v) => ({ label: v, value: v })),
  day_of_week: ([['Mon', 0], ['Tue', 1], ['Wed', 2], ['Thu', 3], ['Fri', 4], ['Sat', 5], ['Sun', 6]] as const)
    .map(([label, value]) => ({ label, value })),
  volatility_regime: ['low', 'normal', 'high', 'extreme'].map((v) => ({ label: v, value: v })),
  market_regime: ['TRENDING', 'RANGING'].map((v) => ({ label: v, value: v })),
  direction: ['BULLISH', 'BEARISH'].map((v) => ({ label: v, value: v })),
}

interface ContextFilterRowState {
  name: ContextKey
  mode: (typeof FILTER_MODES)[number]
  allowed: (string | number)[]
  weight: number
}

// One purpose-built row-builder for this one screen, matching every other
// Filters UI in this arc (IndicatorsStep in BacktestingLab.tsx) — mode +
// (for context filters) an "allowed" multi-select instead of numeric params,
// since every dimension here restricts to a fixed enum, not a threshold.
function ContextFiltersBuilder({ rows, setRows }: { rows: ContextFilterRowState[]; setRows: (r: ContextFilterRowState[]) => void }) {
  const rowFor = (name: ContextKey) => rows.find((r) => r.name === name)

  const setMode = (name: ContextKey, mode: (typeof FILTER_MODES)[number]) => {
    const others = rows.filter((r) => r.name !== name)
    if (mode === 'disabled') {
      setRows(others)
      return
    }
    const existing = rowFor(name)
    setRows([...others, { name, mode, allowed: existing?.allowed ?? [], weight: existing?.weight ?? 0 }])
  }

  const toggleAllowed = (name: ContextKey, value: string | number) => {
    const existing = rowFor(name)
    if (!existing) return
    const has = existing.allowed.includes(value)
    const nextAllowed = has ? existing.allowed.filter((v) => v !== value) : [...existing.allowed, value]
    setRows(rows.map((r) => (r.name === name ? { ...r, allowed: nextAllowed } : r)))
  }

  const setWeight = (name: ContextKey, weight: number) =>
    setRows(rows.map((r) => (r.name === name ? { ...r, weight } : r)))

  return (
    <div className="flex flex-col gap-2.5">
      <div className="text-[0.78em] bg-accent/10 border border-accent/30 text-accent rounded px-3 py-2">
        Context filters never set direction/bias — they can only confirm, veto, or nudge the score of a decision the
        engines already produced, based on session/day-of-week/volatility regime/market regime/direction.
      </div>
      {CONTEXT_KEYS.map((name) => {
        const row = rowFor(name)
        const mode = row?.mode ?? 'disabled'
        return (
          <div
            key={name}
            className={`rounded-lg border px-3.5 py-3 flex flex-col gap-2 ${
              mode !== 'disabled' ? 'border-accent/40 bg-accent/[0.04]' : 'border-border bg-surface/40'
            }`}
          >
            <div className="flex items-center gap-3 flex-wrap">
              <span className="font-bold text-text w-32 shrink-0">{name.replace(/_/g, ' ')}</span>
              <select
                value={mode}
                onChange={(e) => setMode(name, e.target.value as (typeof FILTER_MODES)[number])}
                className="px-2 py-1 bg-bg border border-border rounded text-text text-[0.82em]"
              >
                {FILTER_MODES.map((m) => (
                  <option key={m} value={m}>{m.replace('_', ' ')}</option>
                ))}
              </select>
              {mode === 'score_weight' && (
                <label className="flex items-center gap-1.5 text-[0.78em] text-muted">
                  weight
                  <input
                    type="number" min={0} max={100} value={row?.weight ?? 0}
                    onChange={(e) => setWeight(name, Number(e.target.value))}
                    className="w-16 px-1.5 py-1 bg-bg border border-border rounded text-text"
                  />
                </label>
              )}
            </div>
            {mode !== 'disabled' && (
              <div className="flex flex-wrap gap-1.5">
                <span className="text-muted text-[0.72em] self-center">allowed (empty = unrestricted):</span>
                {CONTEXT_OPTIONS[name].map((opt) => {
                  const checked = row?.allowed.includes(opt.value) ?? false
                  return (
                    <label
                      key={String(opt.value)}
                      className={`flex items-center gap-1 px-1.5 py-0.5 rounded border text-[0.72em] cursor-pointer ${
                        checked ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted'
                      }`}
                    >
                      <input type="checkbox" checked={checked} onChange={() => toggleAllowed(name, opt.value)} className="hidden" />
                      {opt.label}
                    </label>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function MissionBuilder({ onCreated }: { onCreated: (missionId: string) => void }) {
  const { markUnauthenticated } = useAuth()
  const symbolsQuery = useApiQuery(['research-symbols'], getResearchSymbols, POLL_MS, markUnauthenticated)

  const [name, setName] = useState('')
  const [symbols, setSymbols] = useState<string[]>([])
  const [sampler, setSampler] = useState<MissionRequest['sampler']>('tpe')
  const [nTrials, setNTrials] = useState(50)
  const [objectiveMetric, setObjectiveMetric] = useState<MissionRequest['objective_metric']>('profit_factor')
  const [timeframes, setTimeframes] = useState<string[]>(['H1'])
  const [engines, setEngines] = useState<string[]>(['nnfx', 'price_action', 'smc', 'wyckoff'])
  const [riskRanges, setRiskRanges] = useState<Record<string, [number, number]>>({
    sl_atr_multiplier: [1.0, 3.0],
  })
  const [maxWallClock, setMaxWallClock] = useState<number | ''>('')
  const [contextFilters, setContextFilters] = useState<ContextFilterRowState[]>([])
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const toggleTimeframe = (tf: string) =>
    setTimeframes((prev) => (prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf]))
  const toggleEngine = (e: string) =>
    setEngines((prev) => (prev.includes(e) ? prev.filter((x) => x !== e) : [...prev, e]))
  const toggleRiskField = (field: string) =>
    setRiskRanges((prev) => {
      const next = { ...prev }
      if (field in next) delete next[field]
      else next[field] = [1.0, 2.0]
      return next
    })

  const submit = async () => {
    setError(null)
    if (symbols.length === 0) { setError('Select at least one symbol.'); return }
    if (timeframes.length === 0) { setError('Select at least one timeframe.'); return }
    if (engines.length === 0) { setError('Select at least one engine.'); return }

    setSubmitting(true)
    try {
      const contextSpecs = contextFilters.map((r) => ({
        name: r.name,
        mode: r.mode,
        params: r.allowed.length > 0 ? { [r.name === 'day_of_week' ? 'allowed_days' : 'allowed']: r.allowed } : {},
        weight: r.weight,
      }))
      const body: MissionRequest = {
        name: name || undefined,
        symbols,
        sampler,
        n_trials_per_symbol: nTrials,
        objective_metric: objectiveMetric,
        min_trades: 10,
        seed: 42,
        timeframes_choices: [timeframes],
        engine_set_choices: [engines],
        indicator_set_choices: [[]],
        context_filter_set_choices: [contextSpecs],
        risk_param_ranges: riskRanges,
        risk_param_grid: {},
        max_wall_clock_seconds: maxWallClock === '' ? undefined : maxWallClock,
      }
      const result = await createMission(body)
      onCreated(result.mission_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Panel title="New Mission" right="EXPLORATORY — every trial is a lead, never evidence">
      <div className="p-4 flex flex-col gap-3">
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Name (optional)</span>
            <input value={name} onChange={(e) => setName(e.target.value)}
              className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-48"
              placeholder="e.g. EURUSD sl-tuning" />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Symbols</span>
            <SymbolMultiSelect value={symbols} onChange={setSymbols} symbolsData={symbolsQuery.data} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Sampler</span>
            <select value={sampler} onChange={(e) => setSampler(e.target.value as MissionRequest['sampler'])}
              className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text">
              {SAMPLER_KEYS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Trials / symbol</span>
            <input type="number" min={1} max={2000} value={nTrials}
              onChange={(e) => setNTrials(Number(e.target.value))}
              className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-24" />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Objective</span>
            <select value={objectiveMetric} onChange={(e) => setObjectiveMetric(e.target.value as MissionRequest['objective_metric'])}
              className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text">
              {OPTIMIZABLE_METRICS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Wall-clock cap (s, optional)</span>
            <input type="number" min={1} value={maxWallClock}
              onChange={(e) => setMaxWallClock(e.target.value === '' ? '' : Number(e.target.value))}
              className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-32" />
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase">Timeframes (this run only)</span>
          <MultiCheckbox options={SUPPORTED_TIMEFRAMES} value={timeframes} onToggle={toggleTimeframe} />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase">Engine set (this run only)</span>
          <MultiCheckbox options={ENGINE_KEYS} value={engines} onToggle={toggleEngine} />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase">Risk params to search (ranges)</span>
          <div className="flex flex-wrap gap-3">
            {DEFAULT_RISK_RANGE_FIELDS.map((field) => (
              <label key={field} className="flex items-center gap-2 text-[0.78em]">
                <input type="checkbox" checked={field in riskRanges} onChange={() => toggleRiskField(field)} />
                <span className="w-32">{field}</span>
                {field in riskRanges && (
                  <>
                    <input type="number" step={0.01} value={riskRanges[field][0]}
                      onChange={(e) => setRiskRanges((p) => ({ ...p, [field]: [Number(e.target.value), p[field][1]] }))}
                      className="bg-surface border border-border rounded px-1.5 py-1 w-20 text-[0.9em]" />
                    <span className="text-muted">to</span>
                    <input type="number" step={0.01} value={riskRanges[field][1]}
                      onChange={(e) => setRiskRanges((p) => ({ ...p, [field]: [p[field][0], Number(e.target.value)] }))}
                      className="bg-surface border border-border rounded px-1.5 py-1 w-20 text-[0.9em]" />
                  </>
                )}
              </label>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase">Context filters (this run only)</span>
          <ContextFiltersBuilder rows={contextFilters} setRows={setContextFilters} />
        </div>

        {error && <div className="text-red text-[0.8em]">{error}</div>}
        <div>
          <button onClick={submit} disabled={submitting}
            className="px-4 py-2 rounded bg-accent text-bg font-bold text-[0.82em] disabled:opacity-50">
            {submitting ? 'Launching…' : 'Launch Mission'}
          </button>
        </div>
      </div>
    </Panel>
  )
}

function MissionsList({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const { markUnauthenticated } = useAuth()
  const missionsQuery = useApiQuery(['missions-list'], listMissions, POLL_MS, markUnauthenticated)
  const missions = missionsQuery.data?.missions ?? []

  if (missions.length === 0) return <Empty>No missions launched yet.</Empty>

  return (
    <div className="flex flex-col gap-1 p-2">
      {missions.map((m: MissionSummary) => (
        <button key={m.job_id} onClick={() => onSelect(m.job_id)}
          className={`flex items-center justify-between px-3 py-2 rounded text-left text-[0.8em] border ${
            selected === m.job_id ? 'border-accent bg-accent/10' : 'border-border hover:border-accent/40'
          }`}>
          <span className="font-mono">{m.job_id}</span>
          <StatusBadge status={m.status} />
        </button>
      ))}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const tone = status === 'finished' ? 'good' : status === 'failed' || status === 'timeout' ? 'poor'
    : status === 'cancelled' ? 'marginal' : 'neutral'
  return <Badge tone={tone}>{status}</Badge>
}

function VerdictBadge({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return <Badge tone="neutral">pending</Badge>
  const tone = verdict === 'STRONG_LEAD' ? 'good' : verdict === 'WEAK_LEAD' ? 'marginal' : 'poor'
  return <Badge tone={tone}>{verdict}</Badge>
}

// Per-trial "Validate…" action — never auto-picks a candidate, the
// operator must open this on a specific COMPLETE row and choose the
// cross-symbol validation set themselves (≥2 symbols, server-enforced).
function ValidateAction({
  missionId, trial, symbolsData,
}: {
  missionId: string
  trial: MissionTrial
  symbolsData: SymbolsResponse | null
}) {
  const [open, setOpen] = useState(false)
  const [validationSymbols, setValidationSymbols] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  if (trial.state !== 'COMPLETE') return <span className="text-muted text-[0.75em]">—</span>

  const submit = async () => {
    setError(null)
    if (validationSymbols.length < 2) { setError('Pick at least 2 validation symbols.'); return }
    setSubmitting(true)
    try {
      await createValidation(missionId, {
        trial_number: trial.trial_number,
        trial_symbol: trial.symbol,
        validation_symbols: validationSymbols,
      })
      setOpen(false)
      setDone(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="text-[0.75em] text-accent hover:underline">
        {done ? 'Launched — see Validations below' : 'Validate…'}
      </button>
      {open && (
        <div className="absolute z-20 right-0 mt-1 w-72 bg-panel border border-border rounded shadow-md p-3 flex flex-col gap-2">
          <span className="text-[0.7em] text-muted uppercase">Validation symbols (≥2)</span>
          <SymbolMultiSelect value={validationSymbols} onChange={setValidationSymbols} symbolsData={symbolsData} />
          {error && <div className="text-red text-[0.75em]">{error}</div>}
          <button onClick={submit} disabled={submitting}
            className="px-3 py-1.5 rounded bg-accent text-bg font-bold text-[0.78em] disabled:opacity-50">
            {submitting ? 'Launching…' : 'Run Validation'}
          </button>
        </div>
      )}
    </div>
  )
}

function FrequencyTable({ title, rows }: { title: string; rows: DimensionFrequency[] }) {
  const sorted = [...rows].sort((a, b) => (b.lift ?? 0) - (a.lift ?? 0))
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[0.72em] text-muted uppercase tracking-[1px]">{title}</span>
      <div className="flex flex-col gap-0.5">
        {sorted.map((f) => (
          <div key={f.value} className="flex items-center gap-2 text-[0.78em]">
            <span className="font-mono w-24 truncate">{f.value}</span>
            <span className="text-muted">top {(f.top_fraction * 100).toFixed(0)}%</span>
            <span className="text-muted">all {(f.all_fraction * 100).toFixed(0)}%</span>
            {f.lift != null && (
              <span className={f.lift > 1.2 ? 'text-green' : f.lift < 0.8 ? 'text-red' : 'text-muted'}>
                lift {f.lift.toFixed(2)}x
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function MetaAnalysisPanel({ missionId }: { missionId: string }) {
  const { markUnauthenticated } = useAuth()
  const query = useApiQuery(['mission-meta-analysis', missionId], () => getMetaAnalysis(missionId), POLL_MS, markUnauthenticated)
  const data = query.data

  if (!data) return <Panel title="Meta-Analysis"><Empty>Loading…</Empty></Panel>

  if (data.insufficient_data) {
    return (
      <Panel title="Meta-Analysis" right="what do the best trials share? (not just the single best number)">
        <div className="p-4"><Empty>{data.note}</Empty></div>
      </Panel>
    )
  }

  return (
    <Panel title="Meta-Analysis" right="retrospective pattern-spotting — a lead, not confirmation">
      <div className="p-4 flex flex-col gap-4">
        <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
          {data.note}
        </div>
        <div className="text-[0.78em] text-muted">
          Top {data.top_n} of {data.n_complete_trials} completed trials (top {(data.top_fraction_used * 100).toFixed(0)}%) — sampler: {data.sampler}
        </div>
        <div className="grid gap-4 grid-cols-[repeat(auto-fit,minmax(260px,1fr))]">
          <FrequencyTable title="Engines" rows={data.engine_frequencies} />
          <FrequencyTable title="Timeframes" rows={data.timeframe_frequencies} />
        </div>
        {data.consensus_bands.length > 0 && (
          <div className="flex flex-col gap-3">
            <span className="text-[0.72em] text-muted uppercase tracking-[1px]">Consensus bands (risk params) — plateau vs. spike</span>
            {data.consensus_bands.map((band) => (
              <div key={band.risk_param} className="flex flex-col gap-1.5">
                <div className="flex items-center gap-2 text-[0.8em]">
                  <span className="font-mono">{band.risk_param}</span>
                  <Badge tone={band.shape === 'PLATEAU' ? 'good' : band.shape === 'SPIKE' ? 'poor' : 'neutral'}>{band.shape}</Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                  {band.bins.map((b, i) => (
                    <div key={i} className={`px-2 py-1 rounded border text-[0.72em] ${b.n_trials > 0 ? 'border-border' : 'border-border/30 text-muted/50'}`}>
                      <div>{b.bin_lo.toFixed(3)}–{b.bin_hi.toFixed(3)}</div>
                      <div>n={b.n_trials}{b.mean_objective != null ? `, obj=${b.mean_objective.toFixed(3)}` : ''}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Panel>
  )
}

function ValidationDetail({ missionId, validationId }: { missionId: string; validationId: string }) {
  const { markUnauthenticated } = useAuth()
  const query = useApiQuery(
    ['mission-validation', missionId, validationId],
    () => getValidation(missionId, validationId), POLL_MS, markUnauthenticated,
  )
  const data = query.data
  if (!data) return <Empty>Loading…</Empty>
  const v = data.validation

  return (
    <div className="border-t border-border pt-3 mt-1 flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <StatusBadge status={data.job_status ?? v?.status ?? 'unknown'} />
        {v && <VerdictBadge verdict={v.overall_verdict} />}
        {v && (
          <span className="text-[0.78em] text-muted">
            {v.passing_symbols ?? '—'}/{v.total_symbols ?? '—'} symbols passing
          </span>
        )}
        {v?.error && <span className="text-red text-[0.78em]">{v.error}</span>}
      </div>
      {data.results.length === 0 ? (
        <Empty>No per-symbol results yet.</Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {data.results.map((r) => {
            const breakdown: Record<string, CriteriaEntry> = r.criteria_breakdown_json
              ? JSON.parse(r.criteria_breakdown_json) : {}
            return (
              <details key={r.symbol} className="border border-border rounded px-3 py-2">
                <summary className="cursor-pointer text-[0.8em] flex items-center gap-2">
                  <span className="font-mono w-20">{r.symbol}</span>
                  <Badge tone={r.passed ? 'good' : 'poor'}>{r.passed ? 'passed' : 'failed'}</Badge>
                  {r.error && <span className="text-red text-[0.75em]">{r.error}</span>}
                </summary>
                {Object.keys(breakdown).length > 0 && (
                  <div className="mt-2 flex flex-col gap-1">
                    {Object.entries(breakdown).map(([criterion, c]) => (
                      <div key={criterion} className="flex items-center gap-2 text-[0.75em] flex-wrap">
                        <span className="w-52 text-muted">{criterion}</span>
                        <span className="font-mono">{JSON.stringify(c.actual)}</span>
                        <span className="text-muted">vs</span>
                        <span className="font-mono">{JSON.stringify(c.threshold)}</span>
                        <Badge tone={c.passed ? 'good' : 'poor'}>{c.passed ? 'pass' : 'fail'}</Badge>
                      </div>
                    ))}
                  </div>
                )}
              </details>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ValidationsPanel({ missionId }: { missionId: string }) {
  const { markUnauthenticated } = useAuth()
  const validationsQuery = useApiQuery(
    ['mission-validations', missionId], () => listValidations(missionId), POLL_MS, markUnauthenticated,
  )
  const [selectedValidation, setSelectedValidation] = useState<string | null>(null)
  const validations = validationsQuery.data?.validations ?? []

  return (
    <Panel title="Validations" right="Monte Carlo + walk-forward + robustness on one operator-chosen candidate">
      <div className="p-4 flex flex-col gap-3">
        {validations.length === 0 ? (
          <Empty>No validations run yet — click "Validate…" on a completed trial above.</Empty>
        ) : (
          <div className="flex flex-col gap-1">
            {validations.map((v) => {
              let syms: string[] = []
              try { syms = JSON.parse(v.validation_symbols_json) } catch { /* ignore malformed row */ }
              return (
                <button key={v.id} onClick={() => setSelectedValidation(v.id)}
                  className={`flex items-center justify-between px-3 py-2 rounded text-left text-[0.8em] border ${
                    selectedValidation === v.id ? 'border-accent bg-accent/10' : 'border-border hover:border-accent/40'
                  }`}>
                  <span className="font-mono truncate max-w-[60%]">
                    {v.trial_symbol} trial #{v.trial_number} → {syms.join(', ')}
                  </span>
                  <div className="flex items-center gap-2 shrink-0">
                    <StatusBadge status={v.status} />
                    <VerdictBadge verdict={v.overall_verdict} />
                  </div>
                </button>
              )
            })}
          </div>
        )}
        {selectedValidation && <ValidationDetail missionId={missionId} validationId={selectedValidation} />}
      </div>
    </Panel>
  )
}

function significanceFromLeaderboard(trials: MissionTrial[]): { banner: string; nTrials: number } | null {
  // The backend's own mission_significance_summary already computed this
  // and wrote it into the mission report file — the leaderboard endpoint
  // itself doesn't re-expose it per-request, so this panel shows the real,
  // available signal instead: raw completed-trial count context, pointing
  // at the full report file for the authoritative Bonferroni banner.
  const complete = trials.filter((t) => t.state === 'COMPLETE')
  if (complete.length === 0) return null
  return {
    nTrials: complete.length,
    banner: `EXPLORATORY — NOT EVIDENCE. ${complete.length} completed trial(s) shown below. ` +
      `See the full mission report (reports/mission_${trials[0]?.mission_id}_*.json, via File Explorer) ` +
      `for the Bonferroni-corrected significance summary — with this many trials, some will look ` +
      `"good" by chance alone. None of this is validated until manually re-registered and re-tested.`,
  }
}

function MissionDetail({ missionId }: { missionId: string }) {
  const { markUnauthenticated } = useAuth()
  const statusQuery = useApiQuery(['mission-status', missionId], () => getMissionStatus(missionId), POLL_MS, markUnauthenticated)
  const symbolsQuery = useApiQuery(['research-symbols'], getResearchSymbols, POLL_MS, markUnauthenticated)
  const [leaderboardTrials, setLeaderboardTrials] = useState<MissionTrial[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [draftStatus, setDraftStatus] = useState<Record<number, string>>({})

  const status: MissionStatusResponse | null = statusQuery.data

  useEffect(() => {
    let cancelled = false
    getMissionLeaderboard(missionId, selectedSymbol ?? undefined).then((r) => {
      if (!cancelled) setLeaderboardTrials(r.trials)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [missionId, selectedSymbol, status?.progress.total])

  if (!status) return <Empty>Loading…</Empty>

  const symbols = Object.keys(status.progress.by_symbol)
  const isRunning = status.job_status === 'queued' || status.job_status === 'running'

  const cancel = async () => {
    setCancelling(true)
    try { await cancelMission(missionId) } finally { setCancelling(false) }
  }

  const proposeAsDraft = async (t: MissionTrial) => {
    try {
      const metrics = t.metrics_json ? JSON.parse(t.metrics_json) : {}
      const result = await saveHypothesisDraft({
        title: `Mission ${missionId} trial ${t.trial_number} (${t.symbol})`,
        statement: `A candidate configuration found by mission ${missionId} (sampler-driven search, ` +
          `symbol ${t.symbol}, trial #${t.trial_number}) showed objective_value=${t.objective_value} ` +
          `over ${t.trades} trades. This is a LEAD from an exploratory search, not a tested hypothesis.`,
        why_this_might_be_true: 'Not yet reviewed — fill in before registering.',
        data_required: { symbol: t.symbol, params: JSON.parse(t.params_json), metrics },
        falsification_criteria: 'Not yet defined — must be written BEFORE re-testing (CLAUDE.md rule 1).',
        distinct_from_prior_kill: 'Not yet reviewed — check against CLAUDE.md\'s dead list before registering.',
        notes: `Auto-generated from Mission Center. mission_id=${missionId}, trial_number=${t.trial_number}.`,
      })
      setDraftStatus((s) => ({ ...s, [t.trial_number]: result.file }))
    } catch (e) {
      setDraftStatus((s) => ({ ...s, [t.trial_number]: `error: ${e instanceof Error ? e.message : String(e)}` }))
    }
  }

  const columns: Column<MissionTrial>[] = [
    { header: 'Symbol', render: (t) => t.symbol },
    { header: 'Trial #', render: (t) => t.trial_number, align: 'right', accessorFn: (t) => t.trial_number },
    { header: 'State', render: (t) => <StatusBadge status={t.state.toLowerCase()} /> },
    { header: 'Objective', render: (t) => t.objective_value != null ? t.objective_value.toFixed(3) : '—', align: 'right', accessorFn: (t) => t.objective_value ?? -Infinity, sortingFn: 'basic' },
    { header: 'Trades', render: (t) => t.trades, align: 'right', accessorFn: (t) => t.trades, sortingFn: 'basic' },
    {
      header: 'Draft', render: (t) => (
        draftStatus[t.trial_number]
          ? <span className="text-[0.75em] text-muted truncate max-w-[160px] inline-block">{draftStatus[t.trial_number]}</span>
          : <button onClick={() => proposeAsDraft(t)} className="text-[0.75em] text-accent hover:underline">
              Propose as draft
            </button>
      ),
    },
    {
      header: 'Validate',
      render: (t) => <ValidateAction missionId={missionId} trial={t} symbolsData={symbolsQuery.data ?? null} />,
    },
  ]

  const sig = significanceFromLeaderboard(leaderboardTrials)

  return (
    <div className="flex flex-col gap-4">
      <Panel title={`Mission ${missionId}`} right={<StatusBadge status={status.job_status ?? status.mission?.status ?? 'unknown'} />}>
        <div className="p-4 flex flex-col gap-3">
          <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
            <KpiCard label="Total trials" value={status.progress.total} />
            <KpiCard label="Symbols" value={symbols.length} />
            <KpiCard label="Sampler" value={status.mission?.sampler ?? '—'} />
            <KpiCard label="Objective" value={status.mission?.objective_metric ?? '—'} />
          </div>
          <div className="flex flex-col gap-1">
            {symbols.map((sym) => {
              const counts = status.progress.by_symbol[sym]
              return (
                <div key={sym} className="flex items-center gap-2 text-[0.8em]">
                  <span className="w-20 font-mono">{sym}</span>
                  <span className="text-green">{counts.COMPLETE ?? 0} complete</span>
                  <span className="text-amber">{counts.PRUNED ?? 0} pruned</span>
                  <span className="text-red">{counts.FAIL ?? 0} failed</span>
                </div>
              )
            })}
          </div>
          {isRunning && (
            <button onClick={cancel} disabled={cancelling}
              className="self-start px-3 py-1.5 rounded border border-red/40 text-red text-[0.78em] hover:bg-red/10 disabled:opacity-50">
              {cancelling ? 'Cancelling…' : 'Cancel Mission'}
            </button>
          )}
        </div>
      </Panel>

      <Panel title="Leaderboard" right="every trial reported — never an auto-selected winner">
        <div className="p-4 flex flex-col gap-3">
          {symbols.length > 1 && (
            <div className="flex gap-2">
              <button onClick={() => setSelectedSymbol(null)}
                className={`px-2 py-1 rounded text-[0.75em] border ${selectedSymbol === null ? 'border-accent text-accent' : 'border-border text-muted'}`}>
                All
              </button>
              {symbols.map((s) => (
                <button key={s} onClick={() => setSelectedSymbol(s)}
                  className={`px-2 py-1 rounded text-[0.75em] border ${selectedSymbol === s ? 'border-accent text-accent' : 'border-border text-muted'}`}>
                  {s}
                </button>
              ))}
            </div>
          )}
          {sig && (
            <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
              {sig.banner}
            </div>
          )}
          {leaderboardTrials.length === 0 ? (
            <Empty>No completed trials yet.</Empty>
          ) : (
            <DataTable columns={columns} rows={leaderboardTrials} rowKey={(t) => `${t.symbol}-${t.trial_number}`} />
          )}
        </div>
      </Panel>

      <MetaAnalysisPanel missionId={missionId} />
      <ValidationsPanel missionId={missionId} />
    </div>
  )
}

export function MissionCenter() {
  const [selectedMission, setSelectedMission] = useState<string | null>(null)

  return (
    <div className="flex flex-col gap-4 p-4">
      <MissionBuilder onCreated={setSelectedMission} />
      <div className="grid gap-4 grid-cols-[280px_1fr] items-start">
        <Panel title="Missions">
          <MissionsList selected={selectedMission} onSelect={setSelectedMission} />
        </Panel>
        {selectedMission ? (
          <MissionDetail missionId={selectedMission} />
        ) : (
          <Panel title="Mission Detail"><Empty>Select a mission to see its progress and leaderboard.</Empty></Panel>
        )}
      </div>
    </div>
  )
}
