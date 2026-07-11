import { apiGet } from '../../lib/api'

export interface ReportKind {
  id: string
  title: string
  description: string
}

export const REPORT_KINDS: ReportKind[] = [
  { id: 'research', title: 'Research Report', description: 'Hypothesis registry + every evidence manifest, mechanically aggregated.' },
  { id: 'manifest_summary', title: 'Manifest Summary', description: 'Just the manifest ledger — which runs are reproducible.' },
  { id: 'system', title: 'System Health Report', description: 'Snapshot of /health/full — CPU/RAM/disk, scheduler, DB, providers.' },
  { id: 'provider', title: 'Data Provider Report', description: 'Snapshot of /provider-chains — fallback order per asset class.' },
  { id: 'forward', title: 'Forward Demo Report', description: 'D001/D002 rule progress + outcome-tracker performance summary.' },
]

export interface ReportJsonResponse {
  kind: string
  title: string
  generated_at: string
  data: unknown
}

export const getReportJson = (kind: string) => apiGet<ReportJsonResponse>(`/reports/${kind}`, { format: 'json' })
export const reportDownloadUrl = (kind: string) => `/reports/${kind}?format=md`
