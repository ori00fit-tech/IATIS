# IATIS — Strategy Evidence & Forward Path (July 2026)

Consolidated record of every enhancement tested during the production
hardening + strategy-research work, with the measured verdict for each.
Companion to `docs/PRODUCTION_AUDIT_2026-07.md`. Method throughout:
evidence only — real deep data, cost-inclusive backtests, out-of-sample
splits, git-tracked reproducibility manifests. Every "no edge" below is a
measured result, not an opinion.

---

## The measured edge (what actually works)

The system's real, cost-inclusive edge — validated across 6.4 years of H4
and up to 22 years of daily, stable across every market regime (COVID,
2022 inflation, 2024–26), and confirmed to survive **real IC Markets
spreads** (measured live via cTrader):

| carrier | real-cost PF | note |
|---|---|---|
| ETHUSD | ~1.40–1.56 | strongest — now confirmed over 8.9y, n=347 (see 2026-07-13 addendum) |
| BTCUSD | ~1.5 | $12 spread negligible vs the asset's range — now confirmed over 8.9y, n=345 |
| XAUUSD | ~1.21–1.34 | modest haircut from real 12-pip spread |
| FX majors (7 kept) | ~1.03–1.10 (⚠ stale, see below) | measured before `confluence.min_informative_weight_share` (Axis-8) existed; the 2026-07-13 re-run under the CURRENT config shows all 7 pairs at 0.907–1.008 — see addendum |

Portfolio PF by year never fell below 1.0 (2020→2026, on the config as of
that measurement — see the 2026-07-13 addendum for the current-config
re-read). The edge is: **4
confluence engines (SMC, Price Action, NNFX, Wyckoff) on a curated
symbol set, H4 decision timeframe with D1 confirmation.**

---

## What was tested and REJECTED (the honest ledger)

Every one of these was proposed as an accuracy/PF improvement. Each was
measured. Each failed or proved to be an artifact.

| enhancement | method | verdict |
|---|---|---|
| **Enable more engines** (ICT, Quant, Divergence, MarketStructure, Sentiment) | add-one-in + all-9 on a 6-symbol H4 basket | **Dilutes.** Portfolio PF 1.27 → 1.11 (ALL9). Every addition lowered PF. |
| **Volume / order-flow** on existing engines | controlled A/B: same ccxt bars, real volume vs zeroed | **Zero edge.** BTC ΔPF −0.016, ETH 0.000. And FX has no real volume on any free feed (measured: all Twelve Data H4 = zero volume). |
| **Liquidity sweeps (SMC/ICT)** | H001/H002/H002b hypotheses | **Failed** pre-existing tests (p=0.63/0.22/0.43). "Unfiltered sweeps have no directional edge." |
| **Statistical arbitrage / pairs trading** | Engle-Granger cointegration (in-sample select) + z-score (OOS test), all asset classes | **No edge.** 3/105 FX-crypto pairs cointegrated, 0 profitable OOS. Gold/silver didn't even cointegrate; indices didn't cointegrate with each other. |
| **Advanced trade management** (partial TP + breakeven + ATR trail) | re-simulate same entries with managed exits | **No improvement** once intrabar look-ahead was removed and OOS-split: ΔPF −0.008 / −0.011 / −0.125. The naive version showed +100% PF — a pure methodology artifact (a caution worth remembering). |
| **Currency index strength** (relative value) | per-currency strength z-score, fade & follow, OOS | **Losing.** Portfolio PF 0.90 (fade) / 0.49 (follow). |
| **Engine-set change** (H015 rigorous subset search) | greedy bidirectional add/drop from prod4 on TRAIN, validated on a held-out TEST slice (EURUSD/XAUUSD/BTCUSD H4) | **Production-4 is OOS-optimal.** In-sample the greedy climbed to PF 1.26 by adding market_structure+ict — but that 6-set *loses* OOS (TEST 1.219 vs prod4 1.239); all-9 is worse (1.156); dropping any single prod engine also fails OOS (drop smc −0.071, price_action −0.362, nnfx −0.073, wyckoff +0.012 within noise). The old single-LOO "SMC dilutes / market_structure adds" was an in-sample mirage. |
| **H020 — `min_informative_weight_share` blamed for the FX regression** | controlled A/B (H017 method), gate 0.0 vs 0.6, chronological TEST slice, 7 FX pairs + 3 carriers as control | **Refuted.** TEST FX mean ΔPF +0.005 (needed ≥0.03), 3/7 FX pairs worse with the gate OFF, and carriers swung MORE than FX (mean \|ΔPF\| 0.056 vs 0.005). The gate is not why FX looks worse now than the 2026-07-05 baseline — that cause is still open. |

