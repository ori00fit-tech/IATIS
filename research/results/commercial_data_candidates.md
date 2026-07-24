# Commercial Data Candidates — Evaluation and Recommendations

**Status of every claim below: [UNVERIFIED — no credentials]** unless
marked otherwise. This project has no account with any vendor in this
document. All figures are from public pricing/documentation pages via
WebSearch/WebFetch on 2026-07-24, not from an actual trial or purchase.
Treat this document as a shortlist for a manual pricing call, not as
confirmed procurement terms. See `data_sources_registry.json` for the
scored entries these recommendations summarize.

## Recommendation summary

| Vendor | What it would unlock | Recommendation |
|---|---|---|
| Tardis.dev | Historical tick/order-flow backfill for H104, eliminating the 3-month forward-only wait | **GET A QUOTE** — the one candidate in this whole pass with a real, unmet need behind it |
| CoinGlass | Deeper OI history than the 3 free exchanges gave H019 | **DO NOT BUY** — H019 is resolved (FAILED); reopening its OI leg needs a new hypothesis first, and CoinGlass's actual free-tier depth couldn't be confirmed in this pass anyway (pricing page blocked automated access) |
| Kaiko, Glassnode, Amberdata, CryptoQuant, CoinMetrics | Institutional-grade crypto tick/on-chain data | **DO NOT BUY** — priced for desks running many strategies ($1,000–$55,000+/yr), disproportionate to any single hypothesis in this registry today |
| CFTC yearly archive (not commercial — free) | Deep COT history for H012 | **BUILD THE FREE BACKFILL SCRIPT FIRST** — see Part B.2 of the feasibility report; there is no reason to pay for COT data the source publishes free |
| GDELT (not commercial — free) | Deeper, unrated-limit news history than MarketAux | **CONSIDER AS A NEW HYPOTHESIS**, not a swap into H021 — different sentiment construct, needs its own pre-registration |
| NewsAPI.org, Polygon.io/Massive, Tiingo, Nasdaq Data Link, TradingEconomics, CME DataMine, SEC EDGAR, Stooq, ForexFactory, Deribit | Various | **NO ACTION** — either redundant with an already-adequate free source this project uses, or serves a feature no current hypothesis requires |

---

## Tardis.dev — the one genuine candidate

**Why it's different from the rest of this list**: H104's own
pre-registered decision rule already accepts a slow path (n≥150 after 3
months of live collection) — Tardis.dev is the only vendor found in this
pass whose documented product would directly shorten that specific,
already-identified wait, rather than adding a feature no hypothesis has
asked for.

- **What it offers**: tick-level trade and order-book data since 2019
  across 50+ exchanges including Binance, plus historical funding, open
  interest, and liquidations as secondary datasets.
- **Fit**: would let H104 backtest on real historical CVD data today
  instead of waiting until ~October 2026 for the live collector to
  accumulate n≥150.
- **Unknowns**: no public pricing found in this pass (quote-based
  vendor) — actual cost, minimum contract term, and whether Binance
  aggTrade-equivalent granularity is included at an accessible tier are
  all unconfirmed.
- **Recommendation**: get an actual quote before deciding. If the price
  is proportionate to a single still-unproven hypothesis (this report
  cannot judge that without a number), buying a short historical window
  to pre-test H104's mechanism before committing to 3 months of live
  collection would be a reasonable, bounded experiment — the config-gated
  live pipeline this session built stays exactly as-is either way (the
  historical data would only inform whether to keep the collector running
  through the full clock, not replace it, since the live collector must
  keep running regardless for the forward evidence to ever accrue).

## Everything else — why "do not buy" is the honest answer today

The brief asked this report to search broadly, and it did — 17
not-yet-integrated vendors were researched (see
`data_sources_registry.json` for each one's scored entry). The finding,
stated plainly rather than padded: **this project's free-data-only
discipline (CLAUDE.md data layer notes) is not costing it access to
anything a currently-registered hypothesis actually needs.** Every
commercial vendor evaluated either:

1. **Duplicates a free source already in production** at comparable or
   worse depth (Polygon.io/Massive, Tiingo, TradingEconomics vs.
   cTrader/Twelve Data/FRED), or
2. **Serves a feature no hypothesis in the registry requires** (Deribit
   options skew, CME futures, SEC EDGAR equity filings — evaluated for
   completeness per the brief's required-feature checklist, not because
   any H-number asked for them), or
3. **Would only extend the one leg (crypto OI) that a single resolved
   hypothesis (H019) already tried, measured, and closed** — reopening it
   needs a new hypothesis with its own pre-registered decision rule
   before a purchase would even be well-defined, per CLAUDE.md's evidence
   discipline.

This mirrors the judgment the operator already made once this session for
H019's OI leg specifically (CryptoQuant/CoinGlass/Glassnode/Kaiko/
Amberdata/Tardis.dev all researched, all rejected as disproportionate)
— this pass's broader search reached the same conclusion for every other
open hypothesis too, not just H019.
