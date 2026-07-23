# Hypothesis

## ID
H104

## Title
Crypto tick-level order-flow imbalance (cumulative delta divergence) as an
independent entry signal for BTCUSD/ETHUSD.

## Statement
Cumulative Delta (CD) — the running sum of (buy-initiated − sell-initiated)
taker volume from tick-level trade data — computed on a 15-minute
aggregation, diverging from price (price makes a new H4 high/low while CD
does not confirm) predicts mean-reversion within the next 4–8 H4 candles on
BTCUSD/ETHUSD, with **PF ≥ 1.3** after realistic costs (taker fee 0.04% +
spread).

## Why this might be true
- Crypto trades 24/7 with no session gaps, so order flow is continuous —
  unlike FX, which has no reliable free tick/volume feed at all
  (`docs/STRATEGY_EVIDENCE_2026-07.md:64`).
- Binance's `aggTrade` WebSocket stream is free and unlimited.
- Aggressor-classified tick data carries information genuinely absent from
  OHLC bars (who initiated the trade, not just how much volume traded).

## Why this might be false
The project has already measured that trade volume carries no edge for
these two engines when added as a bar-level feature (`crypto_volume`
experiment, ΔPF −0.016 BTC / 0.000 ETH). It is possible the information
content of tick-level aggressor flow is not meaningfully different from
aggregate volume once resampled to 15-minute buckets — divergence framing
could just be re-deriving the same signal in a more elaborate form.

## Explicitly not a re-run of a dead idea
- **NOT** the `crypto_volume` experiment (`research/results/crypto_volume_20260706_manifest.json`):
  that test fed raw ccxt bar-level volume into the existing engines as a
  feature. It has no `H`-number in this registry — a prior plan review
  mistakenly cited it as "H019 (rejected)"; that citation is corrected
  here.
- **NOT H019** (`Crypto positioning/sentiment as an internal confluence
  modulator` — funding rate, open interest, Fear & Greed Index): a
  different, still-`PLANNED` hypothesis about aggregated positioning
  proxies, not tick-level order flow.
- Three genuine differences from the dead test: tick/aggTrade granularity
  (not bar-level), aggressor-side classification (not an undifferentiated
  volume total), and an independent entry signal with its own logic (not a
  feature bolted onto an existing engine's score).

## Data required
- Source: Binance WebSocket `aggTrade` stream
  (`wss://stream.binance.com:9443/ws/<symbol>@aggTrade`) → local tick store
  → 15-minute CD aggregation. This pipeline does not exist yet.
- Symbols: BTCUSD, ETHUSD only (the only two crypto carriers).
- Minimum sample: pooled OOS n ≥ 300
  (`research/edge_gate.py` `PROMOTION_CRITERIA`) — the operator draft
  proposed n ≥ 100; raised here for consistency with every other engine's
  promotion bar rather than a lower bar for convenience.

## Decision rule (written before any data collection)
- **PASS** (promote to `RESEARCH`, eligible for demo paper-trading):
  pooled OOS n ≥ 300 AND PF ≥ 1.3 AND bootstrap p < 0.05.
- **FAIL**: PF < 1.1, or n < 150 after 3 months of live tick collection
  (a data-availability floor, distinct from the OOS-trade floor above).
- **INCONCLUSIVE**: between FAIL and PASS thresholds — extend 60 days,
  re-evaluate; does not reset the clock.

## Falsification criteria
Same as the decision rule's FAIL branch.

## Live safety
Research/data-collection only. No engine exists yet, no config flag, no
live-path change. Building the tick collector and 15-minute CD aggregation
is infrastructure work that can start immediately (the decision rule only
gates *promotion*, not data collection) — but no signal derived from this
work may influence any live or demo trading decision until PASS.

## Status
`PLANNED` — pre-registered only, no code runs yet.

## Linked experiment
None yet — first step is the tick-collector pipeline itself (does not
exist in this repo).

## Linked result
None yet.
