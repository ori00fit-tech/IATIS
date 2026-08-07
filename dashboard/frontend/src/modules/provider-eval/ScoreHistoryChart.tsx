import { useMemo, useState } from 'react'
import ReactEChartsCore from 'echarts-for-react/esm/core'
import { echarts, baseOption, CHART_COLORS } from '../../lib/echartsTheme'
import { Empty } from '../../components/Panel'
import type { ScoreHistoryRow } from './priceBenchmarkApi'

// Provider Benchmark Phase 1c — real longitudinal trend charts over
// storage/provider_benchmark.py's own score_history() aggregation: one
// point per (finished run, provider). Deferred from Phase 1 specifically
// because there was no accumulated run history yet to chart honestly —
// this component renders exactly what score_history() returns, real
// empty-state included, never a fabricated trend line.

type Metric = 'composite' | 'latency' | 'coverage'

const METRIC_LABELS: Record<Metric, string> = {
  composite: 'Composite Score',
  latency: 'Latency (ms)',
  coverage: 'Coverage (fetch success %)',
}

const LINE_COLORS = [CHART_COLORS.accent, CHART_COLORS.green, CHART_COLORS.red, '#7c5cfc', '#ffb020', '#f472b6', '#38bdf8']

function metricValue(row: ScoreHistoryRow, metric: Metric): number | null {
  if (metric === 'composite') return row.mean_composite_score
  if (metric === 'latency') return row.mean_latency_ms
  // coverage
  if (row.fetch_total_count === 0) return null
  return Math.round((row.fetch_ok_count / row.fetch_total_count) * 10000) / 100
}

export function ScoreHistoryChart({ history }: { history: ScoreHistoryRow[] }) {
  const [metric, setMetric] = useState<Metric>('composite')

  const { providers, runTimestamps, seriesData } = useMemo(() => {
    const providersSet = [...new Set(history.map((r) => r.provider))].sort()
    const timestamps = [...new Set(history.map((r) => r.created_at))].sort()
    const byProviderAndTs = new Map<string, ScoreHistoryRow>()
    for (const r of history) byProviderAndTs.set(`${r.provider}|${r.created_at}`, r)
    const data = providersSet.map((provider) =>
      timestamps.map((ts) => {
        const row = byProviderAndTs.get(`${provider}|${ts}`)
        return row ? metricValue(row, metric) : null
      }),
    )
    return { providers: providersSet, runTimestamps: timestamps, seriesData: data }
  }, [history, metric])

  if (history.length === 0) {
    return <Empty>No finished benchmark runs yet — run Smoke/Standard/Deep at least once to start a real trend.</Empty>
  }

  const option = {
    ...baseOption(),
    legend: { top: 0, textStyle: { color: CHART_COLORS.muted, fontSize: 11 } },
    grid: { left: 50, right: 20, top: 36, bottom: 40, containLabel: false },
    xAxis: {
      type: 'category',
      data: runTimestamps.map((ts) => {
        const d = new Date(ts)
        return Number.isNaN(d.getTime()) ? ts : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      }),
      axisLine: { lineStyle: { color: CHART_COLORS.border } },
      axisLabel: { color: CHART_COLORS.muted, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: CHART_COLORS.border } },
      splitLine: { lineStyle: { color: CHART_COLORS.border, opacity: 0.4 } },
      axisLabel: { color: CHART_COLORS.muted, fontSize: 10 },
    },
    series: providers.map((provider, i) => ({
      name: provider,
      type: 'line',
      data: seriesData[i],
      connectNulls: true,
      symbolSize: 6,
      lineStyle: { color: LINE_COLORS[i % LINE_COLORS.length], width: 2 },
      itemStyle: { color: LINE_COLORS[i % LINE_COLORS.length] },
    })),
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        {(Object.keys(METRIC_LABELS) as Metric[]).map((m) => (
          <button
            key={m}
            onClick={() => setMetric(m)}
            className={`px-2.5 py-1 rounded border text-[0.75em] ${
              metric === m ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:bg-surface'
            }`}
          >
            {METRIC_LABELS[m]}
          </button>
        ))}
      </div>
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 260 }} notMerge />
    </div>
  )
}