**Conclusion: the system is at the edge frontier available on free
data.** Every sophisticated addition tested either dilutes the existing
edge, is infeasible on free data, or evaporates under rigorous
(non-look-ahead, out-of-sample) testing. This is not failure — it is a
rare, measured honesty most systems never establish.

---

## Why: the binding constraint is DATA, not code

The advanced concepts that could plausibly add edge — Order Flow,
Footprint, Volume Profile, Level-2 depth — all require **institutional
paid data** that does not exist for free in FX:

- **FX/metals volume is decentralised (OTC).** Any free "volume" is
  tick-count, not money flow. Real order flow needs CME futures data
  (dxFeed/Rithmic) — paid.
- **Crypto is the only exception:** exchanges (Binance/Bybit) publish
  real tick + order-book data for free. But real crypto *volume* was
  already tested here and added nothing to the current engines — a
  genuine order-flow *engine* (delta/imbalance/footprint), not just
  feeding volume in, would be a new, unproven, multi-day build.

So further edge is a **data-spend or a large speculative build**
decision, not a code tweak.

---

## The forward path (in priority order)

1. **Accumulate forward demo evidence — highest priority, already
   running.** Real orders now execute on the cTrader demo (spread +
   slippage real, `allow_live_trading` hard-guarded off). The 100-trade
   evidence counter on Mission Control is the only thing that will prove
   the edge *prospectively* rather than in-sample. Nothing else matters
   more.

2. **Rotate the leaked credentials (audit C1).** Still open, still the
   top operational risk. Mandatory before any real-capital step.

3. **Optional discovery:** `scripts/backtest_ic_symbols.py` sweeps all
   ~351 IC Markets instruments with the broker's own H4 bars + real
   spread — may surface symbols beyond the current 15 the strategy fits.
   In-sample; any winner still needs walk-forward + demo before live.

4. **Only real "new edge" avenue:** a crypto tick/order-book collector
   (free, Binance/Bybit WebSocket → local store) feeding a genuine
   order-flow engine. Large build, unproven payoff — start only on an
   explicit decision.

5. **H021 (PLANNED, 2026-07-14):** MarketAux news sentiment as an
   additional input to the (disabled) Sentiment engine, alongside its
   existing COT + retail-proxy logic. Pre-registered before any code was
   wired — decision rule requires a carrier-group (XAUUSD/BTCUSD/ETHUSD)
   TEST-slice mean ΔPF ≥ +0.05 with no single-symbol regression >0.03 and
   no >20% drop in EXECUTE-signal count, or the verdict is REJECT/NO
   ACTION. Client (`fundamentals/marketaux_client.py`) and engine wiring
   exist in the codebase, config-gated — `engines.enabled.sentiment`
   stays `false` until this hypothesis resolves. Known constraint:
   MarketAux's free tier serves recent articles, not a historical
   sentiment time series, so the TRAIN/TEST window will be far shorter
   than H020's and the honest verdict may be "INSUFFICIENT DATA" rather
   than a forced ADOPT/REJECT call.

**What NOT to do:** keep inventing entry-signal experiments. Everything
tried has been measured and rejected; more of the same is flailing, not
research.

---

## IC Markets full-universe discovery sweep (2026-07-06)

Ran the frozen production strategy against **every** instrument IC Markets
lists on the cTrader demo — 351 symbols, the broker's own H4 bars, real
spread where available (`scripts/backtest_ic_symbols.py --all`). 209 had
≥500 H4 bars and were backtested; 77 showed PF>1.1 with ≥20 trades. **That
headline is misleading — here is the honest reading.**

**The binding caveat: 72 of the 77 "winners" never paid a real spread.**
Live-spread measurement (`get_spot`) only resolves the 20 symbols already
mapped in `IATIS_TO_CTRADER`. Every other symbol — including all the
top-of-list crypto alts (IPUSD PF 3.31, INJUSD 2.41, STRKUSD 2.39,
ENAUSD 2.32, NEARUSD 2.25 …) — was charged only the generic default
spread, optimistic for illiquid alt-coins whose real IC spreads are wide.
Their true cost-inclusive PF is unknown and almost certainly far lower.
Add in-sample selection, small n (20–47), and short histories: **these are
not validated results.**

