# Hypothesis H025 — Opportunity Scarcity Engine (statistical rarity as edge)

## ID
H025

## Title
Does the *statistical rarity* of the current decision context — how infrequently
this exact market state has occurred recently — predict trade quality, such that
filtering the system's EXECUTE signals to only their rarest states improves
out-of-sample profit factor?

## Statement
Every prior hypothesis tested the *content* of the setup — price structure
(SMC/ICT/BOS/FVG), indicators, regime value, session, news. None tested a
*meta* property: the **frequency of the decision context itself**. H025 asks
whether markets reward exceptional (rare) states more than routine (common) ones.

For each decision, define a pre-specified discrete **state tuple** and compute
its **rarity** = 1 − frequency of that tuple among all decisions in the trailing
180 calendar days. Claim to test: overlaying the current system with a rarity
filter — take an EXECUTE signal only when its state-rarity is high — raises OOS
PF, with a **monotonic dose-response** (rarer ⇒ better), not just one lucky bucket.

This is a filter overlay on the unchanged confluence output: it never creates a
trade, alters an entry/exit, or adds a new signal. It only suppresses common-state
trades. So an A/B attributes any ΔPF solely to the rarity overlay.

## The state tuple (pre-declared — NOT to be fitted after seeing results)
Built entirely from fields the pipeline already emits (non-circular w.r.t. buried
entry patterns):
- `regime_state` ∈ {TRENDING, RANGING}
- `volatility` ∈ {low, normal, high, extreme}
- `confluence.score` bucket ∈ 5-pt bins {55-60,60-65,65-70,70-75,75-80,80-85,85-90}
- `mtf.confirming` ∈ {True, False}
- `session` (primary) ∈ {Asia, London, NewYork, Overlap}
- `winning_bias` ∈ {BULLISH, BEARISH}

Rarity is computed over **all decisions** in the trailing window (EXECUTE and
NO_TRADE), giving a stable frequency base; the filter is then applied only to the
EXECUTE subset. Window = 180 calendar days (pre-declared).

## Why this might be true (and the honest reason it might not)
Rare joint states may coincide with genuine regime shifts / dislocations the
routine machinery under-serves. **But the dominant risk, stated up front:** rare
states are by construction low-count, so their in-sample PF is high-variance and
the rarest buckets will show extreme PF from noise alone. "Rare = good" is
structurally the same trap that killed H008b (63.6% train → 35.5% test) and H015
(subset selection = universe-dependent noise). H025 is only worth anything if it
survives a **minimum-sample OOS test with a monotonic dose-response** — a single
lucky rare bucket is explicitly disqualified below.

## Data required
- Symbols: full production universe (the 19 of `full_pipeline_backtest`
  ACTIVE_SYMBOLS), so consistency and carrier-isolation are meaningful.
- Timeframe: H4 decision TF (+ D1 MTF gate), FROZEN prod4, real measured spread.
- Split: chronological TRAIN(65%)/TEST(35%) per symbol; rarity for a TEST-slice
  decision uses only the trailing-180d window ending before that decision (no
  look-ahead — the window can include TRAIN-era decisions, never future ones).
- Rarity grid (pre-declared): keep EXECUTE trades in the least-frequent
  {20%, 10%, 5%, 2%} of the state-frequency distribution (four operating points).

## Falsification criteria
Decided before any result. **ADOPT the OSE overlay only if ALL hold on the pooled
TEST slice:**
1. **Dose-response:** pooled TEST PF is monotonically non-decreasing as the
   rarity threshold tightens (20%→10%→5%→2%, each ≥ previous − 0.02 tolerance),
   AND the tightest bucket that still satisfies the sample guard has
   `PF(B) ≥ PF(A) + 0.15`.
2. **Sample guard (anti-mirage, the critical one):** the bucket carrying the
   adopt decision has pooled TEST `n ≥ 200`. Buckets with `n < 200` are reported
   but CANNOT carry a PASS — if no bucket reaches n≥200, the verdict is
   `INCONCLUSIVE (insufficient sample)`, never ADOPT.
3. **Expectancy:** `expectancy(B) > expectancy(A)` (mean R-multiple / $ per trade)
   at the adopt bucket.
4. **Consistency:** at the adopt bucket, `≥ 60%` of symbols with `n ≥ 15` improve
   vs arm A (H015 cherry-pick guard).
5. **Carriers not degraded:** carriers pooled `PF(B) ≥ PF(A) − 0.05`.

Any failure → **FAILED / NO CHANGE** (logged in the rejected ledger). No monotonic
trend and `|PF(B) − PF(A)| < 0.15` at all feasible buckets = valid **NULL**
(statistical rarity is immaterial). Insufficient sample at every bucket =
**INCONCLUSIVE** (not evidence either way — the honest outcome if 2% is too strict
to test).

## Distinct from prior kills and from H024
- Not an entry pattern (SMC/ICT/BOS/FVG — all dead): adds no setup detector.
- Not trade management (dead): exits untouched.
- Not traditional filtering (ATR/session/news): those gate on the *value* of a
  feature; H025 gates on the *frequency* of the joint state.
- Not H024: H024 gated on regime *state* (trend vs range); H025 gates on state
  *rarity*. Orthogonal axis.

## Live-safety (non-negotiable)
Measurement only. Feature flag `features.opportunity_scarcity` (**default false**).
No live change until the forward-demo milestone (CLAUDE.md rule 6). H025 is
**FROZEN** like H018/H023/H024 and never resets the prospective counter.

## Status
`PLANNED`

## Linked experiment
`scripts/H025_opportunity_scarcity_ab.py` — single frozen-pipeline pass per
symbol recording each EXECUTE trade's pre-declared state tuple + trailing-180d
rarity; arms derived post-hoc by rarity threshold (arm A = all trades; arm B(p) =
rarest p%). backtest_mode=True. Single pass because the overlay only suppresses,
so ranking arm-A's actual opportunities by rarity is the faithful and cheap test.

## Linked result
`research/results/H025_opportunity_scarcity_ab.json`
