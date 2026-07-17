import { usePolling } from '../../lib/usePolling'
import { useAuth } from '../../lib/auth'
import { Panel, Empty } from '../../components/Panel'
import { getProviderChains, getDataConfidence } from './api'
import { evaluateProviders, reviewChains, RETIRED_PROVIDERS, type ProviderScore } from './scoring'

const POLL_MS = 60_000

function scoreColor(s: number): string {
  if (s >= 75) return 'text-green'
  if (s >= 55) return 'text-amber'
  return 'text-red'
}
function scoreBar(s: number): string {
  if (s >= 75) return 'bg-green'
  if (s >= 55) return 'bg-amber'
  return 'bg-red'
}

function fmtAge(iso: string | null): string {
  if (!iso) return 'never'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = (Date.now() - t) / 1000
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172800) return `${(s / 3600).toFixed(0)}h ago`
  return `${(s / 86400).toFixed(0)}d ago`
}

function ProviderRow({ p, rank }: { p: ProviderScore; rank: number }) {
  const b = p.breakdown
  return (
    <div className="border-b border-border last:border-b-0 px-4 py-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-muted text-[0.8em] w-6 shrink-0">#{rank}</span>
        <span className="font-bold text-accent w-28 shrink-0">{p.provider}</span>
        <span
          className={`inline-block w-2 h-2 rounded-full shrink-0 ${p.availability_state === 'up' ? 'bg-green' : p.availability_state === 'down' ? 'bg-red' : 'bg-muted'}`}
          title={p.availability_state === 'up' ? 'usable now (credentials present)' : p.availability_state === 'down' ? 'unavailable — no credentials / dependency' : 'availability not reported by /provider-chains'}
        />
        <div className="flex-1 min-w-[120px] h-2 rounded bg-surface overflow-hidden">
          <div className={`h-full ${scoreBar(p.score)}`} style={{ width: `${p.score}%` }} />
        </div>
        <span className={`font-extrabold w-10 text-right shrink-0 ${scoreColor(p.score)}`}>{p.score}</span>
      </div>
      <div className="flex items-center gap-2 flex-wrap mt-2 pl-9 text-[0.72em]">
        {/* Native decision-TF coverage — the headline "valid data" signal */}
        {['H4', 'D1', 'H1'].map((tf) => {
          const native = p.nativeDecisionTFs.includes(tf)
          return (
            <span
              key={tf}
              className={`px-1.5 py-0.5 rounded border ${native ? 'border-green/40 text-green' : 'border-red/30 text-red/80'}`}
              title={native ? `${tf} served natively` : `${tf} NOT native — would be resampled (decision-poisoning risk)`}
            >
              {tf} {native ? 'native' : 'resampled'}
            </span>
          )
        })}
        {p.inActiveChain ? (
          <span className="text-muted">chains: {p.chainsIn.map((c) => c.cls).join(', ')}</span>
        ) : (
          <span className="text-red/80 border border-red/30 rounded px-1.5 py-0.5" title="Not in any configured chain — delivers no data to the pipeline right now">
            not in any chain
          </span>
        )}
        <span className="text-muted">served {p.usageCount}× · {fmtAge(p.lastUsed)}</span>
        {p.checksInvolving > 0 && (
          <span className={p.disagreements > 0 ? 'text-amber' : 'text-muted'}>
            agreement {p.checksInvolving - p.disagreements}/{p.checksInvolving}
          </span>
        )}
        <span className="text-muted/70" title="score = native TF (40) + availability (20) + chain trust (20) + usage (10) + agreement (10)">
          [{b.native}+{b.availability}+{b.chainTrust}+{b.usage}+{b.agreement}]
        </span>
      </div>
      {p.note && <div className="pl-9 mt-1 text-[0.68em] text-muted/80 italic">{p.note}</div>}
    </div>
  )
}

export function ProviderEval() {
  const { markUnauthenticated } = useAuth()
  const chains = usePolling(getProviderChains, POLL_MS, markUnauthenticated)
  const confidence = usePolling(getDataConfidence, POLL_MS, markUnauthenticated)

  if (!chains.data) {
    return (
      <Panel title="Provider Evaluation">
        <Empty>{chains.loading ? 'Loading provider chains…' : 'No provider data available'}</Empty>
      </Panel>
    )
  }

  const ranked = evaluateProviders(chains.data, confidence.data)
  const reviews = reviewChains(chains.data, ranked)
  const best = ranked[0]

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[0.78em] text-muted">
        Ranks every data provider for its ability to deliver <b>valid</b> data to the pipeline. Native decision-timeframe
        candles dominate the score — a resampled or wrong-instrument bar silently poisons decisions, which is worse than a
        missing one (CLAUDE.md). Synthesis over <code className="text-accent2">/provider-chains</code> and{' '}
        <code className="text-accent2">/data-confidence</code>; it never changes a chain.
      </p>

      {best && (
        <div className="px-3.5 py-2.5 rounded-md border border-green/30 bg-green/5 text-[0.82em]">
          Top provider: <b className="text-green">{best.provider}</b> ({best.score}/100) — native{' '}
          {best.nativeDecisionTFs.join('/') || 'none'} · in {best.chainsIn.length} chain(s)
        </div>
      )}

      <Panel title="Provider Ranking" right={`${ranked.length} providers · best data first`}>
        <div>
          {ranked.map((p, i) => (
            <ProviderRow key={p.provider} p={p} rank={i + 1} />
          ))}
        </div>
        {RETIRED_PROVIDERS.size > 0 && (
          <div className="px-4 py-2 border-t border-border text-[0.7em] text-muted">
            Retired (untrusted, excluded): {[...RETIRED_PROVIDERS].join(', ')} — removed from all price chains and
            replaced by CBOE/FRED in the macro layer.
          </div>
        )}
      </Panel>

      <Panel title="Chain Order Review" right="advisory — configured order encodes measured reliability">
        <div className="p-4 flex flex-col gap-3">
          <p className="text-[0.74em] text-muted">
            Each configured chain re-sorted by score. A divergence is a prompt to investigate — not an instruction to
            re-order. The live order is authoritative and changed only in <code className="text-accent2">config.yaml</code>{' '}
            by an operator.
          </p>
          {reviews.map((r) => (
            <div key={r.cls} className="border border-border rounded-md p-3 text-[0.8em]">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="font-bold text-accent uppercase text-[0.8em] tracking-[1px]">{r.cls}</span>
                {r.differs ? (
                  <span className="text-[0.7em] text-amber border border-amber/40 rounded px-1.5 py-0.5">differs</span>
                ) : (
                  <span className="text-[0.7em] text-green border border-green/40 rounded px-1.5 py-0.5">aligned</span>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <div className="flex gap-2">
                  <span className="text-muted w-20 shrink-0">configured</span>
                  <span className="font-mono text-[0.92em]">{r.current.join(' → ')}</span>
                </div>
                {r.differs && (
                  <div className="flex gap-2">
                    <span className="text-muted w-20 shrink-0">by score</span>
                    <span className="font-mono text-[0.92em] text-amber">{r.suggested.join(' → ')}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  )
}
