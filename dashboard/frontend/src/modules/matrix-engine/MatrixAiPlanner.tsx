import { useEffect, useState } from 'react'
import { useAuth } from '../../lib/auth'
import { useApiQuery } from '../../lib/useApiQuery'
import { ApiError } from '../../lib/api'
import { Panel, Empty } from '../../components/Panel'
import { DataTable, type Column } from '../../components/DataTable'
import { Badge } from '../../components/Badge'
import {
  RECOMMENDATION_STATUSES,
  proposeMatrixAIRecommendation, listMatrixAIRecommendations, getMatrixAIRecommendation, reviewMatrixAIRecommendation,
  listMatrixAIRecommendationReviews,
  type MatrixAIRecommendation, type RecommendationStatus, type ProposedCell, type ConstraintsUsed,
} from './matrixApi'

const POLL_MS = 8_000

// Hypothesis Discovery Engine, Phase 3B — AI Research Orchestrator UI.
//
// AI is a PLANNER here, never a JUDGE (operator's own non-negotiable
// boundary). This panel renders exactly what execution/routes/matrix_ai.py
// returns: a DRAFT proposal of which NEW cells might be worth generating,
// with full audit-trail provenance. It computes nothing itself, never
// auto-approves anything, and never calls POST /research/matrix/generate
// on its own — an APPROVED recommendation still requires the operator to
// read proposed_next_cells and submit them through the Generate form
// above, by hand.

const DISCLAIMER_TEXT =
  'DRAFT ONLY — this is a proposal of where to look next, never evidence and never a verdict. ' +
  'Nothing here can create, promote, or validate a Matrix cell. Approving a recommendation only records that a ' +
  'human reviewed it — generating real cells from proposed_next_cells still requires a separate, manual ' +
  'submission through "Generate Matrix Family" above.'

function statusTone(status: RecommendationStatus): 'good' | 'poor' | 'neutral' {
  if (status === 'APPROVED') return 'good'
  if (status === 'REJECTED') return 'poor'
  return 'neutral'
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

function parseJsonArray<T>(raw: string | null): T[] {
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as T[]) : []
  } catch {
    return []
  }
}

function parseConstraints(raw: string): ConstraintsUsed | null {
  try {
    return JSON.parse(raw) as ConstraintsUsed
  } catch {
    return null
  }
}

// ── Propose form ─────────────────────────────────────────────────────────

