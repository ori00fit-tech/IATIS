import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, createSeriesMarkers, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts'
import { useApiQuery } from '../../lib/useApiQuery'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { DataTable, type Column } from '../../components/DataTable'
import { AiResearchAssistant } from '../../components/AiResearchAssistant'
import {
  getBacktestResults,
  getOutcomesCalibration,
  getRunReports,
  getChartDataFile,
  getRobustnessReportFile,
  formatPossiblyInfinite,
  type BacktestRun,
  type CalibrationBucket,
  type RunReportEntry,
  type ChartDataFile,
  type ChartTrade,
  type ChartCandle,
  type RobustnessReportFile,
  type RobustnessSweep,
} from './api'

const POLL_MS = 60_000

type Metric = 'profit_factor' | 'total_return_pct' | 'win_rate' | 'max_drawdown_pct'
const METRICS: { id: Metric; label: string; unit: string; goodHigh: boolean }[] = [
  { id: 'profit_factor', label: 'Profit Factor', unit: '', goodHigh: true },
  { id: 'total_return_pct', label: 'Return %', unit: '%', goodHigh: true },
  { id: 'win_rate', label: 'Win Rate', unit: '%', goodHigh: true },
  { id: 'max_drawdown_pct', label: 'Max Drawdown', unit: '%', goodHigh: false },
]

function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

