import { useState } from 'react'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import type { AuditCheck, PhilosophyAuditResponse } from './api'
import { runPhilosophyAudit } from './api'

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
    </div>
  )
}
