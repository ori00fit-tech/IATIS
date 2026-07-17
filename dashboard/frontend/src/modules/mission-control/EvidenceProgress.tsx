import { Panel, Empty } from '../../components/Panel'
import { wilsonInterval, maxDrawdownR } from '../../lib/stats'
import type { OutcomesSummary, ClosedOutcomeRow } from './api'

// n≈100 closed paper trades is the milestone the whole evidence case walks
// toward (CLAUDE.md: live forward outcomes are the only defensible edge claim).
const EVIDENCE_TARGET = 100
// Below this, performance figures are noise — the spec's non-negotiable
// evidence guardrail: show them greyed with a "data collection" banner, never
// as headline conclusions.
const MIN_SIGNIFICANT_N = 30

const isBuy = (dir: string) => dir === 'BUY' || dir === 'BULLISH'

/** Realized R of a closed row — same formula as storage/outcome_tracker.py. */
function realizedR(r: ClosedOutcomeRow): number | null {
  if (r.entry_price == null || r.stop_loss == null || r.exit_price == null) return null
  const risk = Math.abs(r.entry_price - r.stop_loss)
  if (risk <= 0) return null
  const diff = isBuy(r.direction) ? r.exit_price - r.entry_price : r.entry_price - r.exit_price
  return diff / risk
}

function fmtPf(pf: number | 'Infinity' | null | undefined): string {
  if (pf == null) return '—'
  if (pf === 'Infinity') return '∞'
  return pf.toFixed(2)
}

function sufficiencyBand(n: number): { label: string; tone: string } {
  if (n < MIN_SIGNIFICANT_N) return { label: 'DATA COLLECTION', tone: 'text-muted border-border' }
  if (n < EVIDENCE_TARGET) return { label: 'FORMING', tone: 'text-amber border-amber/40' }
  return { label: 'SAMPLE SUFFICIENT', tone: 'text-green border-green/40' }
}

function Stat({
  label,
  value,
  sub,
  muted,
}: {
  label: string
  value: string
  sub?: string
  muted: boolean
}) {
  return (
    <div className="border border-border rounded-md p-3">
      <div className="text-muted uppercase text-[0.64em] tracking-[1px] mb-1">{label}</div>
      <div className={`text-[1.25em] font-bold ${muted ? 'text-muted' : 'text-text'}`}>{value}</div>
      {sub && <div className="text-[0.7em] text-muted mt-0.5">{sub}</div>}
    </div>
  )
}

