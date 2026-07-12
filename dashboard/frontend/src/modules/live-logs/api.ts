import { apiGet } from '../../lib/api'

export interface LogSource {
  id: string
  label: string
  kind: 'file' | 'journal'
}

export interface LogSourcesResponse {
  sources: LogSource[]
}

export interface LogsResponse {
  source: string
  lines_requested: number
  lines_returned: number
  search: string | null
  entries: string[]
  error: string | null
}

export const getLogSources = () => apiGet<LogSourcesResponse>('/logs/sources')

export const getLogs = (source: string, lines = 200, search?: string) =>
  apiGet<LogsResponse>('/logs', { source, lines, search: search || undefined })
