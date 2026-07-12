import { apiGet, apiPost } from '../../lib/api'

export interface ReloadConfigResponse {
  success: boolean
  message: string
}

export const reloadConfig = () => apiPost<ReloadConfigResponse>('/ops/reload-config')

// Audit log (module 15) — real trail for mutating actions. Role-based
// access is a deliberately scoped-out gap, not built here — see
// execution/api_server.py's Security module docstring.
export interface AuditEntry {
  timestamp: string
  action: string
  actor: string
  success: boolean
  detail: string | null
}

export interface AuditLogResponse {
  count: number
  entries: AuditEntry[]
}

export const getAuditLog = (limit = 100) => apiGet<AuditLogResponse>('/audit-log', { limit })
