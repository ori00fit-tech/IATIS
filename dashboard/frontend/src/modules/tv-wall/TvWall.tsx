import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useApiQuery } from '../../lib/useApiQuery'
import { useAuth } from '../../lib/auth'
import {
  getHealthFull,
  getKillSwitchStatus,
  getMarketHealth,
  getOutcomes,
  getReconciliation,
  type HealthFull,
} from '../mission-control/api'
import { formatMetric, getForwardReview, type ForwardRule } from '../forward-demo/api'
import { getDecisions, type DecisionEntry } from '../live-signals/api'
import './tv.css'

/**
 * TV Wall (2026-11) — a read-only, 10-foot display for a living-room TV
 * (built for a 32" HD smart TV's browser, works at 1366×768 and 1080p).
 *
 * Deliberately has NO controls that change system state: no kill-switch
 * toggle, no AI calls, no analyze button. A wall display that can be
 * operated by a TV remote must not be able to touch the book. It only
 * reads endpoints the console already polls, and it shows the evidence
 * counters (D001/D002) exactly as scripts/forward_review.py computes
 * them — nothing is re-derived or re-thresholded here.
 *
 * Navigation: ◀ ▶ (remote D-pad) switch scenes · OK/Enter pauses rotation.
 */

const ROTATE_MS = 20_000
const CARRIERS = ['XAUUSD', 'XAGUSD', 'BTCUSD', 'ETHUSD'] as const

// ─── helpers ────────────────────────────────────────────────────────────

function ago(iso: string | null | undefined): string {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 90) return `${Math.round(s)}s ago`
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172800) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

function secondsLabel(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s < 90) return `${Math.round(s)}s`
  if (s < 5400) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

function price(v: number | null | undefined): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  return v.toFixed(abs >= 1000 ? 1 : abs >= 10 ? 2 : 5)
}

function latestPerSymbol(decisions: DecisionEntry[] | undefined): Map<string, DecisionEntry> {
  const map = new Map<string, DecisionEntry>()
  for (const d of decisions ?? []) {
    const prev = map.get(d.symbol)
    if (!prev || Date.parse(d.timestamp) > Date.parse(prev.timestamp)) map.set(d.symbol, d)
  }
  return map
}

type Tone = 'ok' | 'warn' | 'bad' | 'idle'

// ─── shared bits ────────────────────────────────────────────────────────

function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`tv-pill tv-${tone}`}>
      <span className="tv-dot" />
      {children}
    </span>
  )
}

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const hh = String(now.getUTCHours()).padStart(2, '0')
  const mm = String(now.getUTCMinutes()).padStart(2, '0')
  const local = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return (
    <div className="tv-clock">
      <div className="tv-clock-main">
        {hh}
        <span className="tv-blink">:</span>
        {mm}
        <small>UTC</small>
      </div>
      <div className="tv-clock-sub">
        Local {local} · {now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })}
      </div>
    </div>
  )
}

/** SVG progress ring — n / min_n toward a pre-registered decision rule. */
function Ring({ pct, tone, label, sub }: { pct: number; tone: Tone; label: string; sub: string }) {
  const r = 52
  const c = 2 * Math.PI * r
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <div className="tv-ring">
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle cx="60" cy="60" r={r} className="tv-ring-track" />
        <circle
          cx="60"
          cy="60"
          r={r}
          className={`tv-ring-bar tv-stroke-${tone}`}
          strokeDasharray={c}
          strokeDashoffset={c * (1 - clamped / 100)}
          transform="rotate(-90 60 60)"
        />
      </svg>
      <div className="tv-ring-label">
        <div className="tv-ring-num">{label}</div>
        <div className="tv-ring-sub">{sub}</div>
      </div>
    </div>
  )
}