Only 5 rows in the top list paid a real, measured broker spread:

| symbol | real spread | PF (in-sample) | n | reading |
|---|---|---|---|---|
| XAGUSD | 2.0 pip | **2.07** | 36 | silver — liquid, real cost, same family as gold. The one credible NEW signal. |
| BTCUSD | $12 | 1.78 | 40 | confirms the known crypto edge |
| AUDJPY | 0.3 pip | 1.61 | 31 | confirms the FX-major edge |
| NZDUSD | 0.2 pip | 1.58 | 20 | broker H4 disagrees with the Twelve-Data backtest (0.985) that had it disabled — a data-source discrepancy to investigate, not a promotion |
| XAUUSD | 40 pip | 0.96 | 36 | the 40-pip figure is an off-hours (20:10 UTC) wide-quote snapshot vs the 12-pip peak spread the frozen edge uses — a spread-timing caution, not a refutation |

Excluded on sight: the dated futures contracts (`WTI_N6`, `Wheat_U6`, …)
are single-expiry, illiquid series — not a continuous tradeable strategy.

**Verdict:** one genuinely credible new macro candidate — **XAGUSD
(silver)** — and it is *already* `enabled: true` on the demo, so the
forward-evidence machinery is already testing it. Everything else is either
already known (BTC / FX majors) or unvalidated (the alts). To evaluate a
crypto-alt winner honestly it must first be mapped, charged its real (wide)
spread, and split out-of-sample — the same bar every prior "discovery"
failed to clear. Full ranked list:
`research/results/ic_symbols_backtest_20260706_manifest.json` (on the VPS).

---

## Closed-source signal toolkits (LuxAlgo et al.) — why they add nothing here

Periodically a polished commercial TradingView toolkit (LuxAlgo, and its
kind) is proposed as an upgrade. Two structural facts settle it before any
backtest:

1. **It cannot integrate.** These are closed-source Pine Script overlays
   that run *inside* TradingView, repaint, and expose no API, no historical
   signal export, no cost-inclusive backtest. There is no artefact to plug
   into a Python/cTrader automated pipeline. The most you could do is
   *re-derive the concept* — which brings us to fact 2.

2. **We already have the substance, or measured it away.** Every headline
   concept maps to an existing IATIS engine or a hypothesis already on the
   registry:

| LuxAlgo concept | IATIS status (evidence) |
|---|---|
| PAC — CHoCH, break-of-structure, order blocks, premium/discount | `smc_engine.py` + `ict_engine.py` + `market_structure_engine.py` — already coded |
| HTF Sweep Signals (liquidity grab) | **H001/H002/H002b — FAILED, verdict "ABANDON"** (p=0.63/0.22/0.43; unfiltered sweeps have no directional edge) |
| Ultimate AMD (accumulation / manipulation / distribution) + FVG | Wyckoff accumulation/distribution is *live* (`wyckoff_engine.py`); BOS+FVG is **H008 — NEEDS_MORE_DATA** (not universal; consistent only on EURUSD+XAUUSD) |
| HTF Reversal Divergences (RSI divergence) | `divergence_engine.py` — coded, dormant |
| Oscillator Matrix (RSI / MFI / Stochastics cluster) | `quant_engine.py` (H005, RESEARCH); adding oscillator engines **dilutes** (measured PF 1.27→1.11) |
| HTF Volume Spike & Imbalance | volume A/B — **zero edge** (BTC ΔPF −0.016, ETH 0.000); FX has no real free volume |
| Predictive Ranges (dynamic ATR bands) | already present via the regime detector / ATR bands |
| Signal Forge "Strict mode = all filters must agree" | this *is* the IATIS confluence vote |
| Performance Dashboard (Net Profit % / Win Rate / PF for the current chart) | in-sample, repaint-prone, single-symbol stats — **precisely the artefact this whole document rejects** (cf. the +100% trade-management mirage above) |

