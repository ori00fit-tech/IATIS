import { apiGet } from '../../lib/api'

// Risk Center reads only what other endpoints already compute — it is a
// monitoring surface, never a gate and never a writer. Exposure comes from
// /health/full (an explicit upper-bound estimate), realized risk from
// /outcomes (raw rows, R recomputed client-side with the same formula the
// backend uses), and per-symbol risk scaling from /symbol-health.
export {
  getHealthFull,
  getSymbolHealth,
  getReconciliation,
  type HealthFull,
  type SymbolHealthEntry,
  type SymbolHealthResponse,
  type ReconciliationResult,
} from '../mission-control/api'

/** One row of the outcomes table (storage/outcome_tracker.py schema). */
export interface OutcomeRow {
  signal_id: string
  symbol: string
  direction: string
  entry_price: number | null
  stop_loss: number | null
  take_profit: number | null
  entry_time: string
  exit_time: string | null
  exit_price: number | null
  outcome: string // 'open' | 'win' | 'loss' | 'breakeven' | …
  pnl_pips: number | null
  pnl_usd: number | null
  cf_score: number | null
  regime: string | null
  news_risk: number | null
  engines: string | null
  notes: string | null
}

export interface RiskSummary {
  total_closed: number
  wins: number
  losses: number
  win_rate: number
  total_pips: number
  // "Infinity" (string sentinel) when there are zero losing trades — a bare
  // Infinity token is invalid JSON, so the backend sends the string.
  profit_factor: number | 'Infinity' | null
  avg_r_multiple: number | null
  open_signals: number
  calibration: { bucket: string; n: number; wins: number }[]
  by_regime: { regime: string; n: number; wins: number; avg_pips: number | null }[]
  note: string
}

export interface OutcomesFull {
  summary: RiskSummary
  open_signals: OutcomeRow[]
  recent: OutcomeRow[]
}

// limit=200 so the realized-R distribution and per-symbol rollups have a
// meaningful window; the summary block itself is always whole-book.
export const getOutcomesFull = () => apiGet<OutcomesFull>('/outcomes', { limit: 200 })
