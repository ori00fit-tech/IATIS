import { useState } from 'react'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import type { AuditCheck, PhilosophyAuditResponse, ResearchIntegrityResponse } from './api'
import { runPhilosophyAudit, runResearchIntegrity } from './api'

const AXIS_TITLES: Record<number, string> = {
  1: 'Hierarchy — verdicts respect their gates',
  2: 'Engine independence',
  3: 'Phantom / starved engines',
  4: 'Per-engine decision impact',
  5: 'Gate bottleneck',
  6: 'Score continuity',
  7: 'Selectivity vs validated baseline',
  8: 'No-information confluence',
}

const STATUS_CLASS: Record<AuditCheck['status'], string> = {
  PASS: 'bg-green/15 text-green',
  FAIL: 'bg-red/15 text-red',
  WARN: 'bg-amber/15 text-amber',
  INFO: 'bg-accent/10 text-muted',
}

const CHECK_BADGE: Record<string, 'good' | 'marginal' | 'poor' | 'neutral'> = {
  PASS: 'good',
  WARNING: 'marginal',
  FAIL: 'poor',
  ERROR: 'poor',
}

function ResearchIntegrityPanel() {
  const [data, setData] = useState<ResearchIntegrityResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      setData(await runResearchIntegrity())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Panel
      title="Research Integrity"
      right={
        <div className="flex items-center gap-2">
          {data && <Badge tone={CHECK_BADGE[data.overall] ?? 'neutral'}>{data.overall}</Badge>}
          <button
            onClick={run}
            disabled={running}
            className="px-3 py-1 text-[0.8em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50 disabled:cursor-wait"
          >
            {running ? 'Running…' : data ? 'Re-run checks' : 'Run checks'}
          </button>
        </div>
      }
    >
      {error ? (
        <Empty>Checks failed: {error}</Empty>
      ) : !data ? (
        <Empty>
          {running
            ? 'Scanning research scripts, manifests, and symbol evidence…'
            : 'Leakage guard (static scan), survivorship checker, and manifest validator — read-only, no network calls. Cross-provider diff lives in the Experiment Runner instead (it burns provider API quota).'}
        </Empty>
      ) : (
        <div className="flex flex-col gap-3 p-4">
          <div className="text-[0.72em] text-muted">checked {new Date(data.checked_at).toLocaleString()} UTC</div>

          <div className="rounded border border-border bg-bg/40 px-3 py-2.5">
            <div className="flex items-center gap-2 mb-1">
              <Badge tone={CHECK_BADGE[data.checks.leakage_guard.status] ?? 'neutral'}>{data.checks.leakage_guard.status}</Badge>
              <span className="text-[0.82em] font-bold">Leakage Guard</span>
              <span className="text-[0.75em] text-muted">
                {data.checks.leakage_guard.files_scanned ?? 0} files scanned, {data.checks.leakage_guard.total_high_severity ?? 0} high-severity findings
              </span>
            </div>
            {data.checks.leakage_guard.error && <div className="text-[0.78em] text-red">{data.checks.leakage_guard.error}</div>}
            {(data.checks.leakage_guard.reports ?? [])
              .filter((r) => r.findings.length > 0)
              .map((r) => (
                <div key={r.file} className="mt-1.5 text-[0.75em]">
                  <div className="text-muted font-mono">{r.file}</div>
                  {r.findings.map((f, i) => (
                    <div key={i} className="pl-3 text-muted/90">
                      L{f.line}: [{f.severity}] {f.pattern} — {f.message}
                    </div>
                  ))}
                </div>
              ))}
          </div>

          <div className="rounded border border-border bg-bg/40 px-3 py-2.5">
            <div className="flex items-center gap-2 mb-1">
              <Badge tone={CHECK_BADGE[data.checks.survivorship.status] ?? 'neutral'}>{data.checks.survivorship.status}</Badge>
              <span className="text-[0.82em] font-bold">Survivorship Checker</span>
            </div>
            {data.checks.survivorship.error && <div className="text-[0.78em] text-red">{data.checks.survivorship.error}</div>}
            {data.checks.survivorship.symbol_evidence && (
              <div className="text-[0.75em] text-muted mt-1">
                {data.checks.survivorship.symbol_evidence.enabled_no_evidence.length > 0 && (
                  <div className="text-red">
                    ENABLED with no evidence: {data.checks.survivorship.symbol_evidence.enabled_no_evidence.join(', ')}
                  </div>
                )}
                {data.checks.survivorship.symbol_evidence.disabled_no_evidence.length > 0 && (
                  <div>DISABLED with no evidence: {data.checks.survivorship.symbol_evidence.disabled_no_evidence.join(', ')}</div>
                )}
                {data.checks.survivorship.selection_disclosure && (
                  <div>
                    {data.checks.survivorship.selection_disclosure.disclosed.length} manifests declare selection,{' '}
                    {data.checks.survivorship.selection_disclosure.undisclosed.length} undisclosed (grandfathered pre-
                    {data.checks.survivorship.selection_disclosure.convention_introduced})
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="rounded border border-border bg-bg/40 px-3 py-2.5">
            <div className="flex items-center gap-2 mb-1">
              <Badge tone={CHECK_BADGE[data.checks.manifest_validator.status] ?? 'neutral'}>{data.checks.manifest_validator.status}</Badge>
              <span className="text-[0.82em] font-bold">Manifest Validator</span>
              <span className="text-[0.75em] text-muted">
                {data.checks.manifest_validator.reproducible_count ?? 0}/{data.checks.manifest_validator.total ?? 0} reproducible
              </span>
            </div>
            {data.checks.manifest_validator.error && <div className="text-[0.78em] text-red">{data.checks.manifest_validator.error}</div>}
            {(data.checks.manifest_validator.non_reproducible ?? []).length > 0 && (
              <div className="text-[0.75em] text-muted mt-1">
                {data.checks.manifest_validator.non_reproducible!.map((m) => (
                  <div key={m.file} className="font-mono">
                    {m.file} ({m.kind ?? '?'})
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}

export function SystemAudit() {
  const [data, setData] = useState<PhilosophyAuditResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      setData(await runPhilosophyAudit())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  const byAxis = new Map<number, AuditCheck[]>()
  data?.checks.forEach((c) => {
    byAxis.set(c.axis, [...(byAxis.get(c.axis) ?? []), c])
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={data?.summary.pass ?? '—'} label="Pass" color="green" />
        <KpiCard value={data?.summary.fail ?? '—'} label="Fail" color="red" />
        <KpiCard value={data?.summary.warn ?? '—'} label="Warn" color="amber" />
        <KpiCard value={data?.summary.total ?? '—'} label="Checks" color="blue" />
      </div>

      <Panel
        title="System Philosophy Audit"
        right={
          <button
            onClick={run}
            disabled={running}
            className="px-3 py-1 text-[0.8em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50 disabled:cursor-wait"
          >
            {running ? 'Running (~15s)…' : data ? 'Re-run audit' : 'Run audit'}
          </button>
        }
      >
        {error ? (
          <Empty>Audit failed: {error}</Empty>
        ) : !data ? (
          <Empty>
            {running
              ? 'Querying the live decisions DB…'
              : '29 automated checks across 8 axes (same as scripts/philosophy_audit.py) — runs read-only against the production decisions DB.'}
          </Empty>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="text-[0.72em] text-muted">
              generated {new Date(data.generated_at).toLocaleString()} UTC
            </div>
            {Array.from(byAxis.entries()).map(([axis, checks]) => (
              <div key={axis}>
                <div className="text-[0.8em] font-bold text-accent mb-1.5">
                  Axis {axis} — {AXIS_TITLES[axis] ?? ''}
                </div>
                <div className="flex flex-col gap-1.5">
                  {checks.map((c, i) => (
                    <div key={i} className="rounded border border-border bg-bg/40 px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className={`px-1.5 py-0.5 rounded text-[0.68em] font-bold ${STATUS_CLASS[c.status]}`}>
                          {c.status}
                        </span>
                        <span className="text-[0.82em]">{c.name}</span>
                      </div>
                      <div className="text-[0.74em] text-muted mt-1 whitespace-pre-wrap">{c.detail}</div>
                      {c.evidence.length > 0 && (
                        <div className="text-[0.7em] text-muted/80 mt-1 font-mono">
                          {c.evidence.map((e, j) => (
                            <div key={j}>{e}</div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <ResearchIntegrityPanel />
    </div>
  )
}