// ── Per-symbol metric comparison ──────────────────────────────────────────
function MetricComparison({ runs }: { runs: BacktestRun[] }) {
  const [metric, setMetric] = useState<Metric>('profit_factor')
  const meta = METRICS.find((m) => m.id === metric)!
  // One bar per symbol: /backtest-results returns up to 3 result files, so a
  // symbol re-run in a newer file appeared twice (EURUSD 1.17 and 1.27 side
  // by side, unlabeled). Files arrive newest-first — keep the first (newest).
  const seen = new Set<string>()
  const rows = runs
    .map((r) => ({ symbol: r.symbol, file: r.file, value: num(r[metric]) }))
    .filter((r): r is { symbol: string; file: string; value: number } => r.value !== undefined)
    .filter((r) => (seen.has(r.symbol) ? false : (seen.add(r.symbol), true)))
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), meta.id === 'profit_factor' ? 2 : 1)

  const color = (v: number): string => {
    if (metric === 'profit_factor') return v >= 1.5 ? 'bg-green' : v >= 1.0 ? 'bg-amber' : 'bg-red'
    if (metric === 'max_drawdown_pct') return v >= 25 ? 'bg-red' : v >= 12 ? 'bg-amber' : 'bg-green'
    if (metric === 'total_return_pct') return v >= 0 ? 'bg-green' : 'bg-red'
    return v >= 50 ? 'bg-green' : 'bg-amber'
  }

  return (
    <Panel
      title="Per-Symbol Comparison"
      right={
        <div className="flex gap-1">
          {METRICS.map((m) => (
            <button
              key={m.id}
              onClick={() => setMetric(m.id)}
              className={`px-2 py-0.5 rounded text-[0.9em] ${metric === m.id ? 'text-accent border border-accent/50' : 'text-muted border border-transparent hover:text-text'}`}
            >
              {m.label}
            </button>
          ))}
        </div>
      }
    >
      {rows.length === 0 ? (
        <Empty>No comparable runs for this metric</Empty>
      ) : (
        <div className="p-4 flex flex-col gap-2">
          {rows.map((r) => (
            <div key={`${r.file}-${r.symbol}`} className="flex items-center gap-2 text-[0.8em]">
              <span className="w-20 shrink-0 font-bold text-accent">{r.symbol}</span>
              <div className="flex-1 h-4 bg-surface rounded overflow-hidden">
                <div className={`h-full ${color(r.value)}`} style={{ width: `${Math.min(100, (Math.abs(r.value) / max) * 100)}%` }} />
              </div>
              <span className="w-16 text-right tabular-nums shrink-0">
                {metric === 'profit_factor' ? r.value.toFixed(2) : `${r.value.toFixed(1)}${meta.unit}`}
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}

// ── Equity curve (SVG, index-based x — the legacy files carry no timestamps) ─
function drawdownSeries(curve: number[]): { maxDD: number; ddAt: number } {
  let peak = curve[0] ?? 0
  let maxDD = 0
  let ddAt = 0
  curve.forEach((v, i) => {
    if (v > peak) peak = v
    const dd = peak > 0 ? (peak - v) / peak : 0
    if (dd > maxDD) {
      maxDD = dd
      ddAt = i
    }
  })
  return { maxDD: maxDD * 100, ddAt }
}

function EquityCurve({ run }: { run: BacktestRun }) {
  const curve = run.equity_curve ?? []
  if (curve.length < 2) {
    return (
      <Empty>
        No per-bar equity series for {run.symbol} — pipeline backtests store summary metrics only. Equity curves appear
        for legacy per-bar runs (backtest_engine.save).
      </Empty>
    )
  }
  return <EquityCurveSvg curve={curve} />
}

function EquityCurveSvg({ curve }: { curve: number[] }) {
  const { maxDD, ddAt } = drawdownSeries(curve) // ≤500 points, cheap enough to run inline
  const W = 800
  const H = 240
  const min = Math.min(...curve)
  const max = Math.max(...curve)
  const range = max - min || 1
  const x = (i: number) => (i / (curve.length - 1)) * W
  const y = (v: number) => H - ((v - min) / range) * H
  const linePath = curve.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const areaPath = `${linePath} L${W},${H} L0,${H} Z`
  const start = curve[0]
  const end = curve[curve.length - 1]
  const ret = ((end - start) / start) * 100
  const up = end >= start

  return (
    <div className="p-4 flex flex-col gap-3">
      <div className="flex items-baseline gap-4 flex-wrap text-[0.82em]">
        <span>
          Return <b className={up ? 'text-green' : 'text-red'}>{ret >= 0 ? '+' : ''}{ret.toFixed(1)}%</b>
        </span>
        <span>
          Max Drawdown <b className="text-red">−{maxDD.toFixed(1)}%</b>
        </span>
        <span className="text-muted">{curve.length} points · start {start.toFixed(0)} → end {end.toFixed(0)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-[240px]">
        <defs>
          <linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={up ? 'var(--green)' : 'var(--red)'} stopOpacity="0.28" />
            <stop offset="100%" stopColor={up ? 'var(--green)' : 'var(--red)'} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill="url(#eqfill)" />
        <path d={linePath} fill="none" stroke={up ? 'var(--green)' : 'var(--red)'} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        {/* max-drawdown marker */}
        <line x1={x(ddAt)} y1="0" x2={x(ddAt)} y2={H} stroke="var(--red)" strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" opacity="0.6" />
      </svg>
    </div>
  )
}

// ── Score calibration (predicted score bucket vs realized win rate) ─────────
function CalibrationChart({ buckets }: { buckets: CalibrationBucket[] }) {
  const rows = buckets.filter((b) => b.n > 0)
  if (rows.length === 0) return <Empty>No calibration data yet — needs closed trades with recorded scores</Empty>
  const midpoint: Record<string, number> = { '55-60': 57.5, '60-70': 65, '70-80': 75, '80-90': 85, '90-100': 95 }
  return (
    <div className="p-4 flex flex-col gap-3">
      <p className="text-[0.75em] text-muted">
        Each confluence-score bucket vs the win rate it actually delivered. A well-calibrated score tracks the dotted
        expectation; large gaps mean the score isn't pricing risk the way the backtest implied.
      </p>
      {rows.map((b) => {
        const actual = (b.wins / b.n) * 100
        const expected = midpoint[b.bucket] ?? actual
        return (
          <div key={b.bucket} className="flex items-center gap-2 text-[0.8em]">
            <span className="w-16 shrink-0 text-muted">{b.bucket}</span>
            <div className="relative flex-1 h-4 bg-surface rounded overflow-hidden">
              <div className={`h-full ${actual >= expected - 5 ? 'bg-green/70' : 'bg-amber/70'}`} style={{ width: `${Math.min(100, actual)}%` }} />
              <div className="absolute top-0 bottom-0 w-px bg-muted" style={{ left: `${Math.min(100, expected)}%` }} title={`expected ≈ ${expected}%`} />
            </div>
            <span className="w-24 text-right tabular-nums shrink-0">
              {actual.toFixed(0)}% <span className="text-muted">/ {expected}%</span>
            </span>
            <span className="w-12 text-right text-muted shrink-0">n={b.n}</span>
          </div>
        )
      })}
    </div>
  )
}

// ── Queue Manager runs (Phase 5/6, 2026-07-24) — real backtest.runner /
// backtest.walk_forward / backtest.robustness job output from the
// Experiment Runner tab, distinct from the legacy /backtest-results
// pipeline scans above. Verdicts here are the ones those modules'
// docstrings define: STABLE/SENSITIVE/INSUFFICIENT (robustness) and
// CONSISTENT/INCONSISTENT/INSUFFICIENT (walk-forward) — neither is
// evidence of edge either; see the Forward Demo tab for that.
const VERDICT_TONE: Record<string, 'exec' | 'no-trade' | 'marginal' | 'neutral'> = {
  STABLE: 'exec',
  CONSISTENT: 'exec',
  SENSITIVE: 'no-trade',
  INCONSISTENT: 'no-trade',
  INSUFFICIENT: 'marginal',
}

function VerdictBadge({ verdict }: { verdict: string }) {
  return <Badge tone={VERDICT_TONE[verdict] ?? 'neutral'}>{verdict}</Badge>
}

// Full per-point sweep table (Parameter Sweep, 2026-07-25, Backtesting
// Lab priority #4) — every measured point, sorted by multiplier. NO
// "winner" row, no highlighted best cell: the operator reads the numbers
// and decides, same guardrail agreed for this feature (backtest/
// robustness.py's own module docstring: STABLE/SENSITIVE/INSUFFICIENT
// verdicts only, never an auto-selected value).
function SweepTable({ sweep }: { sweep: RobustnessSweep }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-bold text-accent">{sweep.param}</span>
        <VerdictBadge verdict={sweep.verdict} />
        <span className="text-muted text-[0.78em]">baseline value {sweep.baseline_value} → PF {formatPossiblyInfinite(sweep.baseline_pf)}</span>
      </div>
      <table className="w-full text-[0.78em] border-collapse">
        <thead>
          <tr className="text-muted uppercase text-[0.72em] tracking-[0.5px]">
            <th className="text-left py-1 px-2">Multiplier</th>
            <th className="text-right py-1 px-2">Value</th>
            <th className="text-right py-1 px-2">Trades</th>
            <th className="text-right py-1 px-2">PF</th>
            <th className="text-right py-1 px-2">WR%</th>
            <th className="text-right py-1 px-2">Max DD%</th>
          </tr>
        </thead>
        <tbody>
          {sweep.points.map((p) => (
            <tr key={p.multiplier} className={p.multiplier === 1.0 ? 'bg-accent/[0.06]' : ''}>
              <td className="py-1 px-2 font-mono">{p.multiplier.toFixed(2)}{p.multiplier === 1.0 && <span className="text-muted"> (baseline)</span>}</td>
              <td className="py-1 px-2 text-right font-mono">{p.value}</td>
              <td className="py-1 px-2 text-right">{p.trades}</td>
              <td className={`py-1 px-2 text-right font-mono ${!p.sufficient ? 'text-muted' : ''}`}>{formatPossiblyInfinite(p.profit_factor)}</td>
              <td className="py-1 px-2 text-right">{p.win_rate.toFixed(1)}%</td>
              <td className="py-1 px-2 text-right text-red">{p.max_drawdown_pct.toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      {sweep.points.some((p) => !p.sufficient) && (
        <span className="text-[0.72em] text-muted">Greyed PF values had fewer trades than the run's min_trades floor — not enough to judge.</span>
      )}
    </div>
  )
}

function RobustnessDetail({ file }: { file: string }) {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: RobustnessReportFile | null }>({
    loading: true, error: null, data: null,
  })

  useEffect(() => {
    let cancelled = false
    getRobustnessReportFile(file)
      .then((data) => !cancelled && setState({ loading: false, error: null, data }))
      .catch((err) => !cancelled && setState({ loading: false, error: err instanceof Error ? err.message : String(err), data: null }))
    return () => {
      cancelled = true
    }
  }, [file])

  if (state.loading) return <Empty>Loading full sweep…</Empty>
  if (state.error) return <Empty>Failed: {state.error}</Empty>
  if (!state.data) return null

  return (
    <div className="flex flex-col gap-4 pt-2">
      {Object.entries(state.data.symbols).map(([symbol, result]) => (
        <div key={symbol} className="flex flex-col gap-2">
          <span className="font-bold text-text">{symbol}</span>
          {result.sweeps.map((sweep) => (
            <SweepTable key={sweep.param} sweep={sweep} />
          ))}
        </div>
      ))}
      <AiResearchAssistant
        context={state.data}
        examples={['Which parameter is least stable?', 'Is the baseline value the best choice here?', 'Summarize the sweep verdicts.']}
      />
    </div>
  )
}

function WalkForwardRobustnessPanel({ entry }: { entry: RunReportEntry }) {
  const title = entry.kind === 'walk_forward' ? 'Walk-Forward' : 'Robustness'
  const highlights = entry.highlights as Record<string, unknown>
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="p-4 flex flex-col gap-2 text-[0.82em]">
      <div className="flex items-center gap-3 flex-wrap text-muted">
        <span className="font-bold text-text">{title}</span>
        <span>{entry.file}</span>
        {entry.evaluated != null && <span>{entry.evaluated} symbols evaluated</span>}
        {entry.generated_utc && <span>{entry.generated_utc}</span>}
        {entry.kind === 'robustness' && (
          <button onClick={() => setExpanded((v) => !v)} className="text-accent hover:text-accent2 underline decoration-dotted ml-auto">
            {expanded ? 'Hide full sweep' : 'View full sweep'}
          </button>
        )}
      </div>
      <div className="flex flex-col gap-1.5">
        {Object.entries(highlights).map(([symbol, verdict]) => (
          <div key={symbol} className="flex items-center gap-2 flex-wrap">
            <span className="w-20 shrink-0 font-bold text-accent">{symbol}</span>
            {typeof verdict === 'string' ? (
              <VerdictBadge verdict={verdict} />
            ) : verdict && typeof verdict === 'object' ? (
              Object.entries(verdict as Record<string, string>).map(([param, v]) => (
                <span key={param} className="flex items-center gap-1">
                  <span className="text-muted text-[0.85em]">{param}</span>
                  <VerdictBadge verdict={v} />
                </span>
              ))
            ) : (
              <span className="text-muted">—</span>
            )}
          </div>
        ))}
      </div>
      {expanded && entry.kind === 'robustness' && <RobustnessDetail file={entry.file} />}
    </div>
  )
}

// ── Real candlestick chart + trade markers + decision panel ─────────────
// (Interactive Chart, 2026-07-25, Backtesting Lab priority #2) — real
// OHLC from backtest/report.py's chart_data sidecar (downsampled to
// <=500 bars server-side), real entry/exit markers, real per-engine
// decision snapshot on click (backtesting.backtest_engine.py attaches it
// at entry time — nothing here is synthesized client-side).

function nearestCandleTime(candles: { time: number }[], target: number): number {
  // Candle series may be downsampled server-side (stride sampling), so a
  // trade's exact entry/exit second may not exist as a bar — markers must
  // anchor to a REAL bar or lightweight-charts silently drops them.
  let best = candles[0]?.time ?? target
  let bestDiff = Infinity
  for (const c of candles) {
    const diff = Math.abs(c.time - target)
    if (diff < bestDiff) {
      bestDiff = diff
      best = c.time
    }
  }
  return best
}

function CandlestickTradeChart({ candles, trades, onSelectTrade }: { candles: ChartCandle[]; trades: ChartTrade[]; onSelectTrade: (t: ChartTrade) => void }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      height: 340,
      layout: { background: { color: 'transparent' }, textColor: '#64748b', fontFamily: 'monospace', fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: '#1a2236' }, horzLines: { color: '#1a2236' } },
      timeScale: { borderColor: '#1a2236', timeVisible: true },
      rightPriceScale: { borderColor: '#1a2236' },
      crosshair: { mode: 0 },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#00e676', downColor: '#ff5252', borderVisible: false,
      wickUpColor: '#00e676', wickDownColor: '#ff5252',
    })
    chartRef.current = chart
    seriesRef.current = series

    const resize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    }
    resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    const series = seriesRef.current
    if (!series || candles.length === 0) return
    series.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })))

    type Marker = {
      time: UTCTimestamp
      position: 'belowBar' | 'aboveBar' | 'inBar'
      shape: 'arrowUp' | 'arrowDown' | 'circle'
      color: string
      text?: string
      id: string
    }

    const markers: Marker[] = trades
      .filter((t) => t.entry_time != null)
      .flatMap((t): Marker[] => {
        const entryTime = nearestCandleTime(candles, t.entry_time!)
        const isBuy = t.direction === 'BUY'
        const out: Marker[] = [
          {
            time: entryTime as UTCTimestamp,
            position: isBuy ? 'belowBar' : 'aboveBar',
            shape: isBuy ? 'arrowUp' : 'arrowDown',
            color: isBuy ? '#00d4ff' : '#7c5cfc',
            text: t.cf_score != null ? t.cf_score.toFixed(0) : undefined,
            id: `${t.trade_id}-entry`,
          },
        ]
        if (t.exit_time != null) {
          const exitTime = nearestCandleTime(candles, t.exit_time)
          out.push({
            time: exitTime as UTCTimestamp,
            position: 'inBar',
            shape: 'circle',
            color: t.is_win ? '#00e676' : '#ff5252',
            id: `${t.trade_id}-exit`,
          })
        }
        return out
      })
      .sort((a, b) => (a.time as number) - (b.time as number))

    const markersApi = createSeriesMarkers(series, markers)
    return () => {
      markersApi.detach()
    }
  }, [candles, trades])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const handler: Parameters<IChartApi['subscribeClick']>[0] = (param) => {
      // Candle times are always plain unix-second numbers (UTCTimestamp) in
      // this chart — never the BusinessDay/string variants of lightweight-
      // charts' broader Time union — so a numeric guard is exact here.
      if (typeof param.time !== 'number') return
      const clickedTime = param.time
      // Nearest trade by entry or exit time within one bar's tolerance.
      let closest: ChartTrade | null = null
      let closestDiff = Infinity
      for (const t of trades) {
        for (const ts of [t.entry_time, t.exit_time]) {
          if (ts == null) continue
          const diff = Math.abs(ts - clickedTime)
          if (diff < closestDiff) {
            closestDiff = diff
            closest = t
          }
        }
      }
      if (closest) onSelectTrade(closest)
    }
    chart.subscribeClick(handler)
    return () => chart.unsubscribeClick(handler)
  }, [trades, onSelectTrade])

  return <div ref={containerRef} className="px-4 pb-2" />
}

