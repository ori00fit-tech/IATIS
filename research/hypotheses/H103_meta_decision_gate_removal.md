# Hypothesis

## ID
H103

## Title
Does `confluence/meta_decision.py`'s live confidence-gate (BLOCK when
confidence < 40, downgrades EXECUTE → NO_TRADE) improve OOS PF, or does it
just double-count already-gated information and cost good trades for free?

## Statement
`meta_decision.py` is live in the current decision path (`main.py:485-502`):
after confluence/risk pass, it recomputes a "confidence" score from the
confluence score, engine agreement, engine-contribution stability, and a
data-quality estimate — all four already priced into the decision by
earlier gates (`min_score_to_trade`, `min_engines_agreeing`) — and can
independently flip `EXECUTE` to `NO_TRADE` when that recombined confidence
falls below 40.

The claim under test: **removing this gate (letting decisions through that
it currently blocks) does not materially cost out-of-sample PF, because the
information it acts on is already spent by earlier gates.**

## Why this might be true
`docs/PHILOSOPHY_AUDIT_2026-07.md:83` names this exact mechanism as
structural double-counting: *"`meta_decision.py` recombines the confluence
score (already gated) with agree_count (already gated by
min_engines_agreeing) into a 'confidence' that gates again — the same
information vetoes twice."* Lines 144/191/240 of the same document
recommend deleting it outright.

A much stronger, quantitative prior exists from **H033** (`FAILED`,
2026-07-22): a *trained* logistic-regression meta-model fit on this exact
feature family — confluence score, per-engine agreement vector, regime,
volatility, session, ATR percentile, D1 alignment, RR — could not rank the
system's own historical trades out-of-sample at all (TEST AUC **0.5071**,
sanity floor 0.55; walk-forward read even flipped sign, W1 0.548 → W2
0.474). `meta_decision._confidence_score()` is an *untrained*, hand-picked
linear-ish combination of a subset of those same features (30% score + 30%
stability + 20% agreement + 20% data quality). If a fitted model on richer
inputs has ~zero OOS ranking power over win/loss, there is no reason to
expect an unfitted formula on fewer inputs to do better.

## Why this might be false
H033 tested a *different* mechanism (a trained model scoring the whole
decision for win probability) — it does not itself prove
`meta_decision.py`'s specific formula and threshold (confidence < 40 →
BLOCK) is worthless; the two could behave differently in practice,
especially since `meta_decision.py`'s stability/data-quality terms are not
identical to anything in H033's feature set. It is also possible the gate
disproportionately blocks *bad* trades in a way a linear correlation with
win/loss wouldn't fully capture (e.g. tail-risk avoidance rather than
average-case ranking). This is exactly why H103 is a dedicated A/B rather
than an inference drawn straight from H033.

## Data required
- Symbol(s): full 20-symbol universe (subset selection is known noise —
  H015).
- Timeframe(s): H4 decision / D1 confirmation (production timeframe).
- Date range: chronological TRAIN/TEST split, H008c method.
- Minimum sample size: pooled arm-A TEST n ≥ 300
  (`research/edge_gate.py` `PROMOTION_CRITERIA`).

## Method
Reuse the H024/H033 arm-A/arm-B harness pattern on the frozen prod4
pipeline. Arm A = current live behavior (gate active). Arm B = identical
in every other respect (entries/exits/thresholds/all other gates
byte-identical — isolates only this one gate, same discipline as
H017/H024) except the BLOCK verdict never downgrades the decision.

## Decision rule (written before any run)
REMOVE the gate only if, on pooled TEST:
1. PF(B) ≥ PF(A) − 0.03, AND
2. carriers-only PF(B) ≥ PF(A) − 0.03 (the one book CLAUDE.md says matters
   must not degrade), AND
3. trade count(B) > trade count(A) (confirms the gate was actually
   blocking something, not vacuous).

KEEP the gate (FAILED — do not remove) if PF(B) drops by more than 0.03
pooled or on carriers specifically. `|ΔPF| < 0.03` with no material n
difference = NULL, keep as-is (not worth the governance churn of removing
something inert-but-harmless). A different feature set, formula, or
trained-model version of this gate is a new hypothesis with a new ID.

## Falsification criteria
Same as the decision rule above — this is a symmetric A/B, not a one-sided
promotion test, since the gate is already live (the null result is "keep
current behavior," not "do nothing").

## Distinct from prior kills
NOT H033 (that tested a *new* trained model as a gate; H103 tests whether
the *existing*, already-live hand-crafted `meta_decision.py` gate should be
removed — different mechanism, though H033's finding is the motivating
prior). NOT H013/`reversal_veto` (that gate was a proven structural no-op,
removed outright the same day this hypothesis was registered — H103's gate
is demonstrably live and must be measured, not assumed dead).

## Live safety
Measurement only, on the backtest harness. Zero live/config change until
this hypothesis resolves and is promoted through the normal process.
`main.py`'s live `meta_decision` gate stays exactly as-is regardless of how
long this takes — same FROZEN discipline as H018/H023/H024/H033. A plan
review's plausible-sounding critique does not become a live change without
its own OOS evidence (CLAUDE.md rule 6).

## Status
`PLANNED` — pre-registered only, no code runs yet.

## Linked experiment
None yet — natural implementation point is a variant of
`research/experiments/H033_meta_confidence_gate.py`'s arm-A/arm-B harness,
substituting the BLOCK-downgrade toggle for H033's percentile-skip toggle.

## Linked result
None yet.
