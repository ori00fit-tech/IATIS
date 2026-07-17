import { useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { getBacktestResults, getOutcomesCalibration, type BacktestRun, type CalibrationBucket } from './api'

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
  const rows = runs
    .map((r) => ({ symbol: r.symbol, file: r.file, value: num(r[metric]) }))
    .filter((r): r is { symbol: string; file: string; value: number } => r.value !== undefined)
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
  const { maxDD, ddAt } = drawdownSeries(curve) // ≤500 points, cheap enough to run inline
  if (curve.length < 2) {
    return (
      <Empty>
        No per-bar equity series for {run.symbol} — pipeline backtests store summary metrics only. Equity curves appear
        for legacy per-bar runs (backtest_engine.save).
      </Empty>
    )
  }
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

export function BacktestingCharts() {
  const { markUnauthenticated } = useAuth()
  const backtests = usePolling(getBacktestResults, POLL_MS, markUnauthenticated)
  const outcomes = usePolling(getOutcomesCalibration, POLL_MS, markUnauthenticated)
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
