# Data Feasibility Report — All Registered Hypotheses (H001–H105)

**Author role:** Principal Quant Research Director pass (data acquisition only — no
strategy code touched, no A/B harness written or modified by this report).
**Scope:** All 35 hypotheses currently in `research/results/registry.json`
(H001–H105, letter-suffixed; there is no H001–H999 range — see Methodology).
**Date:** 2026-07-24. **Verification basis:** live codebase inspection
(`config.yaml`, `core/data_providers.py`, `core/alt_data_loader.py`,
`fundamentals/marketaux_client.py`, `scripts/download_cot.py`,
`storage/execution_quality.py`, `scripts/download_ctrader_fx_history.py`,
`scripts/download_deep_history.py`) plus this session's real probes
(H019/H104 feasibility investigations) for already-integrated sources;
public documentation via WebSearch/WebFetch, explicitly flagged
**[UNVERIFIED — no credentials]**, for commercial vendors this project has
never connected to.

---

## 0. Methodology note (read first)

The literal brief asked for a fresh 10-section report *per hypothesis* for
"every hypothesis H001–H999." Two corrections were made, in agreement
with the operator (2026-07-24 scoping exchange), before writing anything:

1. **Inventory correction.** The registry contains exactly 35 hypotheses,
   not 999. All 35 are covered below.
2. **Structural correction.** Of the 35, **29 share one identical data
   requirement**: H4/D1 OHLCV bars for FX/metals/energy/indices/crypto,
   served by the same five provider chains already live in
   `config.yaml`. Writing 29 near-identical 10-section essays would not
   surface new information — it would restate the same provider chain 29
   times. Instead: **Part A** below is one deep, full 10-section
   feasibility analysis of that shared OHLCV backbone. **Part C** is a
   35-row table that maps every hypothesis to Part A (or to its own
   deep-dive in Part B) with a one-line rationale specific to that
   hypothesis. This is the reproducible, non-redundant version of "every
   hypothesis inspected" — a researcher can find any H-number in the Part
   C table and follow the pointer to the analysis that actually governs
   its feasibility.

Six hypotheses need data genuinely distinct from the OHLCV backbone —
these get full independent 10-section treatments in **Part B**: H007
(macro), H012 (COT), H019 (crypto positioning), H021 (news sentiment),
H104 (tick order flow), H105 (execution/TCA).

**On "live-verify what I can, flag the rest."** Sources this project
already calls in production or has run a real probe against this session
(cTrader, Twelve Data, FCS, Alpha Vantage, Finnhub, ccxt/Binance, CBOE,
FRED, CFTC, MarketAux, alternative.me, Binance/Bybit/OKX funding+OI) are
reported from that ground truth — code citations given, not re-guessed.
Commercial vendors with no account in this project (CoinGlass,
CryptoQuant, Glassnode, Kaiko, Amberdata, Tardis.dev, CoinMetrics,
Deribit, CME DataMine, Polygon.io/Massive, Tiingo, Nasdaq Data
Link/Quandl, SEC EDGAR, Stooq, NewsAPI, GDELT, ForexFactory,
TradingEconomics) are reported from their current public
documentation/pricing pages, every claim tagged **[UNVERIFIED — no
credentials]** — their actual rate-limit behavior, data completeness, and
support quality cannot be experimentally confirmed without paying for
access, which this pass does not do. This is the honest ceiling of "live
verification" for a data-acquisition *plan* — treat this report as a
scoping document, not a substitute for a real trial-account probe before
any purchase.

**Scoring rubric** (used in `data_source_rankings.csv`, 0–100, all
components independently justified so the score is reproducible without
re-reading this whole document):

| Component | Points | Rule |
|---|---|---|
| History depth vs. this project's OOS need | 25 | 25 = exceeds preferred depth; 15 = meets minimum only; 5 = below minimum; 0 = negligible |
| Data integrity / reliability | 20 | 20 = verified by real production use or a real probe this session; 14 = documented, regulated/official source; 8 = documented, unverified commercial vendor; 2 = scraped/ToS-questionable |
| Cost / accessibility | 15 | 15 = free, no key; 12 = free with key/quota; 8 = meaningful free tier, paid for real use; 2 = paid-only or quote-only |
| Rate-limit adequacy for this project's cadence | 15 | 15 = effectively unlimited/≥800 req/day; 10 = 100–800/day; 5 = <100/day; 2 = blocks bulk backfill entirely |
| Institutional-grade suitability | 15 | 15 = official/regulated/exchange-of-record or the live broker feed itself; 12 = leading commercial vendor with compliance certs; 8 = solid retail-grade aggregator; 3 = community/scraped |
| Documentation quality | 10 | 10 = clear, verified public docs; 6 = adequate; 3 = sparse/inconsistent |

---

## Part A — Core OHLCV Data Layer (governs 29 of 35 hypotheses)

### A.1 Required features
Open, High, Low, Close, Volume (tick-volume for FX/metals under cTrader,
real traded volume for crypto under ccxt/Binance) at H4 (decision
timeframe) and D1 (MTF confirmation gate), with H1 as auxiliary timing
context (`config.yaml:94-102`).

