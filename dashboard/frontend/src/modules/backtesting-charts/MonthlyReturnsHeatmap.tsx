import ReactEChartsCore from 'echarts-for-react/esm/core'
import { echarts, baseOption, CHART_COLORS } from '../../lib/echartsTheme'
import { Empty } from '../../components/Panel'

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * Monthly PnL calendar heatmap (Phase 3 institutional redesign) — real,
 * already-computed data (backtest/metrics.py's monthly_returns, $ PnL per
 * "YYYY-MM" key), shipped in every chart_data.json since it was added but
 * never rendered by any component until now. Dollar amounts, not a
 * fabricated "% return" the backend doesn't actually provide.
 */
export function MonthlyReturnsHeatmap({ monthlyReturns }: { monthlyReturns: Record<string, number> }) {
  const entries = Object.entries(monthlyReturns)
  if (entries.length === 0) return <Empty>No monthly PnL data for this run</Empty>

  const parsed = entries
    .map(([key, value]) => {
      const [yearStr, monthStr] = key.split('-')
      const year = Number(yearStr)
      const month = Number(monthStr) - 1 // 0-indexed for MONTH_LABELS
      return Number.isFinite(year) && month >= 0 && month <= 11 ? { year, month, value } : null
    })
    .filter((x): x is { year: number; month: number; value: number } => x != null)

  if (parsed.length === 0) return <Empty>No monthly PnL data for this run</Empty>

  const years = Array.from(new Set(parsed.map((p) => p.year))).sort((a, b) => a - b)
  const data = parsed.map((p) => [p.month, years.indexOf(p.year), Math.round(p.value * 100) / 100])
  const absMax = Math.max(...parsed.map((p) => Math.abs(p.value)), 1)

  const option = {
    ...baseOption(),
    grid: { left: 60, right: 20, top: 10, bottom: 60, containLabel: false },
    xAxis: {
      type: 'category',
      data: MONTH_LABELS,
      axisLine: { lineStyle: { color: CHART_COLORS.border } },
      splitArea: { show: true },
    },
    yAxis: {
      type: 'category',
      data: years.map(String),
      axisLine: { lineStyle: { color: CHART_COLORS.border } },
      splitArea: { show: true },
    },
    visualMap: {
      type: 'continuous',
      min: -absMax,
      max: absMax,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      textStyle: { color: CHART_COLORS.muted },
      inRange: { color: [CHART_COLORS.red, '#1a2236', CHART_COLORS.green] },
    },
    series: [
      {
        type: 'heatmap',
        data,
        label: {
          show: true,
          color: CHART_COLORS.text,
          fontSize: 10,
          formatter: (p: { data: [number, number, number] }) => (p.data[2] >= 0 ? '+' : '') + p.data[2].toFixed(0),
        },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.5)' } },
      },
    ],
    tooltip: {
      ...baseOption().tooltip,
      formatter: (p: { data: [number, number, number] }) =>
        `${MONTH_LABELS[p.data[0]]} ${years[p.data[1]]}<br/>PnL: ${p.data[2] >= 0 ? '+' : ''}$${p.data[2].toFixed(2)}`,
    },
  }

  return <ReactEChartsCore echarts={echarts} option={option} style={{ height: 220 + years.length * 8 }} notMerge />
}
