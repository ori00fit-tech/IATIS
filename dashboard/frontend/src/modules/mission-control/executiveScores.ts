// Executive Overview scoring (v0.6 spec §1). Every score here is a
// DETERMINISTIC rollup of checks other endpoints already measured — not a new
// opinion. Each function returns a 0-100 score (or null when its inputs
// aren't loaded yet) plus a one-line `why` an operator can read at a glance.
//
// Deliberately no performance figures (PF/WR/expectancy) are scored here: per
// CLAUDE.md and the spec's evidence guardrail those are noise below n≈30 and
// must never drive a headline health light.
import type { HealthFull } from './api'
import type { MarketHealth, ReconciliationResult } from './api'
import type { DataHealthResponse } from '../data-center/api'
import type { PhilosophyAuditResponse, ResearchIntegrityResponse } from '../system-audit/api'

export interface Score {
  value: number | null
  why: string
}

const clamp = (n: number) => Math.max(0, Math.min(100, Math.round(n)))

export type ScoreTone = 'green' | 'amber' | 'red' | 'default'
export function scoreTone(value: number | null): ScoreTone {
  if (value === null) return 'default'
  if (value >= 80) return 'green'
  if (value >= 60) return 'amber'
  return 'red'
}

/** Is the machine running? services + resources + scheduler + D1 freshness. */
export function systemHealthScore(hf: HealthFull | null, mh: MarketHealth | null): Score {
  if (!hf) return { value: null, why: 'awaiting /health/full' }
  let s = 100
  const notes: string[] = []
  if (hf.status === 'degraded') {
    s -= 40
    notes.push(`degraded: ${hf.issues.slice(0, 2).join('; ') || 'see Mission Control'}`)
  }
  if (hf.services) {
    const down = Object.entries(hf.services).filter(([, v]) => !v.healthy)
    if (down.length) {
      s -= 15 * down.length
      notes.push(`${down.length} service(s) unhealthy`)
    }
  }
  const sys = hf.system
  if (sys) {
    if (sys.cpu_pct > 80) { s -= 10; notes.push(`CPU ${sys.cpu_pct.toFixed(0)}%`) }
    if (sys.ram_pct > 85) { s -= 10; notes.push(`RAM ${sys.ram_pct.toFixed(0)}%`) }
    if (sys.disk_pct > 80) { s -= 10; notes.push(`disk ${sys.disk_pct.toFixed(0)}%`) }
    if (sys.swap_pct > 50) { s -= 5; notes.push(`swap ${sys.swap_pct.toFixed(0)}%`) }
  }
  if (hf.scheduler && hf.scheduler.status !== 'running') {
    s -= 20
    notes.push(`scheduler ${hf.scheduler.status}`)
  }
  if (mh && mh.d1Up === false) { s -= 15; notes.push('D1 storage down') }
  return { value: clamp(s), why: notes.length ? notes.join(' · ') : 'all systems nominal' }
}

/** Can I trust the inputs? cache completeness weighted by severity. */
export function dataQualityScore(dh: DataHealthResponse | null): Score {
  if (!dh) return { value: null, why: 'awaiting /data-health' }
  const su = dh.summary
  const total = su.ok + su.stale + su.gaps + (su.starved ?? 0) + su.missing
  if (total === 0) return { value: null, why: 'no symbols tracked' }
  // ok=full credit, stale=0.6, gaps=0.4, starved/missing=0 — matches the
  // CacheStatus severity ladder in data-center.
  const weighted = su.ok * 1 + su.stale * 0.6 + su.gaps * 0.4
  const problems: string[] = []
  if (su.missing) problems.push(`${su.missing} missing`)
  if (su.starved) problems.push(`${su.starved} starved`)
  if (su.gaps) problems.push(`${su.gaps} with gaps`)
  if (su.stale) problems.push(`${su.stale} stale`)
  return {
    value: clamp((weighted / total) * 100),
    why: problems.length ? problems.join(' · ') : `${su.ok}/${total} caches OK`,
  }
}

/** Exposure headroom + reconciliation cleanliness (not a performance read). */
export function riskStatusScore(hf: HealthFull | null, rec: ReconciliationResult | null): Score {
  if (!hf) return { value: null, why: 'awaiting /health/full' }
  const exp = hf.exposure_estimate
  if (!exp) return { value: null, why: 'no exposure estimate' }
  const util = exp.utilization_pct ?? 0
  let s = 100 - util // headroom: full cap used → 0
  const notes: string[] = [`${util.toFixed(0)}% of cap used`]
  if (rec && rec.status === 'mismatch') {
    s -= 25
    notes.push('reconciliation mismatch')
  }
  return { value: clamp(s), why: notes.join(' · ') }
}

/** Are verdicts sound? philosophy-audit pass/warn/fail ratio (on-demand). */
export function decisionQualityScore(audit: PhilosophyAuditResponse | null): Score {
  if (!audit) return { value: null, why: 'run the philosophy audit to score' }
  const { pass, warn, fail } = audit.summary
  const denom = pass + warn + fail
  if (denom === 0) return { value: null, why: 'no scorable checks' }
  const s = ((pass + 0.5 * warn) / denom) * 100
  return { value: clamp(s), why: `${pass} pass · ${warn} warn · ${fail} fail` }
}

/** Is the evidence auditable? reproducibility + leakage + survivorship (on-demand). */
export function researchIntegrityScore(integ: ResearchIntegrityResponse | null): Score {
  if (!integ) return { value: null, why: 'run the integrity check to score' }
  const mv = integ.checks.manifest_validator
  let s: number
  if (mv.total && mv.total > 0) {
    s = ((mv.reproducible_count ?? 0) / mv.total) * 100
  } else {
    s = integ.overall === 'PASS' ? 100 : integ.overall === 'WARNING' ? 70 : 40
  }
  const notes: string[] = []
  if (mv.total) notes.push(`${mv.reproducible_count ?? 0}/${mv.total} reproducible`)
  const leak = integ.checks.leakage_guard.total_high_severity ?? 0
  if (leak > 0) { s -= 20; notes.push(`${leak} leakage finding(s)`) }
  if (integ.checks.survivorship.status !== 'PASS') { s -= 10; notes.push('survivorship gaps') }
  return { value: clamp(s), why: notes.length ? notes.join(' · ') : integ.overall }
}

/** Weakest-link readiness over whatever domain scores are available. */
export function productionReadinessScore(domain: (number | null)[]): Score {
  const present = domain.filter((v): v is number => v !== null)
  if (present.length === 0) return { value: null, why: 'no domains scored yet' }
  const min = Math.min(...present)
  const missing = domain.length - present.length
  const tail = missing ? ` · ${missing} domain(s) not yet scored` : ''
  return { value: clamp(min), why: `weakest domain at ${min}${tail}` }
}
