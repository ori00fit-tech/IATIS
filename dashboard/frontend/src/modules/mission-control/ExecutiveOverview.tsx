import { useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel } from '../../components/Panel'
import type { HealthFull, MarketHealth, ReconciliationResult } from './api'
import type { DataHealthResponse } from '../data-center/api'
import {
  runPhilosophyAudit,
  runResearchIntegrity,
  type PhilosophyAuditResponse,
  type ResearchIntegrityResponse,
} from '../system-audit/api'
import { getResearch, getManifests } from '../research-backtests/api'
import {
  systemHealthScore,
  dataQualityScore,
  riskStatusScore,
  decisionQualityScore,
  researchIntegrityScore,
  productionReadinessScore,
  scoreTone,
  type Score,
  type ScoreTone,
} from './executiveScores'

// Deep-link into a module without coupling to App's tab state: useHashTab
// listens on hashchange, so writing the hash navigates. A red score is never
// a dead end — every card jumps to the tab that explains it.
function navigate(tabId: string) {
  window.location.hash = `#/${tabId}`
}

const toneRing: Record<ScoreTone, string> = {
  green: 'text-green',
  amber: 'text-amber',
  red: 'text-red',
  default: 'text-muted',
}
const toneBar: Record<ScoreTone, string> = {
  green: 'bg-green',
  amber: 'bg-amber',
  red: 'bg-red',
  default: 'bg-border',
}

function ScoreCard({
  label,
  score,
  tab,
  pending,
}: {
  label: string
  score: Score
  tab: string
  pending?: boolean
}) {
  const tone = scoreTone(score.value)
  return (
    <button
      onClick={() => navigate(tab)}
      className="text-left relative overflow-hidden bg-card border border-border rounded-[10px] p-3.5 hover:border-accent/40 transition-colors group"
      title={`Open ${label} detail`}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-[0.68em] text-muted uppercase tracking-[1px]">{label}</span>
        <span className="text-muted text-[0.9em] opacity-0 group-hover:opacity-100 transition-opacity">↗</span>
      </div>
      <div className={`text-[1.9em] font-extrabold leading-none mt-1 ${toneRing[tone]}`}>
        {pending ? '…' : score.value === null ? '—' : score.value}
        {score.value !== null && !pending && <span className="text-muted text-[0.4em] font-normal"> /100</span>}
      </div>
      <div className="mt-2 h-1 rounded bg-surface overflow-hidden">
        <div className={`h-full ${toneBar[tone]}`} style={{ width: `${score.value ?? 0}%` }} />
      </div>
      <div className="text-[0.68em] text-muted mt-1.5 leading-snug line-clamp-2 min-h-[2.2em]">{score.why}</div>
    </button>
  )
}

export function ExecutiveOverview({
  healthFull,
  marketHealth,
  dataHealth,
  reconciliation,
}: {
  healthFull: HealthFull | null
  marketHealth: MarketHealth | null
  dataHealth: DataHealthResponse | null
  reconciliation: ReconciliationResult | null
}) {
  const { markUnauthenticated } = useAuth()
  const research = usePolling(getResearch, 120_000, markUnauthenticated)
  const manifests = usePolling(getManifests, 120_000, markUnauthenticated)

  // The two audit endpoints do ~10-20s of D1 round-trips — never polled, run
  // on demand from one button that fills both scores at once.
  const [deep, setDeep] = useState<{
    loading: boolean
    audit: PhilosophyAuditResponse | null
    integrity: ResearchIntegrityResponse | null
    error: string | null
  }>({ loading: false, audit: null, integrity: null, error: null })

  const runDeep = () => {
    setDeep((d) => ({ ...d, loading: true, error: null }))
    Promise.all([runPhilosophyAudit(), runResearchIntegrity()])
      .then(([audit, integrity]) => setDeep({ loading: false, audit, integrity, error: null }))
      .catch((err) => setDeep((d) => ({ ...d, loading: false, error: err instanceof Error ? err.message : String(err) })))
  }

  const sysHealth = systemHealthScore(healthFull, marketHealth)
  const dataQ = dataQualityScore(dataHealth)
  const risk = riskStatusScore(healthFull, reconciliation)
  const decisionQ = decisionQualityScore(deep.audit)
  const integ = researchIntegrityScore(deep.integrity)
  const readiness = productionReadinessScore([sysHealth.value, dataQ.value, risk.value, decisionQ.value, integ.value])

  const hs = research.data?.hypothesis_summary
  const mans = manifests.data?.manifests ?? []
  const repro = mans.length ? mans.filter((m) => m.reproducible).length : 0

  return (
    <Panel
      title="Executive Overview"
      right={
        <button
          onClick={runDeep}
          disabled={deep.loading}
          className="text-accent hover:text-accent2 text-[0.78em] disabled:opacity-50"
          title="Runs the philosophy audit + research integrity checks (~20s of D1 round-trips)"
        >
          {deep.loading ? 'Auditing…' : deep.audit ? 'Re-run deep checks' : 'Run deep checks'}
        </button>
      }
    >
      <div className="p-4 flex flex-col gap-3">
        <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(150px,1fr))]">
          <ScoreCard label="System Health" score={sysHealth} tab="ops" />
          <ScoreCard label="Data Quality" score={dataQ} tab="data-center" />
          <ScoreCard label="Risk Status" score={risk} tab="risk-center" />
          <ScoreCard label="Decision Quality" score={decisionQ} tab="system-audit" pending={deep.loading && !deep.audit} />
          <ScoreCard label="Research Integrity" score={integ} tab="system-audit" pending={deep.loading && !deep.integrity} />
          <ScoreCard label="Production Readiness" score={readiness} tab="system-audit" />
        </div>

        {deep.error && <div className="text-red text-[0.78em]">Deep audit failed: {deep.error}</div>}

        {/* Research Status card (spec §7) — pure reuse of /research + manifests. */}
        <button
          onClick={() => navigate('research')}
          className="text-left flex items-center justify-between gap-4 flex-wrap px-3.5 py-2.5 rounded-md border border-border bg-surface hover:border-accent/40 transition-colors text-[0.8em]"
        >
          <span className="text-muted uppercase text-[0.82em] tracking-[0.8px] shrink-0">Research Status ↗</span>
          {hs ? (
            <span className="flex gap-4 flex-wrap">
              <span>Hypotheses <b>{hs.total}</b></span>
              <span className="text-green">passed <b>{hs.passed}</b></span>
              <span className="text-red">failed <b>{hs.failed}</b></span>
              <span className="text-amber">in research <b>{hs.research}</b></span>
              <span className="text-muted">reproducible manifests <b>{repro}/{mans.length}</b></span>
            </span>
          ) : (
            <span className="text-muted">{research.loading ? 'loading…' : 'no research data'}</span>
          )}
        </button>
      </div>
    </Panel>
  )
}
