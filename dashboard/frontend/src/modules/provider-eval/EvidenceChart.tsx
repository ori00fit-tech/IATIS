import { useEffect, useRef } from 'react'
import {
  createChart, createSeriesMarkers, LineSeries,
  type IChartApi, type ISeriesApi, type UTCTimestamp,
} from 'lightweight-charts'
import { Panel, Empty } from '../../components/Panel'
import {
  buildEvidenceChartSeries, buildDeviationEvents, formatDeviationEvent,
  type ProviderEvidenceSeries,
} from './evidence'
import type { BenchmarkResultRow } from './priceBenchmarkApi'

// Phase 1b — Evidence drill-down. Kept strictly as a secondary, per-
// (symbol, timeframe) drill-down view, never the main decision surface:
// the Price Quality Benchmark panel's tables/scores stay primary. This
// overlays each provider's real close-price series (from the capped
// evidence_series the backend already computed) and marks every bar a
// provider deviated from the run's median consensus beyond tolerance.

const SERIES_COLORS = ['#00d4ff', '#7c5cfc', '#00e676', '#ff5252', '#ffb020', '#f472b6', '#38bdf8']

function toUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(Date.parse(iso) / 1000) as UTCTimestamp
}

function EvidenceOverlayChart({ series }: { series: ProviderEvidenceSeries[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRefs = useRef<Map<string, ISeriesApi<'Line'>>>(new Map())

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const chart = createChart(container, {
      height: 300,
      layout: { background: { color: 'transparent' }, textColor: '#64748b', fontFamily: 'monospace', fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: '#1a2236' }, horzLines: { color: '#1a2236' } },
      timeScale: { borderColor: '#1a2236', timeVisible: true },
      rightPriceScale: { borderColor: '#1a2236' },
    })
    chartRef.current = chart
    const resize = () => chart.applyOptions({ width: container.clientWidth })
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRefs.current = new Map()
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    // Rebuild series each time the drill-down target changes — simpler
    // and safe at this data scale (<=100 points x a handful of providers)
    // than diffing series in place.
    for (const s of seriesRefs.current.values()) chart.removeSeries(s)
    seriesRefs.current = new Map()

    series.forEach((s, i) => {
      const line = chart.addSeries(LineSeries, {
        color: SERIES_COLORS[i % SERIES_COLORS.length],
        lineWidth: 2,
        title: s.provider,
      })
      line.setData(s.points.map((p) => ({ time: toUtcSeconds(p.ts), value: p.close })))
      const markers = s.points
        .filter((p) => p.exceeds_tolerance)
        .map((p) => ({
          time: toUtcSeconds(p.ts),
          position: 'aboveBar' as const,
          shape: 'circle' as const,
          color: '#ff5252',
          text: '!',
        }))
      if (markers.length > 0) createSeriesMarkers(line, markers)
      seriesRefs.current.set(s.provider, line)
    })

    chart.timeScale().fitContent()
  }, [series])

  return <div ref={containerRef} className="w-full" />
}

export function EvidenceChart({
  symbol, timeframe, results, tolerancePct,
}: {
  symbol: string
  timeframe: string
  results: BenchmarkResultRow[]
  tolerancePct: number
}) {
  const series = buildEvidenceChartSeries(results, symbol, timeframe)
  const events = buildDeviationEvents(series, tolerancePct)

  return (
    <Panel
      title={`Evidence — ${symbol} · ${timeframe}`}
      right={`${series.length} provider(s) overlaid · drill-down only`}
    >
      <div className="p-4 flex flex-col gap-3">
        {series.length === 0 ? (
          <Empty>No evidence series recorded for this symbol/timeframe (run may predate Phase 1b, or every fetch failed).</Empty>
        ) : (
          <>
            <EvidenceOverlayChart series={series} />
            <div>
              <div className="text-[0.72em] text-muted uppercase tracking-[1px] mb-1.5">
                Deviation Events <span className="normal-case text-muted/70">(bars exceeding tolerance vs. median consensus)</span>
              </div>
              {events.length === 0 ? (
                <div className="text-[0.78em] text-green">No deviations detected in this window.</div>
              ) : (
                <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
                  {events.map((e, i) => (
                    <div
                      key={`${e.provider}-${e.ts}-${i}`}
                      className={`text-[0.75em] font-mono px-2 py-1 rounded border ${
                        e.severity === 'HIGH' ? 'border-red/40 text-red' : e.severity === 'MEDIUM' ? 'border-amber/40 text-amber' : 'border-border text-muted'
                      }`}
                    >
                      {formatDeviationEvent(e)}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </Panel>
  )
}
