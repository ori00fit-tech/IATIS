import { apiGet } from '../../lib/api'

export interface Health {
  status: string
  version: string
  timestamp: string
  twelve_data_credits_remaining: number | null
}

export interface HealthFull {
  status: 'healthy' | 'degraded'
  issues: string[]
  checked_at: string
  system?: { cpu_pct: number; ram_pct: number; disk_pct: number; uptime_hours: number; error?: string }
  scheduler?: { last_run: string | null; last_execute_count: number; status: string }
  database?: { status: string; total_decisions?: number; last_24h?: number; error?: string }
  calendar?: { status: string; fetched_at?: string; event_count?: number; note?: string }
  outcome_tracker?: { status: string; total_closed?: number; win_rate?: number; open_signals?: number }
  data_providers?: Record<string, string>
  ctrader?: { configured: boolean; account_id: string; environment: string }
}

export interface Budget {
  max_per_day: number
  used_today: number
  remaining_today: number
  percent_used: number
}

export interface SymbolHealthEntry {
  symbol: string
  shi_score: number
  status: 'HEALTHY' | 'CAUTION' | 'PAUSED'
  win_rate: number | null
  profit_factor: number | null
  trades_count: number
  consecutive_losses: number
  position_multiplier: number
  last_updated: string
  reason: string
}

export interface SymbolHealthResponse {
  total: number
  healthy: number
  caution: number
  paused: number
  symbols: SymbolHealthEntry[]
}

export const getHealth = () => apiGet<Health>('/health')
export const getHealthFull = () => apiGet<HealthFull>('/health/full')
export const getBudget = () => apiGet<Budget>('/budget')
export const getSymbolHealth = () => apiGet<SymbolHealthResponse>('/symbol-health')
