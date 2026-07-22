import { apiGet, apiPost } from '../../lib/api'

// One journal entry — the raw outcomes row plus every derived field the
// backend recomputes from prices (storage/journal.py). The stored pnl_*
// columns are deliberately not trusted client-side either.
export interface JournalTrade {
  signal_id: string
  symbol: string
  direction: string
  outcome: 'win' | 'loss' | 'breakeven' | 'open'
  entry_price: number | null
  stop_loss: number | null
  take_profit: number | null
  exit_price: number | null
  entry_time: string
  exit_time: string | null
  cf_score: number | null
  regime: string | null
  news_risk: number | null
  notes: string | null
  engines: Record<string, { bias: string; score: number }>
  realized_r: number | null
  planned_rr: number | null
  pnl_pips_clean: number | null
  duration_hours: number | null
  tags: string[]
}

export interface JournalListing {
  total: number
  returned: number
  offset: number
  trades: JournalTrade[]
}

export interface JournalBucket {
  n: number
  wins: number
  win_rate: number | null
  total_r: number | null
  avg_r: number | null
  symbol?: string
  regime?: string
  direction?: string
}

export interface EquityPoint {
  signal_id: string
  exit_time: string
  r: number
  cum_r: number
}

export interface JournalStats {
  total: number
  closed: number
  open: number
  wins: number
  losses: number
  breakeven: number
  win_rate: number | null
  total_r: number | null
  avg_r: number | null
  profit_factor: number | 'Infinity' | null
  max_drawdown_r: number | null
  longest_win_streak: number
  longest_loss_streak: number
  avg_duration_hours: number | null
  best_trade: EquityPoint | null
  worst_trade: EquityPoint | null
  equity_curve: EquityPoint[]
  by_symbol: JournalBucket[]
  by_regime: JournalBucket[]
  by_direction: JournalBucket[]
}

export interface JournalFilters {
  symbol?: string
  outcome?: string
  direction?: string
  regime?: string
  search?: string
  limit?: number
  offset?: number
}

export const getJournal = (filters: JournalFilters) =>
  apiGet<JournalListing>('/journal', {
    symbol: filters.symbol || undefined,
    outcome: filters.outcome || undefined,
    direction: filters.direction || undefined,
    regime: filters.regime || undefined,
    search: filters.search || undefined,
    limit: filters.limit,
    offset: filters.offset,
  })

export const getJournalStats = () => apiGet<JournalStats>('/journal/stats')

export const annotateTrade = (signalId: string, body: { notes?: string; tags?: string[] }) =>
  apiPost<{ success: boolean }>(`/journal/${encodeURIComponent(signalId)}/annotate`, body)

/** The CSV export is a plain authenticated GET — used as a download link. */
export const JOURNAL_EXPORT_URL = '/journal/export'
