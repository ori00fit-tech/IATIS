# Hypothesis

## ID
H101

## Title
SMC swing-structure engine as a confluence input (governance closure — not new work)

## Statement
This hypothesis does not propose new code or a new test. It retroactively
documents the SMC engine's standing evidence, closing a governance gap:
`smc` has been enabled in production (`config/engines.yaml`) since before
this repo's edge-gate discipline existed, and was carried in
`research/edge_gate.py`'s `EXEMPT_ENGINES` set on the stated rationale that
it is "a plain technical structure/trend read" claiming no edge. That
rationale does not hold up: SMC is weighted (`config.yaml`, weight ≈0.202)
and votes inside a scored confluence system exactly like every other
gated engine, so it should carry a hypothesis like every other engine
(CLAUDE.md rule 1) rather than bypass the gate.

The claim under test, stated honestly: **the SMC engine's current
implementation — swing-point structural bias only (HH/HL vs LH/LL); order
blocks, FVG, BOS/CHOCH, and liquidity zones are explicit
`NOT_IMPLEMENTED_PHASE_3` stubs — contributes non-negative marginal value
to the production 4-engine confluence set (nnfx, price_action, smc,
wyckoff) out-of-sample.**

## Why this might be true
Structural swing bias is a legitimate, if narrow, trend-confirmation
signal, and is correlated with (but not identical to) the NNFX trend
baseline (measured pairwise vote agreement smc↔nnfx 73.6%,
`docs/PHILOSOPHY_AUDIT_2026-07.md` §5).

## Why this might be false
The same document's leave-one-out ablation found removing SMC *improved*
PF on 7/10 symbols (mean ΔPF +0.054, the largest drag of any engine in
that study) — though that study used a different (pre-Axis-6) voting
implementation than the current production system, so it is suggestive,
not decisive, on today's code.

## Existing evidence (not new — cited from the registry)
`H015` (Ablation Study — Minimum Engine Set, `RESOLVED`, 2026-07-10,
15-symbol OOS-confirmed): *"drop-smc helps mildly in-train everywhere but
no alternative containing it survives the adoption rule OOS."* In other
words: dropping SMC looks attractive in-sample, but every subset search
that tried to capitalize on that (replacing or removing SMC) failed the
pre-registered OOS adoption rule at 15 symbols. This is genuinely mixed
evidence, not a clean win either direction — which is exactly why this
engine's status is `RESEARCH`, not `PASSED`, below.

## Data required (for a future dedicated test)
- Symbol(s): the full 20-symbol universe (not a 3-symbol or 6-symbol
  subset — H015 demonstrated subset selection is universe-dependent noise).
- Timeframe(s): H4 decision / D1 confirmation (production timeframe).
- Date range: chronological TRAIN/TEST split, H008c method (causal, no
  look-ahead).
- Minimum sample size: ≥300 OOS trades per `research/edge_gate.py`
  `PROMOTION_CRITERIA`.

## Falsification criteria
A dedicated A/B (prod4 vs. prod4-minus-SMC, or prod4 vs. prod4 with SMC's
score contribution zeroed) on the full 20-symbol universe, chronological
OOS split: reject (demote engine toward disable) if the SMC-included arm's
TEST-slice mean ΔPF is not positive with the OOS-win-fraction ≥ 60%
(matching H024/H017's adoption bar). This test has not yet been run in
this exact form — H015 answered a related but not identical question
(best subset, not marginal SMC contribution under the current voting
system) — and is the natural next step before this hypothesis can move to
`PASSED` or `FAILED`.

## Status
`RESEARCH` — enabled for live (demo) paper-trading and evidence
accumulation, consistent with every other production engine
(nnfx=H004, wyckoff=H006), not proven, not disproven. Superseded a bare
`EXEMPT` bypass that predated the edge gate's PROMOTION_CRITERIA (2026-07-09).

## Linked experiment
None dedicated yet — see "Falsification criteria" for the specific A/B
that would resolve this. Related existing evidence:
`scripts/engine_research.py` (H015), `research/results/registry.json` H015.

## Linked result
`research/results/engine_subset_search.json`,
`research/results/engine_subset_search_15sym.json` (H015's artifacts —
cited as existing evidence, not generated for this hypothesis).