export function EvidenceProgress({ outcomes }: { outcomes: OutcomesSummary | null }) {
  const s = outcomes?.summary
  if (!s) return <Panel title="Evidence Progress"><Empty>No outcome data yet</Empty></Panel>

  const closed = s.total_closed
  const progress = Math.min(100, Math.round((closed / EVIDENCE_TARGET) * 100))
  const insufficient = closed < MIN_SIGNIFICANT_N
  const band = sufficiencyBand(closed)

  const wilson = wilsonInterval(s.wins, closed)
  const rSeries = (outcomes?.recent ?? [])
    .filter((r) => r.outcome !== 'open')
    .map(realizedR)
    .filter((r): r is number => r != null)
  const maxDD = rSeries.length ? maxDrawdownR(rSeries) : null
  const expectancyPips = closed > 0 ? s.total_pips / closed : null

  return (
    <Panel title="Evidence Progress" right={`target: ${EVIDENCE_TARGET} closed trades`}>
      <div className="p-4 flex flex-col gap-4">
        {/* Headline counter + progress */}
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-[1.8em] font-extrabold text-accent leading-none">
              {closed}
              <span className="text-muted text-[0.5em] font-normal"> / {EVIDENCE_TARGET} closed</span>
            </span>
            <span className={`text-[0.62em] font-bold uppercase tracking-[1px] border rounded px-2 py-0.5 ${band.tone}`}>{band.label}</span>
            <span className="text-[0.8em] text-muted">open <b className="text-accent2">{s.open_signals}</b></span>
          </div>
          <div className="h-2 rounded bg-surface border border-border overflow-hidden">
            <div className="h-full bg-gradient-to-r from-accent to-accent2" style={{ width: `${progress}%` }} />
          </div>
        </div>

        {insufficient && (
          <div className="text-[0.78em] text-amber bg-amber/10 border border-amber/30 rounded px-3 py-2">
            Data collection — {closed}/{MIN_SIGNIFICANT_N} toward statistical significance. Figures below are shown for
            transparency but are <b>not yet meaningful</b>; the interval matters more than the point value.
          </div>
        )}

        {/* Win rate with Wilson 95% CI — the interval is the headline */}
        <div className="border border-border rounded-md p-3">
          <div className="flex items-baseline justify-between mb-1">
            <span className="text-muted uppercase text-[0.64em] tracking-[1px]">Win Rate (95% Wilson CI)</span>
            <span className={`text-[0.72em] ${insufficient ? 'text-muted' : 'text-text'}`}>
              W/L {s.wins}/{s.losses}
            </span>
          </div>
          {wilson ? (
            <>
              <div className="flex items-baseline gap-2">
                <span className={`text-[1.4em] font-bold ${insufficient ? 'text-muted' : 'text-text'}`}>
                  {(wilson.center * 100).toFixed(1)}%
                </span>
                <span className="text-[0.8em] text-muted">
                  CI [{(wilson.low * 100).toFixed(1)}% – {(wilson.high * 100).toFixed(1)}%]
                </span>
              </div>
              {/* CI band visual */}
              <div className="relative h-2 mt-2 rounded bg-surface overflow-hidden">
                <div
                  className={`absolute h-full ${insufficient ? 'bg-muted/40' : 'bg-accent/50'}`}
                  style={{ left: `${wilson.low * 100}%`, width: `${(wilson.high - wilson.low) * 100}%` }}
                />
                <div className="absolute top-0 bottom-0 w-px bg-accent" style={{ left: `${wilson.center * 100}%` }} />
                {/* 50% breakeven reference */}
                <div className="absolute top-0 bottom-0 w-px bg-muted/60" style={{ left: '50%' }} />
              </div>
              <div className="text-[0.68em] text-muted mt-1">
                {wilson.low > 0.5
                  ? 'CI clears 50% — win rate is above breakeven at 95%.'
                  : wilson.high < 0.5
                    ? 'CI is entirely below 50%.'
                    : 'CI straddles 50% — not distinguishable from breakeven yet.'}
              </div>
            </>
          ) : (
            <span className="text-muted">—</span>
          )}
        </div>

        {/* Supporting metrics */}
        <div className="grid gap-3 grid-cols-[repeat(auto-fit,minmax(130px,1fr))]">
          <Stat label="Profit Factor" value={fmtPf(s.profit_factor)} muted={insufficient} />
          <Stat
            label="Avg R (expectancy)"
            value={s.avg_r_multiple != null ? `${s.avg_r_multiple >= 0 ? '+' : ''}${s.avg_r_multiple.toFixed(2)}R` : '—'}
            muted={insufficient}
          />
          <Stat
            label="Expectancy / trade"
            value={expectancyPips != null ? `${expectancyPips >= 0 ? '+' : ''}${expectancyPips.toFixed(1)} pips` : '—'}
            muted={insufficient}
          />
          <Stat
            label="Max Drawdown"
            value={maxDD != null ? `−${maxDD.toFixed(2)}R` : '—'}
            sub={maxDD != null ? `over ${rSeries.length} closed` : undefined}
            muted={insufficient}
          />
        </div>

        <p className="text-muted text-[0.72em]">
          Live forward outcomes are the only proof of edge — backtests are in-sample. Point values below n≈{MIN_SIGNIFICANT_N}
          {' '}are noise; treat everything before the target as data collection.
        </p>
      </div>
    </Panel>
  )
}