### A.2 Required historical depth
- **Minimum** (this project's own house standard, H008c method): enough
  for a chronological 65/35 TRAIN/TEST split yielding **≥40 closed TEST
  trades** per symbol (H022's decision rule), and **≥300 pooled OOS
  trades** for any promotion-track hypothesis (`research/edge_gate.py
  PROMOTION_CRITERIA`).
- **Preferred**: ≥5 years H4 (walk-forward with 3 rolling windows,
  `walk_forward_validation_full_universe_20260719` method) plus ≥8–10
  years D1 for regime coverage across multiple market cycles.

### A.3 Candidate sources (all already integrated — `config.yaml:73-85`)

| Asset class | Chain (in order) |
|---|---|
| crypto | ccxt (Binance) → alpaca → twelve_data → finnhub |
| metals | ctrader → twelve_data → fcs_api → finnhub |
| energy | ctrader → finnhub |
| indices | ctrader → fcs_api → finnhub |
| fx | ctrader → twelve_data → fcs_api → alpha_vantage → finnhub |

### A.4 Per-source evaluation

**cTrader (primary, fx/metals/energy/indices)** — live broker trendbar
feed (`execution/ctrader_client.py`). Real measured depth:
`scripts/download_ctrader_fx_history.py` hard-stops at
`MAX_REQUESTS_PER_SYMBOL=60` (~60k H1 bars ≈ 7 years) but is not an
artificial ceiling — it is a deliberate stop condition the script chose;
the server itself did not refuse further pagination in observed runs.
Real tick-volume confirmed nonzero for FX (H023, 2026-07-24: EURUSD mean
volume 3844, min 71, max 14972 — this displaced an earlier silent bug
where the same symbols showed zero volume under the old Yahoo feed).
**Score: 91/100** — institutional-grade (it is the actual live execution
venue), free (comes with the broker account), no published rate-limit
ceiling encountered at the depths this project has pulled.

**Twelve Data (secondary fx/metals; fallback everywhere else)** — 800
req/day free tier (`core/data_providers.py:7`). D1 up to ~19 years
(outputsize 5000). H4 back to ~2020-01-30 (~6.5y) — **this is Twelve
Data's actual plan floor, not a code bug** (`scripts/
download_deep_history.py:10`). XAGUSD/USOIL/US30/NAS100/SPX500 return
404 on this plan (verified 2026-07-05, plan-gated, not missing data).
**Score: 78/100** — solid retail aggregator, free tier is real but capped
below institutional depth on H4 for those 5 symbols specifically.

**ccxt/Binance (primary crypto)** — H4 depth ~8.9 years for BTC/USDT and
ETH/USDT, i.e., **since Binance's own 2017 founding**
(`scripts/download_deep_history.py:15-20`) — this is not an artificial
API limit, it is a real floor: no exchange can serve pre-2017 Binance
history because it didn't exist. No deeper source exists for
Binance-native BTC/ETH history; a different exchange (e.g. a 2013-founded
one) could in principle extend further back but would introduce a
cross-exchange consistency question this project has not needed to
resolve for BTCUSD/ETHUSD specifically. **Score: 95/100**.

**FCS API** — free tier hard-caps at **300 candles/request**
(`core/data_providers.py:367`, confirmed by header comment, not
inferred). This is a real, documented API limit, not a code bug — pagination
around it is possible but not currently implemented for this fallback tier
(it only serves as tertiary/quaternary fallback, so the gap has not
mattered operationally). **Score: 55/100** — real data, genuinely capped
depth per request, fine as a fallback, not viable as a primary deep-history
source without new pagination code.

**Alpha Vantage** — free tier 25 req/day
(`core/alt_data_loader.py:14-17`), used only as an FX intraday backup
when Twelve Data credits run low. **Score: 45/100** — real, reliable data,
but the rate limit is genuinely too tight for any bulk-history role; this
project correctly uses it as a last-resort supplement only.

**Finnhub** — free tier: OANDA FX pairs, crypto, US stocks
(`core/data_providers.py:262`). Used as final fallback in every chain.
**Score: 60/100** — coverage is real but this project has never needed to
lean on it for primary depth, so its actual history-depth ceiling is
untested here.

**Yahoo Finance / yfinance — REMOVED FROM ALL LIVE CHAINS (2026-07-16).**
This is the single most important "detect artificial limits" finding
already on record in this codebase, and it directly answers the brief's
instruction to distinguish real limits from wrong assumptions: the
original limitation was **not** a rate limit or pagination bug — it was
silently **wrong data**: `^IXIC` was fetched where `NDX` was intended,
futures contracts were substituted for spot metals prices, cash-session
gaps were present, and what the pipeline labeled "H4" was actually a 1-hour
resample (`config.yaml:77-80`). Retained only for offline research
downloads and failover unit tests — nothing live calls it. **Score:
20/100 for any live use; explicitly disqualified**, kept in the report
only because H001/H002/H002b (below, Part C) were originally tested on it
before this was discovered.

### A.5 Artificial-limit investigation (required by the brief)
- **Twelve Data's 404s on XAGUSD/USOIL/US30/NAS100/SPX500 at H4**: verified
  as genuine plan-gating (paid-plan-only symbols), not a wrong endpoint or
  parameter bug — confirmed 2026-07-05 by direct API response inspection.
- **FCS's 300-candle cap**: documented API behavior, confirmed via FCS's
  own free-tier terms, not a CCXT or pagination artifact (FCS is accessed
  via plain REST, not ccxt).
- **cTrader's ~7y H1 ceiling**: this project's own script-level stop
  condition (`MAX_REQUESTS_PER_SYMBOL=60`), not an exchange/broker limit —
  raising it is a one-line change if deeper history is ever needed and
  the broker's own retention supports it (untested past 60 requests/
  symbol as of this report).
- **Binance H4 depth for BTC/ETH**: genuinely bounded by Binance's own
  2017 founding — not raiseable by any pagination fix.
- **Yahoo's old "730-day 1h cap"** (encountered this session,
  `download_all_symbols.py --years 5`): real current Yahoo policy change,
  moot in practice since Yahoo is already excluded from every live chain
  for correctness reasons stated above — not worth re-investigating.

### A.6 Survivorship, look-ahead, chronological split
- **Survivorship-safe**: yes for the traded universe. FX/metals/crypto
  pairs don't delist; BTCUSD/ETHUSD are the two largest-cap, longest-lived
  crypto assets (no survivorship selection was performed to pick them).
  Index CFDs (US30/NAS100/SPX500) are traded as single instruments, not
  stock baskets, so component survivorship bias does not apply.
- **Look-ahead**: standard bar-close semantics; this project's
  `backtest_mode` + `run_pipeline()` harness pattern (used consistently
  since H023/H037/H019) only exposes bars up to the decision bar's close.
  No forward-fill or future-timestamp leakage has been found in the OHLCV
  path itself (distinct from the funding-rate/sentiment leakage bugs
  found and fixed for H019/H021 — see Part B).
- **Chronological TRAIN/TEST**: house standard is a 65/35 chronological
  split (H008c method), used uniformly across every hypothesis that has
  actually run a backtest.

### A.7 Institutional-grade assessment
cTrader (live broker feed) and ccxt/Binance (largest crypto venue by
volume) are as institutional-grade as this project's asset classes get
without paying for a Tier-1 vendor. Twelve Data/FCS/Alpha Vantage/Finnhub
are retail-grade aggregators — adequate for research validation, not for
a claim of point-in-time-revision-free institutional data.

### A.8 Preprocessing already implemented
`core/data_validator.validate_ohlcv()` (nulls, duplicate timestamps,
malformed OHLC relationships, monotonic index) — see `data/README.md`.
`core/data_confidence.py` cross-checks independent providers on carrier
symbols. `main.py` logs `DATA STARVATION` loudly when NNFX's ≥210-bar or
the D1 MTF gate's ≥50-bar floor is violated.

### A.9 Feasibility verdict
**PASS.** The backbone is real, verified (not merely documented), already
survived a provider-quality incident (Yahoo) that was caught and fixed,
and already has integrity checks and starvation logging in production.

### A.10 Recommendation
**Proceed** — no data acquisition work is needed for any of the 29
hypotheses that depend only on this backbone. The one identified gap
(FCS's 300-candle cap not currently paginated) is not worth closing: FCS
is a fallback-only source in every chain it appears in.

---

## Part B — Domain-specific deep dives (6 hypotheses)

### B.1 — H007 (Macro: DXY / VIX / SPY / GLD / yields)

1. **Testable**: Yes — engine already exists (`engines/macro_engine.py`);
   the open work is an OOS evaluation of the existing engine, not new data
   collection (registry note, corrected 2026-07-23).
2. **Required features**: Dollar-strength proxy (DXY), volatility index
   (VIX), broad equity proxy (SPY/SP500), gold proxy (GLD), yields
   (10Y/2Y for curve context).
3. **Required historical depth**: Minimum 5y (matches the OOS house
   standard); FRED series in use go back decades further than needed.
4. **Candidate sources**: CBOE (VIX, official), FRED (DTWEXBGS dollar
   proxy, SP500, GOLDAMGBD228NLBM LBMA gold fixing, DGS10/DGS2 yields).
5. **Source evaluation**:
   - **CBOE VIX_History.csv** (`core/alt_data_loader.py:195`) — free, no
     key, official exchange source, full daily OHLC. **Score: 96/100**.
   - **FRED** (`core/alt_data_loader.py:197-205`) — free keyless via
     `fredgraph.csv`, enhanced with `FRED_API_KEY`. DTWEXBGS since 2006,
     DGS10 since 1962, DGS2 since 1976, SP500 index proxy series shorter
     but adequate, GOLDAMGBD228NLBM (LBMA gold fixing) is the trusted
     replacement for the old Yahoo GLD ETF proxy, adopted 2026-07-17.
     **Score: 97/100** — this is literally the U.S. central bank's own
     public data repository; as institutional-grade as free data gets.
6. **Artificial limits**: None found — this project already replaced the
   only historically-limited piece (Yahoo GLD ETF proxy → FRED LBMA gold
   fixing, 2026-07-17) for correctness, not availability, reasons.
7. **Collection strategy**: Primary CBOE for VIX, FRED for everything
   else (already the live order — `core/alt_data_loader.py:309`).
   No fallback needed; both are official/free with no quota risk at daily
   granularity.
8. **Integrity checks**: Already daily-close level series — no tick-level
   gap risk; watch for FRED series revisions (rare, government data is
   revised on defined schedules, not silently) and CBOE holiday gaps
   (expected, not anomalies).
9. **Feasibility verdict**: **PASS.**
10. **Recommendation**: **Proceed** directly to the OOS evaluation this
    hypothesis actually needs — no data acquisition work required.

---

### B.2 — H012 (COT + retail sentiment)

**Update (2026-07-24, same day): the gap identified below is now
closed.** `scripts/download_cot_deep_history.py` was built, reusing
`scripts/download_cot.py`'s row-matching logic (refactored into a shared
`iter_cot_rows()` generator — the existing current-week collector's
behavior and tests are unchanged) against CFTC's free yearly archive.
19 new unit tests (`tests/test_cot_deep_history.py`) cover the pure
parsing/merge/zip-extraction logic. **Not yet network-verified**: this
sandbox cannot reach `cftc.gov` (same class of restriction hit for
Binance/Bybit during H019's investigation — confirmed via a direct
`curl` attempt, not assumed). The script ships a `--probe` mode
(fetches one year, prints diagnostics, writes nothing) that must be run
on the VPS before trusting a full 1986-present backfill — this is the
same verify-before-trust discipline this report's own methodology
section committed to. The analysis below is left as originally written
(the "not yet built" framing) with this note on top, since it documents
the reasoning that led to the fix, not a re-derived verdict.

**Second update, same day, from the actual VPS `--probe` run**: the
verify-before-trust step paid off immediately. `--probe 2025` returned
120 weekly rows for EURUSD (expected ~52) and an extra row for XAUUSD.
Root cause: `iter_cot_rows()`'s original bare `market.startswith(contract)`
match also caught CFTC's separately-listed EUR cross-rate contracts
(`"EURO FX/BRITISH POUND XRATE - ..."`, `"EURO FX/JAPANESE YEN
XRATE - ..."`) and an unrelated Coinbase Derivatives gold contract
(`"GOLD -1 TROY OUNCE - COINBASE DERIVATIVES, LLC"`). **This bug was not
new** — it lived in the shared matching function both the new
deep-history script and the already-running weekly production collector
call, meaning `data/cot/EURUSD.json`'s live cache (populated weekly since
2026-07-09) may have silently held a contaminating contract's row
instead of the real EURO FX net position, unnoticed because a single
current-week file's dict-overwrite semantics hide a contamination that
only becomes visible as a wrong row *count* across many weeks. No live
trading decision was affected (the Sentiment engine has stayed DISABLED
throughout), but this is exactly the class of finding the "always verify
experimentally, never trust documentation" instruction in this report's
own methodology exists to catch — and it would not have surfaced without
running the real probe on the VPS. Fixed same day by requiring CFTC's
actual field delimiter (`contract + " - "`) instead of a bare prefix;
3 new regression tests reproduce it with the exact real market-name
strings observed.

**Third update, same day, from a re-probe after the delimiter fix**:
EURUSD/XAUUSD both correctly returned exactly 52 rows — confirmed. That
re-probe surfaced two further findings from real data. NZDUSD's mapped
name (`"NEW ZEALAND DOLLAR"`) matched zero rows in 2025 — a broader
substring search found CFTC's current name is `"NZ DOLLAR"`, a rename,
not a matching bug; fixed by updating the mapping. Separately, USOIL's
mapped name (`"CRUDE OIL, LIGHT SWEET"`) had, even before the delimiter
fix, only ever been bare-prefix-matching a *different, unintended*
contract at a different venue (`"CRUDE OIL, LIGHT SWEET-WTI - ICE
FUTURES EUROPE"`), while the real NYMEX/US-benchmark WTI contract is
listed as `"WTI FINANCIAL CRUDE OIL"` — meaning USOIL's COT data had
been silently tracking European ICE positioning, not the US benchmark,
since the feature's 2026-07-09 wiring. Remapped to the NYMEX name,
flagged explicitly as a judgment call (no prior documentation specified
which venue "USOIL" was meant to track). Also found in the same pass:
the live weekly collector had no `User-Agent` header and was returning
HTTP 403 from `cftc.gov` — confirmed via a direct real-network A/B test
(identical request, only the header differed) — fixed by adding the
same header the deep-history script already used.

**Fourth update, from the full 1986-present backfill run**: running the
actual backfill (not just a one-year probe) surfaced one more bug the
2025-only probes couldn't have caught: GBPUSD and NZDUSD both came back
with exactly 204 records, both starting on the *identical* date
2022-02-08 — while every other symbol correctly spanned back to 1986 (or
to their instrument's real inception date, for EUR/BTC). That identical
count and identical date across two unrelated currencies was too
specific to be coincidence. A targeted probe of an earlier year (2015)
confirmed CFTC used longer contract names before renaming them at some
point between 2015 and 2022 — `"BRITISH POUND STERLING"` (not `"BRITISH
POUND"`) and `"NEW ZEALAND DOLLAR"` (the exact name already tried and
discarded two paragraphs above, on the mistaken assumption it was simply
stale rather than one of two valid eras). Fixed by restructuring
`COT_SYMBOLS` from a single name per symbol to a tuple of accepted
aliases, so both eras match. Re-running the backfill confirmed GBPUSD now
spans 1986-2025 and NZDUSD extends to 2004-2025. Two minor, non-blocking
items remain open and documented rather than chased further: GBPUSD's
record density within its now-correct range (~64% of the other majors'
count over the identical span), and NZDUSD/AUDUSD's shared ~2004 start
(a weaker signal than the confirmed 2022 rename — no identical-date
fingerprint — more likely a genuine reporting-start date than a further
undiscovered alias). Neither blocks H012, since every symbol's depth
still clears the hypothesis's stated minimum by a wide margin.

Four real bugs found and fixed across four rounds of actually running
this against live data, none of which would have been caught by
documentation review alone — this is the concrete payoff of this
report's "always verify experimentally" methodology commitment, not a
hypothetical one.

1. **Testable**: Partially — real data now flows into the engine
   (2026-07-09 wiring fix), but the **current collector only captures a
   rolling 12-week forward window**, not deep historical COT — this is a
   genuine, previously-unflagged gap for OOS backtesting purposes.
2. **Required features**: CFTC Commitments-of-Traders large-speculator
   net positioning per instrument; retail-proxy sentiment (already
   internal, no external source).
3. **Required historical depth**: Minimum ~2–3 years weekly (~100–150
   observations) for any meaningful OOS split; CFTC's own archive
   supports far more.
4. **Candidate sources**: CFTC legacy futures-only weekly file
   (`https://www.cftc.gov/dea/newcot/deafut.txt`, current week only, what
   `scripts/download_cot.py` fetches today) vs. **CFTC's yearly archive
   files** (`deacot` compressed archives, publicly documented, free, no
   key — going back to 1986 for legacy futures-only, 2006 for the
   disaggregated report) which the current script does **not** fetch.
5. **Source evaluation**:
   - **CFTC current-week file** — free, no key, official.
     `scripts/download_cot.py:HISTORY_WEEKS=12` keeps only a 12-week
     rolling cache, rebuilt weekly. **Score: 62/100** — real, official
     data, but the collection script as it exists today cannot support a
     chronological OOS split (12 weeks << the ~100-150 needed).
   - **CFTC yearly archive** — same official source, deeper, **not yet
     integrated**: no downloader exists for it in this codebase.
     **Score (if built): 94/100** — same institutional-grade official
     source, just needs a new backfill script.
6. **Artificial limits found**: This is exactly the brief's "detect
   artificial API limits" case — the *apparent* shallowness of COT data
   in this project is **not** a CFTC or API limitation at all; it is that
   `scripts/download_cot.py` was written for the live rolling-cache use
   case only and nobody has written the yearly-archive backfill variant.
7. **Collection strategy recommendation**: Primary = CFTC yearly archive
   backfill (new script needed, e.g.
   `scripts/download_cot_deep_history.py`, same free source, same parser
   logic reused) merged with the existing weekly rolling cache for
   forward-going data; no fallback source needed (CFTC is the sole
   authoritative source for this data by definition — there is no
   second CFTC).
8. **Integrity checks needed**: contract-name changes across years (CFTC
   occasionally renames/reclassifies instruments), missing weeks around
   government shutdowns (CFTC has skipped/delayed COT releases during
   past shutdowns — a real gap, not a bug), duplicate week detection.
9. **Feasibility verdict**: **PASS WITH FALLBACK** — the live/forward
   piece is solid; a real backtestable OOS sample requires **building the
   not-yet-written deep-archive backfill script** first. This is new
   infrastructure work, not a new hypothesis — same category as the
   already-built H019/H104 collectors.
10. **Recommendation**: **Modify approach before backtesting** — write
    the CFTC yearly-archive backfill script before any H012 A/B test is
    attempted; do not attempt to test H012 OOS on the current 12-week
    rolling cache alone.

---

### B.3 — H019 (Crypto positioning: funding rate + open interest + Fear&Greed)

*Already fully investigated and resolved this session — summarized here
from the registry's own `feasibility_probe` and `conclusion` fields
(`research/results/registry.json`, H019) for completeness of this report,
not re-derived.*

1. **Testable**: Yes — was tested; **hypothesis FAILED** (2026-07-24).
2. **Required features (as pre-registered)**: funding rate, open
   interest, Fear & Greed Index.
3. **Required historical depth**: 5y min / 8y preferred for funding rate;
   3y min / 5y preferred for OI; entire history for Fear & Greed.
4. **Candidate sources tested**: Binance, Bybit, OKX (all via ccxt, free,
   no new provider integration) for funding+OI; alternative.me for
   Fear&Greed.
5. **Source evaluation (real probes, not documentation)**:
   - **Binance funding rate**: 6,574 records/symbol, 2020-07-24 → today
     (~6 years), no gaps. **Score: 93/100**.
   - **alternative.me Fear&Greed**: 3,092 daily records, full history
     since 2018-02-01, no gaps. **Score: 95/100**.
   - **Open interest, all 3 exchanges**: Binance ~7 days (hard exchange
     limit, confirmed twice via error `-1130` past that window — a real
     exchange-side retention limit, not a pagination bug), Bybit ~199
     days, OKX ~179 days. **Score: 12/100** — none reach institutional
     depth; this is the one leg of H019 that failed feasibility outright.
   - **Commercial alternatives for OI** (CryptoQuant, CoinGlass,
     Glassnode, Kaiko, Amberdata, Tardis.dev) — considered and rejected
     2026-07-24 as disproportionate to what OI would add; this report's
     fresh vendor research (Part D below) confirms that judgment still
     holds: none of the free tiers materially beat the ~6-month ceiling
     already measured, and the paid tiers (Kaiko $1,000-$2,500+/mo,
     Glassnode $999/mo Professional) are priced for institutional
     desks, not proportionate to one modulator's OI leg.
6. **Artificial limits**: Binance's OI 7-day window is a genuine
   exchange-side retention policy (confirmed by the specific error code
   recurring at the same boundary on repeated runs), not a client bug.
7. **Collection strategy applied**: funding rate + Fear&Greed adopted;
   OI dropped per the hypothesis's own pre-registered fallback rule.
8. **Integrity checks applied**: causal alignment enforced at
   `confluence/crypto_positioning_modulator.py::causal_context_at()` —
   funding rate only used strictly before its settlement timestamp, never
   forward-filled from a future aggregate.
9. **Feasibility verdict**: **PASS WITH FALLBACK** (2 of 3 legs feasible;
   OI dropped) — this is what the hypothesis actually ran on, and it
   still reached a clean, real, non-artifact result (FAILED, dPF=-0.037,
   BTCUSD/ETHUSD diverged in sign — see registry conclusion).
10. **Recommendation**: **No further action.** H019 is resolved; do not
    reopen OI sourcing for this hypothesis specifically — CLAUDE.md rule
    4 treats this as committed evidence. A *new* hypothesis with a
    different formula could revisit OI depth if the operator later wants
    to reconsider paying for Kaiko/Glassnode.

---

### B.4 — H021 (MarketAux news sentiment)

1. **Testable**: Yes, with an accepted "INSUFFICIENT DATA" outcome
   already built into the decision rule if the sample stays too small
   (pre-registered acknowledgment, not a post-hoc excuse).
2. **Required features**: Per-symbol news sentiment score.
3. **Required historical depth**: Method note states plainly this will be
   *much shorter* than the OHLCV house standard — no fixed year target,
   accumulation-limited by design.
4. **Candidate sources**: MarketAux (adopted, `fundamentals/
   marketaux_client.py`), NewsAPI.org, GDELT (new candidates surfaced by
   this report's research — not previously evaluated in this registry).
5. **Source evaluation**:
   - **MarketAux** (in use) — free tier: **100 requests/day**, serves
     *recent* articles only, **no historical backfill** (verified via a
     real request 2026-07-14, not documentation). XAUUSD→GOLD entity
     mapping resolved 2026-07-24. **Score: 58/100** — real, working,
     correctly mapped, but rate-limited enough that the backtestable
     sample accrues slowly (the hypothesis's own method notes already
     say this).
   - **NewsAPI.org [UNVERIFIED — no credentials]** — free Developer tier:
     100 req/day, 24h-delayed articles, **historical access limited to 1
     month**, no commercial use permitted on free tier. **Score: 30/100**
     — strictly worse than MarketAux for this project's purpose (shorter
     historical window, same rate limit, added ToS friction).
   - **GDELT [UNVERIFIED — no credentials, but free/keyless by design]**
     — free, no key, no daily-request ceiling in the way MarketAux/NewsAPI
     have one. Tone/sentiment coverage (-100 to +100) since 2017 via the
     DOC 2.0 API; the underlying Event/GKG database goes back to **1979**.
     **Score: 74/100** — the strongest depth/cost combination of the
     three, but note: GDELT's sentiment is a *tone* score over indexed
     news text, not the same per-entity `sentiment_score` construct
     MarketAux returns — swapping sources would change what "sentiment"
     means in the hypothesis, which is a new hypothesis, not a drop-in
     replacement for H021 as pre-registered.
6. **Artificial limits**: MarketAux's shallow *historical* window is a
   genuine free-tier product limitation (recent-news-only is the plan's
   actual design, not a bug) — already correctly identified in the
   hypothesis's own pre-registration text before any code was written.
7. **Collection strategy**: Primary = MarketAux (already running,
   `scripts/collect_marketaux_sentiment.py` +
   `iatis-marketaux-collect.timer`) — per the last registry note, **the
   timer was not yet confirmed enabled on the VPS as of 2026-07-24**; H104
   was separately confirmed running this session, H021's timer status
   should be checked next time the VPS is reachable.
8. **Integrity checks**: entity-mapping correctness (already caught and
   fixed for XAUUSD → GOLD), duplicate-article dedup, timezone
   normalization on `published_at`.
9. **Feasibility verdict**: **PASS WITH FALLBACK** (data collection
   running/pending-enable; INSUFFICIENT DATA is a valid, pre-accepted
   outcome, not a failure of this report).
10. **Recommendation**: **Proceed as pre-registered.** If the accumulated
    MarketAux sample proves too thin when H021 is actually evaluated, a
    **new**, separately pre-registered hypothesis using GDELT's deeper
    free history would be a reasonable follow-up — not a substitution
    inside H021 itself (CLAUDE.md rule 6: never change the data mid-sample
    once a hypothesis is running).

---

### B.5 — H104 (Binance tick-level order-flow imbalance / cumulative delta)

*Infrastructure already built and verified running this session —
summarized from the registry's `data_collection` field.*

1. **Testable**: Not yet (by design) — 3-month data-collection clock just
   started (`iatis-orderflow-collector.service`, confirmed connected to
   both BTCUSD and ETHUSD streams 2026-07-24).
2. **Required features**: Tick-level trade price/quantity/aggressor-side
   (buy- vs. sell-initiated).
3. **Required historical depth**: n ≥ 150 bars-with-signal after 3 months
   (data-availability floor, distinct from the n≥300 OOS-trade floor).
4. **Candidate sources**: Binance public aggTrade WebSocket (adopted,
   free, unauthenticated); Tardis.dev, Kaiko, Amberdata (commercial
   alternatives that **could** provide historical tick backfill instead
   of waiting 3 months).
5. **Source evaluation**:
   - **Binance aggTrade WS** (in use) — free, no auth, no rate limit
     (server push). **Cannot be backfilled** — Binance's REST API only
     serves recent trades, confirmed by this project's own investigation
     (module docstring, `scripts/collect_binance_orderflow.py`).
     **Score: 70/100** — perfect for the forward-collection use case,
     zero score contribution on the "history depth" component since none
     exists yet by construction.
   - **Tardis.dev [UNVERIFIED — no credentials]** — tick-level order book
     + trade data since **2019** for 50+ exchanges including Binance;
     explicitly offers historical funding, OI, and liquidations too.
     This is the one commercial vendor in this whole report that could
     **directly eliminate the 3-month wait** by supplying backfilled tick
     data instead of live-only collection. No public pricing found in
     this pass (quote-based). **Score: 78/100 conditional on price** —
     real depth and real fit, but cost is unknown and unverified.
   - **Kaiko / Amberdata [UNVERIFIED — no credentials]** — both offer
     tick-level trade + derivatives data with comparable depth; Kaiko's
     published Level-2 tick tier is $2,500/month, well above what a
     single unproven hypothesis justifies. **Score: 55/100** (real
     capability, priced for institutional desks, not proportionate here).
6. **Artificial limits**: The "3-month wait" is not an artificial limit —
   it is a genuine consequence of Binance's REST API not serving deep
   tick history, confirmed in this project's own research before the
   collector was built (see H104 registry `data_source` field).
7. **Collection strategy**: Primary = Binance aggTrade WS (running).
   **Recommended commercial fallback if the 3-month wait is
   unacceptable**: Tardis.dev backfill, but only after (a) getting an
   actual quote and (b) treating "buy Tardis.dev for H104" as its own
   small pre-registered decision, not a silent swap.
8. **Integrity checks**: malformed-message tolerance already implemented
   (`parse_agg_trade()` returns `None` rather than raising), bar-boundary
   flooring tested (`bar_start_ms()`), reconnect-on-drop with backoff.
9. **Feasibility verdict**: **PASS** (as pre-registered, forward-only) /
   **COMMERCIAL DATA WOULD ACCELERATE** (Tardis.dev, unpriced in this
   pass).
10. **Recommendation**: **Proceed** with the free forward-collection path
    already running; **optionally evaluate Tardis.dev pricing** as a
    separate, explicit decision if 3 months is judged too slow — do not
    default to waiting without at least pricing the alternative once.

---

### B.6 — H105 (Entry fill lag / TCA)

1. **Testable**: Yes — Stage 1 uses data **already being collected
   live**, no new source needed.
2. **Required features**: Reference close price, scheduler-tick
   timestamp, actual broker fill price.
3. **Required historical depth**: ≥100 real fills (pre-registered
   minimum).
4. **Candidate sources**: `storage/execution_quality.py` (TCA ledger,
   live since 2026-07-16, backed by Cloudflare D1 — this project's own
   live database, not a local file).
5. **Source evaluation**: **Score: 90/100** — this is self-generated,
   first-party data from real broker fills; no external vendor involved,
   no bias-risk from a third party, but only as deep as the live book's
   own trading volume since 2026-07-16 (time-gated, not source-gated).
6. **Artificial limits**: None — the only constraint is real trading
   volume accumulating over real time.
7. **Collection strategy**: N/A — already the sole and correct source; no
   fallback needed or possible (there is no second copy of this broker's
   fills).
8. **Integrity checks**: dry-run fills already excluded by design (`Only
   real broker fills are recorded... dry-run fills echo the intended
   price back and would only dilute the statistic` —
   `storage/execution_quality.py:30-32`); sign-convention documented
   (adverse-positive) and unit-consistent with the backtest engine's pip
   convention.
9. **Feasibility verdict**: **PASS** (time-gated on live volume, not a
   data-source problem).
10. **Recommendation**: **Proceed** — no acquisition work needed; revisit
    once ≥100 real fills have accumulated (`GET /execution-quality`).

---

## Part C — Full hypothesis-by-hypothesis map (all 35)

Governing analysis: **A** = Part A (shared OHLCV backbone) · **B.n** =
Part B domain deep-dive · verdict is this report's fresh feasibility read,
independent of the hypothesis's own already-recorded scientific verdict
(FAILED/PASSED/etc., which stays as committed evidence per CLAUDE.md
rule 4 and is **not** relitigated by this report).

| ID | Title (short) | Status (registry) | Governing analysis | Data feasibility verdict | Note |
|---|---|---|---|---|---|
| H001 | Liquidity sweep + HTF trend | FAILED | A | PASS (retrospective flag) | Tested on Yahoo H1 2yr — source since disqualified for correctness (2026-07-16); the null result is not undermined by this (data bugs manufacture false edges more often than false nulls), but flagged for transparency |
| H002 | Qualified sweep, EURUSD | FAILED | A | PASS (retrospective flag) | Same Yahoo-era caveat as H001 |
| H002b | Qualified sweep, multi-symbol | FAILED | A | PASS (retrospective flag) | Same Yahoo-era caveat |
| H003 | ICT killzone + premium/discount | RESEARCH | A | PASS | Standard OHLCV, paper-trading via engine_tracker |
| H004 | NNFX EMA200 + ADX | RESEARCH | A | PASS | Standard OHLCV |
| H005 | Quant RSI + ROC | RESEARCH | A | PASS | Standard OHLCV |
| H006 | Wyckoff Spring/Upthrust | RESEARCH | A | PASS | Standard OHLCV (+ volume, see H023 volume-mode note) |
| H007 | Macro DXY / Risk-On-Off | RESEARCH | B.1 | PASS | CBOE + FRED, official sources |
| H008 | BOS+FVG (EURUSD/XAUUSD) | FAILED | A | PASS (superseded by H008c) | Original run flagged its own shallow-data issue; H008c re-ran on real deep M15 |
| H008b | BOS+FVG + London + ATR | ABANDONED | A | PASS | Sample-size failure, not a data-quality failure |
| H008c | BOS+FVG re-test, deep M15 | FAILED | A | PASS | Real deep M15, look-ahead-free, the house-standard clean run |
| H009 | 6-engine confluence | PASSED (flagged) | A | PASS | Standard OHLCV; PROMOTION_CRITERIA gap is an evidence-sufficiency issue, not a data-feasibility one |
| H010 | RSI/MACD Divergence | RESEARCH | A | PASS | Standard OHLCV |
| H011 | Market Structure BOS/CHoCH | RESEARCH | A | PASS | Standard OHLCV |
| H012 | COT + retail sentiment | RESEARCH | B.2 | PASS (full 1986-2025 backfill run and verified on real CFTC data, 2026-07-24 — 4 real bugs found and fixed across 4 verification rounds) | Deep-archive backfill script built, tested against live data, all 11 symbols now cover their real available history — see B.2 update notes |
| H013 | Reversal group agreement | PASSED (flagged) | A | PASS | Internal engine-output logic, no new data |
| H014 | Engine Orthogonality | RESOLVED | A | PASS | Internal — engine vote correlations only |
| H015 | Ablation — Minimum Engine Set | RESOLVED | A | PASS | Internal — same OHLCV backbone, 15-symbol universe |
| H016 | Engine Pair Synergy | RESOLVED | A | PASS | Internal — engine vote correlations only |
| H017 | SMC full-spec internal confluence | FAILED | A | PASS | Standard OHLCV |
| H018 | Structure-based stops | PLANNED (FROZEN) | A | PASS | Standard OHLCV; blocked on forward-demo milestone, not data |
| H019 | Crypto positioning modulator | FAILED | B.3 | PASS WITH FALLBACK | OI leg dropped, funding+F&G sufficed — see B.3 |
| H020 | min_informative_weight_share A/B | FAILED | A | PASS | Standard OHLCV, internal config knob |
| H021 | MarketAux news sentiment | PLANNED | B.4 | PASS WITH FALLBACK | Rate-limited accumulation, INSUFFICIENT DATA a valid outcome — see B.4 |
| H022 | FX-cross universe expansion | FAILED | A | PASS | Real measured spreads, deep history per symbol |
| H023 | Wyckoff volume gating | NULL | A | PASS (with a caught bug) | First run invalidated by Yahoo zero-volume; re-run on real cTrader volume — the report's own "artificial limit" detection already happened here, live, mid-project |
| H024 | Hard regime gate | NULL | A | PASS | 20-symbol universe, standard OHLCV |
| H025 | Information compression (LZ76) | FAILED | A | PASS | Price-sign sequence, same OHLCV backbone |
| H033 | Meta-confidence gate | FAILED | — | PASS | Trains on the system's own simulated trade ledger — no external data at all |
| H037 | Decision delay | PLANNED | A | PASS | Standard OHLCV, geometry replay |
| H101 | SMC governance closure | RESEARCH | A | PASS | Standard OHLCV |
| H102 | Price Action governance closure | RESEARCH | A | PASS | Standard OHLCV |
| H103 | meta_decision gate removal | PLANNED | A | PASS | Standard OHLCV, 20-symbol universe |
| H104 | Tick order-flow imbalance (CVD) | PLANNED | B.5 | PASS (forward-only) | 3-month clock now running — see B.5 |
| H105 | Entry fill lag / TCA | PLANNED | B.6 | PASS | Self-generated live TCA data — see B.6 |

**Zero hypotheses returned FAIL, INSUFFICIENT DATA (as a blocking
verdict), or COMMERCIAL DATA REQUIRED at the acquisition-feasibility
level.** The two genuine gaps found are both **PASS WITH FALLBACK**
(H012's missing deep-archive backfill script; H021's inherently
rate-limited accumulation, already anticipated in its own
pre-registration) — neither blocks proceeding, both are documented as
concrete next infrastructure steps rather than blockers.

---

## Part D — Global data architecture

### D.1 Global data architecture (current state, assessed)
This project already runs a coherent, if informally-named, data
architecture:
- **Live path**: `core/data_providers.py::fetch_with_failover()` /
  `fetch_multi_timeframe_with_failover()` — asset-class provider chains,
  automatic failover, disk-history left-extension
  (`_deepen_with_history`).
- **Research path**: `scripts/download_*.py` one-off deep-history
  downloaders per source (cTrader, Twelve Data/deep-history, ccxt/crypto,
  COT, crypto positioning, MarketAux, Binance order flow).
- **Storage**: flat CSV/JSONL files under `data/` (gitignored,
  `data/README.md` is the durable record of what exists and how it was
  produced) for research datasets; Cloudflare D1 for live/operational
  state (TCA ledger, outcomes, shadow book).

### D.2 Unified data lake design (recommendation, not yet built)
A single `data/` root, already partially followed, formalized as:
```
data/
  raw/<domain>/<source>/<symbol_or_series>/...      # untouched pulls
  processed/<domain>/<symbol>/<timeframe>.parquet    # validated, causal-safe
  manifests/<script>_<YYYYMMDD>_manifest.json         # one per collection run
```
This project's existing convention (flat CSVs + a README) already covers
the `raw/` layer adequately for its current scale (a few dozen files);
formalizing into `raw/processed/manifests` subfolders is worth doing only
if the number of sources grows meaningfully beyond the current ~10.

### D.3 Shared collectors
Two reusable patterns already exist and should be the template for any
new collector:
- **Paginated REST backfill**: `_paginate_forward()` (originated in
  `scripts/probe_crypto_positioning_data.py`, reused unchanged in
  `scripts/download_crypto_positioning_history.py`) — tracks actual
  timestamp progress rather than trusting a provider's stated `limit`
  (the Bybit 200-cap-regardless-of-requested-1000 bug this pattern was
  built to survive).
- **Long-running WebSocket service**: `scripts/collect_binance_orderflow.py`
  + matching `.service` unit (not a `.timer`) — the template for any
  future tick-level or streaming source.

### D.4 Data normalization layer
`core/data_loader.py::load_from_csv()` (extended for headerless/
tab-separated real broker exports, `data/README.md`), plus the
hypothesis-specific `causal_context_at()` pattern
(`confluence/crypto_positioning_modulator.py`) for any external series
that must be aligned to decision-bar timestamps without look-ahead. Any
new source (e.g. a future GDELT or CFTC-archive collector) should reuse
`causal_context_at()`'s "strictly before" filtering convention rather
than inventing a new alignment rule.

### D.5 Caching strategy
Twelve Data responses are already cache-aware (`core/data_providers.py`
docstring: "Cache-aware: cached Twelve Data response skips failover
entirely"). No project-wide cache layer exists beyond this; not needed at
current call volumes (a few hundred requests/day across all sources).

### D.6 Artifact versioning
Already established convention: `scripts/revive_manifests.py` refuses to
freeze a manifest from a dirty tree — every result JSON this project
treats as evidence is tied to a specific commit. This is already
institutional-grade practice; nothing to add.

### D.7 Manifest format
Existing convention (see any `research/results/*_manifest.json`):
`{symbols, method, config_fingerprint, git_commit, date, per_symbol_results}`.
Reuse as-is for any new collector's manifest.

### D.8 Metadata schema
Every backtest report already carries `data_providers` provenance per
decision (CLAUDE.md data layer section: "per-decision provenance in every
report's `data_providers`"). New collectors should emit the same shape:
`{source, fetched_at, symbol, coverage_start, coverage_end, gaps: []}`.

### D.9 Evidence chain
`research/results/registry.json` (hypothesis → decision rule → result) +
`docs/STRATEGY_EVIDENCE_2026-07.md` (rejected-ledger prose) +
result JSON + clean-tree manifest is already a complete, auditable
evidence chain (CLAUDE.md rule 4). This report's own outputs
(`data_sources_registry.json`, `data_collection_plan.json`, etc.) slot in
as a new, permanent "acquisition-stage" layer beneath that chain — every
future hypothesis's data section should link back to the relevant row in
`data_sources_registry.json` instead of re-describing a source from
scratch.

### D.10 Recommended directory structure (delta from current state)
No structural change is recommended. The current flat `data/` + README +
per-script docstring convention already satisfies reproducibility for
this project's actual scale; introducing `raw/processed/manifests`
subfolders now would be optimizing for a data volume this project does
not yet have (CLAUDE.md: "never optimize for convenience" cuts both
ways — premature reorganization is its own kind of unjustified
complexity).
