import { apiGet } from '../../lib/api'

export interface ForwardRule {
  rule_id: string
  statement: string
  bucket: string
  metric: string
  current_value: number | 'Infinity' | '-Infinity' | null
  op: string
  threshold: number
  n: number
  min_n: number
  progress_pct: number | null
  sufficient_n: boolean
  triggered: boolean
  action: string | null
}

export interface ForwardReviewResponse {
  checked_at: string
  rules: ForwardRule[]
}

export interface ShadowGate {
  primary_gate: string
  n_closed: number
  wins: number
  avg_r: number | null
  total_r: number | null
  verdict: string
}

export interface ShadowBookResponse {
  note: string
  open: number
  gates: ShadowGate[]
}

export const getForwardReview = () => apiGet<ForwardReviewResponse>('/forward-review')
export const getShadowBook = () => apiGet<ShadowBookResponse>('/shadow-book')

export function formatMetric(value: number | 'Infinity' | '-Infinity' | null, digits = 2): string {
  if (value === null) return '—'
  if (value === 'Infinity') return '∞'
  if (value === '-Infinity') return '-∞'
  return value.toFixed(digits)
}
