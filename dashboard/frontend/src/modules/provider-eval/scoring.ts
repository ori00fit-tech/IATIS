import type { ProviderChainsResponse } from '../system-audit/api'
import type { DataConfidenceHistory } from '../data-center/api'

// Deterministic provider quality score. "Best valid data" for IATIS means,
// above all, NATIVE decision-timeframe candles — CLAUDE.md is explicit that a
// resampled or wrong-instrument bar (Yahoo's "H4" is a 1h resample) silently
// poisons decisions, which is worse than a missing bar. So native H4/D1/H1
// coverage dominates the score, then whether the provider is usable now, how
// far it is trusted in the configured chains, whether it has actually been
// serving, and how well its data agrees with the independent cross-check.

// Decision timeframes (config data.timeframes) and their weight. H4 is the
// primary decision TF, D1 the MTF confirmation, H1 auxiliary timing.
const DECISION_TF_WEIGHTS: Record<string, number> = { H4: 20, D1: 12, H1: 8 }
const DECISION_TFS = Object.keys(DECISION_TF_WEIGHTS)
const W_AVAILABILITY = 20
const W_CHAIN_TRUST = 20
const W_USAGE = 10
const W_AGREEMENT = 10

// Institutional caveats sourced from config.yaml's provider_chains comments
// and CLAUDE.md's data-layer section — measured facts the raw endpoints don't
// carry. Shown as context, never folded silently into the number.
export const PROVIDER_NOTES: Record<string, string> = {
  ctrader: 'Broker feed — native all timeframes; primary when credentials are present.',
  ccxt: 'Binance, native H4/D1 for crypto — first in the crypto chain.',
  alpaca: 'Crypto only. First crypto fallback and the independent cross-check partner for data-confidence on carriers.',
  twelve_data: 'Free plan serves no native H4/D1 (resampled); several symbols are plan-gated (404).',
  fcs_api: 'FX / metals / indices only (no crypto endpoint used).',
  alpha_vantage: 'FX only; no native H4/D1.',
  finnhub: 'Deep fallback across asset classes.',
  yahoo_finance:
    'Removed from every price chain (2026-07-16): measured wrong instruments (^IXIC≠NDX, futures≠spot metals), cash-session gaps, and H4 as a 1h resample. Kept for offline diffs only.',
}

export type AvailabilityState = 'up' | 'down' | 'unknown'

export interface ProviderScore {
  provider: string
  score: number
  // 'unknown' when the endpoint doesn't report this provider's availability
  // (e.g. fcs_api/alpaca aren't in /provider-chains' availability map) — not
  // the same as measured-down, so it earns partial rather than zero credit.
  availability_state: AvailabilityState
  // False when the provider is in no configured price chain — it delivers zero
  // data to the pipeline right now regardless of how good its feed is (e.g.
  // yahoo_finance, removed for cause), so its score is hard-capped.
  inActiveChain: boolean
  nativeDecisionTFs: string[]
  resampledDecisionTFs: string[] // decision TFs it does NOT serve natively
  chainsIn: { cls: string; pos: number; len: number }[]
  usageCount: number
  lastUsed: string | null
  disagreements: number
  checksInvolving: number
  breakdown: { native: number; availability: number; chainTrust: number; usage: number; agreement: number }
  note?: string
}

