# Hypothesis H025 — Information Compression (pre-breakout order, not volatility)

## ID
H025

## Title
Before large moves, the H4 price path becomes more *algorithmically ordered*
(lower Lempel-Ziv complexity) — not merely lower-volatility — and gating the
frozen prod4 pipeline to trade only in below-median-complexity states improves
pooled out-of-sample profit factor.

## Statement
Operator-proposed (2026-07-21): "the market tells us when it becomes
predictable." Formalized as two claims, tested in strict order:

1. **Information claim (Stage 1).** Let `C_t` = LZ76 complexity of the binary
   sign-of-return sequence over the trailing 64 H4 bars. Bars in the bottom
   complexity quintile (vs the symbol's trailing 500-bar distribution) are
   followed by larger normalized moves: median of
   `M_t = (max(high)−min(low) over next 20 bars) / ATR14_t`
   is ≥ 1.10 × the unconditional median.
2. **System claim (Stage 2).** Adding a gate to the FROZEN prod4 pipeline —
   arm B emits `NO_TRADE` unless `C_t` is below its trailing 500-bar median
   ("market more compressible than usual"), all else byte-identical — improves
   pooled TEST-slice PF vs arm A (current system) by a material, robust amount.

Stage 2 runs **only if Stage 1 passes on the TRAIN slice**. A dead Stage 1
kills the hypothesis without spending the OOS data.

## Pre-registered parameters (frozen NOW to kill forking paths)
- Complexity measure (decision input): **LZ76** on the binary sequence
  `b_i = 1 if close_i > close_{i−1} else 0`, window **64** H4 bars,
  normalized by the random-sequence expectation `n / log2(n)`.
- Percentile reference: trailing **500** bars, per symbol.
- Stage-1 forward horizon: **20** bars; normalization **ATR(14)** at signal bar.
- Stage-2 gate threshold: percentile ≤ **0.50** (median).
- Secondary measures — Shannon entropy (8-bin discretized returns, same
  window) and zlib compression ratio — are computed and *recorded for the
  registry only*. They are NOT decision inputs and cannot rescue a failed LZ76.
- No parameter above may be changed after the first data touch. A variant
  with different parameters is a NEW hypothesis with a new ID.

## Why this might be true (and the honest reason it might not)
Accumulation/coiling before breakouts can produce *structured* paths
(repeating micro-patterns, compression in the algorithmic sense) that a
volatility measure misses: a low-ATR window can still be maximally random,
and a high-ATR window can be highly ordered. Complexity is a genuinely
different axis from ATR/Bollinger width — which is why this is not the dead
"squeeze" folklore.

**Stated up front:** LZ complexity on sign sequences correlates with trend
persistence and with volatility clustering. If Stage 1 "works" only because
low complexity proxies for an ATR squeeze or an existing trend, the gate adds
nothing over what the system already conditions on. Diagnostic (recorded, not
a gate): Stage-1 effect re-computed within the middle ATR-compression tercile.
If the effect vanishes there, that is written into the result JSON verbatim.

## Distinct from prior kills (not a rebuild of the dead list)
- **Not H001/H002/H002b (liquidity sweeps)** — no liquidity or equal-high/low
  concept anywhere; input is the sign sequence of closes only.
- **Not crypto_volume A/B (volume inputs, dead)** — price-only; no volume term.
- **Not an entry pattern (H008 family)** — arm B adds no entries; it only
  withholds existing decisions, like H024's gate.
- **Not "Energy Accumulation" (operator idea H028, unregistered)** — H028's
  composite includes liquidity build-up (dead concept) and volume (measured
  ≈ 0); H025 deliberately isolates the single non-dead component.

## Data required
- Symbols: full 20-symbol production universe (`walk_forward_20260719` set),
  carriers isolatable as a control.
- Timeframe: H4 decision TF (+ D1 MTF gate in Stage 2), FROZEN prod4 pipeline,
  real measured spread as commission.
- Split: chronological TRAIN(65%)/TEST(35%) per symbol (H008c house standard).
  Stage 1 uses TRAIN only. Stage 2 verdict comes from TEST only.
- Minimum samples stated before running: Stage 1 pooled bottom-quintile
  `n ≥ 500` bars; Stage 2 pooled arm-A TEST `n ≥ 300` closed trades.

## Falsification criteria
Decided before any result exists.

**Stage 1 — proceed to Stage 2 only if ALL hold on TRAIN:**
1. pooled bottom-quintile median `M` ≥ 1.10 × unconditional median `M`;
2. bootstrap (1000 resamples, per-symbol block) p < 0.05 for that ratio > 1;
3. effect sign positive (ratio > 1.0) in ≥ 60% of symbols individually.

Stage-1 failure → **FAILED**, committed to the rejected ledger; Stage 2 is
never run; no OOS data is consumed.

**Stage 2 — ADOPT the complexity gate (arm B) only if ALL hold on pooled TEST:**
1. pooled `PF(B) − PF(A) ≥ 0.15`;
2. arm B retains ≥ 50% of arm A's TEST trade count (volume-collapse guard);
3. improvement in ≥ 60% of symbols individually (H015 cherry-pick guard);
4. carriers (XAUUSD/BTCUSD/ETHUSD) pooled `PF(B) ≥ PF(A) − 0.05`.

Any failure → **FAILED / NO CHANGE**. `|PF(B) − PF(A)| < 0.15` with volume
retained = valid **NULL** (complexity is real but immaterial at system level).

## Live-safety (non-negotiable)
Measurement only. Stage-2 gate lives behind `features.complexity_gate`
(**default `false`**, mirroring `features.regime_gate` / H024). No live
change regardless of verdict until the forward-demo milestone (CLAUDE.md
rule 6). FROZEN like H018/H023/H024 — cannot reset the prospective counter.

## Numbering note
H024's "not in scope" section previously floated "candidate H025" for a
regime-multipliers ON/OFF test. That idea was never registered; it is now
reserved as **H034** in `research/hypotheses/BACKLOG_2026-07-21.md`. This
H025 (operator's numbering, 2026-07-21 proposal) is the registered one.

## Status
`FAILED` — Stage 1, 2026-07-21 (same day as registration; the rule
pre-existed the data by hours, which is exactly the point).

**Result (VPS run, 15/20 symbols evaluable, pooled quintile n = 7353):**
pooled ratio **1.0049** vs the required ≥ 1.10; bootstrap p **0.309** vs
the required < 0.05; 9/15 symbols > 1.0 (the one guard that passed).
The mid-ATR-tercile diagnostic ≈ 1.0 as well — the effect is not hiding
behind volatility conditioning; low LZ76 complexity simply carries no
information about forward 20-bar range on this universe. Per the
pre-registered rule, Stage 2 is never built and no OOS data was consumed.
Ledger: `docs/STRATEGY_EVIDENCE_2026-07.md`; result JSON + manifest
committed from the VPS.

## Linked experiment
`research/experiments/H025_information_compression.py` (to be written AFTER
this registration: Stage-1 script first; Stage-2 A/B runner only if Stage 1
passes, following the `scripts/H024_regime_gate_ab.py` two-arm pattern with
`backtest_mode=True`).

## Linked result
`research/results/H025_information_compression.json`
