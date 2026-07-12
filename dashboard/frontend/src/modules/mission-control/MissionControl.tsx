import { useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { KpiCard } from '../../components/KpiCard'
import { Panel, Empty } from '../../components/Panel'
import { Badge } from '../../components/Badge'
import { StatusRow } from '../../components/StatusDot'
import { AiStatusFrame } from '../../components/AiStatusFrame'
import { DataTable, type Column } from '../../components/DataTable'
import {
  getHealth,
  getHealthFull,
  getBudget,
  getSymbolHealth,
  getOutcomes,
  getAiNewsAnalysis,
  getAiMacroAnalysis,
  getAiDailyReport,
  type SymbolHealthEntry,
  type OutcomesSummary,
  type AiNewsAnalysis,
  type AiMacroAnalysis,
  type AiDailyReport,
} from './api'

// Audit Phase 5: any edge claim needs a live forward track record before
// it means anything. This is the sample-size milestone the tracker walks
// toward — n>=100 closed paper trades.
const EVIDENCE_TARGET = 100

const POLL_MS = 15_000

function pct(n: number | undefined | null) {
  return n === undefined || n === null ? '—' : `${n.toFixed(0)}%`
}

interface Briefing {
  loading: boolean
  error: string | null
  news: AiNewsAnalysis | null
  macro: AiMacroAnalysis | null
  daily: AiDailyReport | null
}

function AiBriefingPanel() {
  const [b, setB] = useState<Briefing>({ loading: false, error: null, news: null, macro: null, daily: null })
  const generated = b.news !== null || b.macro !== null || b.daily !== null

  const generate = () => {
    setB({ loading: true, error: null, news: null, macro: null, daily: null })
    Promise.all([getAiNewsAnalysis(), getAiMacroAnalysis(), getAiDailyReport()])
      .then(([news, macro, daily]) => setB({ loading: false, error: null, news, macro, daily }))
      .catch((err) => setB({ loading: false, error: err instanceof Error ? err.message : String(err), news: null, macro: null, daily: null }))
  }

  // All three share one ai.enabled flag — if one is disabled, they all are.
  const allDisabled = b.news?.status === 'disabled' && b.macro?.status === 'disabled' && b.daily?.status === 'disabled'

  return (
    <Panel
      title="AI Briefing"
      right={
        <button
          onClick={generate}
          disabled={b.loading}
          className="text-accent hover:text-accent2 text-[0.78em] disabled:opacity-50"
        >
          {b.loading ? 'Generating…' : generated ? 'Regenerate' : 'Generate'}
        </button>
      }
    >
      <div className="p-4">
        {!generated && !b.loading && !b.error && (
          <Empty>On-demand only — fetches live news/macro context and phrases today's stats. Click Generate.</Empty>
        )}
        {b.loading && <Empty>Asking the AI provider…</Empty>}
        {b.error && <Empty>Request failed: {b.error}</Empty>}
        {generated && !b.loading && !b.error && allDisabled && (
          <Empty>AI is disabled — set `ai.enabled: true` and an API key in config.yaml to turn this on.</Empty>
        )}
        {generated && !b.loading && !b.error && !allDisabled && (
          <div className="grid grid-cols-3 gap-4 max-[900px]:grid-cols-1">
            <div>
              <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-2">Daily Report</div>
              <AiStatusFrame loading={false} fetchError={null} status={b.daily?.status} providerError={b.daily?.error}>
                <p className="text-[0.88em]">{b.daily?.text}</p>
              </AiStatusFrame>
            </div>
            <div>
              <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-2">Macro Context</div>
              <AiStatusFrame loading={false} fetchError={null} status={b.macro?.status} providerError={b.macro?.error}>
                <div className="flex flex-col gap-2 text-[0.88em]">
                  <div className="flex gap-2 flex-wrap">
                    <Badge tone={b.macro?.risk_on_off === 'RISK_ON' ? 'exec' : b.macro?.risk_on_off === 'RISK_OFF' ? 'no-trade' : 'neutral'}>
                      {b.macro?.risk_on_off ?? 'NEUTRAL'}
                    </Badge>
                    <span className="text-muted">{`DXY: ${b.macro?.dxy_bias ?? 'Neutral'}`}</span>
                  </div>
                  <p>{b.macro?.summary}</p>
                </div>
              </AiStatusFrame>
            </div>
            <div>
              <div className="text-muted uppercase text-[0.7em] tracking-[1px] mb-2">News Read</div>
              <AiStatusFrame loading={false} fetchError={null} status={b.news?.status} providerError={b.news?.error}>
                <div className="flex flex-col gap-2 text-[0.88em]">
                  <div className="flex gap-2 flex-wrap">
                    <Badge tone={b.news?.impact === 'HIGH' ? 'poor' : b.news?.impact === 'MEDIUM' ? 'marginal' : 'good'}>
                      {`Impact: ${b.news?.impact ?? 'LOW'}`}
                    </Badge>
                    <span className="text-muted">{b.news?.sentiment}</span>
                  </div>
                  <p>{b.news?.summary}</p>
                </div>
              </AiStatusFrame>
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}

function PaperTradingPanel({ outcomes }: { outcomes: OutcomesSummary | null }) {
  const s = outcomes?.summary
  const closed = s?.total_closed ?? 0
  const progress = Math.min(100, Math.round((closed / EVIDENCE_TARGET) * 100))
  return (
    <Panel
      title="Paper Trading Evidence"
      right={`target: ${EVIDENCE_TARGET} closed trades`}
    >
      {s ? (
        <div className="p-4 flex flex-col gap-3">
          <div className="flex items-baseline gap-4 flex-wrap">
            <span className="text-[1.6em] font-extrabold text-accent">{closed}<span className="text-muted text-[0.55em] font-normal"> / {EVIDENCE_TARGET} closed</span></span>
            <span className="text-[0.85em]">WR <b className={s.win_rate >= 50 ? 'text-green' : 'text-amber'}>{closed ? `${s.win_rate.toFixed(1)}%` : '—'}</b></span>
            <span className="text-[0.85em]">W/L <b>{s.wins}/{s.losses}</b></span>
            <span className="text-[0.85em]">open <b className="text-accent2">{s.open_signals}</b></span>
          </div>
          <div className="h-2 rounded bg-surface border border-border overflow-hidden">
            <div className="h-full bg-gradient-to-r from-accent to-accent2" style={{ width: `${progress}%` }} />
          </div>
          <p className="text-muted text-[0.75em]">
            Live forward outcomes are the only proof of edge — backtests here are in-sample.
            Statistics below n≈30 are noise; treat everything before the target as data collection.
          </p>
        </div>
      ) : (
        <Empty>No outcome data yet</Empty>
      )}
    </Panel>
  )
}

export function MissionControl() {
  const { markUnauthenticated } = useAuth()
  const health = usePolling(getHealth, POLL_MS, markUnauthenticated)
  const healthFull = usePolling(getHealthFull, POLL_MS, markUnauthenticated)
  const budget = usePolling(getBudget, POLL_MS, markUnauthenticated)
  const symbolHealth = usePolling(getSymbolHealth, POLL_MS, markUnauthenticated)
  const outcomes = usePolling(getOutcomes, POLL_MS, markUnauthenticated)

  const hf = healthFull.data
  const creditsColor = (budget.data?.remaining_today ?? 0) > 400 ? 'green' : (budget.data?.remaining_today ?? 0) > 100 ? 'amber' : 'red'

  const symbolColumns: Column<SymbolHealthEntry>[] = [
    { header: 'Symbol', render: (s) => <span className="font-bold text-accent">{s.symbol}</span> },
    { header: 'SHI', render: (s) => s.shi_score, align: 'right' },
    {
      header: 'Status',
      render: (s) => (
        <span
          className={`font-bold ${s.status === 'HEALTHY' ? 'text-green' : s.status === 'CAUTION' ? 'text-amber' : 'text-red'}`}
        >
          {s.status}
        </span>
      ),
    },
    { header: 'Win Rate', render: (s) => (s.win_rate != null ? `${s.win_rate.toFixed(1)}%` : '—'), align: 'right' },
    { header: 'Trades', render: (s) => <span className="text-muted">{s.trades_count}</span>, align: 'right' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className={`flex items-center gap-2 px-3.5 py-2 rounded-md border text-[0.78em] ${
        hf?.status === 'degraded' ? 'border-red/40 bg-red/5' : 'border-border bg-surface'
      }`}>
        <span className={`inline-block w-2 h-2 rounded-full ${hf?.status === 'degraded' ? 'bg-red' : 'bg-green'}`} />
        <span>
          {healthFull.loading ? 'Checking system health...' : hf?.status === 'degraded' ? `Degraded — ${hf.issues.join('; ')}` : `All systems nominal · v${health.data?.version ?? '?'}`}
        </span>
      </div>

      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(140px,1fr))]">
        <KpiCard value={pct(hf?.system?.cpu_pct)} label="CPU" color={hf && hf.system && hf.system.cpu_pct > 80 ? 'red' : 'default'} />
        <KpiCard value={pct(hf?.system?.ram_pct)} label="RAM" color={hf && hf.system && hf.system.ram_pct > 85 ? 'red' : 'default'} />
        <KpiCard value={pct(hf?.system?.disk_pct)} label="Disk" color={hf && hf.system && hf.system.disk_pct > 80 ? 'red' : 'default'} />
        <KpiCard value={pct(hf?.system?.swap_pct)} label="Swap" color={hf && hf.system && hf.system.swap_pct > 50 ? 'red' : 'default'} />
        <KpiCard
          value={hf?.system?.load_1m != null ? hf.system.load_1m.toFixed(2) : '—'}
          label="Load (1m)"
          color="default"
        />
        <KpiCard value={hf?.system?.uptime_hours != null ? `${hf.system.uptime_hours}h` : '—'} label="Uptime" color="purple" />
        <KpiCard value={budget.data?.remaining_today ?? '—'} label="API Credits" color={creditsColor} />
        <KpiCard
          value={symbolHealth.data ? `${symbolHealth.data.healthy}/${symbolHealth.data.total}` : '—'}
          label="Symbols Healthy"
          color="blue"
        />
        <KpiCard value={health.data?.decision_timeframe ?? '—'} label="Decision TF" color="purple" />
        <KpiCard
          value={outcomes.data ? `${outcomes.data.summary.total_closed}/${EVIDENCE_TARGET}` : '—'}
          label="Evidence Trades"
          color={outcomes.data && outcomes.data.summary.total_closed >= EVIDENCE_TARGET ? 'green' : 'amber'}
        />
      </div>

      <div className="grid grid-cols-2 gap-4 max-[768px]:grid-cols-1">
        <Panel title="System Status">
          <StatusRow label="API Server" state={health.error ? 'err' : 'ok'} detail={health.data?.status} />
          <StatusRow
            label="Scheduler"
            state={hf?.scheduler?.status === 'running' ? 'ok' : hf?.scheduler?.status === 'unknown' ? 'warn' : 'err'}
            detail={hf?.scheduler?.last_run ?? 'no run seen'}
          />
          {hf?.services &&
            Object.entries(hf.services).map(([unit, status]) => (
              <StatusRow
                key={unit}
                label={`svc: ${unit}`}
                state={status === 'active' ? 'ok' : status === 'failed' ? 'err' : 'warn'}
                detail={status}
              />
            ))}
          <StatusRow
            label="Database"
            state={hf?.database?.status === 'ok' ? 'ok' : 'err'}
            detail={hf?.database ? `${hf.database.total_decisions ?? 0} decisions` : undefined}
          />
          <StatusRow
            label="News Calendar"
            state={hf?.calendar?.status === 'ok' ? 'ok' : 'warn'}
            detail={hf?.calendar?.event_count != null ? `${hf.calendar.event_count} events` : hf?.calendar?.note}
          />
          <StatusRow
            label="cTrader"
            state={hf?.ctrader?.configured ? 'ok' : 'warn'}
            detail={hf?.ctrader?.environment}
          />
          {hf?.data_providers &&
            Object.entries(hf.data_providers).map(([name, status]) => (
              <StatusRow key={name} label={name} state={status.includes('configured') || status === 'always_available' ? 'ok' : 'warn'} detail={status} />
            ))}
        </Panel>

        <Panel title="Symbol Health" right={symbolHealth.data ? `${symbolHealth.data.caution} caution · ${symbolHealth.data.paused} paused` : undefined}>
          {symbolHealth.data && symbolHealth.data.symbols.length > 0 ? (
            <DataTable columns={symbolColumns} rows={symbolHealth.data.symbols} rowKey={(s) => s.symbol} />
          ) : (
            <Empty>{symbolHealth.loading ? 'Loading...' : 'No symbol health data yet'}</Empty>
          )}
        </Panel>
      </div>

      <PaperTradingPanel outcomes={outcomes.data} />

      <AiBriefingPanel />
    </div>
  )
}