**Update (2026-07-06): the one open thread is now closed too.** H008
(BOS+FVG) was the only LuxAlgo-adjacent concept not yet settled. It has now
been re-tested rigorously (H008c) — real deep M15 (EURUSD ~1yr, XAUUSD
~3yr), look-ahead removed via causal swing confirmation, chronological
out-of-sample split. **Pooled TEST WR = 0.489 (n=562, p=0.83): the
coin-flip baseline.** The London+ATR "quality" filter H008b hoped would
lift WR to 60% collapsed out-of-sample (train n=11 → 63.6%, test → 35.5%;
pooled test 42.2%) — a textbook overfit the OOS split caught. BOS+FVG has
no standalone directional edge, joining H001/H002/H002b. Everything on the
LuxAlgo list is now either already built or measured and buried. No fix
warranted; the discipline is the fix.

### H008c — the honest BOS+FVG re-test (added to the rejected ledger)

| enhancement | method | verdict |
|---|---|---|
| **BOS+FVG entry** (SMC "market-structure break + fair-value-gap", the strongest LuxAlgo PAC concept) | causal (no look-ahead) detector on real deep M15, chronological OOS split, EURUSD+XAUUSD pooled | **No edge.** Pooled OOS test WR 0.489 (n=562, p=0.83) = coin flip; session/ATR filter *worse* (0.422). The earlier 55.2% was shallow, H1-resampled, look-ahead-inflated. |

## Reproducibility

Every result above is bound to a git-tracked manifest in
`research/results/*_manifest.json` (commit hash + config hash + dataset
SHA256): `engine_activation_*`, `crypto_volume_*`, `pairs_trading_*`,
`h4_yearly_stability_*`, `d1_backtest_*`, `h4_backtest_*`,
`ctrader_spread_recost_*`, `ic_symbols_backtest_*`, `h008c_oos_*`,
`engine_subset_search_*`. Re-run
any experiment from its script in `scripts/` (or `run_h008c.py`) to
reproduce; `scripts/fetch_m15_twelvedata.py` rebuilds the real M15 inputs.

### H017 — SMC full-spec as internal confluence (added to the rejected ledger, 2026-07-10)

The one formulation H008c left open: OB+FVG+BOS/CHoCH not as *entries* but
as causal *score modulators inside* the SMC engine, A/B'd with one flag
(`engines.smc_full_spec`) against a pre-registered decision rule.

