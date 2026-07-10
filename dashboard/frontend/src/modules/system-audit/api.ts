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