function Bar({ label, pct, warnAt = 75, badAt = 90 }: { label: string; pct: number | null | undefined; warnAt?: number; badAt?: number }) {
  const v = pct ?? 0
  const tone: Tone = pct == null ? 'idle' : v >= badAt ? 'bad' : v >= warnAt ? 'warn' : 'ok'
  return (
    <div className="tv-bar">
      <div className="tv-bar-head">
        <span>{label}</span>
        <b>{pct == null ? '—' : `${v.toFixed(0)}%`}</b>
      </div>
      <div className="tv-bar-track">
        <div className={`tv-bar-fill tv-bg-${tone}`} style={{ width: `${Math.min(100, v)}%` }} />
      </div>
    </div>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="tv-empty">{children}</div>
}

// ─── scenes ─────────────────────────────────────────────────────────────

function RuleCard({ rule, title, subtitle }: { rule: ForwardRule | undefined; title: string; subtitle: string }) {
  if (!rule) {
    return (
      <div className="tv-card tv-rule">
        <div className="tv-eyebrow">{subtitle}</div>
        <h2>{title}</h2>
        <Empty>Rule not reported by /forward-review</Empty>
      </div>
    )
  }
  const pct = rule.progress_pct ?? (rule.min_n ? (rule.n / rule.min_n) * 100 : 0)
  const tone: Tone = rule.triggered ? (rule.rule_id === 'D002' ? 'ok' : 'bad') : rule.sufficient_n ? 'warn' : 'idle'
  const state = rule.triggered ? 'TRIGGERED' : rule.sufficient_n ? 'n reached · not triggered' : 'Accumulating evidence'
  return (
    <div className={`tv-card tv-rule tv-edge-${tone}`}>
      <div className="tv-eyebrow">
        {rule.rule_id} · {subtitle}
      </div>
      <h2>{title}</h2>
      <div className="tv-rule-body">
        <Ring pct={pct} tone={tone} label={`${rule.n}`} sub={`of ${rule.min_n} trades`} />
        <div className="tv-rule-metric">
          <div className="tv-metric-label">{rule.metric.toUpperCase()}</div>
          <div className={`tv-metric-big tv-fg-${tone === 'idle' ? 'text' : tone}`}>
            {formatMetric(rule.current_value)}
          </div>
          <div className="tv-metric-label">
            rule: {rule.op} {rule.threshold}
          </div>
        </div>
      </div>
      <div className={`tv-state tv-fg-${tone === 'idle' ? 'muted' : tone}`}>{state}</div>
      <div className="tv-statement">{rule.statement}</div>
    </div>
  )
}

function EvidenceScene() {
  const fr = useApiQuery(['tv', 'forward-review'], getForwardReview, 60_000)
  const oc = useApiQuery(['tv', 'outcomes'], getOutcomes, 60_000)
  const rules = fr.data?.rules ?? []
  const d001 = rules.find((r) => r.rule_id === 'D001') ?? rules.find((r) => r.bucket === 'fx')
  const d002 = rules.find((r) => r.rule_id === 'D002') ?? rules.find((r) => r.bucket === 'carriers')
  const s = oc.data?.summary
  const pf = s?.profit_factor
  return (
    <section className="tv-scene">
      <header className="tv-scene-head">
        <h1>Forward Evidence</h1>
        <p>The only prospective evidence. Pre-registered rules, applied by forward_review — never at read time.</p>
      </header>
      <div className="tv-grid-2">
        <RuleCard rule={d002} title="Carriers → live-capital talk" subtitle="XAU · XAG · BTC · ETH" />
        <RuleCard rule={d001} title="FX book → cut if PF < 1.0" subtitle="FX majors" />
      </div>
      <div className="tv-kpis">
        <Kpi label="Closed" value={s ? `${s.total_closed}` : '—'} />
        <Kpi label="Win rate" value={s ? `${s.win_rate.toFixed(1)}%` : '—'} />
        <Kpi label="Profit factor" value={pf == null ? '—' : pf === 'Infinity' ? '∞' : pf.toFixed(2)} />
        <Kpi
          label="Total R"
          value={s?.total_r == null ? '—' : `${s.total_r >= 0 ? '+' : ''}${s.total_r.toFixed(1)}R`}
          tone={s?.total_r == null ? undefined : s.total_r >= 0 ? 'ok' : 'bad'}
        />
        <Kpi label="Open" value={s ? `${s.open_signals}` : '—'} />
      </div>
    </section>
  )
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: Tone }) {
  return (
    <div className="tv-kpi">
      <div className="tv-kpi-label">{label}</div>
      <div className={`tv-kpi-value ${tone ? `tv-fg-${tone}` : ''}`}>{value}</div>
    </div>
  )
}

