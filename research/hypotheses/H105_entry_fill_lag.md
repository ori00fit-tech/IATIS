# Hypothesis

## ID
H105

## Title
Does the gap between the strategy's reference entry price and the actual
broker fill cost measurable edge, and can a limit order close it?

## Statement
Placing a limit order at the decision-bar close price (the strategy's own
reference price) instead of an immediate market order at scheduler-tick
time reduces the average entry cost (measured against that reference
price) by **≥ 0.15 ATR**, without missing more than **15%** of trades that
would otherwise have filled (price moving away before the limit fills).

## Correction from the original framing
An operator plan review (2026-07-23) proposed this as "limit entry at
`H4_open − 0.3×ATR`, instead of market-open entry." Verified against the
actual code before registering:

- `main.py:369` — `entry = df_base["close"].iloc[-1]`: the strategy's
  reference entry price is the **close** of the last decision-timeframe
  bar, not the bar's open.
- `iatis-scheduler.service` runs `scheduler.py --interval 120` — every 2
  hours, **not synchronized to H4 candle boundaries**. The real cTrader
  fill happens whenever `execute_from_report()` next runs, at whatever
  live spot price exists then — via a market order
  (`execution/ctrader_client.py: place_market_order`), which fills near
  current spot, not at any specific offset from a candle open.

The actual untested gap is **reference-price-vs-live-fill lag** (how much
the market moved between the decision bar's close and the scheduler's next
tick), not "open vs. limit within a single candle" as originally framed.

## Why this might be true
Any nonzero gap between decision time and execution time on trend-following
entries is expected to be adverse on average (chasing the trend costs a
few pips), especially with a 2-hour scheduler cadence that can leave
substantial drift between the reference close and the actual fill.

## Why this might be false
The gap could be small and unbiased (as likely to help as hurt), in which
case building limit-order infrastructure would add execution risk (missed
fills, partial fills, added complexity) for no real gain — exactly the
kind of "complexity without edge" this project's dead list repeatedly
found in unrelated contexts.

## Explicitly not a re-run of a dead idea
- **NOT** managed exits (partial TP / break-even / trailing stops) — dead
  list: `trade-management A/B`, "+100% was a look-ahead artifact; OOS ≈ 0."
  H105 only concerns **entry** fill quality; exits are untouched.
- **NOT** a correlation cap between BTCUSD/ETHUSD — already live:
  `risk/correlation_engine.py` groups both under `RISK_ASSETS`, and
  `correlated_exposure_pct` already gates on it (`risk/risk_engine.py`).
  Not a new idea; not part of this hypothesis.

## Data required
None new for Stage 1 — the TCA ledger (`storage/execution_quality.py`,
live since 2026-07-16, fixed 2026-07-23 to actually populate fill prices —
see the `position.price` fix in `execution/ctrader_client.py`) already
records, for every real fill: intended price, actual fill price, and
slippage in pips. Stage 1 is pure analysis of data already accumulating.

## Method
**Stage 1 (measurement, no execution change):** once enough real fills
exist in the TCA ledger, compute the realized slippage distribution
directly — mean, median, and adverse-tail — against the strategy's
reference close price, expressed in ATR-equivalent units per symbol.

**Stage 2 (only if Stage 1 passes):** design and backtest a limit-order
variant, then a demo-only live trial behind a new config flag.

## Decision rule (written before any analysis)
- **PASS** (proceed to Stage 2 — build limit-order execution path): TCA
  data shows mean adverse slippage ≥ 0.15 ATR-equivalent across ≥ 100 real
  fills.
- **FAIL** (not worth building): mean adverse slippage < 0.05
  ATR-equivalent, or the distribution is not consistently adverse (current
  market-order timing is not systematically costly).
- Between: **INCONCLUSIVE**, keep accumulating TCA data.

## Falsification criteria
Same as the decision rule's FAIL branch.

## Live safety
Stage 1 uses TCA data already being collected live — no code or config
change, no live-decision impact. Stage 2 (building limit-order logic) only
starts if Stage 1 PASSes, and even then stays behind a new config flag,
demo-only, until its own forward evidence accumulates — same discipline as
every other live-path change (CLAUDE.md rule 6).

## Status
`PLANNED` — pre-registered only. Stage 1 can begin as soon as enough real
TCA fills exist (blocked on real trade volume, not on any code work).

## Linked experiment
None yet.

## Linked result
None yet — will draw on `storage/execution_quality.py`'s ledger once
populated.
