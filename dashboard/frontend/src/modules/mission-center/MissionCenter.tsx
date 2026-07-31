import { useEffect, useState } from 'react'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { KpiCard } from '../../components/KpiCard'
import { DataTable, type Column } from '../../components/DataTable'
import { useApiQuery } from '../../lib/useApiQuery'
import { useAuth } from '../../lib/auth'
import {
  ENGINE_KEYS, SUPPORTED_TIMEFRAMES, FILTER_MODES, INDICATOR_KEYS, DEFAULT_INDICATOR_PARAMS,
  type IndicatorFilterState,
} from '../backtesting-lab/BacktestingLab'
import {
  getResearchSymbols, suggestHypothesis, saveHypothesisDraft,
  type SymbolsResponse, type SuggestHypothesisResponse,
} from '../research-backtests/api'
import { AiStatusFrame } from '../../components/AiStatusFrame'
import { PerformanceBreakdownTable } from '../backtesting-charts/BacktestingCharts'
import type { BreakdownBucket } from '../backtesting-charts/api'
import {
  createMission, listMissions, getMissionStatus, getMissionLeaderboard, cancelMission,
  createValidation, listValidations, getValidation, getMetaAnalysis, getFeatureMining,
  SAMPLER_KEYS, OPTIMIZABLE_METRICS,
  type MissionRequest, type MissionSummary, type MissionStatusResponse, type MissionTrial, type MissionRow,
  type CriteriaEntry, type DimensionFrequency, type Verdict, type FeatureAssociation,
  type ValidationRow,
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

// Indicator Filters (2026-07-30) — mirrors ContextFiltersBuilder's row
// pattern, reusing INDICATOR_KEYS/DEFAULT_INDICATOR_PARAMS/FILTER_MODES/
// IndicatorFilterState already exported from BacktestingLab.tsx's own
// IndicatorsStep Filters panel (Backtesting Lab Pro Phase D) — same
// backend contract (confluence/indicator_filters.py), same UI convention,
// just numeric params instead of an "allowed values" checkbox set.
function IndicatorFiltersBuilder({
  rows, setRows,
}: {
  rows: IndicatorFilterState[]
  setRows: (r: IndicatorFilterState[]) => void
}) {
  const rowFor = (name: (typeof INDICATOR_KEYS)[number]) => rows.find((r) => r.name === name)

  const setMode = (name: (typeof INDICATOR_KEYS)[number], mode: (typeof FILTER_MODES)[number]) => {
    const others = rows.filter((r) => r.name !== name)
    if (mode === 'disabled') {
      setRows(others)
      return
    }
    const existing = rowFor(name)
    setRows([...others, { name, mode, params: existing?.params ?? { ...DEFAULT_INDICATOR_PARAMS[name] }, weight: existing?.weight ?? 0 }])
  }

  const setParam = (name: (typeof INDICATOR_KEYS)[number], key: string, value: number) => {
    const current = rowFor(name)
    if (!current) return
    setRows(rows.map((r) => (r.name === name ? { ...r, params: { ...r.params, [key]: value } } : r)))
  }

  const setWeight = (name: (typeof INDICATOR_KEYS)[number], weight: number) =>
    setRows(rows.map((r) => (r.name === name ? { ...r, weight } : r)))

  return (
    <div className="flex flex-col gap-2.5">
      <div className="text-[0.78em] bg-accent/10 border border-accent/30 text-accent rounded px-3 py-2">
        Indicators never open or close a trade on their own — they can only confirm, veto, or nudge the score of a
        decision the engines already produced.
      </div>
      {INDICATOR_KEYS.map((name) => {
        const row = rowFor(name)
        const mode = row?.mode ?? 'disabled'
        const params = row?.params ?? DEFAULT_INDICATOR_PARAMS[name]
        return (
          <div
            key={name}
            className={`rounded-lg border px-3.5 py-3 flex flex-col gap-2 ${
              mode !== 'disabled' ? 'border-accent/40 bg-accent/[0.04]' : 'border-border bg-surface/40'
            }`}
          >
            <div className="flex items-center gap-3 flex-wrap">
              <span className="font-bold text-text uppercase w-14 shrink-0">{name}</span>
              <select
                value={mode}
                onChange={(e) => setMode(name, e.target.value as (typeof FILTER_MODES)[number])}
                className="px-2 py-1 bg-bg border border-border rounded text-text text-[0.82em]"
              >
                {FILTER_MODES.map((m) => (
                  <option key={m} value={m}>{m.replace('_', ' ')}</option>
                ))}
              </select>
              {mode !== 'disabled' &&
                Object.entries(DEFAULT_INDICATOR_PARAMS[name]).map(([key, def]) => (
                  <label key={key} className="flex items-center gap-1.5 text-[0.78em] text-muted">
                    {key}
                    <input
                      type="number"
                      value={params[key] ?? def}
                      onChange={(e) => setParam(name, key, Number(e.target.value))}
                      placeholder={String(def)}
                      className="w-20 px-1.5 py-1 bg-bg border border-border rounded text-text"
                    />
                  </label>
                ))}
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
          </div>
        )
      })}
    </div>
  )
}

// Hypothesis Bundles (2026-07-30) — an atomic {timeframes, engines,
// indicators, context filters} combo, named by the operator. Fixes a real,
// live-diagnosed bug: the flat Timeframes/Engines/Indicator Filters/
// Context Filters controls above each only ever build ONE choice, so a
// mission's sampler only ever varied risk params (PF clustered ~1.0 as a
// direct result). This lets the operator define N genuinely different
// named hypotheses ("SMC only", "SMC + London + Trending", ...) and search
// across them as discrete, atomic candidates — see backtest/optimizer.py's
// hypothesis_bundle_choices for why this is a shared index, not 4
// independent ones (independent indices would explore the Cartesian
// product of bundle PIECES, not the operator's actual discrete ideas).
interface HypothesisBundleRowState {
  name: string
  timeframes: string[]
  engines: string[]
  indicatorFilters: IndicatorFilterState[]
  contextFilters: ContextFilterRowState[]
}

function blankBundle(name = ''): HypothesisBundleRowState {
  return { name, timeframes: ['H1'], engines: [], indicatorFilters: [], contextFilters: [] }
}

function HypothesisBundleBuilder({
  bundles, setBundles,
}: {
  bundles: HypothesisBundleRowState[]
  setBundles: (b: HypothesisBundleRowState[]) => void
}) {
  const updateAt = (idx: number, patch: Partial<HypothesisBundleRowState>) =>
    setBundles(bundles.map((b, i) => (i === idx ? { ...b, ...patch } : b)))
  const removeAt = (idx: number) => setBundles(bundles.filter((_, i) => i !== idx))
  const duplicateAt = (idx: number) => {
    const copy = { ...bundles[idx], name: `${bundles[idx].name} (copy)` }
    setBundles([...bundles.slice(0, idx + 1), copy, ...bundles.slice(idx + 1)])
  }
  const toggleTimeframeAt = (idx: number, tf: string) => {
    const b = bundles[idx]
    updateAt(idx, { timeframes: b.timeframes.includes(tf) ? b.timeframes.filter((t) => t !== tf) : [...b.timeframes, tf] })
  }
  const toggleEngineAt = (idx: number, e: string) => {
    const b = bundles[idx]
    updateAt(idx, { engines: b.engines.includes(e) ? b.engines.filter((x) => x !== e) : [...b.engines, e] })
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="text-[0.78em] bg-accent/10 border border-accent/30 text-accent rounded px-3 py-2">
        Each hypothesis is searched as one atomic, named candidate — the mission's sampler picks WHICH hypothesis to
        try per trial (plus risk-param sensitivity within it), never mixes pieces across hypotheses.
      </div>
      {bundles.map((b, idx) => (
        <div key={idx} className="rounded-lg border border-border bg-surface/40 px-3.5 py-3 flex flex-col gap-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <input
              value={b.name}
              onChange={(e) => updateAt(idx, { name: e.target.value })}
              placeholder={`Hypothesis ${idx + 1} name, e.g. "SMC + London + Trending"`}
              className="flex-1 min-w-[220px] bg-bg border border-border rounded px-2 py-1.5 text-[0.82em] text-text font-bold"
            />
            <button onClick={() => duplicateAt(idx)} className="text-[0.72em] text-muted hover:text-accent">Duplicate</button>
            <button onClick={() => removeAt(idx)} className="text-[0.72em] text-red hover:underline">Remove</button>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.68em] text-muted uppercase">Timeframes</span>
            <MultiCheckbox options={SUPPORTED_TIMEFRAMES} value={b.timeframes} onToggle={(tf) => toggleTimeframeAt(idx, tf)} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.68em] text-muted uppercase">Engines</span>
            <MultiCheckbox options={ENGINE_KEYS} value={b.engines} onToggle={(e) => toggleEngineAt(idx, e)} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.68em] text-muted uppercase">Indicator filters</span>
            <IndicatorFiltersBuilder rows={b.indicatorFilters} setRows={(r) => updateAt(idx, { indicatorFilters: r })} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.68em] text-muted uppercase">Context filters</span>
            <ContextFiltersBuilder rows={b.contextFilters} setRows={(r) => updateAt(idx, { contextFilters: r })} />
          </div>
        </div>
      ))}
      <button
        onClick={() => setBundles([...bundles, blankBundle()])}
        className="self-start px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.78em] hover:bg-accent/10"
      >
        + Add Hypothesis
      </button>
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
  const [indicatorFilters, setIndicatorFilters] = useState<IndicatorFilterState[]>([])
  const [hypothesisMode, setHypothesisMode] = useState(false)
  const [bundles, setBundles] = useState<HypothesisBundleRowState[]>([])
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

  const toIndicatorSpecs = (rows: IndicatorFilterState[]) =>
    rows.map((r) => ({ name: r.name, mode: r.mode, params: r.params, weight: r.weight }))
  const toContextSpecs = (rows: ContextFilterRowState[]) =>
    rows.map((r) => ({
      name: r.name,
      mode: r.mode,
      params: r.allowed.length > 0 ? { [r.name === 'day_of_week' ? 'allowed_days' : 'allowed']: r.allowed } : {},
      weight: r.weight,
    }))

  const submit = async () => {
    setError(null)
    if (symbols.length === 0) { setError('Select at least one symbol.'); return }
    if (hypothesisMode) {
      if (bundles.length === 0) { setError('Add at least one hypothesis.'); return }
      for (const b of bundles) {
        if (!b.name.trim()) { setError('Every hypothesis needs a name.'); return }
        if (b.timeframes.length === 0) { setError(`Hypothesis "${b.name}": select at least one timeframe.`); return }
        if (b.engines.length === 0) { setError(`Hypothesis "${b.name}": select at least one engine.`); return }
      }
      const names = bundles.map((b) => b.name.trim())
      if (new Set(names).size !== names.length) { setError('Hypothesis names must be unique.'); return }
    } else {
      if (timeframes.length === 0) { setError('Select at least one timeframe.'); return }
      if (engines.length === 0) { setError('Select at least one engine.'); return }
    }

    setSubmitting(true)
    try {
      const indicatorSpecs = toIndicatorSpecs(indicatorFilters)
      const contextSpecs = toContextSpecs(contextFilters)
      const body: MissionRequest = {
        name: name || undefined,
        symbols,
        sampler,
        n_trials_per_symbol: nTrials,
        objective_metric: objectiveMetric,
        min_trades: 10,
        seed: 42,
        // Vestigial-but-valid placeholders when hypothesisMode is on — the
        // backend ignores these 4 fields once hypothesis_bundle_choices is
        // set (see backtest/optimizer.py's resolve_point() branch), but
        // they're still required by validation, so reuse the FIRST
        // hypothesis's own definition rather than sending meaningless data.
        timeframes_choices: [hypothesisMode ? bundles[0].timeframes : timeframes],
        engine_set_choices: [hypothesisMode ? bundles[0].engines : engines],
        indicator_set_choices: [hypothesisMode ? toIndicatorSpecs(bundles[0].indicatorFilters) : indicatorSpecs],
        context_filter_set_choices: [hypothesisMode ? toContextSpecs(bundles[0].contextFilters) : contextSpecs],
        hypothesis_bundle_choices: hypothesisMode
          ? bundles.map((b) => ({
              name: b.name.trim(),
              timeframes: b.timeframes,
              engines: b.engines,
              indicators: toIndicatorSpecs(b.indicatorFilters),
              context_filters: toContextSpecs(b.contextFilters),
            }))
          : undefined,
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

        <label className="flex items-center gap-2 text-[0.78em] text-text cursor-pointer">
          <input type="checkbox" checked={hypothesisMode} onChange={(e) => setHypothesisMode(e.target.checked)} />
          <span className="font-bold">Search across named hypotheses</span>
          <span className="text-muted">
            (engine + timeframe + context combos) instead of one fixed combo — without this, only risk params vary.
          </span>
        </label>

        {hypothesisMode ? (
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Hypotheses</span>
            <HypothesisBundleBuilder bundles={bundles} setBundles={setBundles} />
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Timeframes (this run only)</span>
              <MultiCheckbox options={SUPPORTED_TIMEFRAMES} value={timeframes} onToggle={toggleTimeframe} />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Engine set (this run only)</span>
              <MultiCheckbox options={ENGINE_KEYS} value={engines} onToggle={toggleEngine} />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Indicator filters (this run only)</span>
              <IndicatorFiltersBuilder rows={indicatorFilters} setRows={setIndicatorFilters} />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[0.7em] text-muted uppercase">Context filters (this run only)</span>
              <ContextFiltersBuilder rows={contextFilters} setRows={setContextFilters} />
            </div>
          </>
        )}

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

function FeatureAssociationTable({ association }: { association: FeatureAssociation }) {
  if (association.insufficient_data) {
    return (
      <div className="flex flex-col gap-1">
        <span className="text-[0.78em] font-mono text-accent">{association.feature}</span>
        <span className="text-muted text-[0.72em]">{association.note}</span>
      </div>
    )
  }
  const bins = [...association.bins].sort((a, b) => (b.lift_win_rate ?? 0) - (a.lift_win_rate ?? 0))
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[0.78em] font-mono text-accent">{association.feature}</span>
        <span className="text-muted text-[0.7em]">({association.feature_type}, n={association.n_observed}, baseline WR {(association.overall_win_rate * 100).toFixed(0)}%)</span>
      </div>
      <div className="flex flex-col gap-0.5">
        {bins.map((b, i) => (
          <div key={i} className="flex items-center gap-2 text-[0.75em] flex-wrap">
            <span className="w-40 truncate">{b.bin_label}</span>
            <span className="text-muted">n={b.n_trades}</span>
            <span className="text-muted">WR {(b.win_rate * 100).toFixed(0)}%</span>
            {b.lift_win_rate != null && (
              <span className={b.lift_win_rate > 1.2 ? 'text-green' : b.lift_win_rate < 0.8 ? 'text-red' : 'text-muted'}>
                lift {b.lift_win_rate.toFixed(2)}x
              </span>
            )}
            <Badge tone={b.significance === 'SURVIVES_CORRECTION' ? 'good' : b.significance === 'NOMINAL_ONLY' ? 'marginal' : 'neutral'}>
              {b.significance}
            </Badge>
          </div>
        ))}
      </div>
    </div>
  )
}

function FeatureMiningPanel({ missionId, validationId }: { missionId: string; validationId: string }) {
  const { markUnauthenticated } = useAuth()
  const query = useApiQuery(
    ['mission-feature-mining', missionId, validationId],
    () => getFeatureMining(missionId, validationId), POLL_MS, markUnauthenticated,
  )
  const data = query.data
  if (!data) return <Panel title="Feature Mining"><Empty>Loading…</Empty></Panel>

  if (data.insufficient_data) {
    return (
      <Panel title="Feature Mining" right="retrospective, descriptive, non-ML — a lead, not confirmation">
        <div className="p-4"><Empty>{data.note || 'Not enough trades yet for feature association analysis.'}</Empty></div>
      </Panel>
    )
  }

  const sorted = [...data.associations].sort((a, b) => {
    const liftA = a.insufficient_data ? 0 : Math.max(0, ...a.bins.map((bin) => bin.lift_win_rate ?? 0))
    const liftB = b.insufficient_data ? 0 : Math.max(0, ...b.bins.map((bin) => bin.lift_win_rate ?? 0))
    return liftB - liftA
  })

  return (
    <Panel title="Feature Mining" right="retrospective, descriptive, non-ML — a lead, not confirmation">
      <div className="p-4 flex flex-col gap-4">
        <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
          {data.caveat}
        </div>
        <div className="text-[0.78em] text-muted">
          {data.n_trades_total} trades pooled across validation symbols — {data.n_features_tested} feature(s) tested
          {data.bonferroni_alpha != null && ` (Bonferroni alpha = ${data.bonferroni_alpha.toFixed(6)})`}
        </div>
        <div className="grid gap-4 grid-cols-[repeat(auto-fit,minmax(280px,1fr))]">
          {sorted.map((a) => <FeatureAssociationTable key={a.feature} association={a} />)}
        </div>
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
      {v?.status === 'finished' && <FeatureMiningPanel missionId={missionId} validationId={validationId} />}
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

// Edge Discovery Summary (2026-07-30) — one row per symbol in a
// multi-symbol mission, showing its single best COMPLETE trial and
// whether that exact trial has already been through Validate (Monte
// Carlo + walk-forward + robustness), so "discover an edge per currency,
// then act on it" doesn't require flipping through every symbol tab and
// eyeballing a sorted column. Still never picks a winner across symbols
// or writes anywhere — same report-everything, LEAD-not-evidence
// contract as the rest of Mission Center.
function bestTrialPerSymbol(trials: MissionTrial[]): Record<string, MissionTrial> {
  const best: Record<string, MissionTrial> = {}
  for (const t of trials) {
    if (t.state !== 'COMPLETE' || t.objective_value == null) continue
    const current = best[t.symbol]
    if (!current || (current.objective_value ?? -Infinity) < t.objective_value) {
      best[t.symbol] = t
    }
  }
  return best
}

// Hypothesis Bundles (2026-07-30) — resolved purely client-side from data
// already fetched (mission.search_space_json + trial.params_json), no new
// backend endpoint. Returns null for every pre-existing, non-bundle-mode
// mission (regression-safe: renders as '—' wherever used).
function hypothesisNameFor(mission: MissionRow | null | undefined, trial: MissionTrial): string | null {
  if (!mission?.search_space_json) return null
  try {
    const bundles = JSON.parse(mission.search_space_json).hypothesis_bundle_choices as
      | { name: string }[]
      | null
      | undefined
    if (!bundles) return null
    const idx = JSON.parse(trial.params_json).__hypothesis_idx as number | undefined
    if (idx == null) return null
    return bundles[idx]?.name ?? null
  } catch {
    return null
  }
}

function latestValidationFor(validations: ValidationRow[], trial: MissionTrial): ValidationRow | null {
  const matches = validations
    .filter((v) => v.trial_number === trial.trial_number && v.trial_symbol === trial.symbol)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
  return matches[0] ?? null
}

function ValidationStatusBadge({ validation }: { validation: ValidationRow | null }) {
  if (!validation) return <span className="text-muted text-[0.75em]">not validated yet</span>
  if (validation.status !== 'finished') return <StatusBadge status={validation.status} />
  const verdict = validation.overall_verdict
  const tone = verdict === 'STRONG_LEAD' ? 'exec' : verdict === 'WEAK_LEAD' ? 'marginal' : 'no-trade'
  return (
    <Badge tone={tone}>
      {`${verdict ?? 'unknown'} (${validation.passing_symbols ?? 0}/${validation.total_symbols ?? 0})`}
    </Badge>
  )
}

// Trial Breakdown (2026-07-31) — by_regime/by_direction/by_session are
// already computed into every trial's metrics_json (backtest/metrics.py,
// unchanged since Phase 6's PerformanceBreakdownTable) but a Mission
// Center operator had no way to see them without downloading the raw
// JSON by hand. This panel just surfaces already-computed data — no new
// backend endpoint, no automated hypothesis generation. The
// "Suggest hypothesis from this trial" action below reuses the existing,
// unmodified, human-triggered /ai/suggest-hypothesis + /ai/save-
// hypothesis-draft flow (Phase 4d) — it only grounds the free-text
// focus_hint in this trial's own numbers, exactly like a human copying
// them into the box themselves would. Nothing here is auto-generated or
// auto-registered; every trial's exploratory status is unchanged.
function buildTrialFocusHint(trial: MissionTrial, hypothesisName: string | null, metrics: Record<string, unknown>): string {
  const parts = [
    `Mission trial #${trial.trial_number}${hypothesisName ? ` ("${hypothesisName}")` : ''}, symbol ${trial.symbol}, ` +
      `objective=${trial.objective_value ?? '—'}, trades=${trial.trades}.`,
  ]
  const summarize = (label: string, buckets: unknown) => {
    if (!buckets || typeof buckets !== 'object') return
    const entries = Object.entries(buckets as Record<string, BreakdownBucket>)
    if (entries.length === 0) return
    parts.push(
      `${label}: ` +
        entries.map(([k, b]) => `${k}=${b.trades}tr/${b.win_rate.toFixed(0)}%wr/${b.pnl >= 0 ? '+' : ''}${b.pnl.toFixed(0)}pnl`).join(', '),
    )
  }
  summarize('By regime', metrics.by_regime)
  summarize('By direction', metrics.by_direction)
  summarize('By session', metrics.by_session)
  return parts.join(' ')
}

function TrialBreakdownPanel({
  trial, hypothesisName, onClose,
}: {
  trial: MissionTrial
  hypothesisName: string | null
  onClose: () => void
}) {
  const metrics: Record<string, unknown> = trial.metrics_json ? JSON.parse(trial.metrics_json) : {}
  const byRegime = (metrics.by_regime ?? {}) as Record<string, BreakdownBucket>
  const byDirection = (metrics.by_direction ?? {}) as Record<string, BreakdownBucket>
  const bySession = (metrics.by_session ?? {}) as Record<string, BreakdownBucket>
  const hasBreakdown = Object.keys(byRegime).length > 0 || Object.keys(byDirection).length > 0 || Object.keys(bySession).length > 0

  const [ai, setAi] = useState<{ triggered: boolean; loading: boolean; error: string | null; data: SuggestHypothesisResponse | null }>({
    triggered: false, loading: false, error: null, data: null,
  })
  const [draftFile, setDraftFile] = useState<string | null>(null)

  const suggest = async () => {
    setAi({ triggered: true, loading: true, error: null, data: null })
    setDraftFile(null)
    try {
      const data = await suggestHypothesis(buildTrialFocusHint(trial, hypothesisName, metrics))
      setAi({ triggered: true, loading: false, error: null, data })
    } catch (e) {
      setAi({ triggered: true, loading: false, error: e instanceof Error ? e.message : String(e), data: null })
    }
  }

  const saveDraft = async () => {
    if (!ai.data || ai.data.status !== 'ok') return
    try {
      const result = await saveHypothesisDraft({
        title: ai.data.title ?? '', statement: ai.data.statement ?? '',
        why_this_might_be_true: ai.data.why_this_might_be_true ?? '',
        data_required: ai.data.data_required ?? {}, falsification_criteria: ai.data.falsification_criteria ?? '',
        distinct_from_prior_kill: ai.data.distinct_from_prior_kill ?? '', notes: ai.data.notes ?? '',
      })
      setDraftFile(result.file)
    } catch (e) {
      setDraftFile(`error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <Panel
      title={`Trial ${trial.trial_number} breakdown — ${trial.symbol}${hypothesisName ? ` — "${hypothesisName}"` : ''}`}
      right={<button onClick={onClose} className="text-[0.75em] text-muted hover:text-accent">Close</button>}
    >
      <div className="p-4 flex flex-col gap-4">
        {!hasBreakdown ? (
          <Empty>No regime/direction/session breakdown available for this trial (0 closed trades, or an older mission run).</Empty>
        ) : (
          <div className="grid gap-4 grid-cols-[repeat(auto-fit,minmax(240px,1fr))]">
            {Object.keys(byDirection).length > 0 && <PerformanceBreakdownTable title="Direction" buckets={byDirection} />}
            {Object.keys(bySession).length > 0 && <PerformanceBreakdownTable title="Session" buckets={bySession} />}
            {Object.keys(byRegime).length > 0 && <PerformanceBreakdownTable title="Regime" buckets={byRegime} />}
          </div>
        )}
        <div className="border-t border-border pt-3 flex flex-col gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={suggest}
              disabled={ai.loading}
              className="text-[0.78em] px-2.5 py-1 rounded border border-accent/40 text-accent hover:bg-accent/10 disabled:opacity-50"
            >
              {ai.loading ? 'Asking AI…' : 'Suggest hypothesis from this trial'}
            </button>
            <span className="text-muted text-[0.72em]">
              Grounded in this trial's own numbers above — still a LEAD, human-triggered, never auto-registered.
            </span>
          </div>
          {ai.triggered && (
            <AiStatusFrame loading={ai.loading} fetchError={ai.error} status={ai.data?.status} providerError={ai.data?.error}>
              {ai.data?.status === 'ok' && (
                <div className="flex flex-col gap-2 text-[0.82em]">
                  <div className="font-bold text-accent">{ai.data.title}</div>
                  <div>{ai.data.statement}</div>
                  <div className="text-muted"><span className="font-bold text-text">Why:</span> {ai.data.why_this_might_be_true}</div>
                  <div className="text-muted"><span className="font-bold text-text">Falsification:</span> {ai.data.falsification_criteria}</div>
                  <div className="text-muted"><span className="font-bold text-text">Distinct from prior kill:</span> {ai.data.distinct_from_prior_kill}</div>
                  {draftFile
                    ? <span className="text-[0.75em] text-muted">{draftFile}</span>
                    : <button onClick={saveDraft} className="self-start text-[0.75em] text-accent hover:underline">Save as draft</button>}
                </div>
              )}
            </AiStatusFrame>
          )}
        </div>
      </div>
    </Panel>
  )
}

function MissionDetail({ missionId }: { missionId: string }) {
  const { markUnauthenticated } = useAuth()
  const statusQuery = useApiQuery(['mission-status', missionId], () => getMissionStatus(missionId), POLL_MS, markUnauthenticated)
  const symbolsQuery = useApiQuery(['research-symbols'], getResearchSymbols, POLL_MS, markUnauthenticated)
  const [leaderboardTrials, setLeaderboardTrials] = useState<MissionTrial[]>([])
  const [allTrials, setAllTrials] = useState<MissionTrial[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [draftStatus, setDraftStatus] = useState<Record<number, string>>({})
  const [breakdownTrial, setBreakdownTrial] = useState<MissionTrial | null>(null)

  const status: MissionStatusResponse | null = statusQuery.data
  const validationsQuery = useApiQuery(
    ['mission-validations', missionId],
    () => listValidations(missionId).then((r) => r.validations),
    POLL_MS,
    markUnauthenticated,
  )
  const validations = validationsQuery.data ?? []

  useEffect(() => {
    let cancelled = false
    getMissionLeaderboard(missionId, selectedSymbol ?? undefined).then((r) => {
      if (!cancelled) setLeaderboardTrials(r.trials)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [missionId, selectedSymbol, status?.progress.total])

  useEffect(() => {
    let cancelled = false
    getMissionLeaderboard(missionId).then((r) => {
      if (!cancelled) setAllTrials(r.trials)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [missionId, status?.progress.total])

  if (!status) return <Empty>Loading…</Empty>

  const symbols = Object.keys(status.progress.by_symbol)
  const isRunning = status.job_status === 'queued' || status.job_status === 'running'
  const bestPerSymbol = bestTrialPerSymbol(allTrials)

  const cancel = async () => {
    setCancelling(true)
    try { await cancelMission(missionId) } finally { setCancelling(false) }
  }

  const proposeAsDraft = async (t: MissionTrial) => {
    try {
      const metrics = t.metrics_json ? JSON.parse(t.metrics_json) : {}
      const validation = latestValidationFor(validations, t)
      const hypothesisName = hypothesisNameFor(status.mission, t)
      const validationNote = validation && validation.status === 'finished'
        ? `A Mission Center validation (Monte Carlo + walk-forward + robustness) was already run against ` +
          `this exact trial: verdict=${validation.overall_verdict} (${validation.passing_symbols}/` +
          `${validation.total_symbols} validation symbols passing, objective_metric=${validation.objective_metric}). ` +
          `Still a LEAD, not registry evidence — see mission_center validation_id=${validation.id} for the full ` +
          `per-symbol breakdown (criteria, Monte Carlo, walk-forward, robustness) before treating this as anything ` +
          `more than a stronger-than-average candidate.`
        : 'No Mission Center validation has been run against this exact trial yet — this is a raw, ' +
          'single-run sampler result with no out-of-sample or Monte Carlo check at all. Strongly consider ' +
          'clicking "Validate…" on this trial before writing falsification criteria.'
      const hypothesisLabel = hypothesisName ? ` — Hypothesis "${hypothesisName}"` : ''
      const result = await saveHypothesisDraft({
        title: `Mission ${missionId} trial ${t.trial_number}${hypothesisLabel} (${t.symbol})`,
        statement: `A candidate configuration found by mission ${missionId} (sampler-driven search, ` +
          `symbol ${t.symbol}, trial #${t.trial_number}${hypothesisName ? `, hypothesis "${hypothesisName}"` : ''}) ` +
          `showed objective_value=${t.objective_value} over ${t.trades} trades. This is a LEAD from an ` +
          `exploratory search, not a tested hypothesis.`,
        why_this_might_be_true: `Not yet reviewed — fill in before registering. Validation context: ${validationNote}`,
        data_required: { symbol: t.symbol, params: JSON.parse(t.params_json), metrics },
        falsification_criteria: 'Not yet defined — must be written BEFORE re-testing (CLAUDE.md rule 1).',
        distinct_from_prior_kill: 'Not yet reviewed — check against CLAUDE.md\'s dead list before registering.',
        notes: `Auto-generated from Mission Center. mission_id=${missionId}, trial_number=${t.trial_number}.` +
          (validation ? ` validation_id=${validation.id}.` : ''),
      })
      setDraftStatus((s) => ({ ...s, [t.trial_number]: result.file }))
    } catch (e) {
      setDraftStatus((s) => ({ ...s, [t.trial_number]: `error: ${e instanceof Error ? e.message : String(e)}` }))
    }
  }

  const columns: Column<MissionTrial>[] = [
    { header: 'Symbol', render: (t) => t.symbol },
    { header: 'Hypothesis', render: (t) => hypothesisNameFor(status.mission, t) ?? '—' },
    { header: 'Trial #', render: (t) => t.trial_number, align: 'right', accessorFn: (t) => t.trial_number },
    { header: 'State', render: (t) => <StatusBadge status={t.state.toLowerCase()} /> },
    { header: 'Objective', render: (t) => t.objective_value != null ? t.objective_value.toFixed(3) : '—', align: 'right', accessorFn: (t) => t.objective_value ?? -Infinity, sortingFn: 'basic' },
    { header: 'Trades', render: (t) => t.trades, align: 'right', accessorFn: (t) => t.trades, sortingFn: 'basic' },
    {
      header: 'Breakdown',
      render: (t) => (
        t.state === 'COMPLETE'
          ? <button onClick={() => setBreakdownTrial(t)} className="text-[0.75em] text-accent hover:underline">View</button>
          : <span className="text-muted text-[0.75em]">—</span>
      ),
    },
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

  const bestColumns: Column<MissionTrial>[] = [
    ...columns,
    {
      header: 'Validation status',
      render: (t) => <ValidationStatusBadge validation={latestValidationFor(validations, t)} />,
    },
  ]

  const sig = significanceFromLeaderboard(leaderboardTrials)
  const bestRows = symbols.map((s) => bestPerSymbol[s]).filter((t): t is MissionTrial => !!t)

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

      {breakdownTrial && (
        <TrialBreakdownPanel
          trial={breakdownTrial}
          hypothesisName={hypothesisNameFor(status.mission, breakdownTrial)}
          onClose={() => setBreakdownTrial(null)}
        />
      )}

      {symbols.length > 1 && (
        <Panel
          title="Edge Discovery Summary — Best Per Symbol"
          right="one best COMPLETE trial per currency, never an auto-selected mission winner"
        >
          <div className="p-4 flex flex-col gap-3">
            <div className="text-[0.78em] text-muted">
              For each symbol searched in this mission, its single best-objective completed trial —
              the fastest way to see "which currency has the most promising raw candidate," before
              deciding which one to send through Validate and (if it survives) draft as a real
              hypothesis. This never picks a winner across symbols or currencies — every symbol gets
              its own row, and the full per-symbol leaderboard above still shows every trial.
            </div>
            {bestRows.length === 0 ? (
              <Empty>No completed trials yet for any symbol.</Empty>
            ) : (
              <DataTable columns={bestColumns} rows={bestRows} rowKey={(t) => `${t.symbol}-best`} />
            )}
          </div>
        </Panel>
      )}

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