function useDecisions() {
  return useApiQuery(['tv', 'decisions'], () => getDecisions(120), 60_000)
}

function CarrierTile({ symbol, d }: { symbol: string; d: DecisionEntry | undefined }) {
  if (!d) {
    return (
      <div className="tv-card tv-tile">
        <div className="tv-tile-sym">{symbol}</div>
        <Empty>No recent decision</Empty>
      </div>
    )
  }
  const r = d.report
  const exec = d.final_verdict === 'EXECUTE'
  const bias = r.confluence?.vote?.winning_bias ?? 'NEUTRAL'
  const biasTone: Tone = /BULL|LONG|BUY/i.test(bias) ? 'ok' : /BEAR|SHORT|SELL/i.test(bias) ? 'bad' : 'idle'
  const score = r.confluence?.score ?? 0
  return (
    <div className={`tv-card tv-tile ${exec ? 'tv-tile-exec' : ''}`}>
      <div className="tv-tile-top">
        <div className="tv-tile-sym">{symbol}</div>
        <span className={`tv-verdict ${exec ? 'tv-verdict-exec' : ''}`}>{exec ? 'EXECUTE' : 'NO TRADE'}</span>
      </div>
      <div className={`tv-bias tv-fg-${biasTone === 'idle' ? 'muted' : biasTone}`}>
        {biasTone === 'ok' ? '▲' : biasTone === 'bad' ? '▼' : '◆'} {bias}
      </div>
      <div className="tv-score">
        <div className="tv-score-track">
          <div className="tv-score-fill" style={{ width: `${Math.min(100, score)}%` }} />
        </div>
        <span>{score.toFixed(0)}</span>
      </div>
      <dl className="tv-tile-facts">
        <div>
          <dt>Regime</dt>
          <dd>{r.regime?.state ?? '—'}</dd>
        </div>
        <div>
          <dt>Votes</dt>
          <dd>
            {r.confluence?.vote ? `${r.confluence.vote.agree_count}/${r.confluence.vote.total_engines}` : '—'}
          </dd>
        </div>
        {exec && (
          <>
            <div>
              <dt>Entry</dt>
              <dd>{price(r.entry_price)}</dd>
            </div>
            <div>
              <dt>SL / TP</dt>
              <dd>
                {price(r.stop_loss)} / {price(r.take_profit)}
              </dd>
            </div>
          </>
        )}
      </dl>
      <div className="tv-tile-foot">{ago(d.timestamp)}</div>
    </div>
  )
}

function CarriersScene() {
  const dq = useDecisions()
  const latest = useMemo(() => latestPerSymbol(dq.data?.decisions), [dq.data])
  return (
    <section className="tv-scene">
      <header className="tv-scene-head">
        <h1>Carrier Assets</h1>
        <p>H4 decisions with D1 confirmation — where the measured edge lives.</p>
      </header>
      <div className="tv-grid-4">
        {CARRIERS.map((sym) => (
          <CarrierTile key={sym} symbol={sym} d={latest.get(sym)} />
        ))}
      </div>
    </section>
  )
}

