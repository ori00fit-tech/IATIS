import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Panel, Empty } from '../../components/Panel'
import { KpiCard } from '../../components/KpiCard'
import { Badge } from '../../components/Badge'
import { usePolling } from '../../lib/usePolling'
import {
  getJournal,
  getJournalStats,
  annotateTrade,
  JOURNAL_EXPORT_URL,
  type JournalFilters,
  type JournalListing,
  type JournalStats,
  type JournalTrade,
  type JournalBucket,
  type EquityPoint,
} from './api'

const inputCls = 'bg-surface border border-border rounded px-2 py-1.5 text-[0.82em] text-text placeholder:text-muted'
const PAGE_SIZE = 50

const fmtR = (r: number | null | undefined, digits = 2) =>
  r == null ? '—' : `${r >= 0 ? '+' : ''}${r.toFixed(digits)}R`
const fmtPf = (pf: number | 'Infinity' | null | undefined) =>
  pf == null ? '—' : pf === 'Infinity' ? '∞' : pf.toFixed(2)
const fmtTime = (t: string | null | undefined) => (t ? t.slice(0, 16).replace('T', ' ') : '—')
const fmtDuration = (h: number | null | undefined) => {
  if (h == null) return '—'
  if (h < 48) return `${h.toFixed(0)}h`
  return `${(h / 24).toFixed(1)}d`
}

function outcomeTone(outcome: string): 'good' | 'poor' | 'marginal' | 'neutral' {
  if (outcome === 'win') return 'good'
  if (outcome === 'loss') return 'poor'
  if (outcome === 'breakeven') return 'marginal'
  return 'neutral'
}

// ── Equity curve (cumulative R, chronological by exit) ──────────────────────

