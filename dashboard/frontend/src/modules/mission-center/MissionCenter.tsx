import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
  SAMPLER_KEYS, OPTIMIZABLE_METRICS, formatPossiblyInfinite,
  type MissionRequest, type MissionSummary, type MissionStatusResponse, type MissionTrial, type MissionRow,
  type CriteriaEntry, type DimensionFrequency, type Verdict, type FeatureAssociation,
  type ValidationRow, type ConsensusClaim, type PooledBreakdownRow, type OpportunityCandidate,
  type PrefillRequest, type PossiblyInfinite, type CandidateLock, type DateOverlap,
  getDirectionSymmetryAudit, type DirectionSymmetryResponse, type SymmetryFinding,
  type SignificanceDiagnostic, type RegimeRobustnessDiagnostic, type StabilityDiagnostic,
  type CostStressDiagnostic, type DiscoveryScoreDiagnostic,
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

// Track C (Phase 4, 2026-08-01) — mirrors backtesting.backtest_engine.
// ENGINE_VARIANT_KEYS exactly: the picker only ever offers variants that
// actually exist, and only for engines that have one. Reachable only
// through Mission Center's ephemeral engine_variant_choices — never
// activates a variant in config/engines.yaml's live default.
const ENGINE_VARIANT_KEYS: Record<string, string[]> = {
  price_action: ['v1', 'v2'],
  wyckoff: ['v1', 'v2'],
}