function TradeDecisionPanel({ trade, onClose }: { trade: ChartTrade; onClose: () => void }) {
  const votes = trade.engine_votes ? Object.values(trade.engine_votes) : []
  return (
    <Panel
      title={`Decision — ${trade.trade_id}`}
      right={<button onClick={onClose} className="text-muted hover:text-text">✕ close</button>}
    >
      <div className="p-4 flex flex-col gap-3 text-[0.85em]">
        <div className="flex items-center gap-3 flex-wrap">
          <Badge tone={trade.direction === 'BUY' ? 'exec' : 'no-trade'}>{trade.direction}</Badge>
          <span>entry <b className="text-text">{trade.entry_price.toFixed(5)}</b></span>
          {trade.exit_price != null && <span>exit <b className="text-text">{trade.exit_price.toFixed(5)}</b></span>}
          <span className="text-red">SL {trade.stop_loss.toFixed(5)}</span>
          <span className="text-green">TP {trade.take_profit.toFixed(5)}</span>
          <span className={trade.is_win ? 'text-green' : 'text-red'}>{trade.pnl_usd >= 0 ? '+' : ''}{trade.pnl_usd.toFixed(2)} ({trade.exit_reason})</span>
        </div>
        <div className="flex items-center gap-3 flex-wrap text-muted">
          {trade.regime && <span>regime <b className="text-text">{trade.regime}</b></span>}
          {trade.cf_score != null && <span>confluence score <b className="text-text">{trade.cf_score.toFixed(1)}</b></span>}
        </div>
        {votes.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <div className="text-muted uppercase text-[0.7em] tracking-[1px]">Engine Votes</div>
            {votes.map((v) => (
              <div key={v.engine} className="flex items-center gap-2 flex-wrap">
                <span className="w-28 shrink-0 font-bold text-accent">{v.engine}</span>
                <Badge tone={v.bias === 'BULLISH' ? 'exec' : v.bias === 'BEARISH' ? 'no-trade' : 'neutral'}>{v.bias}</Badge>
                <span className="tabular-nums">{v.score.toFixed(0)}</span>
                {v.reasons.length > 0 && <span className="text-muted text-[0.82em]">{v.reasons.join(' · ')}</span>}
              </div>
            ))}
          </div>
        ) : (
          <Empty>No engine-vote snapshot for this trade (predates decision capture, or built outside run_backtest).</Empty>
        )}
      </div>
    </Panel>
  )
}

