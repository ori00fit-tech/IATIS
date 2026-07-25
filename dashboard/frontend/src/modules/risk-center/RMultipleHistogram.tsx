import ReactEChartsCore from 'echarts-for-react/esm/core'
import { echarts, baseOption, CHART_COLORS } from '../../lib/echartsTheme'
import { Empty } from '../../components/Panel'
import { realizedR } from './RiskCenter'
import type { OutcomeRow } from './api'

const BIN_WIDTH = 0.5

/**
 * Real R-multiple histogram (Phase 3 institutional redesign) — replaces the
 * old fixed 6-bucket div-bar chart. Same realizedR() formula as the rest of
 * Risk Center (imported, not duplicated, so this panel and the compliance
 * checks can never silently disagree), but binned at a real 0.5R resolution
 * spanning the actual data range instead of 6 hardcoded thresholds.
 */
export function RMultipleHistogram({ rows }: { rows: OutcomeRow[] }) {
  const rs = rows
    .filter((r) => r.outcome !== 'open')
    .map(realizedR)
    .filter((r): r is number => r != null)

  if (rs.length === 0) return <Empty>No closed trades with a computable R-multiple yet</Empty>

  const avg = rs.reduce((a, b) => a + b, 0) / rs.length
  const min = Math.min(...rs)
  const max = Math.max(...rs)
  const binStart = Math.floor(min / BIN_WIDTH) * BIN_WIDTH
  const binCount = Math.max(1, Math.ceil((max - binStart) / BIN_WIDTH))

  const counts = new Array(binCount).fill(0)
  for (const r of rs) {
    const idx = Math.min(binCount - 1, Math.floor((r - binStart) / BIN_WIDTH))
    counts[idx] += 1
  }
  const labels = counts.map((_, i) => {
    const lo = binStart + i * BIN_WIDTH
    return `${lo >= 0 ? '+' : ''}${lo.toFixed(1)}R`
  })

  const option = {
    ...baseOption(),
    grid: { left: 40, right: 16, top: 10, bottom: 40, containLabel: false },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: CHART_COLORS.muted, rotate: 45, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_COLORS.border } },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: CHART_COLORS.muted },
      splitLine: { lineStyle: { color: CHART_COLORS.border, opacity: 0.4 } },
    },
    series: [
      {
        type: 'bar',
        data: counts.map((c, i) => ({
          value: c,
          itemStyle: { color: binStart + i * BIN_WIDTH >= 0 ? CHART_COLORS.green : CHART_COLORS.red },
        })),
        barWidth: '70%',
      },
    ],
    tooltip: {
      ...baseOption().tooltip,
      formatter: (p: { name: string; value: number }[] | { name: string; value: number }) => {
        const point = Array.isArray(p) ? p[0] : p
        return `${point.name} to +${BIN_WIDTH}R<br/>${point.value} trade${point.value === 1 ? '' : 's'}`
      },
    },
  }

  return (
    <div className="p-4 flex flex-col gap-2">
      <div className="flex items-baseline gap-3">
        <span className="text-[0.78em] text-muted">n = {rs.length} closed</span>
        <span className="text-[0.82em]">
          mean{' '}
          <b className={avg >= 0 ? 'text-green' : 'text-red'}>
            {avg >= 0 ? '+' : ''}
            {avg.toFixed(2)}R
          </b>
        </span>
      </div>
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 240 }} notMerge />
    </div>
  )
}