/** Score a single provider given the pre-aggregated inputs. */
function scoreProvider(
  provider: string,
  pc: ProviderChainsResponse,
  agree: Record<string, { involved: number; material: number }>,
  maxUsage: number,
): ProviderScore {
  const nativeSet = new Set(pc.native_timeframes[provider] ?? [])
  const nativeDecisionTFs = DECISION_TFS.filter((tf) => nativeSet.has(tf))
  const resampledDecisionTFs = DECISION_TFS.filter((tf) => !nativeSet.has(tf))
  const native = nativeDecisionTFs.reduce((s, tf) => s + DECISION_TF_WEIGHTS[tf], 0)

  const avEntry = pc.availability[provider]
  const availability_state: AvailabilityState = avEntry === true ? 'up' : avEntry === false ? 'down' : 'unknown'
  const availability = availability_state === 'up' ? W_AVAILABILITY : availability_state === 'unknown' ? W_AVAILABILITY / 2 : 0

  // Chain trust: average normalized position across every asset-class chain the
  // provider appears in (first = 1, last = 0). Not in any chain → 0.
  const chainsIn: { cls: string; pos: number; len: number }[] = []
  for (const [cls, chain] of Object.entries(pc.chains)) {
    const pos = chain.indexOf(provider)
    if (pos >= 0) chainsIn.push({ cls, pos, len: chain.length })
  }
  const chainTrust = chainsIn.length
    ? (chainsIn.reduce((s, c) => s + (c.len > 1 ? (c.len - 1 - c.pos) / (c.len - 1) : 1), 0) / chainsIn.length) *
      W_CHAIN_TRUST
    : 0

  const usageEntry = pc.recent_usage[provider]
  const usageCount = usageEntry?.count ?? 0
  const lastUsed = usageEntry?.last_used_at ?? null
  // Any recent service is positive confirmation the provider works; more use
  // scales it. Never-used deep fallbacks simply earn nothing here (not a penalty).
  const usage = usageCount > 0 ? W_USAGE * (0.5 + 0.5 * (maxUsage > 0 ? usageCount / maxUsage : 0)) : 0

  const a = agree[provider] ?? { involved: 0, material: 0 }
  const agreement = a.involved > 0 ? W_AGREEMENT * (1 - a.material / a.involved) : W_AGREEMENT

  const inActiveChain = chainsIn.length > 0
  const raw = Math.round(native + availability + chainTrust + usage + agreement)
  // A provider in no chain currently serves nothing, so it can't be "best valid
  // data" however capable its feed — cap it well below any in-use provider.
  const score = inActiveChain ? raw : Math.min(raw, 25)
  return {
    provider,
    score,
    availability_state,
    inActiveChain,
    nativeDecisionTFs,
    resampledDecisionTFs,
    chainsIn,
    usageCount,
    lastUsed,
    disagreements: a.material,
    checksInvolving: a.involved,
    breakdown: {
      native,
      availability,
      chainTrust: Math.round(chainTrust),
      usage: Math.round(usage),
      agreement: Math.round(agreement),
    },
    note: PROVIDER_NOTES[provider],
  }
}

/** Evaluate and rank every known provider, best first. */
export function evaluateProviders(pc: ProviderChainsResponse, dc: DataConfidenceHistory | null): ProviderScore[] {
  // Per-provider cross-check tallies from the data-confidence ledger.
  const agree: Record<string, { involved: number; material: number }> = {}
  for (const c of dc?.checks ?? []) {
    const material = String(c.verdict ?? '').startsWith('MATERIAL')
    for (const p of [c.provider_a, c.provider_b]) {
      if (!p) continue
      const e = (agree[p] ??= { involved: 0, material: 0 })
      e.involved += 1
      if (material) e.material += 1
    }
  }

  const providers = new Set<string>([
    ...Object.keys(pc.native_timeframes ?? {}),
    ...Object.keys(pc.availability ?? {}),
    ...Object.values(pc.chains ?? {}).flat(),
    ...Object.keys(pc.recent_usage ?? {}),
  ])
  const maxUsage = Math.max(0, ...Object.values(pc.recent_usage ?? {}).map((u) => u.count ?? 0))

  return [...providers]
    .map((p) => scoreProvider(p, pc, agree, maxUsage))
    .sort((a, b) => b.score - a.score || a.provider.localeCompare(b.provider))
}

export interface ChainReview {
  cls: string
  current: string[]
  suggested: string[]
  differs: boolean
}

/**
 * Advisory per-asset-class ordering: re-sort each configured chain by provider
 * score. This is a read, not an action — the configured order encodes measured
 * reliability the dashboard can't fully see (e.g. why Yahoo was removed), so
 * divergences are prompts to investigate, never to auto-apply.
 */
export function reviewChains(pc: ProviderChainsResponse, ranked: ProviderScore[]): ChainReview[] {
  const scoreOf = new Map(ranked.map((r) => [r.provider, r.score]))
  return Object.entries(pc.chains).map(([cls, current]) => {
    const suggested = [...current].sort((a, b) => (scoreOf.get(b) ?? 0) - (scoreOf.get(a) ?? 0))
    return { cls, current, suggested, differs: current.join() !== suggested.join() }
  })
}