function TradesTable({ trades, onSelect }: { trades: ChartTrade[]; onSelect: (t: ChartTrade) => void }) {
  const columns: Column<ChartTrade>[] = [
    { header: 'ID', render: (t) => <span className="font-mono text-muted text-[0.85em]">{t.trade_id}</span> },
    { header: 'Dir', render: (t) => <Badge tone={t.direction === 'BUY' ? 'exec' : 'no-trade'}>{t.direction}</Badge> },
    { header: 'Entry', render: (t) => t.entry_price.toFixed(5), align: 'right' },
    { header: 'Exit', render: (t) => (t.exit_price != null ? t.exit_price.toFixed(5) : '—'), align: 'right' },
    {
      header: 'PnL',
      render: (t) => <span className={t.is_win ? 'text-green' : 'text-red'}>{t.pnl_usd >= 0 ? '+' : ''}{t.pnl_usd.toFixed(2)}</span>,
      align: 'right',
      accessorFn: (t) => t.pnl_usd,
      sortingFn: 'basic',
    },
    { header: 'Regime', render: (t) => t.regime ?? '—' },
    {
      header: 'Score',
      render: (t) => (t.cf_score != null ? t.cf_score.toFixed(0) : '—'),
      align: 'right',
      accessorFn: (t) => t.cf_score ?? -Infinity,
      sortingFn: 'basic',
    },
    {
      header: 'Decision',
      render: (t) => (
        <button onClick={() => onSelect(t)} className="text-accent hover:text-accent2 text-[0.85em] underline decoration-dotted">
          View
        </button>
      ),
      align: 'right',
    },
  ]
  return <DataTable columns={columns} rows={trades} rowKey={(t) => t.trade_id} />
}