function FleetScene() {
  const dq = useDecisions()
  const latest = useMemo(() => latestPerSymbol(dq.data?.decisions), [dq.data])
  const rows = [...latest.values()].sort((a, b) => {
    const ca = (CARRIERS as readonly string[]).includes(a.symbol) ? 0 : 1
    const cb = (CARRIERS as readonly string[]).includes(b.symbol) ? 0 : 1
    return ca - cb || a.symbol.localeCompare(b.symbol)
  })
  const summary = dq.data?.summary
  return (
    <section className="tv-scene">
      <header className="tv-scene-head">
        <h1>Symbol Board</h1>
        <p>
          Latest verdict per symbol
          {summary ? ` · last ${summary.total}: ${summary.execute} execute / ${summary.no_trade} no-trade` : ''}
        </p>
      </header>
      {rows.length === 0 ? (
        <Empty>{dq.loading ? 'Loading decisions…' : 'No decisions in the log yet'}</Empty>
      ) : (
        <div className="tv-board">
          {rows.slice(0, 18).map((d) => {
            const exec = d.final_verdict === 'EXECUTE'
            const carrier = (CARRIERS as readonly string[]).includes(d.symbol)
            const bias = d.report.confluence?.vote?.winning_bias ?? '—'
            return (
              <div key={d.symbol} className={`tv-board-row ${exec ? 'tv-board-exec' : ''}`}>
                <span className="tv-board-sym">
                  {carrier && <span className="tv-star">★</span>}
                  {d.symbol}
                </span>
                <span className="tv-board-bias">{bias}</span>
                <span className="tv-board-score">{(d.report.confluence?.score ?? 0).toFixed(0)}</span>
                <span className={`tv-board-verdict ${exec ? 'tv-fg-ok' : 'tv-fg-muted'}`}>
                  {exec ? 'EXECUTE' : 'NO TRADE'}
                </span>
                <span className="tv-board-age">{ago(d.timestamp)}</span>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function SystemScene({ health }: { health: HealthFull | null }) {
  const mh = useApiQuery(['tv', 'metrics'], getMarketHealth, 60_000)
  const rec = useApiQuery(['tv', 'reconciliation'], getReconciliation, 120_000)
  const sys = health?.system
  const services = Object.entries(health?.services ?? {})
  const recTone: Tone =
    rec.data?.status === 'match' ? 'ok' : rec.data?.status === 'mismatch' ? 'bad' : 'idle'
  const decisionAge = mh.data?.lastDecisionAgeSeconds
  return (
    <section className="tv-scene">
      <header className="tv-scene-head">
        <h1>System</h1>
        <p>
          {health
            ? health.status === 'healthy'
              ? 'All checks healthy'
              : `${health.issues.length} issue(s): ${health.issues.slice(0, 2).join(' · ')}`
            : 'Waiting for /health/full…'}
        </p>
      </header>
      <div className="tv-grid-sys">
        <div className="tv-card">
          <div className="tv-eyebrow">VPS resources</div>
          <Bar label="CPU" pct={sys?.cpu_pct} />
          <Bar label="RAM" pct={sys?.ram_pct} />
          <Bar label="Disk" pct={sys?.disk_pct} warnAt={80} />
          <div className="tv-small">
            Uptime {sys ? `${(sys.uptime_hours / 24).toFixed(1)} d` : '—'} · load{' '}
            {sys?.load_1m?.toFixed(2) ?? '—'}
          </div>
        </div>
        <div className="tv-card">
          <div className="tv-eyebrow">Pipeline</div>
          <div className="tv-facts">
            <Fact label="Scheduler" value={health?.scheduler?.status ?? '—'} />
            <Fact label="Last run" value={ago(health?.scheduler?.last_run)} />
            <Fact
              label="Last decision"
              value={secondsLabel(decisionAge)}
              tone={decisionAge == null ? 'idle' : decisionAge > 6 * 3600 ? 'warn' : 'ok'}
            />
            <Fact label="Decisions 24h" value={`${health?.database?.last_24h ?? '—'}`} />
            <Fact label="Database" value={mh.data ? (mh.data.d1Up ? 'UP' : 'DOWN') : '—'} tone={mh.data ? (mh.data.d1Up ? 'ok' : 'bad') : 'idle'} />
            <Fact label="Broker recon" value={rec.data?.status?.toUpperCase() ?? '—'} tone={recTone} />
          </div>
        </div>
        <div className="tv-card">
          <div className="tv-eyebrow">Services</div>
          {services.length === 0 ? (
            <Empty>No service data</Empty>
          ) : (
            <ul className="tv-services">
              {services.slice(0, 10).map(([name, s]) => (
                <li key={name}>
                  <span className={`tv-dot tv-fg-${s.healthy ? 'ok' : 'bad'}`} />
                  <span className="tv-svc-name">{name.replace(/^iatis-/, '').replace(/\.(service|timer)$/, '')}</span>
                  <span className="tv-svc-state">{s.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  )
}

function Fact({ label, value, tone }: { label: string; value: string; tone?: Tone }) {
  return (
    <div className="tv-fact">
      <span>{label}</span>
      <b className={tone ? `tv-fg-${tone === 'idle' ? 'muted' : tone}` : ''}>{value}</b>
    </div>
  )
}

// ─── shell ──────────────────────────────────────────────────────────────

const SCENES = ['Evidence', 'Carriers', 'Board', 'System'] as const

function Ticker() {
  const dq = useDecisions()
  const items = (dq.data?.decisions ?? []).slice(0, 12)
  if (items.length === 0) return <div className="tv-ticker" />
  const text = items.map((d) => (
    <span key={`${d.symbol}-${d.timestamp}`} className="tv-ticker-item">
      <b>{d.symbol}</b>
      <span className={d.final_verdict === 'EXECUTE' ? 'tv-fg-ok' : 'tv-fg-muted'}>
        {d.final_verdict === 'EXECUTE' ? 'EXECUTE' : 'NO TRADE'}
      </span>
      <span className="tv-fg-muted">{ago(d.timestamp)}</span>
    </span>
  ))
  return (
    <div className="tv-ticker">
      <div className="tv-ticker-run">
        {text}
        {text}
      </div>
    </div>
  )
}

export function TvWall() {
  const { markUnauthenticated } = useAuth()
  const [idx, setIdx] = useState(0)
  const [paused, setPaused] = useState(false)
  const [cycle, setCycle] = useState(0) // restarts the progress bar animation

  const health = useApiQuery(['tv', 'health-full'], getHealthFull, 30_000, markUnauthenticated)
  const kill = useApiQuery(['tv', 'kill-switch'], getKillSwitchStatus, 30_000)

  const go = useCallback((delta: number) => {
    setIdx((i) => (i + delta + SCENES.length) % SCENES.length)
    setCycle((c) => c + 1)
  }, [])

  useEffect(() => {
    if (paused) return
    const id = setTimeout(() => go(1), ROTATE_MS)
    return () => clearTimeout(id)
  }, [idx, paused, cycle, go])

  // TV remote D-pad arrives as arrow keys; OK as Enter. Number keys jump.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'MediaTrackNext') {
        e.preventDefault()
        go(1)
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'MediaTrackPrevious') {
        e.preventDefault()
        go(-1)
      } else if (e.key === 'Enter' || e.key === ' ' || e.key === 'MediaPlayPause') {
        e.preventDefault()
        setPaused((p) => !p)
        setCycle((c) => c + 1)
      } else if (/^[1-9]$/.test(e.key) && Number(e.key) <= SCENES.length) {
        setIdx(Number(e.key) - 1)
        setCycle((c) => c + 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go])

  const h = health.data
  const healthTone: Tone = !h ? 'idle' : h.status === 'healthy' ? 'ok' : 'warn'
  const killOn = kill.data?.active === true

  const scene =
    idx === 0 ? <EvidenceScene /> : idx === 1 ? <CarriersScene /> : idx === 2 ? <FleetScene /> : <SystemScene health={h} />

  return (
    <div className="tv-root">
      <div className="tv-glow" aria-hidden="true" />
      <header className="tv-top">
        <div className="tv-brand">
          <div className="tv-logo">⚡</div>
          <div>
            <div className="tv-brand-name">IATIS</div>
            <div className="tv-brand-sub">TV WALL · PAPER BOOK</div>
          </div>
        </div>
        <div className="tv-pills">
          <Pill tone={healthTone}>{h ? (h.status === 'healthy' ? 'System healthy' : 'Degraded') : 'Connecting'}</Pill>
          <Pill tone={kill.data ? (killOn ? 'bad' : 'ok') : 'idle'}>
            {kill.data ? (killOn ? 'KILL SWITCH ON' : 'Execution armed') : 'Kill switch —'}
          </Pill>
          {h?.ctrader?.configured && <Pill tone="idle">cTrader {h.ctrader.environment}</Pill>}
        </div>
        <Clock />
      </header>

      {!paused && <div key={`${idx}-${cycle}`} className="tv-progress" style={{ animationDuration: `${ROTATE_MS}ms` }} />}

      <main key={idx} className="tv-stage">
        {scene}
      </main>

      <footer className="tv-bottom">
        <nav className="tv-dots">
          {SCENES.map((name, i) => (
            <span key={name} className={`tv-dot-nav ${i === idx ? 'tv-dot-active' : ''}`}>
              {i + 1} {name}
            </span>
          ))}
          <span className="tv-hint">{paused ? '❚❚ paused · OK to resume' : '◀ ▶ switch · OK pause'}</span>
        </nav>
        <Ticker />
      </footer>
    </div>
  )
}
