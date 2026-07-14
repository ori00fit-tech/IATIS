import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { CandleBar, ChartSignal } from '../modules/live-signals/api'

export function PriceChart({
  bars,
  signal,
  height = 360,
}: {
  bars: CandleBar[]
  signal?: ChartSignal | null
  height?: number
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  // Chart + series are created once; bars/signal are pushed into them via
  // separate effects below rather than recreating the chart on every poll.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const chart = createChart(container, {
      height,
      layout: { background: { color: 'transparent' }, textColor: '#64748b' },
      grid: {
        vertLines: { color: '#1a2236' },
        horzLines: { color: '#1a2236' },
      },
      timeScale: { borderColor: '#1a2236', timeVisible: true },
      rightPriceScale: { borderColor: '#1a2236' },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#00e676',
      downColor: '#ff5252',
      borderUpColor: '#00e676',
      borderDownColor: '#ff5252',
      wickUpColor: '#00e676',
      wickDownColor: '#ff5252',
    })
    chartRef.current = chart
    seriesRef.current = series

    const resize = () => chart.applyOptions({ width: container.clientWidth })
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(container)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    series.setData(bars.map((b) => ({ time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })))
    chartRef.current?.timeScale().fitContent()
  }, [bars])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    const lines: IPriceLine[] = []
    if (signal?.entry_price) {
      lines.push(
        series.createPriceLine({
          price: signal.entry_price,
          color: '#00d4ff',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'Entry',
        }),
      )
    }
    if (signal?.stop_loss) {
      lines.push(
        series.createPriceLine({
          price: signal.stop_loss,
          color: '#ff5252',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'SL',
        }),
      )
    }
    if (signal?.take_profit) {
      lines.push(
        series.createPriceLine({
          price: signal.take_profit,
          color: '#00e676',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'TP',
        }),
      )
    }
    // Price lines have no update-in-place API — drop the previous run's
    // lines before this run's effect adds the new ones.
    return () => {
      lines.forEach((line) => series.removePriceLine(line))
    }
  }, [signal])

  return <div ref={containerRef} className="w-full" />
}