function QueueManagerCharts({ entries }: { entries: RunReportEntry[] }) {
  const [selected, setSelected] = useState<string | null>(entries[0]?.file ?? null)
  const [chart, setChart] = useState<{ loading: boolean; error: string | null; data: ChartDataFile | null }>({
    loading: false,
    error: null,
    data: null,
  })
  const [decisionTrade, setDecisionTrade] = useState<ChartTrade | null>(null)

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    setChart({ loading: true, error: null, data: null })
    setDecisionTrade(null)
    getChartDataFile(selected)
      .then((data) => !cancelled && setChart({ loading: false, error: null, data }))
      .catch((err) => !cancelled && setChart({ loading: false, error: err instanceof Error ? err.message : String(err), data: null }))
    return () => {
      cancelled = true
    }
  }, [selected])

  const curve = chart.data?.equity_curve.map((p) => p.y) ?? []
  const candles = chart.data?.candles ?? null
  const trades = chart.data?.trades ?? []

  return (
    <>
      <Panel
        title={candles && candles.length > 0 ? 'Interactive Chart' : 'Queue Manager Equity Curve'}
        right={
          <div className="flex gap-1 flex-wrap">
            {entries.map((e) => (
              <button
                key={e.file}
                onClick={() => setSelected(e.file)}
                className={`px-2 py-0.5 rounded text-[0.9em] ${selected === e.file ? 'text-accent border border-accent/50' : 'text-muted border border-transparent hover:text-text'}`}
              >
                {String(e.highlights.symbol ?? e.file)}
              </button>
            ))}
          </div>
        }
      >
        {chart.loading ? (
          <Empty>Loading…</Empty>
        ) : chart.error ? (
          <Empty>Failed: {chart.error}</Empty>
        ) : candles && candles.length > 0 ? (
          <CandlestickTradeChart candles={candles} trades={trades} onSelectTrade={setDecisionTrade} />
        ) : curve.length >= 2 ? (
          <EquityCurveSvg curve={curve} />
        ) : (
          <Empty>Select a run</Empty>
        )}
        {chart.data?.monte_carlo && (
          <div className="px-4 pb-4 flex gap-4 flex-wrap text-[0.78em] text-muted">
            <span>MC median return <b className="text-text">{chart.data.monte_carlo.median_return.toFixed(1)}%</b></span>
            <span>MC risk of ruin <b className="text-red">{chart.data.monte_carlo.risk_of_ruin.toFixed(1)}%</b></span>
            <span>MC worst DD <b className="text-red">{chart.data.monte_carlo.worst_max_dd.toFixed(1)}%</b></span>
          </div>
        )}
      </Panel>

      {trades.length > 0 && (
        <Panel title="Trades" right={`${trades.length} closed`}>
          <TradesTable trades={trades} onSelect={setDecisionTrade} />
        </Panel>
      )}

      {decisionTrade && <TradeDecisionPanel trade={decisionTrade} onClose={() => setDecisionTrade(null)} />}
    </>
  )
}