| enhancement | method | verdict |
|---|---|---|
| **SMC full-spec internal confluence** (order blocks + FVG + BOS/CHoCH modulating SMC's structural score, ±12/±8/±8) | controlled A/B, same bars/config, chronological 65/35 split, EURUSD/XAUUSD/BTCUSD/ETHUSD deep H4 (`scripts/smc_fullspec_ab.py`) | **No improvement.** TEST mean ΔPF **−0.04** (rule required ≥ +0.03), 3/4 symbols worse (EURUSD −0.152, XAUUSD −0.190, BTCUSD −0.354; ETHUSD +0.536 is a single-symbol n=55 outlier — per-symbol enabling would be selection bias). TRAIN showed the usual mirage (XAUUSD +0.062 flipped negative OOS). Flag stays FALSE; detectors remain in-repo for future re-tests. |

With H001/H002/H002b (sweeps), H008/H008b/H008c (BOS+FVG entries) and now
H017 (internal confluence), every formulation of the SMC concept family has
been measured. The structural swing bias that already runs is the only part
that earns its seat.

### H015 final — engine-subset search closed (2026-07-10, post-Axis-6 voting)

| enhancement | method | verdict |
|---|---|---|
| **Any engine-subset change to prod4** (greedy bidirectional search under the unified voting logic) | TRAIN-only search + held-out TEST, run at 3 symbols and re-run at 15 (pre-registered rule: ΔPF ≥ +0.03 AND wins ≥ ⅔) | **No robust improvement exists.** The 3-symbol run selected a 5-set (+0.058, 2/3 — passed); the 15-symbol confirmation selected a *different* 7-set (Δ+0.170 but 9/15 wins < ⅔ — failed). Universe-dependent selection = noise-fitting (the 15-sym pick even includes the sentiment price-proxy). prod4 kept by burden-of-proof. Stable across every run: nnfx + price_action are load-bearing; nothing else is provable. |

Sobering side-reading the operator should keep: on the held-out TEST year
(~mid-2025→mid-2026) prod4's mean PF across the full 15-symbol universe was
**0.87** — the FX book sits below breakeven while the carriers (XAUUSD 1.33,
BTCUSD 1.37) stay positive. Third independent confirmation of the audit's
core verdict: the edge lives in the carriers; FX pays for the privilege of
diversification it doesn't deliver.

---

## Deep-history stability re-run (2026-07-13) — carriers strengthen, FX gets a FOURTH confirmation

Server migration (new VPS) motivated pulling the deepest H4/D1 history
each provider actually serves (`scripts/download_deep_history.py`,
extended that day to route crypto H4 through ccxt/Binance — measured
deeper than Twelve Data's floor for BTCUSD/ETHUSD specifically, D1 stays
on Twelve Data since its BTC series predates Binance's 2017 founding).
Re-ran the frozen production system — **engines/thresholds completely
unmodified** — against that deeper data per symbol
(`scripts/run_h4_yearly_backtest.py`), bucketed by exit year. **IN-SAMPLE
relative to system development, same as every backtest in this
document — does not satisfy `research/edge_gate.py` PROMOTION_CRITERIA
(no OOS split, no walk-forward, no Monte Carlo in this run).** Manifest:
`research/results/h4_yearly_stability_deep_20260713_manifest.json`.

**Carriers — same verdict, now on a much longer window:**

| carrier | n | PF | WR | window |
|---|---|---|---|---|
| BTCUSD | 345 | **1.468** | 44.1% | 2017-08-17 → 2026-07-13 (8.9y — ccxt/Binance's real listing history) |
| ETHUSD | 347 | **1.435** | 43.2% | 2017-08-17 → 2026-07-13 (8.9y) |
| XAUUSD | 290 | **1.308** | 40.0% | 2020-01-24 → 2026-07-14 (6.5y — Twelve Data's free-plan H4 floor) |

BTCUSD and ETHUSD now individually clear the `PROMOTION_CRITERIA` sample
bar (≥300 trades) AND the PF bar (≥1.2) **in-sample** — walk-forward and
Monte Carlo are still required before that means anything for promotion,
but it is the strongest single-symbol in-sample reading yet, on the
longest window yet.

**FX — a fourth independent confirmation, and an initial "root cause"
theory that did NOT survive a controlled test:** all 7 tested FX pairs
(EURUSD, GBPUSD, USDJPY, USDCHF, EURJPY, GBPJPY, AUDJPY) came back at PF
0.907–1.008 over the SAME ~6.4y window Twelve Data has always served
(unchanged by this migration) — worse than the ~1.03–1.10 this document
has published since. This lines up with, and is a fourth confirmation
of, the same pattern H015's held-out TEST year and the IC Markets sweep
already found: **the FX book does not clear breakeven under the current
system; the carriers do.**

Diffing this run's config fingerprint against
`research/results/h4_yearly_stability_20260705_manifest.json` (the
source of the original 1.03–1.10 figures) found exactly one
`behavior_blocks` difference — `confluence.min_informative_weight_share`
did not exist on 2026-07-05 and is `0.6` now (the Axis-8 "SPEAKING
panel" gate) — and this document originally floated that as the likely
cause of the discrepancy. **That theory was wrong.** H020 (pre-registered
same day, `research/results/registry.json`) tested it as a controlled A/B
— same bars/config, chronological TRAIN/TEST split, gate 0.0 vs 0.6,
across the 7 FX pairs AND the 3 carriers as a control group — and it was
refuted outright: TEST-slice FX mean ΔPF = +0.005 (needed ≥ +0.03 to
implicate the gate), 3 of 7 FX pairs actually got WORSE with the gate
disabled, and the carriers swung MORE than FX did (mean |ΔPF| 0.056 vs
0.005 — BTCUSD alone moved −0.095). The gate is not the explanation.
**The actual cause of the FX PF discrepancy between the two manifest
dates is unexplained and open** — worth keeping in mind (different git
commit, different code between 07-05 and 07-13, so something else
changed) but not worth chasing further without a fresh, narrower
hypothesis. Full result: `research/results/h020_ab.json`; verdict logic
and per-symbol breakdown in H020's registry entry.

**Inconclusive (sample too small to read either way):** XAGUSD, USOIL,
US30, NAS100, SPX500 showed PF 1.07–1.46 but n=35–93 — Yahoo's ~2.4–2.9y
H4 window (their Twelve Data plan-gate fallback) is nowhere near the
300-trade bar. Not evidence for or against; just short.

Registry: `research/results/registry.json`'s H009 entry carries the full
per-symbol breakdown under `addendum_2026-07-13`, explicitly marked
`in_sample: true` — H009's `status` is unchanged (still PASSED-but-flagged
by the edge_gate trust audit; this addendum does not and should not
change that).
