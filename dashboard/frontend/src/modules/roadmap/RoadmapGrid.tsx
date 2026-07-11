import { RoadmapCard } from '../../components/RoadmapCard'

const PLANNED = [
  { title: 'Risk Center', note: 'Exposure, drawdown, portfolio heat — planned v2' },
  { title: 'Backtesting Charts', note: 'Equity curve, Monte Carlo, walk-forward — planned v2' },
  { title: 'AI Decision Center', note: 'Explain-only layer, per VISION_v2.md constraints — planned v2' },
  { title: 'News Intelligence', note: 'Calendar countdown, blackout windows — planned v2' },
  { title: 'Execution Center', note: 'cTrader open/closed orders, execution log — planned v2' },
  { title: 'Cloudflare Panel', note: 'Requests, cache, firewall — needs Cloudflare API integration' },
  { title: 'Alerts Center', note: 'Consolidated system alerts — planned v2' },
  { title: 'System Performance Charts', note: 'CPU/RAM/latency history — planned v2' },
  { title: 'Settings', note: 'Engine toggles, weight edits, risk limits — planned v2' },
] as const

export function RoadmapGrid() {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.8em] text-muted">
        These modules aren't wired up yet. Per the project's "no future phase functionality pretending to be
        complete" rule, they show as placeholders here instead of mock data.
      </p>
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(220px,1fr))]">
        {PLANNED.map((m) => (
          <RoadmapCard key={m.title} title={m.title} note={m.note} />
        ))}
      </div>
    </div>
  )
}
