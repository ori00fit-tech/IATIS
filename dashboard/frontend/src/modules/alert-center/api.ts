import { apiGet } from '../../lib/api'

export type AlertSeverity = 'error' | 'warning' | 'info'

export interface Alert {
  severity: AlertSeverity
  category: string
  message: string
  detail: Record<string, unknown> | null
}

export interface AlertsResponse {
  checked_at: string
  count: number
  by_severity: Record<AlertSeverity, number>
  alerts: Alert[]
}

export const getAlerts = () => apiGet<AlertsResponse>('/alerts')
