# Hypothesis H033 — Meta-confidence gate (the system predicts its own hit rate)

## ID
H033

## Title
A meta-model trained ONLY on decision-time features of the system's own
historical closed (simulated) trades can rank future trades by win
probability well enough that refusing the lowest-confidence tranche improves
pooled out-of-sample profit factor — i.e. the system learns when NOT to
trust itself.

## Statement
Operator-proposed (2026-07-21). This adds no market signal, no engine, no
entry, no exit. It scores each decision the frozen prod4 pipeline already
produces, using only information available at decision time, and asks: do
trades that "look like" historical losers actually lose more often, out of
sample?

Claim to test: arm B — identical to arm A (frozen prod4) except that it
skips decisions whose meta-model score falls in the bottom 30% of the
TRAIN-slice score distribution — has materially higher pooled TEST PF.

## Pre-registered model spec (frozen NOW — no tuning, no model search)
- Model: **L2-regularized logistic regression**, `C = 1.0`, no interactions,
  no feature selection, scikit-learn defaults otherwise. One model, pooled
  across symbols. Explicitly NOT a model zoo: trying a second model family
  or a second hyperparameter is a new hypothesis with a new ID.
- Label: trade outcome (1 = closed at target, 0 = closed at stop) of the
  simulated trade.
- Features (decision-time only, all already present in decision reports):
  1. confluence score (raw),
  2. per-engine agreement vector (smc, price_action, nnfx, wyckoff:
     agree/disagree/abstain with final direction),
  3. detected regime (TRENDING/RANGING) and volatility class,
  4. session (London/NY/other),
  5. asset class (fx/metal/crypto/index/energy),
  6. ATR(14) percentile vs trailing 500 bars,
  7. D1 alignment flag,
  8. proposed RR.
  No price history, no future information, no per-symbol identity beyond
  asset class (guards against the model memorizing "BTC wins").
- Threshold: skip if score < the **30th percentile** of TRAIN-slice scores
  (relative threshold; the operator's floated "80% confidence" is
  uncalibratable at a ~34% base win rate — an absolute 0.80 cutoff would
  trade never, so the percentile form is registered instead).
- Split discipline: model fitted on TRAIN-slice trades only, then FROZEN and
  applied to TEST decisions. Walk-forward refit windows as robustness check.

## Why this might be true (and the honest reason it might not)
The 2026-07-19 attribution run (3238 in-sample trades) shows outcome
heterogeneity across score bands, regimes, and asset classes; if any of it
is stable rather than noise, a linear model can harvest it as a skip rule.
This is meta-labeling (López de Prado) applied to IATIS's own ledger, and it
is philosophically aligned with the shadow book: measure the system's own
decision quality instead of adding market folklore.

**Stated up front:** every gate this project has measured so far cost edge
or did nothing (7 gates, shadow book; engine additions, H015; SMC full-spec,
H017). The confluence score itself is already a hand-crafted confidence
proxy, and its marginal value was measured ≈ 0 — a learned recombination of
mostly-the-same inputs may inherit that flatness. The prior is against arm
B. That is what the OOS split is for.

## Distinct from prior kills (not a rebuild of the dead list)
- **Not H015 (engine-subset)** — all prod4 engines stay enabled; nothing
  about which engines run changes.
- **Not confluence re-weighting / threshold tuning (frozen state rule)** —
  min_score, quorum, weights are untouched in BOTH arms; the meta-gate sits
  strictly after the frozen decision.
- **Not managed exits (dead)** — exits byte-identical between arms.
- **Not H017 (SMC full-spec)** — no new market-structure detector; inputs
  are the system's own outputs.

## Data required
- Symbols: full 20-symbol universe (`walk_forward_20260719` set).
- Source trades: closed simulated trades of the FROZEN prod4 pipeline, H4 +
  D1 MTF, real measured spread (the same backtest harness as H024, arm A).
- Split: chronological TRAIN(65%)/TEST(35%) per symbol (H008c standard);
  the fit uses TRAIN trades only.
- Minimum samples stated before running: pooled TRAIN `n ≥ 1000` closed
  trades for the fit; pooled arm-A TEST `n ≥ 300` closed trades for the
  verdict. (The 2026-07-19 run produced 3238 trades total, so this is
  feasible without new data sources.)

## Falsification criteria
Decided before any result exists.

**Sanity gate (kills cheaply):** the frozen TRAIN-fit model must reach
pooled TEST **AUC ≥ 0.55**. If the model cannot rank its own trades out of
sample at all, the hypothesis is **FAILED** regardless of any PF delta
(a PF "win" with AUC ≈ 0.5 is luck, not self-knowledge).

**ADOPT the meta-gate (arm B) only if ALL hold on the pooled TEST slice:**
1. pooled `PF(B) − PF(A) ≥ 0.15`;
2. arm B retains ≥ 50% of arm A's TEST trade count (with a 30% skip rule
   this should hold by construction; if drift pushes retention below 50%,
   that is itself a fail);
3. improvement in ≥ 60% of symbols individually (H015 cherry-pick guard);
4. carriers (XAUUSD/BTCUSD/ETHUSD) pooled `PF(B) ≥ PF(A) − 0.05`.

Any failure → **FAILED / NO CHANGE**, committed to the rejected ledger.
`|PF(B) − PF(A)| < 0.15` with AUC ≥ 0.55 = **NULL** worth recording: the
system CAN rank itself but the ranking is not monetizable at this threshold.

## Live-safety (non-negotiable)
Measurement only. Behind `features.meta_gate` (**default `false`**). Never
touches live decisions; no live change regardless of verdict until the
forward-demo milestone (CLAUDE.md rule 6). FROZEN like H018/H023/H024 —
cannot reset the prospective counter mid-sample. The model is fit on
simulated history only; it never trains on or alters the live demo book.

## Status
`FAILED` — 2026-07-22, killed by the pre-registered AUC sanity gate.

**Result (VPS run, 3415-trade arm-A ledger; arm A reproduced H024's arm A
exactly — PF 1.12, n=1187):** TEST AUC **0.5071** vs the 0.55 floor; the
model cannot rank the system's own trades out of sample. Walk-forward
recorded reads (W1 0.548, W2 0.474) show noise flipping sign, not a
threshold artifact. ΔPF −0.004 (immaterial) and carriers degraded
1.335 → 1.233 — the skipped tranche contained *good* carrier trades.
Institutional meaning: the decision-time features carry ≈ zero
information about which EXECUTE decisions win — a coherence check on the
frozen-thresholds stance, and a weakened prior for every backlog idea
recombining the same features (H043, H049, adaptive thresholds).
`features.meta_gate` was never created. The featured ledger
(`research/results/H033_trade_ledger.json`) is preserved as a research
asset. Ledger entry: `docs/STRATEGY_EVIDENCE_2026-07.md`.

## Linked experiment
`research/experiments/H033_meta_confidence_gate.py` (to be written AFTER
this registration; consumes the arm-A trade ledger of the H024/H025 backtest
harness, fits the frozen spec above, replays the TEST slice with the gate).

## Linked result
`research/results/H033_meta_confidence_gate.json`