function ProposeForm({ familyId, onProposed }: { familyId: string | null; onProposed: (recommendationId: string) => void }) {
  const [familyIdsText, setFamilyIdsText] = useState('')
  const [cellIdsText, setCellIdsText] = useState('')
  const [focusHint, setFocusHint] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [aiStatus, setAiStatus] = useState<string | null>(null)

  useEffect(() => {
    if (familyId) setFamilyIdsText(familyId)
  }, [familyId])

  const submit = async () => {
    setError(null)
    setAiStatus(null)
    const family_ids = familyIdsText.split(',').map((s) => s.trim()).filter(Boolean)
    const cell_ids = cellIdsText.split(',').map((s) => s.trim()).filter(Boolean)
    if (family_ids.length === 0 && cell_ids.length === 0) {
      setError('Provide at least one family_id or cell_id.')
      return
    }
    setSubmitting(true)
    try {
      const res = await proposeMatrixAIRecommendation({ family_ids, cell_ids, focus_hint: focusHint || undefined })
      setAiStatus(res.status)
      if (res.status === 'ok' && res.recommendation_id) {
        onProposed(res.recommendation_id)
      } else if (res.status === 'error') {
        setError(res.error || 'AI proposal failed.')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Panel title="Propose a Research Plan" right="advisory-only — never writes a Matrix cell">
      <div className="p-4 flex flex-col gap-3">
        <div className="rounded border border-amber/40 bg-amber/10 px-3 py-2.5 text-[0.78em] text-amber leading-relaxed font-bold">
          {DISCLAIMER_TEXT}
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Family IDs (comma-separated — for coverage-gap planning)</span>
          <input
            value={familyIdsText}
            onChange={(e) => setFamilyIdsText(e.target.value)}
            placeholder="family_id"
            className="px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text font-mono min-h-11"
          />
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Cell IDs (comma-separated — for rejection explanation)</span>
          <input
            value={cellIdsText}
            onChange={(e) => setCellIdsText(e.target.value)}
            placeholder="MATRIX-CELL-..."
            className="px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text font-mono min-h-11"
          />
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Focus hint (optional)</span>
          <input
            value={focusHint}
            onChange={(e) => setFocusHint(e.target.value)}
            placeholder="e.g. metals coverage"
            className="px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text min-h-11"
          />
        </div>

        {error && <div className="text-[0.8em] text-red">{error}</div>}
        {aiStatus === 'disabled' && (
          <div className="text-[0.8em] text-muted">AI is disabled or has no configured API key — see the AI Settings tab.</div>
        )}

        <button
          onClick={submit}
          disabled={submitting}
          className="self-start px-3 py-1.5 rounded border border-accent/40 text-accent text-[0.8em] font-bold hover:bg-accent/10 disabled:opacity-50 min-h-11"
        >
          {submitting ? 'Proposing…' : 'Propose Research Plan'}
        </button>
      </div>
    </Panel>
  )
}

// ── Recommendations list ────────────────────────────────────────────────

function RecommendationsList({ selectedId, onSelect, refreshKey }: {
  selectedId: string | null
  onSelect: (id: string) => void
  refreshKey: number
}) {
  const { markUnauthenticated } = useAuth()
  const [statusFilter, setStatusFilter] = useState<RecommendationStatus | ''>('')
  const { data, refetch } = useApiQuery(
    ['matrix-ai-recommendations', statusFilter],
    () => listMatrixAIRecommendations({ status: statusFilter || undefined, limit: 100 }),
    POLL_MS,
    markUnauthenticated,
  )

  useEffect(() => {
    refetch()
  }, [refreshKey, refetch])

  const rows = data?.recommendations ?? []
  const columns: Column<MatrixAIRecommendation>[] = [
    {
      header: 'Recommendation',
      render: (r) => (
        <button
          onClick={() => onSelect(r.recommendation_id)}
          className={`font-mono text-[0.8em] font-bold ${r.recommendation_id === selectedId ? 'text-accent' : 'text-text hover:text-accent'}`}
        >
          {r.recommendation_id}
        </button>
      ),
    },
    { header: 'Status', render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
    { header: 'Priority', render: (r) => r.priority ?? '—' },
    { header: 'Provider', render: (r) => r.provider },
    { header: 'Reasoning', render: (r) => <span className="line-clamp-1">{r.reasoning_summary}</span> },
    { header: 'Created', accessorFn: (r) => r.created_at, render: (r) => fmtDate(r.created_at) },
  ]

  return (
    <Panel
      title="AI Recommendations"
      right={
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as RecommendationStatus | '')}
          className="bg-bg border border-border rounded px-2 py-1 text-[0.75em] text-text"
        >
          <option value="">All statuses</option>
          {RECOMMENDATION_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      }
    >
      {rows.length > 0 ? <DataTable columns={columns} rows={rows} rowKey={(r) => r.recommendation_id} /> : <Empty>No AI recommendations yet.</Empty>}
    </Panel>
  )
}

// ── Recommendation detail + review ──────────────────────────────────────

function ProposedCellsTable({ cells }: { cells: ProposedCell[] }) {
  const columns: Column<ProposedCell>[] = [
    { header: 'Symbol', render: (c) => <span className="font-bold text-accent">{c.symbol}</span> },
    { header: 'Bundle', render: (c) => c.bundle_name },
    { header: 'Timeframes', render: (c) => (c.timeframes || []).join(', ') },
    { header: 'Engines', render: (c) => (c.engines || []).join(', ') },
    { header: 'Risk Preset', render: (c) => c.risk_preset },
    { header: 'Rationale', render: (c) => <span className="text-[0.95em]">{c.rationale}</span> },
  ]
  return <DataTable columns={columns} rows={cells} rowKey={(c) => `${c.symbol}-${c.bundle_name}-${c.risk_preset}`} />
}

function ReviewControls({ recommendationId, status, onReviewed }: {
  recommendationId: string
  status: RecommendationStatus
  onReviewed: () => void
}) {
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (status !== 'DRAFT') return null

  const review = async (target: 'APPROVED' | 'REJECTED') => {
    setError(null)
    setSubmitting(true)
    try {
      await reviewMatrixAIRecommendation(recommendationId, { status: target, review_note: note || undefined })
      onReviewed()
    } catch (e) {
      // 409 -- another caller already reviewed this recommendation
      // between this panel's last refresh and this click (Phase 3B-H
      // hardening: the transition is atomic, never a silent overwrite).
      if (e instanceof ApiError && e.status === 409) {
        setError('This recommendation was already reviewed by someone else — refresh to see the current status.')
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 pt-3 border-t border-border">
      <span className="text-[0.7em] text-muted uppercase tracking-[1px]">
        Human Review (3B.4) — the only way this can leave DRAFT. Reviewer identity is derived from your own
        authenticated session, not typed here.
      </span>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="review note (optional)"
          className="flex-1 min-w-[180px] px-2 py-1.5 text-[0.8em] rounded border border-border bg-bg text-text min-h-11"
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => review('APPROVED')}
          disabled={submitting}
          className="px-3 py-1.5 rounded border border-green/40 text-green text-[0.8em] font-bold hover:bg-green/10 disabled:opacity-50 min-h-11"
        >
          Approve
        </button>
        <button
          onClick={() => review('REJECTED')}
          disabled={submitting}
          className="px-3 py-1.5 rounded border border-red/40 text-red text-[0.8em] font-bold hover:bg-red/10 disabled:opacity-50 min-h-11"
        >
          Reject
        </button>
      </div>
      {error && <div className="text-[0.78em] text-red">{error}</div>}
    </div>
  )
}

function ReviewHistory({ recommendationId, refreshKey }: { recommendationId: string; refreshKey: string }) {
  // Phase 3B-H hardening pass 2 -- append-only history: re-reviewing a
  // recommendation never erases an earlier review, so this can show more
  // than one row even though the recommendation's own status only ever
  // reflects the LATEST one above.
  const { markUnauthenticated } = useAuth()
  const { data, refetch } = useApiQuery(
    ['matrix-ai-recommendation-reviews', recommendationId],
    () => listMatrixAIRecommendationReviews(recommendationId),
    POLL_MS,
    markUnauthenticated,
  )

  useEffect(() => {
    void refetch()
  }, [refreshKey, refetch])

  const reviews = data?.reviews ?? []
  if (reviews.length === 0) return null

  return (
    <div className="flex flex-col gap-1.5 pt-3 border-t border-border text-[0.8em]">
      <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Review History (append-only)</span>
      {reviews.map((r) => (
        <div key={r.review_id} className="flex justify-between gap-3 text-[0.78em]">
          <span className="text-muted">{fmtDate(r.reviewed_at)}</span>
          <span>{r.old_status} → {r.new_status} by {r.reviewed_by ?? '—'}{r.review_note ? ` — "${r.review_note}"` : ''}</span>
        </div>
      ))}
    </div>
  )
}

function RecommendationDetail({ recommendationId, onClose, onChanged }: {
  recommendationId: string
  onClose: () => void
  onChanged: () => void
}) {
  const { markUnauthenticated } = useAuth()
  const { data, refetch } = useApiQuery(
    ['matrix-ai-recommendation', recommendationId],
    () => getMatrixAIRecommendation(recommendationId),
    POLL_MS,
    markUnauthenticated,
  )

  return (
    <Panel
      title="Recommendation Detail"
      right={<button onClick={onClose} className="text-muted hover:text-text px-2 py-1.5">✕ close</button>}
    >
      {!data ? (
        <Empty>Loading…</Empty>
      ) : (
        <div className="p-4 flex flex-col gap-4">
          <div className="rounded border border-amber/40 bg-amber/10 px-3 py-2.5 text-[0.78em] text-amber leading-relaxed font-bold">
            {DISCLAIMER_TEXT}
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone={statusTone(data.status)}>{data.status}</Badge>
            {data.priority && <Badge tone="neutral">{`priority: ${data.priority}`}</Badge>}
            <span className="text-[0.75em] text-muted font-mono">{data.recommendation_id}</span>
          </div>

          <div className="flex flex-col gap-1.5 text-[0.8em]">
            <div className="flex justify-between gap-3"><span className="text-muted">Provider</span><span>{data.provider}</span></div>
            <div className="flex justify-between gap-3"><span className="text-muted">Requested model</span><span>{data.requested_model ?? '(provider default)'}</span></div>
            <div className="flex justify-between gap-3"><span className="text-muted">Actual model</span><span>{data.actual_model ?? 'UNKNOWN'}</span></div>
            <div className="flex justify-between gap-3"><span className="text-muted">Created</span><span>{fmtDate(data.created_at)}</span></div>
            <div className="flex justify-between gap-3"><span className="text-muted">Evidence snapshot hash</span><span className="font-mono text-[0.85em] break-all text-right">{data.evidence_snapshot_hash}</span></div>
            <div className="flex justify-between gap-3"><span className="text-muted">Input family_ids</span><span className="font-mono text-right break-all">{parseJsonArray<string>(data.input_family_ids_json).join(', ') || '—'}</span></div>
            <div className="flex justify-between gap-3"><span className="text-muted">Input cell_ids</span><span className="font-mono text-right break-all">{parseJsonArray<string>(data.input_cell_ids_json).join(', ') || '—'}</span></div>
          </div>

          {(() => {
            const constraints = parseConstraints(data.constraints_used_json)
            if (!constraints) return null
            return (
              <div className="flex flex-col gap-1.5 pt-3 border-t border-border text-[0.8em]">
                <span className="text-[0.7em] text-muted uppercase tracking-[1px]">
                  Constraints Used — provenance, not just content (Phase 3B-H)
                </span>
                <div className="flex justify-between gap-3">
                  <span className="text-muted">Research code commit</span>
                  <span className="font-mono text-right">
                    {constraints.research_code_commit}
                    {constraints.research_code_dirty && <span className="text-amber"> (dirty working tree)</span>}
                  </span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-muted">Dead list hash</span>
                  <span className="font-mono text-[0.85em] break-all text-right">{constraints.dead_list_hash ?? 'not available'}</span>
                </div>
                <div className="flex justify-between gap-3"><span className="text-muted">Frozen engines</span><span className="text-right">{constraints.frozen_engines.join(', ') || '—'}</span></div>
                <div className="flex justify-between gap-3"><span className="text-muted">Symbol universe size</span><span>{constraints.symbol_universe.length}</span></div>
              </div>
            )
          })()}

          <div className="flex flex-col gap-1.5 pt-3 border-t border-border">
            <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Reasoning Summary</span>
            <p className="text-[0.85em] text-text leading-relaxed">{data.reasoning_summary}</p>
          </div>

          {data.distinct_from_dead_list && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Distinct from CLAUDE.md's dead list</span>
              <p className="text-[0.85em] text-text leading-relaxed">{data.distinct_from_dead_list}</p>
            </div>
          )}

          {parseJsonArray<string>(data.coverage_gaps_json).length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Coverage Gaps</span>
              <ul className="list-disc list-inside text-[0.85em] text-text">
                {parseJsonArray<string>(data.coverage_gaps_json).map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <span className="text-[0.7em] text-muted uppercase tracking-[1px]">
              Proposed Next Cells — NOT yet real Matrix cells; submit via "Generate Matrix Family" above to create them
            </span>
            <ProposedCellsTable cells={parseJsonArray<ProposedCell>(data.proposed_next_cells_json)} />
          </div>

          {data.status !== 'DRAFT' && (
            <div className="flex flex-col gap-1 pt-3 border-t border-border text-[0.8em]">
              <span className="text-[0.7em] text-muted uppercase tracking-[1px]">Review (latest)</span>
              <div className="flex justify-between gap-3"><span className="text-muted">Reviewed by</span><span>{data.reviewed_by ?? '—'}</span></div>
              <div className="flex justify-between gap-3"><span className="text-muted">Reviewed at</span><span>{fmtDate(data.reviewed_at)}</span></div>
              {data.review_note && <div className="flex justify-between gap-3"><span className="text-muted">Note</span><span className="text-right">{data.review_note}</span></div>}
            </div>
          )}

          <ReviewHistory recommendationId={data.recommendation_id} refreshKey={data.status} />

          <ReviewControls
            recommendationId={data.recommendation_id}
            status={data.status}
            onReviewed={() => {
              void refetch()
              onChanged()
            }}
          />
        </div>
      )}
    </Panel>
  )
}

// ── Top level ────────────────────────────────────────────────────────────

export function MatrixAiPlanner({ familyId }: { familyId: string | null }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const bump = () => setRefreshKey((k) => k + 1)

  return (
    <div className="flex flex-col gap-4">
      <ProposeForm
        familyId={familyId}
        onProposed={(id) => {
          setSelectedId(id)
          bump()
        }}
      />
      <RecommendationsList selectedId={selectedId} onSelect={setSelectedId} refreshKey={refreshKey} />
      {selectedId && (
        <RecommendationDetail recommendationId={selectedId} onClose={() => setSelectedId(null)} onChanged={bump} />
      )}
    </div>
  )
}