// Small per-engine v1/v2 <select> row, rendered only for variant-capable
// engines currently checked in the accompanying engine MultiCheckbox —
// an engine that's OFF has no meaningful variant to pick.
function EngineVariantPicker({
  engines, value, onChange,
}: {
  engines: string[]
  value: Record<string, string>
  onChange: (v: Record<string, string>) => void
}) {
  const variantCapable = engines.filter((e) => e in ENGINE_VARIANT_KEYS)
  if (variantCapable.length === 0) return null
  return (
    <div className="flex flex-wrap gap-3">
      {variantCapable.map((e) => (
        <label key={e} className="flex items-center gap-1.5 text-[0.75em] text-muted">
          {e}
          <select
            value={value[e] ?? 'v1'}
            onChange={(ev) => {
              const next = { ...value }
              if (ev.target.value === 'v1') delete next[e]
              else next[e] = ev.target.value
              onChange(next)
            }}
            className="px-1.5 py-1 bg-bg border border-border rounded text-text text-[0.82em]"
          >
            {ENGINE_VARIANT_KEYS[e].map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
      ))}
    </div>
  )
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
  engineVariants: Record<string, string>
}

function blankBundle(name = ''): HypothesisBundleRowState {
  return { name, timeframes: ['H1'], engines: [], indicatorFilters: [], contextFilters: [], engineVariants: {} }
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
            <EngineVariantPicker
              engines={b.engines} value={b.engineVariants}
              onChange={(v) => updateAt(idx, { engineVariants: v })}
            />
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

function MissionBuilder({
  onCreated, prefillRequest, onPrefillConsumed,
}: {
  onCreated: (missionId: string) => void
  prefillRequest?: PrefillRequest | null
  onPrefillConsumed?: () => void
}) {
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
  const [engineVariants, setEngineVariants] = useState<Record<string, string>>({})
  // Mission Center Research Rigor Phase 1 (2026-08-06) — mission-wide,
  // applies regardless of flat/hypothesis mode. '' means "no override"
  // (production quorum, currently 2 in config.yaml, applies unchanged).
  const [quorumOverride, setQuorumOverride] = useState<number | ''>('')
  const [infoShareOverride, setInfoShareOverride] = useState<number | ''>('')
  const [hypothesisMode, setHypothesisMode] = useState(false)
  const [bundles, setBundles] = useState<HypothesisBundleRowState[]>([])
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [prefillNotice, setPrefillNotice] = useState(false)

  // Edge Discovery (2026-07-31) — "Pre-fill…" from TopOpportunitiesPanel.
  // Only ever pre-fills the form; the operator still picks engines/
  // timeframes and clicks Launch Mission themselves — never auto-submits.
  useEffect(() => {
    if (!prefillRequest) return
    setHypothesisMode(true)
    const contextRows: ContextFilterRowState[] = []
    if (prefillRequest.direction) {
      // TradeRecord.direction is BUY/SELL; ContextSpec's "direction" filter
      // uses BULLISH/BEARISH (confluence/context_filters.py) — translate.
      contextRows.push({
        name: 'direction', mode: 'entry_filter',
        allowed: [prefillRequest.direction === 'BUY' ? 'BULLISH' : 'BEARISH'], weight: 0,
      })
    }
    if (prefillRequest.regime) {
      contextRows.push({ name: 'market_regime', mode: 'entry_filter', allowed: [prefillRequest.regime], weight: 0 })
    }
    if (prefillRequest.session) {
      contextRows.push({ name: 'session', mode: 'entry_filter', allowed: [prefillRequest.session], weight: 0 })
    }
    setBundles([{ ...blankBundle(prefillRequest.label), contextFilters: contextRows }])
    setPrefillNotice(true)
    onPrefillConsumed?.()
    // onPrefillConsumed is a fresh inline closure every render (`() =>
    // setPrefillRequest(null)` in MissionCenter()); including it below
    // would re-run this effect on every re-render, not just when a new
    // prefillRequest actually arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillRequest])

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
      // Forensic Audit follow-up (2026-08-03) — mirrors the backend's own
      // fail-fast check: hypothesis-search mode with only 1 hypothesis
      // silently produces a "no variation" mission (every trial samples
      // the sole choice) with no warning before trials execute.
      if (bundles.length === 1) {
        setError('Add at least 2 hypotheses to search across, or turn off "Search across named hypotheses" to run one fixed configuration.')
        return
      }
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
        engine_variant_choices: [hypothesisMode ? bundles[0].engineVariants : engineVariants],
        hypothesis_bundle_choices: hypothesisMode
          ? bundles.map((b) => ({
              name: b.name.trim(),
              timeframes: b.timeframes,
              engines: b.engines,
              indicators: toIndicatorSpecs(b.indicatorFilters),
              context_filters: toContextSpecs(b.contextFilters),
              engine_variants: b.engineVariants,
            }))
          : undefined,
        confluence_overrides:
          quorumOverride === '' && infoShareOverride === ''
            ? undefined
            : {
                ...(quorumOverride === '' ? {} : { min_engines_agreeing: quorumOverride }),
                ...(infoShareOverride === '' ? {} : { min_informative_weight_share: infoShareOverride }),
              },
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

        {!hypothesisMode && (
          <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
            Flat mode fixes ONE engine/timeframe/indicator/context combination for every trial — only risk/cost
            parameters actually vary across trials. To search across genuinely different signal configurations,
            enable "Search across named hypotheses" below and define 2+ bundles.
          </div>
        )}

        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Confluence quorum override (optional)</span>
            <input
              type="number" min={1} max={9} placeholder="production default (2)"
              value={quorumOverride}
              onChange={(e) => setQuorumOverride(e.target.value === '' ? '' : Number(e.target.value))}
              className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-56"
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[0.7em] text-muted uppercase">Min informative weight share (optional)</span>
            <input
              type="number" min={0} max={1} step={0.05} placeholder="production default (0.6)"
              value={infoShareOverride}
              onChange={(e) => setInfoShareOverride(e.target.value === '' ? '' : Number(e.target.value))}
              className="bg-surface border border-border rounded px-2 py-1.5 text-[0.78em] text-text w-56"
            />
          </div>
        </div>
        <span className="text-[0.72em] text-muted -mt-2">
          Lowering quorum below the enabled-engine count is required to test a single engine in isolation —
          production quorum (2) can never pass with fewer than 2 enabled engines. Applies mission-wide, to
          every trial regardless of which hypothesis/engine set it draws.
        </span>

        <label className="flex items-center gap-2 text-[0.78em] text-text cursor-pointer">
          <input type="checkbox" checked={hypothesisMode} onChange={(e) => setHypothesisMode(e.target.checked)} />
          <span className="font-bold">Search across named hypotheses</span>
          <span className="text-muted">
            (engine + timeframe + context combos) instead of one fixed combo — without this, only risk params vary.
          </span>
        </label>

        {prefillNotice && (
          <div className="flex items-center gap-2 text-[0.75em] text-accent bg-accent/10 border border-accent/30 rounded px-3 py-2">
            <span>Pre-filled from Top Unexplored Opportunities — pick engines/timeframes, then Launch Mission when ready.</span>
            <button onClick={() => setPrefillNotice(false)} className="ml-auto text-muted hover:text-accent">Dismiss</button>
          </div>
        )}

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
              <EngineVariantPicker engines={engines} value={engineVariants} onChange={setEngineVariants} />
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
  const tone = verdict === 'STRONG_LEAD' || verdict === 'SAME_SYMBOL_CONFIRMED' ? 'good'
    : verdict === 'WEAK_LEAD' ? 'marginal'
    : 'poor'
  return <Badge tone={tone}>{verdict}</Badge>
}

// Per-trial "Validate…" action — never auto-picks a candidate. Defaults
// to SAME_SYMBOL mode (confirms ONLY the trial's own symbol, read-only —
// nothing to pick). Toggling to CROSS_SYMBOL reveals the existing ≥2-
// symbol picker plus a persistent, unambiguous banner (Forensic Audit
// Phase 1, item D, 2026-08-02 — "must become an invariant in the code,
// not just a UI tweak").
function ValidateAction({
  missionId, trial, symbolsData,
}: {
  missionId: string
  trial: MissionTrial
  symbolsData: SymbolsResponse | null
}) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'SAME_SYMBOL' | 'CROSS_SYMBOL'>('SAME_SYMBOL')
  const [validationSymbols, setValidationSymbols] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  if (trial.state !== 'COMPLETE') return <span className="text-muted text-[0.75em]">—</span>

  const setModeChecked = (next: 'SAME_SYMBOL' | 'CROSS_SYMBOL') => {
    setMode(next)
    setValidationSymbols(next === 'CROSS_SYMBOL' ? [] : [trial.symbol])
  }

  const submit = async () => {
    setError(null)
    const symbols = mode === 'SAME_SYMBOL' ? [trial.symbol] : validationSymbols
    if (mode === 'CROSS_SYMBOL' && symbols.length < 2) { setError('Pick at least 2 validation symbols.'); return }
    setSubmitting(true)
    try {
      await createValidation(missionId, {
        trial_number: trial.trial_number,
        trial_symbol: trial.symbol,
        validation_symbols: symbols,
        validation_mode: mode,
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
      <button onClick={() => { setOpen((o) => !o); setModeChecked('SAME_SYMBOL') }}
        className="text-[0.75em] text-accent hover:underline">
        {done ? 'Launched — see Validations below' : 'Validate…'}
      </button>
      {open && (
        <div className="absolute z-20 right-0 mt-1 w-72 bg-panel border border-border rounded shadow-md p-3 flex flex-col gap-2">
          <div className="flex gap-1">
            {(['SAME_SYMBOL', 'CROSS_SYMBOL'] as const).map((m) => (
              <button key={m} onClick={() => setModeChecked(m)}
                className={`px-2 py-1 rounded text-[0.72em] border ${mode === m ? 'border-accent text-accent' : 'border-border text-muted'}`}>
                {m}
              </button>
            ))}
          </div>
          {mode === 'SAME_SYMBOL' ? (
            <div className="text-[0.78em] text-text">
              Validating <span className="font-mono">{trial.symbol}</span> only — its own training symbol,
              not cross-symbol evidence.
            </div>
          ) : (
            <>
              <div className="text-[0.75em] text-red bg-red/10 border border-red/30 rounded px-2 py-1.5 font-bold">
                CROSS-SYMBOL VALIDATION — this is not same-symbol validation.
              </div>
              <span className="text-[0.7em] text-muted uppercase">Validation symbols (≥2)</span>
              <SymbolMultiSelect value={validationSymbols} onChange={setValidationSymbols} symbolsData={symbolsData} />
            </>
          )}
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

// Edge Discovery (2026-07-31) — real, computed cross-trial claims (exact
// binomial sign test), never AI narrative. Always emitted for the fixed
// 16-claim family; INSUFFICIENT_DATA claims are hidden here (they carry
// no useful text), not silently dropped from the underlying data.
function ConsensusClaimsPanel({ claims }: { claims: ConsensusClaim[] }) {
  const usable = claims.filter((c) => c.significance !== 'INSUFFICIENT_DATA')
  if (usable.length === 0) {
    return <Empty>Not enough trials yet to compare direction/regime/session pairs.</Empty>
  }
  const tone = (s: ConsensusClaim['significance']) =>
    s === 'SURVIVES_CORRECTION' ? 'good'
      : s === 'NOMINAL_ONLY' ? 'marginal'
      : s === 'DEPENDENT_TRIALS_LEAD_ONLY' ? 'poor'
      : 'neutral'
  return (
    <div className="flex flex-col gap-1.5">
      {usable.map((c, i) => (
        <div key={i} className="flex items-start gap-2 text-[0.8em]">
          <Badge tone={tone(c.significance)}>{c.significance}</Badge>
          <span>{c.claim_text}</span>
        </div>
      ))}
    </div>
  )
}

function pfSortValue(pf: PossiblyInfinite): number {
  if (pf === 'Infinity') return Number.POSITIVE_INFINITY
  if (pf === '-Infinity') return Number.NEGATIVE_INFINITY
  if (pf === 'NaN') return Number.NEGATIVE_INFINITY
  return pf
}

// Every tree level (1-way/2-way/3-way) shows up as its own row here —
// derived from ONE pooled 3-way field, so "BUY", "BUY + RANGING", and
// "BUY + RANGING + London" are all real, independently-computed rows.
function PooledBreakdownTable({ rows }: { rows: PooledBreakdownRow[] }) {
  const sorted = [...rows].sort((a, b) => pfSortValue(b.profit_factor) - pfSortValue(a.profit_factor))
  const columns: Column<PooledBreakdownRow>[] = [
    { header: 'Combo', render: (r) => [r.direction, r.regime, r.session].filter(Boolean).join(' + ') },
    { header: 'Trades', render: (r) => r.trades, align: 'right', accessorFn: (r) => r.trades, sortingFn: 'basic' },
    { header: 'Win Rate', render: (r) => `${r.win_rate.toFixed(1)}%`, align: 'right' },
    {
      header: 'PF', render: (r) => formatPossiblyInfinite(r.profit_factor, 2),
      align: 'right', accessorFn: (r) => pfSortValue(r.profit_factor), sortingFn: 'basic',
    },
    {
      header: 'PnL',
      render: (r) => <span className={r.pnl >= 0 ? 'text-green' : 'text-red'}>{r.pnl >= 0 ? '+' : ''}{r.pnl.toFixed(2)}</span>,
      align: 'right',
    },
  ]
  return <DataTable columns={columns} rows={sorted} rowKey={(r) => `${r.level}:${r.direction ?? ''}:${r.regime ?? ''}:${r.session ?? ''}`} />
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
        {data.dependence_warning && (
          <div className="text-[0.78em] text-red bg-red/10 border border-red/30 rounded px-3 py-2 font-bold">
            {data.dependence_warning}
          </div>
        )}
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
        {data.cross_trial_consensus.length > 0 && (
          <div className="flex flex-col gap-2">
            <span className="text-[0.72em] text-muted uppercase tracking-[1px]">
              Cross-trial consensus — real, computed (exact binomial sign test), pooled across all {data.n_complete_trials} trials
            </span>
            <ConsensusClaimsPanel claims={data.cross_trial_consensus} />
          </div>
        )}
        {data.pooled_breakdown.length > 0 && (
          <div className="flex flex-col gap-2">
            <span className="text-[0.72em] text-muted uppercase tracking-[1px]">
              Pooled direction × regime × session breakdown — sorted by PF, sortable by any column
              {data.dependence_detected && ' — ⚠ DEPENDENT TRIALS, see warning above'}
            </span>
            <PooledBreakdownTable rows={data.pooled_breakdown} />
          </div>
        )}
      </div>
    </Panel>
  )
}

// Ranked SUGGESTIONS ONLY — never auto-selected, never auto-run. Clicking
// "Pre-fill…" only populates MissionBuilder's already-built hypothesisMode
// form below; the operator still picks engines/timeframes and clicks
// Launch Mission themselves.
function TopOpportunitiesPanel({
  missionId, onRequestPrefill,
}: {
  missionId: string
  onRequestPrefill: (req: PrefillRequest) => void
}) {
  const { markUnauthenticated } = useAuth()
  const query = useApiQuery(['mission-meta-analysis', missionId], () => getMetaAnalysis(missionId), POLL_MS, markUnauthenticated)
  const data = query.data
  if (!data || data.insufficient_data) return null
  const candidates = data.opportunity_candidates
  if (candidates.length === 0) return null

  const columns: Column<OpportunityCandidate>[] = [
    { header: 'Combo', render: (c) => c.label },
    { header: 'Trades', render: (c) => c.trades, align: 'right' },
    { header: 'PF', render: (c) => formatPossiblyInfinite(c.profit_factor, 2), align: 'right' },
    { header: 'Effect size', render: (c) => formatPossiblyInfinite(c.effect_size, 3), align: 'right' },
    {
      header: 'Pre-fill…',
      render: (c) => (
        <button
          onClick={() => onRequestPrefill({ direction: c.direction, regime: c.regime, session: c.session, label: c.label })}
          className="text-[0.75em] text-accent hover:underline"
        >
          Pre-fill →
        </button>
      ),
    },
  ]

  return (
    <Panel
      title="Top Unexplored Opportunities"
      right="ranked SUGGESTIONS ONLY — never auto-run. Pre-fill a new hypothesis below, you launch it yourself."
    >
      <div className="p-4 flex flex-col gap-3">
        {data.dependence_detected && (
          <div className="text-[0.78em] text-red bg-red/10 border border-red/30 rounded px-3 py-2 font-bold">
            {data.dependence_warning}
          </div>
        )}
        <DataTable
          columns={columns}
          rows={candidates}
          rowKey={(c) => `${c.level}:${c.direction ?? ''}:${c.regime ?? ''}:${c.session ?? ''}`}
        />
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
        {v && <Badge tone={v.validation_mode === 'SAME_SYMBOL' ? 'marginal' : 'neutral'}>{v.validation_mode}</Badge>}
        {v && <VerdictBadge verdict={v.overall_verdict} />}
        {v && (
          <span className="text-[0.78em] text-muted">
            {v.passing_symbols ?? '—'}/{v.total_symbols ?? '—'} symbols passing
          </span>
        )}
        {v?.error && <span className="text-red text-[0.78em]">{v.error}</span>}
      </div>
      {v && v.validation_mode === 'SAME_SYMBOL' && (
        <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
          SAME_SYMBOL validation — this only confirms the trial's own symbol and is NOT cross-symbol evidence.
          It does not rule out curve-fitting. Run a CROSS_SYMBOL validation against ≥3 other symbols before
          treating this as a lead.
        </div>
      )}
      {v && (() => {
        let candidateLock: CandidateLock | null = null
        let dateOverlap: DateOverlap | null = null
        try { candidateLock = v.candidate_lock_json ? JSON.parse(v.candidate_lock_json) : null } catch { /* malformed row */ }
        try { dateOverlap = v.date_overlap_json ? JSON.parse(v.date_overlap_json) : null } catch { /* malformed row */ }
        return (
          <div className="flex flex-col gap-2">
            {candidateLock && candidateLock.available && !candidateLock.matches && (
              <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
                Candidate lock drift since this trial ran: {(candidateLock.diffs ?? []).join('; ')}. Informational only — a growing dataset is not necessarily a bug.
              </div>
            )}
            {candidateLock && !candidateLock.available && (
              <div className="text-[0.78em] text-muted bg-surface/40 border border-border rounded px-3 py-2">
                Candidate lock: {candidateLock.note}
              </div>
            )}
            {dateOverlap && dateOverlap.overlaps && (
              <div className="text-[0.78em] text-red bg-red/10 border border-red/30 rounded px-3 py-2 font-bold">
                {dateOverlap.note} (trial: {dateOverlap.original_start ?? '—'} → {dateOverlap.original_end ?? '—'}, validation: {dateOverlap.validation_start ?? '—'} → {dateOverlap.validation_end ?? '—'})
              </div>
            )}
          </div>
        )
      })()}
      {data.results.length === 0 ? (
        <Empty>No per-symbol results yet.</Empty>
      ) : (
        <div className="flex flex-col gap-2">
          {data.results.map((r) => {
            const breakdown: Record<string, CriteriaEntry> = r.criteria_breakdown_json
              ? JSON.parse(r.criteria_breakdown_json) : {}
            let significance: SignificanceDiagnostic | null = null
            try { significance = r.significance_json ? JSON.parse(r.significance_json) : null } catch { /* malformed row */ }
            let regimeRobustness: RegimeRobustnessDiagnostic | null = null
            try { regimeRobustness = r.regime_robustness_json ? JSON.parse(r.regime_robustness_json) : null } catch { /* malformed row */ }
            let stability: StabilityDiagnostic | null = null
            try { stability = r.stability_json ? JSON.parse(r.stability_json) : null } catch { /* malformed row */ }
            let costStress: CostStressDiagnostic | null = null
            try { costStress = r.cost_stress_json ? JSON.parse(r.cost_stress_json) : null } catch { /* malformed row */ }
            let discoveryScore: DiscoveryScoreDiagnostic | null = null
            try { discoveryScore = r.discovery_score_json ? JSON.parse(r.discovery_score_json) : null } catch { /* malformed row */ }
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
                {significance && significance.effective_sample_size != null && (
                  <div className="mt-2 pt-2 border-t border-border text-[0.75em] text-muted flex flex-col gap-1">
                    <span className="uppercase tracking-[1px] text-[0.85em]">Effective Sample Size (diagnostic only)</span>
                    <span className="font-mono">
                      {significance.effective_sample_size.toFixed(1)} effective of {significance.n_trades} trades
                      {' '}(autocorrelation ratio {significance.autocorrelation_ratio?.toFixed(3) ?? '—'})
                    </span>
                    <span className="font-mono">
                      nominal p={significance.nominal_p_value != null ? significance.nominal_p_value.toFixed(4) : '—'}
                      {' '}→ ESS-adjusted p={significance.ess_adjusted_p_value != null ? significance.ess_adjusted_p_value.toFixed(4) : '—'}
                    </span>
                    <span>{significance.note}</span>
                  </div>
                )}
                {regimeRobustness && regimeRobustness.regime_robustness_score != null && (
                  <div className="mt-2 pt-2 border-t border-border text-[0.75em] text-muted flex flex-col gap-1">
                    <span className="uppercase tracking-[1px] text-[0.85em]">Regime Robustness (diagnostic only)</span>
                    <span className="font-mono">
                      {regimeRobustness.regimes_profitable} of {regimeRobustness.regimes_material} regime(s) profitable
                      {' '}(score {regimeRobustness.regime_robustness_score.toFixed(2)})
                    </span>
                    {regimeRobustness.dominant_regime && regimeRobustness.dominant_regime_share != null && (
                      <span className="font-mono">
                        dominant regime: {regimeRobustness.dominant_regime}
                        {' '}({(regimeRobustness.dominant_regime_share * 100).toFixed(0)}% of trades)
                      </span>
                    )}
                    <span>{regimeRobustness.note}</span>
                  </div>
                )}
                {regimeRobustness && regimeRobustness.regime_robustness_score == null && regimeRobustness.regimes_traded > 0 && (
                  <div className="mt-2 pt-2 border-t border-border text-[0.75em] text-muted">
                    Regime Robustness: {regimeRobustness.note}
                  </div>
                )}
                {stability && stability.stability_score != null && (
                  <div className="mt-2 pt-2 border-t border-border text-[0.75em] text-muted flex flex-col gap-1">
                    <span className="uppercase tracking-[1px] text-[0.85em]">Stability Score (diagnostic only)</span>
                    <span className="font-mono">
                      {stability.params_stable} of {stability.params_measurable} measurable swept param(s) STABLE
                      {' '}(score {stability.stability_score.toFixed(2)})
                      {stability.params_insufficient > 0 ? `, ${stability.params_insufficient} insufficient` : ''}
                    </span>
                    <span>{stability.note}</span>
                  </div>
                )}
                {stability && stability.stability_score == null && stability.params_swept > 0 && (
                  <div className="mt-2 pt-2 border-t border-border text-[0.75em] text-muted">
                    Stability Score: {stability.note}
                  </div>
                )}
                {costStress && costStress.levels.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-border text-[0.75em] text-muted flex flex-col gap-1">
                    <span className="uppercase tracking-[1px] text-[0.85em]">Cost Stress Test (diagnostic only)</span>
                    <span className="font-mono">
                      baseline commission {costStress.baseline_commission_pips} pips / slippage {costStress.baseline_slippage_pips} pips
                    </span>
                    <div className="flex flex-col gap-0.5">
                      {costStress.levels.map((lv) => (
                        <span key={lv.multiplier} className="font-mono">
                          {lv.multiplier}x costs ({lv.commission_pips}/{lv.slippage_pips} pips) — {lv.trades} trade(s),
                          {' '}PF {formatPossiblyInfinite(lv.profit_factor, 2)}
                          {' '}— {lv.edge_survives == null ? 'not measurable' : lv.edge_survives ? 'edge survives' : 'edge fails'}
                        </span>
                      ))}
                    </div>
                    {costStress.survives_all_stress_levels != null && (
                      <span className="font-mono">
                        {costStress.survives_all_stress_levels ? 'Survives all stress levels' : 'Does NOT survive all stress levels'}
                      </span>
                    )}
                    <span>{costStress.note}</span>
                  </div>
                )}
                {discoveryScore && (
                  <div className="mt-2 pt-2 border-t border-border text-[0.75em] text-muted flex flex-col gap-1">
                    <span className="uppercase tracking-[1px] text-[0.85em]">Discovery Score (diagnostic only)</span>
                    <span className="font-mono">
                      {discoveryScore.discovery_score != null
                        ? `${discoveryScore.discovery_score.toFixed(2)} (${discoveryScore.components_used} of ${discoveryScore.components_total} components)`
                        : `not computable (${discoveryScore.components_used} of ${discoveryScore.components_total} components available)`}
                    </span>
                    <span className="font-mono">
                      significance {discoveryScore.significance_component ?? '—'}
                      {' '}| regime robustness {discoveryScore.regime_robustness_component ?? '—'}
                      {' '}| stability {discoveryScore.stability_component ?? '—'}
                      {' '}| cost stress {discoveryScore.cost_stress_component != null ? discoveryScore.cost_stress_component.toFixed(2) : '—'}
                    </span>
                    <span>{discoveryScore.note}</span>
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
                    <Badge tone={v.validation_mode === 'SAME_SYMBOL' ? 'marginal' : 'neutral'}>{v.validation_mode}</Badge>
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

// Trial identity (2026-08-02) — an operator correctly pointed out that
// outside hypothesis-bundle mode, "Hypothesis" always shows "—" and
// nothing in the leaderboard reveals which engines/timeframes/indicators/
// context/risk values a given trial actually ran with — the data is
// real and stored (trial.params_json + mission.search_space_json), it
// was just never resolved and displayed. Mirrors backtest/optimizer.py's
// resolve_point() exactly (both branches: hypothesis-bundle mode picks
// one atomic bundle; flat mode reads the 5 independent __*_idx choices),
// purely client-side from data already fetched — no new backend call.
const _INTERNAL_IDX_KEYS = [
  '__timeframes_idx', '__engines_idx', '__indicators_idx', '__context_idx', '__engine_variants_idx',
]
const _HYPOTHESIS_INTERNAL_KEYS = ['__hypothesis_idx']

interface ResolvedTrialConfig {
  timeframes: string[]
  engines: string[]
  indicators: Record<string, unknown>[]
  context_filters: Record<string, unknown>[]
  engine_variants: Record<string, string>
  risk_overrides: Record<string, number>
}

function resolveTrialConfig(mission: MissionRow | null | undefined, trial: MissionTrial): ResolvedTrialConfig | null {
  if (!mission?.search_space_json) return null
  try {
    const space = JSON.parse(mission.search_space_json)
    const rawParams = JSON.parse(trial.params_json) as Record<string, unknown>
    let timeframes: string[]
    let engines: string[]
    let indicators: Record<string, unknown>[]
    let contextFilters: Record<string, unknown>[]
    let engineVariants: Record<string, string>
    let internalKeys: string[]
    if (space.hypothesis_bundle_choices) {
      const bundle = space.hypothesis_bundle_choices[rawParams.__hypothesis_idx as number]
      if (!bundle) return null
      timeframes = bundle.timeframes ?? []
      engines = bundle.engines ?? []
      indicators = bundle.indicators ?? []
      contextFilters = bundle.context_filters ?? []
      engineVariants = bundle.engine_variants ?? {}
      internalKeys = _HYPOTHESIS_INTERNAL_KEYS
    } else {
      timeframes = space.timeframes_choices?.[rawParams.__timeframes_idx as number] ?? []
      engines = space.engine_set_choices?.[rawParams.__engines_idx as number] ?? []
      indicators = space.indicator_set_choices?.[rawParams.__indicators_idx as number] ?? []
      contextFilters = space.context_filter_set_choices?.[(rawParams.__context_idx as number) ?? 0] ?? []
      engineVariants = space.engine_variant_choices?.[(rawParams.__engine_variants_idx as number) ?? 0] ?? {}
      internalKeys = _INTERNAL_IDX_KEYS
    }
    const riskOverrides: Record<string, number> = {}
    for (const [k, v] of Object.entries(rawParams)) {
      if (!internalKeys.includes(k)) riskOverrides[k] = v as number
    }
    return {
      timeframes, engines, indicators, context_filters: contextFilters,
      engine_variants: engineVariants, risk_overrides: riskOverrides,
    }
  } catch {
    return null
  }
}

function TrialConfigSummary({ config }: { config: ResolvedTrialConfig | null }) {
  if (!config) return null
  const rows: [string, string][] = [
    ['Timeframes', config.timeframes.join(', ') || '—'],
    ['Engines', config.engines.join(', ') || '—'],
    ['Indicators', config.indicators.length > 0
      ? config.indicators.map((i) => `${i.name}:${i.mode}`).join(', ') : 'none'],
    ['Context filters', config.context_filters.length > 0
      ? config.context_filters.map((c) => `${c.name}:${c.mode}`).join(', ') : 'none'],
    ['Engine variants', Object.keys(config.engine_variants).length > 0
      ? Object.entries(config.engine_variants).map(([k, v]) => `${k}=${v}`).join(', ') : 'all v1 (default)'],
    ['Risk overrides', Object.keys(config.risk_overrides).length > 0
      ? Object.entries(config.risk_overrides).map(([k, v]) => `${k}=${typeof v === 'number' ? v.toFixed(3) : v}`).join(', ')
      : 'none'],
  ]
  return (
    <div className="flex flex-col gap-1 text-[0.78em]">
      <span className="text-[0.72em] text-muted uppercase tracking-[1px]">Trial configuration (what was actually run)</span>
      {rows.map(([label, value]) => (
        <div key={label} className="flex gap-2">
          <span className="text-muted w-32 shrink-0">{label}</span>
          <span className="font-mono">{value}</span>
        </div>
      ))}
    </div>
  )
}

function latestValidationFor(validations: ValidationRow[], trial: MissionTrial): ValidationRow | null {
  // Defensive: validationsQuery.data should always be an array (backend
  // always returns validations: []), but a caught, silently-swallowed
  // ".filter is not a function" was reported here in production — guard
  // rather than assume, matching this file's own tolerant-parsing
  // convention elsewhere (bundle/preset field fallbacks).
  if (!Array.isArray(validations)) return null
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
function buildTrialFocusHint(
  trial: MissionTrial, hypothesisName: string | null, metrics: Record<string, unknown>,
  consensusClaims?: ConsensusClaim[],
): string {
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
  // Edge Discovery (2026-07-31) — richer grounding: real, computed
  // consensus across ALL trials in this mission, not just this one.
  const significant = (consensusClaims ?? [])
    .filter((c) => c.significance !== 'INSUFFICIENT_DATA')
    .sort((a, b) => (b.confidence_pct ?? 0) - (a.confidence_pct ?? 0))
    .slice(0, 3)
  if (significant.length > 0) {
    parts.push('Mission-wide cross-trial consensus: ' + significant.map((c) => c.claim_text).join(' '))
  }
  return parts.join(' ')
}

function TrialBreakdownPanel({
  trial, hypothesisName, mission, missionId, onClose,
}: {
  trial: MissionTrial
  hypothesisName: string | null
  mission: MissionRow | null | undefined
  missionId: string
  onClose: () => void
}) {
  const { markUnauthenticated } = useAuth()
  const resolvedConfig = resolveTrialConfig(mission, trial)
  const metrics: Record<string, unknown> = trial.metrics_json ? JSON.parse(trial.metrics_json) : {}
  const byRegime = (metrics.by_regime ?? {}) as Record<string, BreakdownBucket>
  const byDirection = (metrics.by_direction ?? {}) as Record<string, BreakdownBucket>
  const bySession = (metrics.by_session ?? {}) as Record<string, BreakdownBucket>
  const hasBreakdown = Object.keys(byRegime).length > 0 || Object.keys(byDirection).length > 0 || Object.keys(bySession).length > 0
  // Prune Forensic Audit (2026-08-04, reports/forensic/21_...) — WHY bars
  // didn't trade. By construction this is exactly what a low-trade-count/
  // pruned trial has (no closed-trade breakdown above, since by_regime/
  // by_direction/by_session are computed over closed trades only) — so it
  // is rendered independently of hasBreakdown, not gated behind it.
  const gateRejections = (metrics.gate_rejections ?? {}) as Record<string, number>
  const contextRejections = (metrics.context_rejections ?? {}) as Record<string, number>
  const indicatorRejections = (metrics.indicator_rejections ?? {}) as Record<string, number>
  const hasGateRejections = Object.keys(gateRejections).length > 0

  // Same query key MetaAnalysisPanel/TopOpportunitiesPanel already use for
  // this mission — React Query dedupes, no duplicate network call.
  const metaQuery = useApiQuery(['mission-meta-analysis', missionId], () => getMetaAnalysis(missionId), POLL_MS, markUnauthenticated)

  const [ai, setAi] = useState<{ triggered: boolean; loading: boolean; error: string | null; data: SuggestHypothesisResponse | null }>({
    triggered: false, loading: false, error: null, data: null,
  })
  const [draftFile, setDraftFile] = useState<string | null>(null)

  const suggest = async () => {
    setAi({ triggered: true, loading: true, error: null, data: null })
    setDraftFile(null)
    try {
      const hint = buildTrialFocusHint(trial, hypothesisName, metrics, metaQuery.data?.cross_trial_consensus)
      const data = await suggestHypothesis(hint)
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
        observation: ai.data.observation ?? '', effect_size: ai.data.effect_size ?? '',
        confidence: ai.data.confidence ?? '', possible_explanation: ai.data.possible_explanation ?? '',
        suggested_experiments: ai.data.suggested_experiments ?? [], priority: ai.data.priority ?? '',
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
        <TrialConfigSummary config={resolvedConfig} />
        {!hasBreakdown && !hasGateRejections ? (
          <Empty>No regime/direction/session/gate-rejection breakdown available for this trial (0 closed trades, or an older mission run predating this data).</Empty>
        ) : (
          <>
            {hasBreakdown && (
              <div className="grid gap-4 grid-cols-[repeat(auto-fit,minmax(240px,1fr))]">
                {Object.keys(byDirection).length > 0 && <PerformanceBreakdownTable title="Direction" buckets={byDirection} />}
                {Object.keys(bySession).length > 0 && <PerformanceBreakdownTable title="Session" buckets={bySession} />}
                {Object.keys(byRegime).length > 0 && <PerformanceBreakdownTable title="Regime" buckets={byRegime} />}
              </div>
            )}
            {hasGateRejections && (
              <div className="flex flex-col gap-2">
                <span className="text-[0.72em] text-muted uppercase tracking-[1px]">
                  Gate rejections — why bars didn't trade
                </span>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(gateRejections)
                    .sort((a, b) => b[1] - a[1])
                    .map(([reason, count]) => (
                      <Badge key={reason} tone="neutral">{`${reason}: ${count}`}</Badge>
                    ))}
                </div>
                {Object.keys(indicatorRejections).length > 0 && (
                  <div className="text-[0.75em] text-muted">
                    Indicator filter: {Object.entries(indicatorRejections).map(([k, v]) => `${k}=${v}`).join(', ')}
                  </div>
                )}
                {Object.keys(contextRejections).length > 0 && (
                  <div className="text-[0.75em] text-muted">
                    Context filter: {Object.entries(contextRejections).map(([k, v]) => `${k}=${v}`).join(', ')}
                  </div>
                )}
              </div>
            )}
          </>
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
                  {ai.data.priority && (
                    <Badge tone={ai.data.priority === 'HIGH' ? 'exec' : ai.data.priority === 'MEDIUM' ? 'marginal' : 'neutral'}>
                      {`Priority: ${ai.data.priority}`}
                    </Badge>
                  )}
                  {ai.data.observation && (
                    <div className="text-muted"><span className="font-bold text-text">Observation:</span> {ai.data.observation}</div>
                  )}
                  {(ai.data.effect_size || ai.data.confidence) && (
                    <div className="text-muted">
                      <span className="font-bold text-text">Effect size:</span> {ai.data.effect_size || '—'}
                      {'  '}<span className="font-bold text-text">Confidence:</span> {ai.data.confidence || '—'}
                    </div>
                  )}
                  <div className="text-muted"><span className="font-bold text-text">Why:</span> {ai.data.why_this_might_be_true}</div>
                  {ai.data.possible_explanation && (
                    <div className="text-muted"><span className="font-bold text-text">Possible explanation:</span> {ai.data.possible_explanation}</div>
                  )}
                  {ai.data.suggested_experiments && ai.data.suggested_experiments.length > 0 && (
                    <div className="text-muted">
                      <span className="font-bold text-text">Suggested experiments:</span>
                      <ul className="list-disc list-inside">
                        {ai.data.suggested_experiments.map((exp, i) => <li key={i}>{exp}</li>)}
                      </ul>
                    </div>
                  )}
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

function MissionDetail({
  missionId, onRequestPrefill,
}: {
  missionId: string
  onRequestPrefill: (req: PrefillRequest) => void
}) {
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
  // Memoized so a `?? []` fallback during a loading tick doesn't itself
  // manufacture a fresh array reference every render — proposeAsDraft/
  // columns/bestColumns below all depend on this.
  const validations = useMemo(() => validationsQuery.data ?? [], [validationsQuery.data])

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

  // proposeAsDraft/columns/bestColumns are hooks (useCallback/useMemo) and
  // per React's Rules of Hooks must run unconditionally on every render —
  // they're declared here, ABOVE the `if (!status) return` early return
  // below, and each guards internally for status still being null (the
  // brief window before the first successful status poll resolves).
  const proposeAsDraft = useCallback(async (t: MissionTrial) => {
    if (!status) return
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
      // Log the full error (with stack) — the Draft cell below only ever
      // shows a short, truncated message, and this catch previously
      // swallowed the exception entirely (nothing reached window.onerror/
      // console.error, so the built-in Console tab showed nothing either).
      console.error(`proposeAsDraft failed for mission ${missionId} trial ${t.trial_number}:`, e)
      setDraftStatus((s) => ({ ...s, [t.trial_number]: `error: ${e instanceof Error ? e.message : String(e)}` }))
    }
  }, [missionId, status, validations])

  // Memoized (2026-07-31): columns was a fresh array literal on every
  // render, and MissionDetail re-renders on every ~POLL_MS tick (status/
  // validations/leaderboard polling) even when nothing relevant changed.
  // DataTable.tsx's own tableColumns useMemo keys off columns' REFERENCE
  // ([columns]), so a fresh array meant a fresh `cell` closure for every
  // ColumnDef every few seconds — flexRender saw that as a different
  // component and remounted the cell, wiping out ValidateAction's local
  // `open`/`validationSymbols` state (reported: the "Validate…" popup
  // closing itself moments after being opened, before the operator could
  // finish picking symbols). Real fix is here, not in DataTable.tsx —
  // every other DataTable consumer already assumes a stable columns
  // reference (its docstring says so); this was the one call site with
  // per-cell local UI state that made the churn user-visible.
  const columns: Column<MissionTrial>[] = useMemo(() => {
    if (!status) return []
    return [
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
            ? <span
                title={draftStatus[t.trial_number]}
                className={`text-[0.75em] whitespace-normal break-words inline-block max-w-[220px] ${draftStatus[t.trial_number].startsWith('error:') ? 'text-red' : 'text-muted'}`}
              >
                {draftStatus[t.trial_number]}
              </span>
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
  }, [status, draftStatus, proposeAsDraft, missionId, symbolsQuery.data])

  // Same remount hazard as `columns` above — this table also renders
  // ValidateAction (via the spread `...columns`), so it needs the same
  // stable-reference treatment.
  const bestColumns: Column<MissionTrial>[] = useMemo(() => [
    ...columns,
    {
      header: 'Validation status',
      render: (t) => <ValidationStatusBadge validation={latestValidationFor(validations, t)} />,
    },
  ], [columns, validations])

  if (!status) return <Empty>Loading…</Empty>

  const symbols = Object.keys(status.progress.by_symbol)
  const isRunning = status.job_status === 'queued' || status.job_status === 'running'
  const bestPerSymbol = bestTrialPerSymbol(allTrials)

  const cancel = async () => {
    setCancelling(true)
    try { await cancelMission(missionId) } finally { setCancelling(false) }
  }

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
          {status.search_space_kind && (
            <div className="flex items-center gap-2 text-[0.78em]">
              <span className="text-muted uppercase tracking-[1px] text-[0.7em]">Search space</span>
              <Badge tone={
                status.search_space_kind === 'SIGNAL_VARIATION' ? 'good'
                  : status.search_space_kind === 'MIXED' ? 'neutral'
                  : status.search_space_kind === 'RISK_ONLY_VARIATION' ? 'marginal'
                  : 'poor'
              }>
                {status.search_space_kind === 'RISK_ONLY_VARIATION' ? 'Risk-only variation — signal fixed across all trials'
                  : status.search_space_kind === 'SIGNAL_VARIATION' ? 'Signal variation'
                  : status.search_space_kind === 'MIXED' ? 'Mixed (signal + risk) variation'
                  : 'No variation — every trial identical'}
              </Badge>
            </div>
          )}
          {status.effective_config_summary && status.effective_config_summary.total_complete_trials > 0 && (
            <div className="flex items-center gap-2 text-[0.78em]">
              <span className="text-muted uppercase tracking-[1px] text-[0.7em]">Effective configurations</span>
              <Badge tone={status.effective_config_summary.duplicate_trials === 0 ? 'good' : 'marginal'}>
                {`${status.effective_config_summary.unique_effective_configurations} unique across ${status.effective_config_summary.total_complete_trials} complete trial(s)${
                  status.effective_config_summary.duplicate_trials > 0
                    ? ` — ${status.effective_config_summary.duplicate_trials} duplicate`
                    : ''
                }`}
              </Badge>
            </div>
          )}
          {status.abandoned_trials > 0 && (
            <div className="text-[0.75em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
              {status.abandoned_trials} trial(s) lost to a process interruption mid-run — compute wasted, not
              corrupted. The mission still reaches its target trial count via a fresh draw on resume.
            </div>
          )}
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
          mission={status.mission}
          missionId={missionId}
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
      <TopOpportunitiesPanel missionId={missionId} onRequestPrefill={onRequestPrefill} />
      <ValidationsPanel missionId={missionId} />
    </div>
  )
}

// Forensic System Audit Phase 1, item B (2026-08-02) — a standalone
// diagnostic panel, not tied to any one mission. Stateless on the
// backend (fresh AST scan every request), so this re-fetches on every
// click rather than polling — matches this endpoint's own design.
function DirectionSymmetryPanel() {
  const [report, setReport] = useState<DirectionSymmetryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runScan = async () => {
    setLoading(true)
    setError(null)
    try {
      setReport(await getDirectionSymmetryAudit())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const byFile = useMemo(() => {
    if (!report) return []
    const groups = new Map<string, SymmetryFinding[]>()
    for (const f of report.findings) {
      if (!groups.has(f.file)) groups.set(f.file, [])
      groups.get(f.file)!.push(f)
    }
    return Array.from(groups.entries())
  }, [report])

  return (
    <Panel title="Direction Symmetry Audit" right="static, advisory-only — never blocks a build">
      <div className="p-4 flex flex-col gap-3">
        <button onClick={runScan} disabled={loading}
          className="self-start px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.78em] hover:bg-accent/10 disabled:opacity-50">
          {loading ? 'Scanning…' : report ? 'Re-run Scan' : 'Run Scan'}
        </button>
        {error && <div className="text-red text-[0.8em]">{error}</div>}
        {report && (
          <>
            <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
              {report.caveat}
            </div>
            <div className="text-[0.75em] text-muted">
              {report.findings.length} finding(s) across {report.files_scanned.length} file(s)
              (engines/, confluence/, risk/) — generated {report.generated_at}.
            </div>
            {report.findings.length === 0 ? (
              <Empty>No findings — every scanned function referencing a directional token also referenced its mirror.</Empty>
            ) : (
              <div className="flex flex-col gap-3">
                {byFile.map(([file, findings]) => (
                  <div key={file} className="flex flex-col gap-1">
                    <span className="font-mono text-[0.78em] text-text">{file}</span>
                    {findings.map((f, i) => (
                      <div key={i} className="flex items-start gap-2 text-[0.78em] pl-3">
                        <Badge tone={f.severity === 'MEDIUM' ? 'marginal' : 'neutral'}>{f.severity}</Badge>
                        <span className="text-muted">L{f.line} {f.function}()</span>
                        <span className="text-text">{f.detail}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  )
}

export function MissionCenter() {
  const [selectedMission, setSelectedMission] = useState<string | null>(null)
  const [prefillRequest, setPrefillRequest] = useState<PrefillRequest | null>(null)
  const builderAnchorRef = useRef<HTMLDivElement>(null)

  const requestPrefill = (req: PrefillRequest) => {
    setPrefillRequest(req)
    builderAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <DirectionSymmetryPanel />
      <div ref={builderAnchorRef}>
        <MissionBuilder
          onCreated={setSelectedMission}
          prefillRequest={prefillRequest}
          onPrefillConsumed={() => setPrefillRequest(null)}
        />
      </div>
      <div className="grid gap-4 grid-cols-[280px_1fr] items-start">
        <Panel title="Missions">
          <MissionsList selected={selectedMission} onSelect={setSelectedMission} />
        </Panel>
        {selectedMission ? (
          <MissionDetail missionId={selectedMission} onRequestPrefill={requestPrefill} />
        ) : (
          <Panel title="Mission Detail"><Empty>Select a mission to see its progress and leaderboard.</Empty></Panel>
        )}
      </div>
    </div>
  )
}