function EquityCurveR({ curve }: { curve: EquityPoint[] }) {
  const [hover, setHover] = useState<number | null>(null)
  if (curve.length < 2) {
    return <Empty>Equity curve appears once ≥ 2 trades have closed</Empty>
  }
  const W = 800
  const H = 220
  const PAD = 8
  const values = curve.map((p) => p.cum_r)
  const min = Math.min(0, ...values)
  const max = Math.max(0, ...values)
  const range = max - min || 1
  const x = (i: number) => PAD + (i / (curve.length - 1)) * (W - 2 * PAD)
  const y = (v: number) => PAD + (H - 2 * PAD) * (1 - (v - min) / range)
  const linePath = curve.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.cum_r).toFixed(1)}`).join(' ')
  const areaPath = `${linePath} L${x(curve.length - 1).toFixed(1)},${y(Math.max(min, 0)).toFixed(1)} L${x(0).toFixed(1)},${y(Math.max(min, 0)).toFixed(1)} Z`
  const end = values[values.length - 1]
  const up = end >= 0
  const stroke = up ? 'var(--green)' : 'var(--red)'
  const hovered = hover != null ? curve[hover] : null

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = (e.clientX - rect.left) / rect.width
    const i = Math.round(frac * (curve.length - 1))
    setHover(Math.max(0, Math.min(curve.length - 1, i)))
  }

  return (
    <div className="p-4 flex flex-col gap-2">
      <div className="flex items-baseline gap-4 flex-wrap text-[0.82em]">
        <span>
          Cumulative <b className={up ? 'text-green' : 'text-red'}>{fmtR(end)}</b>
        </span>
        <span className="text-muted">{curve.length} closed trades · chronological by exit</span>
        {hovered && (
          <span className="text-muted">
            {fmtTime(hovered.exit_time)} · trade <b className={hovered.r >= 0 ? 'text-green' : 'text-red'}>{fmtR(hovered.r)}</b> · book{' '}
            <b className="text-text">{fmtR(hovered.cum_r)}</b>
          </span>
        )}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="w-full h-[220px] cursor-crosshair"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id="jrnfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* zero-R baseline */}
        <line x1={PAD} y1={y(0)} x2={W - PAD} y2={y(0)} stroke="var(--border)" strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
        <path d={areaPath} fill="url(#jrnfill)" />
        <path d={linePath} fill="none" stroke={stroke} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {hover != null && (
          <>
            <line x1={x(hover)} y1={PAD} x2={x(hover)} y2={H - PAD} stroke="var(--accent)" strokeWidth="1" vectorEffect="non-scaling-stroke" opacity="0.6" />
            <circle cx={x(hover)} cy={y(curve[hover].cum_r)} r="4" fill={stroke} stroke="var(--card, #0d1117)" strokeWidth="2" />
          </>
        )}
      </svg>
    </div>
  )
}

// ── Breakdown table (by symbol / regime / direction) ────────────────────────

function BreakdownTable({ rows, labelKey, title }: { rows: JournalBucket[]; labelKey: 'symbol' | 'regime' | 'direction'; title: string }) {
  if (rows.length === 0) return null
  return (
    <div>
      <div className="text-muted uppercase text-[0.64em] tracking-[1px] mb-1.5">{title}</div>
      <table className="w-full border-collapse text-[0.78em]">
        <thead>
          <tr>
            {[title, 'N', 'WR', 'ΣR', 'avg R'].map((h, i) => (
              <th key={h} className={`px-2 py-1 text-muted text-[0.85em] uppercase bg-surface font-semibold ${i === 0 ? 'text-left' : 'text-right'}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={String(b[labelKey])} className="[&:last-child>td]:border-b-0">
              <td className="px-2 py-1.5 border-b border-border font-bold">{b[labelKey]}</td>
              <td className="px-2 py-1.5 border-b border-border text-right">{b.n}</td>
              <td className="px-2 py-1.5 border-b border-border text-right">{b.win_rate != null ? `${b.win_rate.toFixed(0)}%` : '—'}</td>
              <td className={`px-2 py-1.5 border-b border-border text-right font-bold ${(b.total_r ?? 0) >= 0 ? 'text-green' : 'text-red'}`}>
                {fmtR(b.total_r)}
              </td>
              <td className="px-2 py-1.5 border-b border-border text-right">{fmtR(b.avg_r)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Per-trade detail (expanded row) ─────────────────────────────────────────

function PriceCell({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="border border-border rounded p-2">
      <div className="text-muted uppercase text-[0.62em] tracking-[1px]">{label}</div>
      <div className="text-[0.9em] font-bold">{value != null ? value : '—'}</div>
    </div>
  )
}

function TradeDetail({ trade, onSaved }: { trade: JournalTrade; onSaved: () => void }) {
  const [notes, setNotes] = useState(trade.notes ?? '')
  const [tags, setTags] = useState(trade.tags.join(', '))
  const [saving, setSaving] = useState(false)
  const [saveState, setSaveState] = useState<'idle' | 'saved' | 'error'>('idle')

  const save = async () => {
    setSaving(true)
    setSaveState('idle')
    try {
      await annotateTrade(trade.signal_id, {
        notes,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
      })
      setSaveState('saved')
      onSaved()
    } catch {
      setSaveState('error')
    } finally {
      setSaving(false)
    }
  }

  const engineEntries = Object.entries(trade.engines)

  return (
    <div className="p-4 bg-surface/50 flex flex-col gap-4">
      <div className="grid gap-2 grid-cols-[repeat(auto-fit,minmax(110px,1fr))]">
        <PriceCell label="Entry" value={trade.entry_price} />
        <PriceCell label="Stop Loss" value={trade.stop_loss} />
        <PriceCell label="Take Profit" value={trade.take_profit} />
        <PriceCell label="Exit" value={trade.exit_price} />
        <div className="border border-border rounded p-2">
          <div className="text-muted uppercase text-[0.62em] tracking-[1px]">Planned RR</div>
          <div className="text-[0.9em] font-bold">{trade.planned_rr != null ? `${trade.planned_rr.toFixed(1)}:1` : '—'}</div>
        </div>
        <div className="border border-border rounded p-2">
          <div className="text-muted uppercase text-[0.62em] tracking-[1px]">Duration</div>
          <div className="text-[0.9em] font-bold">{fmtDuration(trade.duration_hours)}</div>
        </div>
      </div>

      <div className="flex gap-6 flex-wrap text-[0.8em]">
        <span>
          Regime <b className="text-accent2">{trade.regime ?? '—'}</b>
        </span>
        <span>
          Confluence <b className="text-accent">{trade.cf_score ?? '—'}</b>
        </span>
        <span>
          News risk <b>{trade.news_risk ?? '—'}</b>
        </span>
        <span className="text-muted">
          {fmtTime(trade.entry_time)} → {fmtTime(trade.exit_time)}
        </span>
      </div>

      {engineEntries.length > 0 && (
        <div>
          <div className="text-muted uppercase text-[0.64em] tracking-[1px] mb-1.5">Engine votes at signal time</div>
          <div className="flex gap-2 flex-wrap">
            {engineEntries.map(([name, vote]) => (
              <span key={name} className="inline-flex items-center gap-1.5 border border-border rounded px-2 py-1 text-[0.75em]">
                <b>{name}</b>
                <span className={vote.bias === 'BULLISH' ? 'text-green' : vote.bias === 'BEARISH' ? 'text-red' : 'text-muted'}>
                  {vote.bias}
                </span>
                <span className="text-muted">{vote.score}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div className="text-muted uppercase text-[0.64em] tracking-[1px]">Notes & tags (annotation only — never feeds any gate)</div>
        <textarea
          className={`${inputCls} min-h-[64px] w-full resize-y`}
          placeholder="What did this trade teach? News context, execution quality, setup grade…"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <div className="flex gap-2 items-center flex-wrap">
          <input
            className={`${inputCls} flex-1 min-w-[200px]`}
            placeholder="tags, comma-separated (e.g. news-spike, a-plus-setup)"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
          />
          <button
            className="bg-accent/15 text-accent border border-accent/40 rounded px-3 py-1.5 text-[0.78em] font-bold hover:bg-accent/25 disabled:opacity-50"
            onClick={save}
            disabled={saving}
          >
            {saving ? 'Saving…' : 'Save annotation'}
          </button>
          {saveState === 'saved' && <span className="text-green text-[0.75em]">Saved</span>}
          {saveState === 'error' && <span className="text-red text-[0.75em]">Save failed — check the server log</span>}
        </div>
      </div>
    </div>
  )
}

// ── Main module ─────────────────────────────────────────────────────────────

export function Journal() {
  const stats = usePolling<JournalStats>(getJournalStats, 60_000)

  const [filters, setFilters] = useState<JournalFilters>({})
  const [page, setPage] = useState(0)
  const [listing, setListing] = useState<JournalListing | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const loadTrades = useCallback(async () => {
    try {
      const res = await getJournal({ ...filters, limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      setListing(res)
      setListError(null)
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err))
    }
  }, [filters, page])

  useEffect(() => {
    loadTrades()
  }, [loadTrades])

  const symbols = useMemo(
    () => (stats.data?.by_symbol ?? []).map((b) => b.symbol!).filter(Boolean).sort(),
    [stats.data],
  )
  const s = stats.data
  const pages = listing ? Math.ceil(listing.total / PAGE_SIZE) : 0

  const setFilter = (patch: Partial<JournalFilters>) => {
    setPage(0)
    setExpanded(null)
    setFilters((f) => ({ ...f, ...patch }))
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Headline stats */}
      <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(150px,1fr))]">
        <KpiCard value={s ? `${s.closed}` : '—'} label={`Closed trades · ${s?.open ?? 0} open`} color="blue" />
        <KpiCard value={s?.win_rate != null ? `${s.win_rate.toFixed(1)}%` : '—'} label={`Win rate (${s?.wins ?? 0}W/${s?.losses ?? 0}L)`} color={s?.win_rate != null && s.win_rate >= 50 ? 'green' : 'amber'} />
        <KpiCard value={s ? fmtPf(s.profit_factor) : '—'} label="Profit factor (R-based)" color={typeof s?.profit_factor === 'number' && s.profit_factor < 1 ? 'red' : 'green'} />
        <KpiCard value={s ? fmtR(s.total_r) : '—'} label="Total R" color={(s?.total_r ?? 0) >= 0 ? 'green' : 'red'} />
        <KpiCard value={s ? fmtR(s.avg_r, 3) : '—'} label="Expectancy / trade" color={(s?.avg_r ?? 0) >= 0 ? 'green' : 'red'} />
        <KpiCard value={s?.max_drawdown_r != null ? `−${s.max_drawdown_r.toFixed(2)}R` : '—'} label="Max drawdown" color="purple" />
      </div>

      {/* Equity curve */}
      <Panel
        title="Equity Curve (cumulative R)"
        right={s ? `streaks: ${s.longest_win_streak}W / ${s.longest_loss_streak}L · avg hold ${fmtDuration(s.avg_duration_hours)}` : undefined}
      >
        {s ? <EquityCurveR curve={s.equity_curve} /> : <Empty>Loading…</Empty>}
      </Panel>

      {/* Trades table */}
      <Panel
        title="Trade Journal"
        right={
          <a
            className="text-accent hover:underline"
            href={JOURNAL_EXPORT_URL}
            download
          >
            Export CSV ↓
          </a>
        }
      >
        <div className="p-3 flex gap-2 flex-wrap items-center border-b border-border">
          <select className={inputCls} value={filters.symbol ?? ''} onChange={(e) => setFilter({ symbol: e.target.value || undefined })}>
            <option value="">All symbols</option>
            {symbols.map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>
          <select className={inputCls} value={filters.outcome ?? ''} onChange={(e) => setFilter({ outcome: e.target.value || undefined })}>
            <option value="">Any outcome</option>
            <option value="win">Win</option>
            <option value="loss">Loss</option>
            <option value="breakeven">Breakeven</option>
            <option value="open">Open</option>
          </select>
          <select className={inputCls} value={filters.direction ?? ''} onChange={(e) => setFilter({ direction: e.target.value || undefined })}>
            <option value="">Any direction</option>
            <option value="BUY">Long</option>
            <option value="SELL">Short</option>
          </select>
          <select className={inputCls} value={filters.regime ?? ''} onChange={(e) => setFilter({ regime: e.target.value || undefined })}>
            <option value="">Any regime</option>
            <option value="TRENDING">Trending</option>
            <option value="RANGING">Ranging</option>
            <option value="VOLATILE">Volatile</option>
          </select>
          <input
            className={`${inputCls} flex-1 min-w-[160px]`}
            placeholder="Search notes / signal id…"
            value={filters.search ?? ''}
            onChange={(e) => setFilter({ search: e.target.value || undefined })}
          />
          {listing && (
            <span className="text-muted text-[0.75em]">
              {listing.total} match{listing.total === 1 ? '' : 'es'}
            </span>
          )}
        </div>

        {listError ? (
          <Empty>Journal unavailable — {listError}</Empty>
        ) : !listing ? (
          <Empty>Loading…</Empty>
        ) : listing.trades.length === 0 ? (
          <Empty>No trades match these filters</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[0.82em]">
              <thead>
                <tr>
                  {['', 'Entry time', 'Symbol', 'Dir', 'Outcome', 'R', 'Pips', 'Score', 'Regime', 'Hold', 'Tags'].map((h) => (
                    <th key={h || 'expand'} className="px-3 py-2 text-left text-muted text-[0.75em] uppercase tracking-[0.8px] bg-surface font-semibold">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {listing.trades.map((t) => {
                  const isOpen = expanded === t.signal_id
                  const isBuy = t.direction === 'BUY' || t.direction === 'BULLISH'
                  return (
                    <Fragment key={t.signal_id}>
                      <tr
                        className="hover:bg-accent/[0.03] cursor-pointer"
                        onClick={() => setExpanded(isOpen ? null : t.signal_id)}
                      >
                        <td className="px-3 py-2.5 border-b border-border text-muted">{isOpen ? '▾' : '▸'}</td>
                        <td className="px-3 py-2.5 border-b border-border whitespace-nowrap">{fmtTime(t.entry_time)}</td>
                        <td className="px-3 py-2.5 border-b border-border font-bold">{t.symbol}</td>
                        <td className={`px-3 py-2.5 border-b border-border font-bold ${isBuy ? 'text-green' : 'text-red'}`}>
                          {isBuy ? 'LONG' : 'SHORT'}
                        </td>
                        <td className="px-3 py-2.5 border-b border-border">
                          <Badge tone={outcomeTone(t.outcome)}>{t.outcome.toUpperCase()}</Badge>
                        </td>
                        <td className={`px-3 py-2.5 border-b border-border font-bold ${(t.realized_r ?? 0) > 0 ? 'text-green' : (t.realized_r ?? 0) < 0 ? 'text-red' : ''}`}>
                          {t.outcome === 'open' ? '—' : fmtR(t.realized_r)}
                        </td>
                        <td className="px-3 py-2.5 border-b border-border">{t.pnl_pips_clean != null ? t.pnl_pips_clean.toFixed(0) : '—'}</td>
                        <td className="px-3 py-2.5 border-b border-border">{t.cf_score ?? '—'}</td>
                        <td className="px-3 py-2.5 border-b border-border text-muted">{t.regime ?? '—'}</td>
                        <td className="px-3 py-2.5 border-b border-border">{fmtDuration(t.duration_hours)}</td>
                        <td className="px-3 py-2.5 border-b border-border">
                          <span className="flex gap-1 flex-wrap">
                            {t.tags.map((tag) => (
                              <span key={tag} className="bg-accent2/10 text-accent2 rounded px-1.5 py-0.5 text-[0.72em]">
                                {tag}
                              </span>
                            ))}
                            {t.notes && <span className="text-muted text-[0.72em]" title={t.notes}>✎</span>}
                          </span>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr>
                          <td colSpan={11} className="border-b border-border p-0">
                            <TradeDetail trade={t} onSaved={loadTrades} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 && (
          <div className="flex items-center gap-2 p-3 border-t border-border text-[0.8em]">
            <button
              className="border border-border rounded px-2 py-1 disabled:opacity-40 hover:border-accent/50"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              ← Prev
            </button>
            <span className="text-muted">
              page {page + 1} / {pages}
            </span>
            <button
              className="border border-border rounded px-2 py-1 disabled:opacity-40 hover:border-accent/50"
              disabled={page + 1 >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next →
            </button>
          </div>
        )}
      </Panel>

      {/* Breakdowns */}
      {s && s.closed > 0 && (
        <Panel title="Breakdowns" right="R recomputed from prices — stored pnl columns are never trusted">
          <div className="p-4 grid gap-6 grid-cols-[repeat(auto-fit,minmax(260px,1fr))]">
            <BreakdownTable rows={s.by_symbol} labelKey="symbol" title="Symbol" />
            <BreakdownTable rows={s.by_regime} labelKey="regime" title="Regime" />
            <BreakdownTable rows={s.by_direction} labelKey="direction" title="Direction" />
          </div>
        </Panel>
      )}

      <p className="text-muted text-[0.72em] px-1">
        The journal reads the paper-trading outcomes ledger. Notes and tags are operator annotations only — they never
        feed a gate, weight, or measurement. Below n≈30 closed trades, treat every aggregate as data collection, not
        evidence.
      </p>
    </div>
  )
}
