import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../../lib/auth'
import { useApiQuery } from '../../lib/useApiQuery'
import { Panel, Empty } from '../../components/Panel'
import { DataTable, type Column } from '../../components/DataTable'
import { Badge } from '../../components/Badge'
import { KpiCard } from '../../components/KpiCard'
import { getResearchSymbols } from '../research-backtests/api'
import { SUPPORTED_TIMEFRAMES, ENGINE_KEYS } from '../backtesting-lab/BacktestingLab'
import {
  CELL_STATUSES, RISK_PRESET_NAMES, MIN_COMPARED_CELLS, MAX_COMPARED_CELLS,
  generateMatrixCells, listMatrixFamilies, getMatrixFamily, getMatrixFamilySummary,
  listMatrixCells, getMatrixCellEvidence, compareMatrixCells, runMatrixBatch, listMatrixRuns, getMatrixRun,
  type MatrixFamily, type MatrixCell, type MatrixRun, type CellStatus, type BundleSpec,
  type GroupStatsBucket, type ComparedCell, type CompareProvenance,
} from './matrixApi'

const POLL_MS = 5_000
const JOB_POLL_MS = 2_000

// Hypothesis Discovery Engine, Phase 2B — Matrix Dashboard. Read-only
// projection of the Phase 2A Evidence Read Model plus the two write
// actions (generate a family / run a batch against it) that already
// existed as backend endpoints since Phase 1. This tab computes NOTHING —
// every status, p-value, verdict, and rejection reason it shows is a
// verbatim re-presentation of a decision already made by backtest/
// matrix_orchestrator.py, exactly like every panel in this module.

