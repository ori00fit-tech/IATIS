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
| ETHUSD | ~1.40–1.56 | strongest |
| BTCUSD | ~1.5 | $12 spread negligible vs the asset's range |
| XAUUSD | ~1.21–1.34 | modest haircut from real 12-pip spread |
| FX majors (7 kept) | ~1.03–1.10 | real spread 0.0–0.4 pip, BELOW the backtest assumption → conservative |

Portfolio PF by year never fell below 1.0 (2020→2026). The edge is: **4
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

**What NOT to do:** keep inventing entry-signal experiments. Everything
tried has been measured and rejected; more of the same is flailing, not
research.

---

## Reproducibility

Every result above is bound to a git-tracked manifest in
`research/results/*_manifest.json` (commit hash + config hash + dataset
SHA256): `engine_activation_*`, `crypto_volume_*`, `pairs_trading_*`,
`h4_yearly_stability_*`, `d1_backtest_*`, `h4_backtest_*`,
`ctrader_spread_recost_*`. Re-run any experiment from its script in
`scripts/` to reproduce.
