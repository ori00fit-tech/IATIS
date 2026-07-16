# Data Sourcing Plan — best source per asset class, measured not assumed

**Written 2026-07-16** alongside the Alpaca integration. This is the
operating doctrine for the data layer: which source serves which asset
class and why, how "best" is decided (by measurement, never by brochure),
and what the honest ceiling of free data is.

## The one architectural fact that answers "feed all engines"

Engines do not have individual feeds. Every engine consumes the same
`mtf_data` dict produced once per run by the failover chain
(`core/data_providers.py` → `main._load_market_data`). Improving the data
for one symbol improves it for **all** engines simultaneously — there is
nothing engine-specific to wire, and any plan that suggests per-engine
feeds misunderstands the pipeline. What CAN be chosen per asset class is
the provider chain, and that is what this plan governs.

## The source matrix (current, with rationale)

| Asset class | Chain (first = primary) | Why this order |
|---|---|---|
| crypto (BTC, ETH) | **ccxt/Binance → alpaca → twelve_data → finnhub → yahoo** | Binance: native H4/D1, free, unlimited, the venue with the deepest crypto liquidity. **Alpaca (added 2026-07-16): a genuinely independent second venue** — first fallback AND the cross-check partner for `core/data_confidence.py`. |
| metals (XAU, XAG) | ctrader → twelve_data → fcs_api → finnhub → yahoo | The broker we execute on is the truth we trade against — its bars ARE the tradeable price. Everything after is failover. |
| fx majors/crosses | ctrader → twelve_data → fcs_api → alpha_vantage → finnhub → yahoo | Same broker-first logic. |
| energy (USOIL) | ctrader → finnhub → yahoo | Thin free coverage; futures-based sources carry roll gaps (documented in the integrity verifier). |
| indices | ctrader → fcs_api → finnhub → yahoo | Yahoo cash indices have no overnight session (28% bar coverage — measured 2026-07-16) and ^IXIC ≠ NDX; broker CFDs first. |

Yahoo is deliberately last everywhere (2026-07-14 decision): no rate-limit
contract, throttles, and its "H4" is a resample of 1h bars.

## What Alpaca does and does NOT add (honesty first)

- **Adds:** a second independent crypto venue. Failover for the two
  strongest carriers (BTC PF≈1.5, ETH PF≈1.4–1.56 — the measured edge),
  and the missing ingredient for runtime cross-validation: comparing
  Binance to a Twelve-Data resample was weak; Binance vs Alpaca is a real
  two-venue check.
- **Does not add:** anything for FX, metals, indices, or energy. Alpaca is
  a US stocks + crypto shop. The documented binding constraint
  (`docs/STRATEGY_EVIDENCE_2026-07.md`: real FX order flow / volume needs
  paid CME-grade data) is untouched by this integration, and no free
  provider will change that.

## How "best" is decided — the measurement loop

1. **Cross-provider diff (offline ranking):** `scripts/cross_provider_diff.py`
   compares any two providers bar-by-bar on a symbol. Run it when adding
   or promoting a provider; MATERIAL_DISAGREEMENT disqualifies a source
   for that symbol until investigated.
2. **Data confidence (runtime guard):** `features.data_confidence_check: true`
   makes the scheduler cross-check ONE symbol per run between its chain's
   top two providers, store the result (`GET /data-confidence`), and alert
   on material divergence. With Alpaca in the crypto chain, the carriers
   get a true two-venue check on every rotation pass.
3. **Integrity verification (batch):** `scripts/verify_data_integrity.py`
   audits stored datasets against market-hour calendars, session
   structure, and event responses (its 2026-07-16 run flagged six FX H1
   files as session-flat — a provider-quality signal, not proof of
   synthetic data, since the same files show real NFP reactions).
4. **Provenance (per decision):** every decision row records
   `{provider, first_ts, last_ts, row_count, sha256}` per timeframe — so
   any future "which data made this decision" question is answerable from
   the database, and a starved or swapped feed is visible in the row.

Promotion/demotion of a provider inside a chain is an ops decision made
from these measurements (precedents: Yahoo demoted 2026-07-14, FCS added
2026-07-14, Alpaca added 2026-07-16). It is **not** a strategy change —
entries/exits/thresholds are untouched — but any primary-source change
for a live symbol should be logged in the ops record because it alters
the bars behind subsequent decisions.

## The ceiling, stated plainly

Free data is at its frontier here. The remaining upgrades all cost money
and should be bought only when D002 opens the live-capital discussion:

| Upgrade | What it buys | When to consider |
|---|---|---|
| Twelve Data paid plan | native H4/D1 for FX/metals (kills the resample dependency) | if cTrader feed ever becomes unavailable |
| CME market data (via a vendor) | real FX/metals order flow & volume | only with a pre-registered hypothesis that needs it |
| Crypto tick/order-book collector (free, Binance/Bybit WS) | genuine order-flow research on carriers | the one "new edge" avenue in STRATEGY_EVIDENCE — explicit decision required, large build |

## Operational notes

- Keys live in `.env` only (`ALPACA_API_KEY` / `ALPACA_API_SECRET`); the
  data host is `data.alpaca.markets` — the `paper-api.alpaca.markets` URL
  is Alpaca's *trading* API and is not used for bars.
- Alpaca refuses non-crypto symbols loudly (`DataFetchError`) rather than
  guessing — chains keep it crypto-only by construction.
- After adding keys: verify with one scheduler run, then check
  `GET /data-health` and (if the flag is on) `GET /data-confidence` for
  the first Binance-vs-Alpaca comparisons.
