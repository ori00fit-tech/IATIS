import type { ChartTrade } from './api'
import { Empty } from '../../components/Panel'

const BIN_WIDTH = 0.5

/**
 * R-multiple distribution for the Results page (2026-07-27) and Run
 * Comparison (2026-07-27). A new, small component rather than reusing
 * risk-center/RMultipleHistogram.tsx — that component is typed to
 * live-outcome OutcomeRow[] (a different domain/data source), and adapting
 * it would need a lossy field-mapping shim for no real benefit over this
 * correctly-typed alternative. Same hand-rolled div-bar idiom Risk
 * Center's own pre-ECharts histogram used, rather than pulling in a
 * charting library for one small panel.
 */
export function realizedR(t: ChartTrade): number | null {
  if (t.exit_price == null) return null
  const risk = Math.abs(t.entry_price - t.stop_loss)
  if (risk <= 0) return null
  const diff = t.direction === 'BUY' ? t.exit_price - t.entry_price : t.entry_price - t.exit_price
  return diff / risk
}

export function ReturnDistribution({ trades }: { trades: ChartTrade[] }) {
  const rs = trades.map(realizedR).filter((r): r is number => r != null)
  if (rs.length === 0) return <Empty>No closed trades with a computable R-multiple yet</Empty>

  const min = Math.min(...rs)
  const max = Math.max(...rs)
  const binStart = Math.floor(min / BIN_WIDTH) * BIN_WIDTH
  const binCount = Math.max(1, Math.ceil((max - binStart) / BIN_WIDTH))
  const counts = new Array(binCount).fill(0)
  for (const r of rs) {
    const idx = Math.min(binCount - 1, Math.floor((r - binStart) / BIN_WIDTH))
    counts[idx] += 1
  }
  const maxCount = Math.max(...counts, 1)
  const avg = rs.reduce((a, b) => a + b, 0) / rs.length

  return (
    <div className="p-4 flex flex-col gap-3">
      <div className="text-[0.82em] text-muted">
        n={rs.length} closed trades · avg <b className={avg >= 0 ? 'text-green' : 'text-red'}>{avg >= 0 ? '+' : ''}{avg.toFixed(2)}R</b>
      </div>
      <div className="flex items-end gap-1 h-40 overflow-x-auto">
        {counts.map((c, i) => {
          const lo = binStart + i * BIN_WIDTH
          const positive = lo >= 0
          return (
            <div key={i} className="flex flex-col items-center gap-1 shrink-0 w-6" title={`${lo >= 0 ? '+' : ''}${lo.toFixed(1)}R: ${c} trade(s)`}>
              <span className="text-[0.65em] text-muted">{c > 0 ? c : ''}</span>
              <div
                className={`w-full rounded-t ${positive ? 'bg-green/70' : 'bg-red/70'}`}
                style={{ height: `${Math.max(2, (c / maxCount) * 100)}px` }}
              />
              <span className="text-[0.6em] text-muted rotate-45 origin-top-left whitespace-nowrap">{lo >= 0 ? '+' : ''}{lo.toFixed(1)}R</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
