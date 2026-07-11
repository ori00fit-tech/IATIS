import { apiGet } from '../../lib/api'

export interface AuditCheck {
  axis: number
  name: string
  status: 'PASS' | 'FAIL' | 'WARN' | 'INFO'
  detail: string
  evidence: string[]
}

export interface PhilosophyAuditResponse {
  generated_at: string
  summary: { total: number; fail: number; warn: number; pass: number; info: number }
  checks: AuditCheck[]
}

export interface ProviderChainsResponse {
  chains: Record<string, string[]>
  native_timeframes: Record<string, string[]>
  availability: Record<string, boolean>
  per_symbol: Record<string, string[]>
}

// ~10-20s of D1 round-trips — call from a button, never on a poll.
export const runPhilosophyAudit = () => apiGet<PhilosophyAuditResponse>('/philosophy-audit')
export const getProviderChains = () => apiGet<ProviderChainsResponse>('/provider-chains')

// ---------------------------------------------------------------------------
// Research Integrity (module 9) — leakage guard, survivorship checker,
// manifest validator. Read-only, no network calls.
// ---------------------------------------------------------------------------

export interface LeakageFinding {
  line: number
  col: number
  pattern: string
  severity: string
  message: string
}

export interface LeakageFileReport {
  file: string
  verdict: string
  note?: string
  findings: LeakageFinding[]
  high_severity_count: number
  info_count?: number
}

export interface LeakageGuardCheck {
  status: 'PASS' | 'WARNING' | 'ERROR'
  files_scanned?: number
  verdict?: string
  total_high_severity?: number
  reports?: LeakageFileReport[]
  error?: string
}

export interface SurvivorshipCheck {
  status: 'PASS' | 'WARNING' | 'FAIL' | 'ERROR'
  error?: string
  symbol_evidence?: {
    caveat: string
    total_symbols: number
    enabled_no_evidence: string[]
    disabled_no_evidence: string[]
    rows: { symbol: string; enabled: boolean; manifest_count: number; manifests: string[]; verdict: string }[]
  }
  selection_disclosure?: {
    convention_introduced: string
    note: string
    disclosed: { manifest: string; label: string }[]
    undisclosed: string[]
    invalid_label: { manifest: string; label: string }[]
  }
}

export interface ManifestValidatorCheck {
  status: 'PASS' | 'WARNING' | 'ERROR'
  error?: string
  total?: number
  reproducible_count?: number
  non_reproducible?: { file: string; kind: string | null; git_dirty: boolean | null }[]
}

export interface ResearchIntegrityResponse {
  checked_at: string
  overall: 'PASS' | 'WARNING' | 'FAIL' | 'ERROR'
  checks: {
    leakage_guard: LeakageGuardCheck
    survivorship: SurvivorshipCheck
    manifest_validator: ManifestValidatorCheck
  }
}

export const runResearchIntegrity = () => apiGet<ResearchIntegrityResponse>('/research/integrity')
