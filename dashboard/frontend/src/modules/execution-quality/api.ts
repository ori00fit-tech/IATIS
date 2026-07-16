import { apiGet } from '../../lib/api'

// TCA ledger (storage/execution_quality.py, gap analysis M1): realized
// intended-vs-fill slippage per real broker fill, in the BACKTEST's pip
// units so every number here is directly comparable to the simulator's
// slippage_pips assumption. Adverse-positive: +2.0 = paid 2 pips worse
// than the decision sized against; negative = price improvement.
export interface SlippageBucket {
  n: number
  mean_slippage_pips?: number
  median_slippage_pips?: number
  p90_slippage_pips?: number
  worst_slippage_pips?: number
  best_slippage_pips?: number
  mean_slippage_r?: number | null
}

export interface RecentFill {
  ts: string
  symbol: string
  direction: string
  session: string | null
  intended_price: number
  fill_price: number
  slippage_pips: number
  slippage_r: number | null
  trade_id: string | null
}

export interface ExecutionQualityReport {
  backtest_assumption_pips: number
  overall: SlippageBucket
  by_symbol: Record<string, SlippageBucket>
  by_session: Record<string, SlippageBucket>
  recent: RecentFill[]
  note: string
}

export function getExecutionQuality(): Promise<ExecutionQualityReport> {
  return apiGet<ExecutionQualityReport>('/execution-quality')
}

export function fmtPips(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}`
}