// ── Experiment Comparison (Backtesting Lab priority #3, 2026-07-25) ─────
// Reuses getChartDataFile — no new backend endpoint needed. KPIs are
// computed client-side from the `trades`/`equity_curve` arrays chart_data
// already carries (added for the Interactive Chart work); nothing here
// is a new source of truth, just a different view over the same file.
const EXPERIMENT_COLORS = ['#00d4ff', '#7c5cfc', '#00e676', '#ffab40', '#ff5252', '#64748b']
const MAX_COMPARED = 6

interface ExperimentKpi {
  file: string
  symbol: string
  trades: number
  winRate: number
  profitFactor: number
  maxDrawdownPct: number
  totalReturnPct: number
  curve: number[]
}

function computeExperimentKpi(file: string, data: ChartDataFile): ExperimentKpi {
  const trades = data.trades
  const wins = trades.filter((t) => t.is_win)
  const losses = trades.filter((t) => !t.is_win)
  const grossProfit = wins.reduce((s, t) => s + t.pnl_usd, 0)
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl_usd, 0))
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0
  const winRate = trades.length > 0 ? (wins.length / trades.length) * 100 : 0
  const curve = data.equity_curve.map((p) => p.y)
  const { maxDD } = drawdownSeries(curve.length > 0 ? curve : [0])
  const start = curve[0] ?? 10_000
  const end = curve[curve.length - 1] ?? start
  const totalReturnPct = start !== 0 ? ((end - start) / start) * 100 : 0
  return { file, symbol: data.symbol, trades: trades.length, winRate, profitFactor, maxDrawdownPct: maxDD, totalReturnPct, curve }
}

function pfTone(pf: number): 'exec' | 'marginal' | 'no-trade' {
  if (pf >= 1.5) return 'exec'
  if (pf >= 1.0) return 'marginal'
  return 'no-trade'
}