function statusTone(status: string): 'good' | 'poor' | 'marginal' | 'neutral' {
  if (status === 'VALIDATED') return 'good'
  if (status === 'REJECTED' || status === 'FAILED') return 'poor'
  if (status === 'CANDIDATE' || status === 'SCREENED') return 'marginal'
  return 'neutral'
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

function symbolCount(symbolsJson: string | null): number {
  if (!symbolsJson) return 0
  try {
    const parsed: unknown = JSON.parse(symbolsJson)
    return Array.isArray(parsed) ? parsed.length : 0
  } catch {
    return 0
  }
}

function bundleName(bundleJson: string): string {
  try {
    const parsed = JSON.parse(bundleJson) as { name?: string }
    return parsed.name ?? '?'
  } catch {
    return '?'
  }
}

// ── Generate: mint a new matrix family ───────────────────────────────────

interface BundleRowState {
  name: string
  timeframes: string[]
  engines: string[]
}

function blankBundle(): BundleRowState {
  return { name: '', timeframes: ['H1'], engines: [] }
}

function BundleRow({ bundle, onChange, onRemove, removable }: {
  bundle: BundleRowState
  onChange: (b: BundleRowState) => void
  onRemove: () => void
  removable: boolean
}) {
  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value]

  return (
    <div className="flex flex-col gap-2 border border-border rounded px-3 py-2.5">
      <div className="flex items-center gap-2">
        <input
          value={bundle.name}
          onChange={(e) => onChange({ ...bundle, name: e.target.value })}
          placeholder="Bundle name (e.g. SMC only)"
          className="flex-1 px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text min-h-11"
        />
        {removable && (
          <button
            onClick={onRemove}
            className="px-2 py-1.5 text-[0.75em] rounded border border-red/40 text-red hover:bg-red/10"
          >
            Remove
          </button>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Timeframes</span>
        <div className="flex items-center gap-3 flex-wrap">
          {SUPPORTED_TIMEFRAMES.map((tf) => (
            <label key={tf} className="flex items-center gap-1.5 text-[0.78em] min-h-11">
              <input type="checkbox" checked={bundle.timeframes.includes(tf)} onChange={() => onChange({ ...bundle, timeframes: toggle(bundle.timeframes, tf) })} />
              {tf}
            </label>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-[0.68em] text-muted uppercase tracking-[0.8px]">Engines</span>
        <div className="flex items-center gap-3 flex-wrap">
          {ENGINE_KEYS.map((eng) => (
            <label key={eng} className="flex items-center gap-1.5 text-[0.78em] min-h-11">
              <input type="checkbox" checked={bundle.engines.includes(eng)} onChange={() => onChange({ ...bundle, engines: toggle(bundle.engines, eng) })} />
              {eng}
            </label>
          ))}
        </div>
      </div>
    </div>
  )
}

function GenerateForm({ onGenerated }: { onGenerated: (familyId: string) => void }) {
  const { markUnauthenticated } = useAuth()
  const symbolsQuery = useApiQuery(['research-symbols'], getResearchSymbols, POLL_MS, markUnauthenticated)
  const totalConfiguredSymbols = symbolsQuery.data
    ? Object.values(symbolsQuery.data.asset_classes).reduce((n, entries) => n + entries.length, 0)
    : null

  const [symbolsText, setSymbolsText] = useState('')
  const [bundles, setBundles] = useState<BundleRowState[]>([blankBundle()])
  const [riskPresets, setRiskPresets] = useState<string[]>([...RISK_PRESET_NAMES])
  const [quorum, setQuorum] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ family_id: string; planned_n: number } | null>(null)

  const updateBundle = (i: number, b: BundleRowState) => setBundles((prev) => prev.map((row, idx) => (idx === i ? b : row)))
  const removeBundle = (i: number) => setBundles((prev) => prev.filter((_, idx) => idx !== i))

  const submit = async () => {
    setError(null)
    setResult(null)
    const cleanBundles: BundleSpec[] = bundles
      .filter((b) => b.name.trim())
      .map((b) => ({ name: b.name.trim(), timeframes: b.timeframes, engines: b.engines, indicators: [], context_filters: [] }))
    if (cleanBundles.length === 0) {
      setError('At least one bundle needs a name.')
      return
    }
    const symbols = symbolsText.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean)
    setSubmitting(true)
    try {
      const res = await generateMatrixCells({
        symbols: symbols.length > 0 ? symbols : undefined,
        bundles: cleanBundles,
        risk_presets: riskPresets.length > 0 ? (riskPresets as typeof RISK_PRESET_NAMES[number][]) : undefined,
        confluence_overrides: quorum.trim() ? { min_engines_agreeing: Number(quorum) } : undefined,
      })
      setResult({ family_id: res.family_id, planned_n: res.planned_n })
      onGenerated(res.family_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Panel title="Generate Matrix Family" right="ephemeral research — never writes config.yaml or registry.json">
      <div className="p-4 flex flex-col gap-4">
        <div className="rounded border border-amber/40 bg-amber/10 px-3 py-2.5 text-[0.78em] text-amber leading-relaxed">
          Every combination of symbol × bundle × risk preset becomes one fingerprinted matrix cell in a new,
          fixed-size research family. <code>planned_n</code> is set once, at generation time, and never changes —
          it is the sole Bonferroni-correction denominator for every batch run against this family.
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">
            Symbols (comma-separated — blank = every configured symbol{totalConfiguredSymbols != null ? `, ${totalConfiguredSymbols} total` : ''})
          </span>
          <input
            value={symbolsText}
            onChange={(e) => setSymbolsText(e.target.value)}
            placeholder="EURUSD, XAUUSD, BTCUSD"
            className="px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text min-h-11"
          />
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Bundles</span>
          {bundles.map((b, i) => (
            <BundleRow key={i} bundle={b} onChange={(next) => updateBundle(i, next)} onRemove={() => removeBundle(i)} removable={bundles.length > 1} />
          ))}
          <button
            onClick={() => setBundles((prev) => [...prev, blankBundle()])}
            className="self-start px-2.5 py-1.5 text-[0.75em] rounded border border-border text-text hover:bg-surface min-h-11"
          >
            + Add Bundle
          </button>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Risk Presets</span>
          <div className="flex items-center gap-4">
            {RISK_PRESET_NAMES.map((p) => (
              <label key={p} className="flex items-center gap-1.5 text-[0.8em] min-h-11">
                <input
                  type="checkbox"
                  checked={riskPresets.includes(p)}
                  onChange={() => setRiskPresets((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]))}
                />
                {p}
              </label>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1 max-w-xs">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">
            Confluence quorum override (optional — production default applies)
          </span>
          <input
            value={quorum}
            onChange={(e) => setQuorum(e.target.value.replace(/[^0-9]/g, ''))}
            placeholder="min_engines_agreeing"
            className="px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text min-h-11"
          />
        </div>

        {error && <div className="text-[0.8em] text-red">{error}</div>}
        {result && (
          <div className="text-[0.8em] text-green">
            Family <code>{result.family_id}</code> created — planned_n = {result.planned_n}.
          </div>
        )}

        <button
          onClick={submit}
          disabled={submitting}
          className="self-start px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.8em] font-bold hover:bg-accent/10 disabled:opacity-50 min-h-11"
        >
          {submitting ? 'Generating…' : 'Generate Cells'}
        </button>
      </div>
    </Panel>
  )
}

// ── Families list ─────────────────────────────────────────────────────

function FamiliesList({ selectedFamilyId, onSelect, refreshKey }: {
  selectedFamilyId: string | null
  onSelect: (id: string) => void
  refreshKey: number
}) {
  const { markUnauthenticated } = useAuth()
  const { data, refetch } = useApiQuery(['matrix-families'], () => listMatrixFamilies(50), POLL_MS, markUnauthenticated)

  useEffect(() => {
    refetch()
  }, [refreshKey, refetch])

  const families = data?.families ?? []

  const columns: Column<MatrixFamily>[] = [
    {
      header: 'Family',
      render: (f) => (
        <button
          onClick={() => onSelect(f.family_id)}
          className={`font-mono text-[0.85em] font-bold ${f.family_id === selectedFamilyId ? 'text-accent' : 'text-text hover:text-accent'}`}
        >
          {f.family_id}
        </button>
      ),
    },
    { header: 'Planned N', align: 'right', accessorFn: (f) => f.planned_n, render: (f) => f.planned_n },
    { header: 'Alpha', align: 'right', accessorFn: (f) => f.family_alpha, render: (f) => f.family_alpha },
    { header: 'Symbols', align: 'right', accessorFn: (f) => symbolCount(f.symbols_json), render: (f) => symbolCount(f.symbols_json) },
    { header: 'Created', accessorFn: (f) => f.created_at, render: (f) => fmtDate(f.created_at) },
  ]

  return (
    <Panel title="Matrix Families" right={`${families.length} shown`}>
      {families.length > 0 ? (
        <DataTable columns={columns} rows={families} rowKey={(f) => f.family_id} />
      ) : (
        <Empty>No matrix families generated yet.</Empty>
      )}
    </Panel>
  )
}

// ── Family detail: fixed evidence summary + run-batch trigger ──────────

interface BreakdownRow {
  key: string
  bucket: GroupStatsBucket
}

function BreakdownTable({ title, buckets }: { title: string; buckets: Record<string, GroupStatsBucket> }) {
  const rows: BreakdownRow[] = Object.entries(buckets).map(([key, bucket]) => ({ key, bucket }))
  if (rows.length === 0) return null
  const columns: Column<BreakdownRow>[] = [
    { header: title, render: (r) => <span className="font-bold text-accent">{r.key}</span> },
    { header: 'Total', align: 'right', accessorFn: (r) => r.bucket.total, render: (r) => r.bucket.total },
    ...CELL_STATUSES.map((status) => ({
      header: status,
      align: 'right' as const,
      accessorFn: (r: BreakdownRow) => r.bucket[status] ?? 0,
      render: (r: BreakdownRow) => r.bucket[status] ?? 0,
    })),
  ]
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} />
}

function RunBatchControls({ familyId, onSubmitted }: { familyId: string; onSubmitted: () => void }) {
  const [batchSize, setBatchSize] = useState('20')
  const [stageBBatchSize, setStageBBatchSize] = useState('5')
  const [runId, setRunId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }
  useEffect(() => stopPolling, [])

  const submit = async () => {
    setError(null)
    setSubmitting(true)
    try {
      const res = await runMatrixBatch({
        family_id: familyId,
        batch_size: Number(batchSize) || 20,
        stage_b_batch_size: Number(stageBBatchSize) || 5,
      })
      setRunId(res.run_id)
      setJobStatus(res.status)
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const status = await getMatrixRun(res.run_id)
          setJobStatus(status.job?.status ?? null)
          if (status.job && !['queued', 'running'].includes(status.job.status)) {
            stopPolling()
            onSubmitted()
          }
        } catch (e) {
          stopPolling()
          setError(e instanceof Error ? e.message : String(e))
        }
      }, JOB_POLL_MS)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const running = jobStatus === 'queued' || jobStatus === 'running'

  return (
    <div className="flex flex-col gap-2 px-4 py-3 border-t border-border">
      <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Run Batch (Stage A screen + Stage B validation)</span>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={batchSize}
          onChange={(e) => setBatchSize(e.target.value.replace(/[^0-9]/g, ''))}
          placeholder="batch_size"
          className="w-28 px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text min-h-11"
        />
        <input
          value={stageBBatchSize}
          onChange={(e) => setStageBBatchSize(e.target.value.replace(/[^0-9]/g, ''))}
          placeholder="stage_b_batch_size"
          className="w-36 px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text min-h-11"
        />
        <button
          onClick={submit}
          disabled={submitting || running}
          className="px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.8em] font-bold hover:bg-accent/10 disabled:opacity-50 min-h-11"
        >
          {running ? 'Running…' : 'Run Batch'}
        </button>
        {runId && <span className="text-[0.75em] text-muted">run {runId} — {jobStatus ?? '…'}</span>}
      </div>
      {error && <div className="text-[0.78em] text-red">{error}</div>}
    </div>
  )
}

function FamilyDetail({ familyId, onDataChanged }: { familyId: string; onDataChanged: () => void }) {
  const { markUnauthenticated } = useAuth()
  const { data, refetch } = useApiQuery(['matrix-family-summary', familyId], () => getMatrixFamilySummary(familyId), POLL_MS, markUnauthenticated)

  if (!data) return <Panel title={`Family ${familyId}`}><Empty>Loading…</Empty></Panel>

  const { family, by_symbol, by_bundle, by_risk_preset } = data
  const breakdown = family.status_breakdown

  return (
    <Panel title={`Family ${familyId}`} right="fixed planned_n — never recomputed">
      <div className="p-4 flex flex-col gap-4">
        <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(160px,1fr))]">
          <KpiCard label="Planned N" value={family.planned_n} />
          <KpiCard label="Family α" value={family.family_alpha} />
          <KpiCard label="Bonferroni α" value={family.bonferroni_alpha != null ? family.bonferroni_alpha.toExponential(3) : '—'} />
          <KpiCard label="Total Requeues" value={family.total_requeues} color={family.total_requeues > 0 ? 'amber' : 'default'} />
          <KpiCard label="Cells Generated" value={family.cells_generated} />
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Status Breakdown</span>
          <div className="flex items-center gap-2 flex-wrap">
            {CELL_STATUSES.map((status) => (
              <Badge key={status} tone={statusTone(status)}>{`${status}: ${breakdown[status] ?? 0}`}</Badge>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">By Symbol</span>
          <BreakdownTable title="Symbol" buckets={by_symbol} />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">By Bundle</span>
          <BreakdownTable title="Bundle" buckets={by_bundle} />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">By Risk Preset</span>
          <BreakdownTable title="Risk Preset" buckets={by_risk_preset} />
        </div>
      </div>
      <RunBatchControls
        familyId={familyId}
        onSubmitted={() => {
          void refetch()
          onDataChanged()
        }}
      />
    </Panel>
  )
}

// ── Runs panel ───────────────────────────────────────────────────────

function RunsPanel({ familyId, refreshKey }: { familyId: string; refreshKey: number }) {
  const { markUnauthenticated } = useAuth()
  const { data, refetch } = useApiQuery(['matrix-runs', familyId], () => listMatrixRuns(familyId, 50), POLL_MS, markUnauthenticated)

  useEffect(() => {
    refetch()
  }, [refreshKey, refetch])

  const runs = data?.runs ?? []
  const columns: Column<MatrixRun>[] = [
    { header: 'Run', render: (r) => <span className="font-mono text-[0.82em]">{r.run_id}</span> },
    { header: 'Status', render: (r) => r.status },
    { header: 'Claimed', align: 'right', accessorFn: (r) => r.cells_claimed, render: (r) => r.cells_claimed },
    { header: 'Screened', align: 'right', accessorFn: (r) => r.cells_screened, render: (r) => r.cells_screened },
    { header: 'Promoted', align: 'right', accessorFn: (r) => r.cells_promoted, render: (r) => r.cells_promoted },
    { header: 'Validated', align: 'right', accessorFn: (r) => r.cells_validated, render: (r) => r.cells_validated },
    { header: 'Error', render: (r) => (r.error ? <span className="text-red">{r.error}</span> : '—') },
    { header: 'Created', accessorFn: (r) => r.created_at, render: (r) => fmtDate(r.created_at) },
  ]

  return (
    <Panel title="Recent Batch Runs" right={`${runs.length} shown`}>
      {runs.length > 0 ? <DataTable columns={columns} rows={runs} rowKey={(r) => r.run_id} /> : <Empty>No batch runs recorded for this family yet.</Empty>}
    </Panel>
  )
}

// ── Cells table + evidence drill-down ───────────────────────────────

function CellsTable({ familyId, onSelectCell, refreshKey, selectedIds, onToggleSelect, onCompare }: {
  familyId: string
  onSelectCell: (id: string) => void
  refreshKey: number
  selectedIds: Set<string>
  onToggleSelect: (id: string) => void
  onCompare: () => void
}) {
  const { markUnauthenticated } = useAuth()
  const [statusFilter, setStatusFilter] = useState<CellStatus | ''>('')
  const { data, refetch } = useApiQuery(
    ['matrix-cells', familyId, statusFilter],
    () => listMatrixCells({ familyId, status: statusFilter || undefined, limit: 500 }),
    POLL_MS,
    markUnauthenticated,
  )

  useEffect(() => {
    refetch()
  }, [refreshKey, refetch])

  const cells = data?.cells ?? []
  const atMax = selectedIds.size >= MAX_COMPARED_CELLS
  const columns: Column<MatrixCell>[] = [
    {
      header: '',
      render: (c) => (
        <input
          type="checkbox"
          checked={selectedIds.has(c.cell_id)}
          disabled={!selectedIds.has(c.cell_id) && atMax}
          onChange={() => onToggleSelect(c.cell_id)}
          title={!selectedIds.has(c.cell_id) && atMax ? `At most ${MAX_COMPARED_CELLS} cells can be compared at once` : 'Select for Evidence Comparison'}
        />
      ),
    },
    { header: 'Symbol', render: (c) => <span className="font-bold text-accent">{c.symbol}</span> },
    { header: 'Bundle', render: (c) => bundleName(c.bundle_json) },
    { header: 'Risk Preset', render: (c) => c.risk_preset },
    { header: 'Status', render: (c) => <Badge tone={statusTone(c.status)}>{c.status}</Badge> },
    { header: 'Stage A p', align: 'right', render: (c) => (c.stage_a_p_value != null ? c.stage_a_p_value.toExponential(2) : '—') },
    { header: 'Stage B Verdict', render: (c) => c.stage_b_verdict ?? '—' },
    { header: 'Rejection Reason', render: (c) => c.rejection_reason ?? '—' },
    { header: 'Requeues', align: 'right', accessorFn: (c) => c.requeue_count, render: (c) => c.requeue_count },
    { header: 'Updated', accessorFn: (c) => c.updated_at, render: (c) => fmtDate(c.updated_at) },
    {
      header: '',
      render: (c) => (
        <button onClick={() => onSelectCell(c.cell_id)} className="px-2 py-1.5 text-[0.75em] rounded border border-border text-text hover:bg-surface min-h-11">
          Evidence
        </button>
      ),
    },
  ]

  return (
    <Panel
      title="Cells"
      right={
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as CellStatus | '')}
            className="bg-bg border border-border rounded px-2 py-1 text-[0.75em] text-text"
          >
            <option value="">All statuses</option>
            {CELL_STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button
            onClick={onCompare}
            disabled={selectedIds.size < MIN_COMPARED_CELLS}
            className="px-2.5 py-1.5 text-[0.75em] rounded border border-accent/40 text-accent font-bold hover:bg-accent/10 disabled:opacity-40 min-h-11"
            title={`Select ${MIN_COMPARED_CELLS}-${MAX_COMPARED_CELLS} cells to compare their evidence side by side`}
          >
            Compare Selected ({selectedIds.size})
          </button>
        </div>
      }
    >
      {cells.length > 0 ? <DataTable columns={columns} rows={cells} rowKey={(c) => c.cell_id} /> : <Empty>No cells match this filter.</Empty>}
    </Panel>
  )
}

function EvidenceField({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[0.8em]">
      <span className="text-muted">{label}</span>
      <span className="text-text text-right break-words">{value}</span>
    </div>
  )
}

function CellEvidencePanel({ cellId, onClose }: { cellId: string; onClose: () => void }) {
  const { markUnauthenticated } = useAuth()
  const { data } = useApiQuery(['matrix-cell-evidence', cellId], () => getMatrixCellEvidence(cellId), POLL_MS, markUnauthenticated)

  return (
    <Panel
      title="Cell Evidence"
      right={
        <button onClick={onClose} className="text-muted hover:text-text px-2 py-1.5">✕ close</button>
      }
    >
      {!data ? (
        <Empty>Loading…</Empty>
      ) : (
        <div className="p-4 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <EvidenceField label="Cell ID" value={data.cell_id} />
            <EvidenceField label="Fingerprint" value={data.fingerprint ?? '—'} />
            <EvidenceField label="Family" value={data.family_id ?? '—'} />
            <EvidenceField label="Symbol" value={data.symbol ?? '—'} />
            <EvidenceField label="Bundle" value={data.bundle ? JSON.stringify(data.bundle) : '—'} />
            <EvidenceField label="Risk Preset" value={data.risk_preset ?? '—'} />
            <EvidenceField label="Confluence Overrides" value={data.confluence_overrides ? JSON.stringify(data.confluence_overrides) : '—'} />
            <EvidenceField label="Engine Variants" value={data.engine_variants ? JSON.stringify(data.engine_variants) : '—'} />
            <EvidenceField label="Data Provider" value={data.data_provider ?? '—'} />
            <EvidenceField label="Code Commit" value={data.research_code_commit ?? '—'} />
            <EvidenceField label="Status" value={data.status ?? '—'} />
            <EvidenceField label="Rejection Reason" value={data.rejection_reason ?? '—'} />
            <EvidenceField label="Requeue Count" value={String(data.requeue_count)} />
            <EvidenceField label="Created" value={fmtDate(data.created_at)} />
            <EvidenceField label="Updated" value={fmtDate(data.updated_at)} />
          </div>

          <div className="flex flex-col gap-1.5 pt-3 border-t border-border">
            <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Stage A</span>
            <EvidenceField label="Mission ID" value={data.stage_a.mission_id ?? '—'} />
            <EvidenceField label="Trial Number" value={data.stage_a.trial_number != null ? String(data.stage_a.trial_number) : '—'} />
            <EvidenceField label="p-value" value={data.stage_a.p_value != null ? data.stage_a.p_value.toExponential(4) : '—'} />
            <EvidenceField label="Lead ID" value={data.stage_a.lead_id ?? '—'} />
            <EvidenceField label="Metrics" value={data.stage_a.metrics ? JSON.stringify(data.stage_a.metrics) : '—'} />
            <EvidenceField label="Trial Detail" value={data.stage_a.trial_detail ? JSON.stringify(data.stage_a.trial_detail) : 'not yet reached'} />
          </div>

          <div className="flex flex-col gap-1.5 pt-3 border-t border-border">
            <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Stage B</span>
            <EvidenceField label="Validation ID" value={data.stage_b.validation_id ?? '—'} />
            <EvidenceField label="Verdict" value={data.stage_b.verdict ?? '—'} />
            <EvidenceField label="Validation Detail" value={data.stage_b.validation_detail ? JSON.stringify(data.stage_b.validation_detail) : 'not yet reached'} />
            <EvidenceField label="Walk-Forward" value={data.stage_b.validation_result?.walk_forward ? JSON.stringify(data.stage_b.validation_result.walk_forward) : '—'} />
            <EvidenceField label="Monte Carlo" value={data.stage_b.validation_result?.monte_carlo ? JSON.stringify(data.stage_b.validation_result.monte_carlo) : '—'} />
            <EvidenceField label="Regime Robustness" value={data.stage_b.validation_result?.regime_robustness ? JSON.stringify(data.stage_b.validation_result.regime_robustness) : '—'} />
            <EvidenceField label="Cost Stress" value={data.stage_b.validation_result?.cost_stress ? JSON.stringify(data.stage_b.validation_result.cost_stress) : '—'} />
          </div>
        </div>
      )}
    </Panel>
  )
}

// ── Evidence Comparison (Phase 2C) — NOT a leaderboard ─────────────────
//
// Operator's own non-negotiable acceptance criterion, verbatim: "No
// ranking, score, sort order, color, badge, or visual emphasis may be
// interpreted as a new statistical verdict. Every authoritative verdict
// must originate from the existing evidence gate." This panel therefore
// never sorts cells by any metric, never assigns a rank number, and never
// colors a cell "best" — the only colors used below are the provenance
// warnings (cross-family / cross-commit), which describe IDENTITY, not
// performance. Every field rendered is a verbatim re-presentation of a
// value already decided by backtest/matrix_orchestrator.py.

const DISCLAIMER_TEXT =
  'No ranking, score, sort order, color, badge, or visual emphasis on this page is a new statistical verdict. ' +
  'Every verdict shown below (Stage A / Stage B) originates from the existing evidence gate ' +
  '(backtest/matrix_orchestrator.py) — this panel only re-presents it, side by side, for identity comparison.'

function useFamilyDetails(familyIds: string[]): Record<string, MatrixFamily> {
  const [details, setDetails] = useState<Record<string, MatrixFamily>>({})
  const key = familyIds.slice().sort().join(',')
  useEffect(() => {
    let cancelled = false
    if (familyIds.length === 0) return
    void Promise.all(familyIds.map((id) => getMatrixFamily(id).then((f) => [id, f] as const))).then((pairs) => {
      if (cancelled) return
      setDetails(Object.fromEntries(pairs))
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return details
}

/** Pure re-labeling of the cell's own `status` field into "what Stage A
 * concluded" — no new inference over trades/p-values, just naming which
 * stage a status belongs to (this schema has one `status` field spanning
 * both stages; Stage B only ever advances CANDIDATE -> VALIDATING ->
 * VALIDATED/REJECTED, Phase 3A's atomic Stage B claim). */
function stageAOutcome(cell: ComparedCell): string {
  if (!cell.found) return '—'
  const c = cell as ComparedCell & { found: true }
  if (c.stage_a.p_value != null) return 'SCREENED'
  if (c.status === 'INSUFFICIENT_DATA' || c.status === 'FAILED') return c.status
  return c.status === 'QUEUED' || c.status === 'RUNNING' ? 'not yet run' : c.status ?? '—'
}

function stageBOutcome(cell: ComparedCell): string {
  if (!cell.found) return '—'
  const c = cell as ComparedCell & { found: true }
  if (c.stage_b.verdict) return c.stage_b.verdict
  return c.stage_b.validation_id ? 'in progress' : 'not yet run'
}

function jsonOr(value: unknown, fallback = '—'): string {
  if (value == null) return fallback
  try {
    return JSON.stringify(value)
  } catch {
    return fallback
  }
}

function ProvenanceBanner({ provenance, familyDetails }: { provenance: CompareProvenance; familyDetails: Record<string, MatrixFamily> }) {
  if (provenance.same_hypothesis_lineage && provenance.lineage_key) {
    return (
      <div className="rounded border border-accent/40 bg-accent/10 px-3 py-2.5 text-[0.8em] text-text leading-relaxed">
        <strong className="text-accent">Comparison Type 3 — Same-Hypothesis Lineage.</strong> Every compared cell is the
        exact same symbol/bundle/risk-preset (<code>{provenance.lineage_key.symbol}</code> / <code>{provenance.lineage_key.bundle}</code> /{' '}
        <code>{provenance.lineage_key.risk_preset}</code>) run under different research code commits
        (<code>{provenance.commits.join(', ')}</code>). Use this to check whether an apparent improvement came from a
        code change rather than the hypothesis itself.
      </div>
    )
  }
  if (!provenance.same_family) {
    return (
      <div className="rounded border border-red/40 bg-red/10 px-3 py-2.5 text-[0.8em] text-red leading-relaxed flex flex-col gap-1.5">
        <div>
          <strong>Comparison Type 2 — Cross-Family. DESCRIPTIVE ONLY.</strong> These cells come from{' '}
          {provenance.family_ids.length} different Matrix Families, each with its own fixed <code>planned_n</code> and
          Bonferroni-corrected α. They must never be read as one unified ranking — a lower p-value in a
          smaller/looser family is not "better evidence" than a higher p-value in a larger/stricter one.
        </div>
        <div className="flex flex-col gap-0.5 text-text/90">
          {provenance.family_ids.map((fid) => {
            const fam = familyDetails[fid]
            return (
              <span key={fid} className="font-mono">
                {fid}: planned_n={fam ? fam.planned_n : '…'}, family_α={fam ? fam.family_alpha : '…'}
              </span>
            )
          })}
        </div>
      </div>
    )
  }
  return (
    <div className="rounded border border-green/40 bg-green/10 px-3 py-2.5 text-[0.8em] text-green leading-relaxed">
      <strong>Comparison Type 1 — Within-Family (primary).</strong> Every compared cell shares Matrix Family{' '}
      <code>{provenance.family_ids[0] ?? '—'}</code> — same fixed <code>planned_n</code> and Bonferroni α, the most
      statistically meaningful axis of comparison this engine offers.
      {!provenance.same_commit && ' Cells differ by research code commit — see per-cell "Code commit" below.'}
    </div>
  )
}

interface ComparisonFieldRow {
  label: string
  render: (cell: ComparedCell) => string
  note?: string
}

function EvidenceComparisonPanel({ cellIds, onClose }: { cellIds: string[]; onClose: () => void }) {
  const { markUnauthenticated } = useAuth()
  const { data } = useApiQuery(['matrix-compare', cellIds.join(',')], () => compareMatrixCells(cellIds), POLL_MS, markUnauthenticated)
  const familyIds = data?.provenance?.family_ids ?? []
  const familyDetails = useFamilyDetails(familyIds)

  const rows: ComparisonFieldRow[] = [
    { label: 'Cell', render: (c) => c.cell_id },
    { label: 'Symbol', render: (c) => (c.found ? c.symbol ?? '—' : '—') },
    { label: 'Bundle', render: (c) => (c.found ? jsonOr(c.bundle) : '—') },
    { label: 'Risk preset', render: (c) => (c.found ? c.risk_preset ?? '—' : '—') },
    { label: 'Code commit', render: (c) => (c.found ? c.research_code_commit ?? 'unknown' : '—') },
    { label: 'Data / provider', render: (c) => (c.found ? c.data_provider ?? 'unspecified' : '—') },
    {
      label: 'Matrix family',
      render: (c) => {
        if (!c.found || !c.family_id) return '—'
        const fam = familyDetails[c.family_id]
        return fam ? `${c.family_id} (planned_n=${fam.planned_n})` : c.family_id
      },
    },
    { label: 'Matrix-family alpha', render: (c) => (c.found && c.family_id ? String(familyDetails[c.family_id]?.family_alpha ?? '…') : '—') },
    { label: 'Stage A verdict', render: (c) => stageAOutcome(c) },
    { label: 'Stage A p-value', render: (c) => (c.found && c.stage_a.p_value != null ? c.stage_a.p_value.toExponential(4) : '—') },
    { label: 'Stage B verdict', render: (c) => stageBOutcome(c) },
    {
      label: 'OOS result / Walk-forward',
      render: (c) => (c.found ? jsonOr(c.stage_b.validation_result?.walk_forward) : '—'),
      note: 'Matrix cells never run a separate Stage A OOS holdout (oos_holdout_fraction is always None) — this Stage B walk-forward result is the only genuine out-of-sample evidence for a cell.',
    },
    { label: 'Monte Carlo', render: (c) => (c.found ? jsonOr(c.stage_b.validation_result?.monte_carlo) : '—') },
    { label: 'Regime robustness', render: (c) => (c.found ? jsonOr(c.stage_b.validation_result?.regime_robustness) : '—') },
    { label: 'Cost stress', render: (c) => (c.found ? jsonOr(c.stage_b.validation_result?.cost_stress) : '—') },
  ]

  return (
    <Panel
      title={`Evidence Comparison (${cellIds.length} cells)`}
      right={<button onClick={onClose} className="text-muted hover:text-text px-2 py-1.5">✕ close</button>}
    >
      <div className="p-4 flex flex-col gap-4">
        <div className="rounded border border-amber/40 bg-amber/10 px-3 py-2.5 text-[0.8em] text-amber leading-relaxed font-bold">
          {DISCLAIMER_TEXT}
        </div>

        {!data ? (
          <Empty>Loading…</Empty>
        ) : (
          <>
            {data.provenance && <ProvenanceBanner provenance={data.provenance} familyDetails={familyDetails} />}

            <div className="overflow-x-auto">
              <table className="w-full text-[0.78em] border-collapse">
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.label} className="border-b border-border align-top">
                      <td className="py-2 pr-3 text-muted whitespace-nowrap font-bold">{row.label}</td>
                      {data.cells.map((c) => (
                        <td key={c.cell_id} className="py-2 pr-4 text-text font-mono break-all max-w-[260px]">
                          {row.render(c)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {rows.filter((r) => r.note).map((r) => (
              <div key={r.label} className="text-[0.72em] text-muted leading-relaxed">
                <strong className="text-text">{r.label}:</strong> {r.note}
              </div>
            ))}

            <div className="rounded border border-border px-3 py-2.5 text-[0.75em] text-muted leading-relaxed">
              <strong className="text-text">Not shown — no per-cell record exists:</strong> "Direction symmetry" is a
              static, codebase-wide scanner (not a per-cell/per-trade metric) — see Mission Center's own Direction
              Symmetry Audit panel for that report. "Provider robustness" has no record tied to any specific matrix
              cell's own backtest run — the Provider Benchmark work is a separate, symbol/timeframe-scoped data-quality
              lab. Neither is fabricated here.
            </div>

            {data.cells.some((c) => !c.found) && (
              <div className="text-[0.78em] text-red">
                Unknown cell ID(s): {data.cells.filter((c) => !c.found).map((c) => c.cell_id).join(', ')}
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  )
}

// ── Top level ────────────────────────────────────────────────────────

export function MatrixEngine() {
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null)
  const [selectedCellId, setSelectedCellId] = useState<string | null>(null)
  const [selectedForCompare, setSelectedForCompare] = useState<Set<string>>(new Set())
  const [comparing, setComparing] = useState<string[] | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const bump = () => setRefreshKey((k) => k + 1)

  const toggleSelect = (id: string) =>
    setSelectedForCompare((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else if (next.size < MAX_COMPARED_CELLS) next.add(id)
      return next
    })

  return (
    <div className="flex flex-col gap-4">
      <GenerateForm
        onGenerated={(id) => {
          setSelectedFamilyId(id)
          bump()
        }}
      />
      <FamiliesList selectedFamilyId={selectedFamilyId} onSelect={setSelectedFamilyId} refreshKey={refreshKey} />
      {selectedFamilyId && (
        <>
          <FamilyDetail familyId={selectedFamilyId} onDataChanged={bump} />
          <RunsPanel familyId={selectedFamilyId} refreshKey={refreshKey} />
          <CellsTable
            familyId={selectedFamilyId}
            onSelectCell={setSelectedCellId}
            refreshKey={refreshKey}
            selectedIds={selectedForCompare}
            onToggleSelect={toggleSelect}
            onCompare={() => setComparing(Array.from(selectedForCompare))}
          />
        </>
      )}
      {selectedCellId && <CellEvidencePanel cellId={selectedCellId} onClose={() => setSelectedCellId(null)} />}
      {comparing && <EvidenceComparisonPanel cellIds={comparing} onClose={() => setComparing(null)} />}
    </div>
  )
}