// Overlaid % return from each experiment's own starting balance — index-
// normalized x-axis (same convention EquityCurveSvg already uses for a
// single curve) since experiments can have different trade counts and
// date ranges; a shared time axis across genuinely different runs would
// be more misleading than an honest "progress through the run" axis.
function ExperimentOverlayChart({ kpis }: { kpis: ExperimentKpi[] }) {
  const W = 800
  const H = 240
  const series = kpis.map((k) => (k.curve.length > 0 ? k.curve.map((v) => ((v - k.curve[0]) / (k.curve[0] || 1)) * 100) : [0]))
  const allValues = series.flat()
  const min = Math.min(0, ...allValues)
  const max = Math.max(0, ...allValues)
  const range = max - min || 1
  const x = (i: number, len: number) => (i / Math.max(1, len - 1)) * W
  const y = (v: number) => H - ((v - min) / range) * H
  const zeroY = y(0)

  return (
    <div className="px-4 pb-4">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-[240px]">
        <line x1={0} y1={zeroY} x2={W} y2={zeroY} stroke="#1a2236" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {series.map((s, idx) => {
          const path = s.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i, s.length).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
          return (
            <path
              key={kpis[idx].file}
              d={path}
              fill="none"
              stroke={EXPERIMENT_COLORS[idx % EXPERIMENT_COLORS.length]}
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
            />
          )
        })}
      </svg>
      <div className="flex gap-4 flex-wrap mt-2 text-[0.78em]">
        {kpis.map((k, idx) => (
          <span key={k.file} className="flex items-center gap-1.5 text-muted">
            <span className="w-2.5 h-2.5 rounded-full inline-block shrink-0" style={{ background: EXPERIMENT_COLORS[idx % EXPERIMENT_COLORS.length] }} />
            {k.symbol}
          </span>
        ))}
      </div>
      <p className="text-[0.72em] text-muted mt-2">% return from each experiment's own starting balance, plotted by trade sequence (not calendar time) — runs may cover different date ranges.</p>
    </div>
  )
}

function ExperimentComparisonPanel({ entries }: { entries: RunReportEntry[] }) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [kpis, setKpis] = useState<ExperimentKpi[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggle = (file: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(file)) next.delete(file)
      else if (next.size < MAX_COMPARED) next.add(file)
      return next
    })
  }

  const compare = async () => {
    setLoading(true)
    setError(null)
    try {
      const files = Array.from(selected)
      const datas = await Promise.all(files.map((f) => getChartDataFile(f)))
      setKpis(files.map((f, i) => computeExperimentKpi(f, datas[i])))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setKpis([])
    } finally {
      setLoading(false)
    }
  }

  const columns: Column<ExperimentKpi>[] = [
    { header: 'Experiment', render: (k) => <span className="font-bold text-accent">{k.symbol}</span> },
    { header: 'Trades', render: (k) => k.trades, align: 'right' },
    { header: 'WR%', render: (k) => `${k.winRate.toFixed(1)}%`, align: 'right' },
    {
      header: 'PF',
      render: (k) => <Badge tone={pfTone(k.profitFactor)}>{Number.isFinite(k.profitFactor) ? k.profitFactor.toFixed(2) : '∞'}</Badge>,
      align: 'right',
    },
    { header: 'Max DD%', render: (k) => <span className="text-red">{k.maxDrawdownPct.toFixed(1)}%</span>, align: 'right' },
    {
      header: 'Return%',
      render: (k) => <span className={k.totalReturnPct >= 0 ? 'text-green' : 'text-red'}>{k.totalReturnPct >= 0 ? '+' : ''}{k.totalReturnPct.toFixed(1)}%</span>,
      align: 'right',
    },
  ]

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Experiment Comparison" right={`select 2-${MAX_COMPARED} runs · ${entries.length} available`}>
        <div className="p-4 flex flex-col gap-3">
          <div className="flex flex-wrap gap-1.5">
            {entries.map((e) => {
              const isSelected = selected.has(e.file)
              return (
                <button
                  key={e.file}
                  onClick={() => toggle(e.file)}
                  title={e.file}
                  className={`px-2.5 py-1 rounded text-[0.78em] font-mono border ${
                    isSelected ? 'border-accent bg-accent/15 text-accent' : 'border-border text-muted hover:border-accent/50'
                  }`}
                >
                  {String(e.highlights.symbol ?? e.file)}
                </button>
              )
            })}
          </div>
          <button
            onClick={compare}
            disabled={selected.size < 2 || loading}
            className="self-start px-4 py-1.5 text-[0.82em] rounded border border-accent text-accent bg-transparent cursor-pointer hover:bg-accent/10 disabled:opacity-50 font-bold"
          >
            {loading ? 'Comparing…' : `Compare Selected (${selected.size})`}
          </button>
          {error && <span className="text-red text-[0.8em]">{error}</span>}
        </div>
        {kpis.length > 0 && (
          <>
            <DataTable columns={columns} rows={kpis} rowKey={(k) => k.file} />
            <ExperimentOverlayChart kpis={kpis} />
          </>
        )}
      </Panel>
      {kpis.length > 0 && (
        <AiResearchAssistant
          context={kpis}
          examples={['Which experiment had the best profit factor?', 'Why might the drawdowns differ between these runs?', 'Summarize the comparison.']}
        />
      )}
    </div>
  )
}

function QueueManagerRuns() {
  const { markUnauthenticated } = useAuth()
  const reports = useApiQuery(['run-reports'], getRunReports, POLL_MS, markUnauthenticated)

  const all = reports.data?.reports ?? []
  const chartEntries = all.filter((r) => r.kind === 'chart_data' && r.readable !== false)
  const wfEntries = all.filter((r) => r.kind === 'walk_forward' && r.readable !== false)
  const robustnessEntries = all.filter((r) => r.kind === 'robustness' && r.readable !== false)

  if (all.length === 0) {
    return (
      <Panel title="Queue Manager Runs" right="from the Experiment Runner tab">
        <Empty>
          {reports.loading
            ? 'Loading…'
            : 'No completed backtest / walk_forward / robustness jobs yet — run one from the Experiment Runner tab.'}
        </Empty>
      </Panel>
    )
  }

  return (
    <>
      {chartEntries.length > 0 && <QueueManagerCharts entries={chartEntries} />}
      {chartEntries.length >= 2 && <ExperimentComparisonPanel entries={chartEntries} />}
      {(wfEntries.length > 0 || robustnessEntries.length > 0) && (
        <Panel title="Walk-Forward / Robustness Verdicts" right={`${wfEntries.length + robustnessEntries.length} runs`}>
          <div className="divide-y divide-border">
            {[...wfEntries, ...robustnessEntries].map((e) => (
              <WalkForwardRobustnessPanel key={e.file} entry={e} />
            ))}
          </div>
        </Panel>
      )}
    </>
  )
}

export function BacktestingCharts() {
  const { markUnauthenticated } = useAuth()
  const backtests = useApiQuery(['backtest-results'], getBacktestResults, POLL_MS, markUnauthenticated)
  const outcomes = useApiQuery(['outcomes-calibration'], getOutcomesCalibration, POLL_MS, markUnauthenticated)
  const [selected, setSelected] = useState<string | null>(null)

  const runs = backtests.data?.results ?? []
  const withCurve = runs.filter((r) => (r.equity_curve?.length ?? 0) >= 2)
  const activeKey = selected ?? (withCurve[0] ? `${withCurve[0].file}::${withCurve[0].symbol}` : null)
  const activeRun = runs.find((r) => `${r.file}::${r.symbol}` === activeKey) ?? withCurve[0] ?? runs[0]

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.78em] text-muted">
        Visualization only, over data <code className="text-accent2">/backtest-results</code> and{' '}
        <code className="text-accent2">/outcomes</code> already compute. In-sample backtests are not evidence of edge —
        see the Forward Demo tab for the live counter that is.
      </p>

      <QueueManagerRuns />

      {runs.length === 0 ? (
        <Panel title="Backtest Runs">
          <Empty>{backtests.loading ? 'Loading…' : 'No backtest results on disk yet'}</Empty>
        </Panel>
      ) : (
        <>
          <MetricComparison runs={runs} />

          <Panel
            title="Equity Curve"
            right={
              withCurve.length > 0 ? (
                <div className="flex gap-1 flex-wrap">
                  {withCurve.map((r) => {
                    const key = `${r.file}::${r.symbol}`
                    return (
                      <button
                        key={key}
                        onClick={() => setSelected(key)}
                        className={`px-2 py-0.5 rounded text-[0.9em] ${activeKey === key ? 'text-accent border border-accent/50' : 'text-muted border border-transparent hover:text-text'}`}
                      >
                        {r.symbol}
                      </button>
                    )
                  })}
                </div>
              ) : (
                'legacy per-bar runs only'
              )
            }
          >
            {activeRun ? <EquityCurve run={activeRun} /> : <Empty>Select a run</Empty>}
          </Panel>

          <Panel title="Score Calibration" right={outcomes.data ? `${outcomes.data.summary.total_closed} closed` : undefined}>
            {outcomes.data ? (
              <CalibrationChart buckets={outcomes.data.summary.calibration} />
            ) : (
              <Empty>{outcomes.loading ? 'Loading…' : 'No outcome data'}</Empty>
            )}
          </Panel>
        </>
      )}
    </div>
  )
}
