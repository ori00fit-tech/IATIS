# Hypothesis H024 — Hard regime gate vs. the current soft regime-weighting

## ID
H024

## Title
The system already *reweights* engines by regime (`confluence/regime_weights.py`)
but never *blocks* on regime. Does a HARD regime gate — emit `NO_TRADE` when the
detected regime is unfavorable (trade only `TRENDING`) — beat the current
always-on, soft-weighted baseline out-of-sample across the universe?

## Statement
`regimes/regime_detector.py` classifies each bar as `TRENDING` / `RANGING`
(other states are Phase-3 stubs), and `confluence/regime_weights.py` applies
hand-crafted multipliers that *tilt* engine weights toward the regime. That is
**soft** gating: every regime is still tradeable, and the multipliers are — by
the file's own admission — "hand-crafted domain knowledge … can be validated
once enough data accumulates," i.e. never validated. IATIS's *measured* edge is
trend-capture on carriers; the design docstring of `regime_detector.py` even
states the intent that "schools of thought are turned on/off depending on
regime, not blended blindly" — an intent the code never implemented as an
actual on/off gate.

Claim to test: adding a hard gate — arm B emits `NO_TRADE` on any decision where
`regime == RANGING` (trade only `TRENDING`), all else identical — changes the
full pipeline's out-of-sample profit factor by a material, robust amount versus
arm A (the current soft-weighted, always-on system).

## Why this might be true (and the honest reason it might not)
Motivation: the measured house edge is *trend* capture; a gate that refuses to
trade outside trends could concentrate risk where the edge lives and drop the
breakeven-ish range trades. **Counter-evidence, stated up front:** the
2026-07-19 in-sample attribution run (3238 trades) found regime performance
essentially FLAT — `RANGING` WR 34.8% vs `TRENDING` WR 33.2% — i.e. in-sample,
RANGING is not the worse bucket, so the naive prior is *against* arm B. That is
exactly why this needs a chronological OOS test and a pre-registered rule: like
H017 and H015, the intuition ("trend-only must be better") is the kind of thing
that dies honestly when the OOS split refuses it. This hypothesis is worth
running *because* the in-sample hint is unfavorable — a robust OOS win against
that prior would be real signal; anything less confirms the frozen state.

## Distinct from prior kills (not a rebuild of the dead list)
- **H015 (engine-subset search, dead):** H024 does NOT change which engines are
  enabled — all of prod4 stay on. It gates whether to *trade at all* on the
  existing confluence output, given the detected regime. Different lever.
- **H002/H002b (qualified sweep + TRENDING regime, dead):** those tested a
  regime-filtered *entry pattern* (liquidity sweep). H024 adds no entry pattern;
  it is a portfolio-level tradeable/not-tradeable switch on the unchanged
  confluence decision. Different object.
- **Trade-management A/B (managed exits, dead):** H024 does not touch exits,
  stops, or targets — entries and exits are byte-identical between arms; only
  the decision to *take* the trade differs.

## Data required
- Symbols: full production universe (the 20 symbols of the
  `walk_forward_20260719` run), so the "≥ N% of symbols" guard is meaningful and
  the carriers can be isolated as a control.
- Timeframe: H4 decision TF (+ D1 for the MTF gate), FROZEN prod4 pipeline, real
  measured spread as commission.
- Split: chronological TRAIN(65%)/TEST(35%) per symbol (H008c house standard);
  walk-forward windows as a robustness check.
- Minimum sample stated before running: pooled arm-A TEST `n ≥ 300` closed
  trades (so the volume-retention guard below has a real denominator).

## Falsification criteria
Decided before looking at any result. **ADOPT the hard regime gate (arm B) only
if ALL hold on the pooled TEST slice:**
1. pooled `PF(B) − PF(A) ≥ 0.15` (beat the control by a real margin, not noise);
2. arm B retains `≥ 50%` of arm A's TEST trade count (a gate that "wins" only by
   collapsing to a tiny n is not an edge — this is the primary trap);
3. the improvement holds in `≥ 60%` of symbols individually (not carried by 1–2
   — the H015 cherry-pick guard);
4. carriers (XAUUSD/BTCUSD/ETHUSD) not degraded: pooled carrier
   `PF(B) ≥ PF(A) − 0.05`.

Any single failure → **FAILED / NO CHANGE** (soft weighting stays; logged in the
rejected ledger + `docs/STRATEGY_EVIDENCE_2026-07.md`). `|PF(B) − PF(A)| < 0.15`
with volume retained is a valid **NULL** — hard regime gating is immaterial at
system level, and the current soft weighting is not costing us edge.

## Not in scope (deliberately, for a clean single-mechanism test)
Whether the *existing* hand-crafted `regime_weights.py` multipliers add anything
over flat weights is a separate, also-worth-testing question (reserved **H034**:
multipliers ON vs OFF A/B — see `BACKLOG_2026-07-21.md`; H025 was subsequently
assigned to the information-compression hypothesis). H024 changes exactly one
thing — the presence of the
hard gate — so its verdict is attributable.

## Live-safety (non-negotiable)
REGARDLESS of the OOS verdict this is measurement only. Implemented behind a
feature flag `features.regime_gate` (**default `false`**, mirroring the existing
`features.market_quality_gate` pattern) so arm B never touches live decisions by
accident. No live code change until the forward-demo evidence counter reaches its
milestone (CLAUDE.md rule 6). H024 is **FROZEN** like H018/H023 and can never
reset the prospective counter mid-sample.

## Status
`NULL` — closed 2026-07-22 by the pre-registered rule, applied by the
pre-built runner. Pooled TEST PF(A)=1.12 (n=1187) vs PF(B)=1.096 (n=1000):
ΔPF −0.024 (needed ≥ +0.15); volume retention 0.842 (passed); B>A in 42.1%
of symbols (needed ≥ 60%); carriers PF 1.335 → 1.256 (−0.079, beyond the
−0.05 allowance). The RANGING trades the gate removed were contributing —
exactly the in-sample counter-prior stated at registration. Soft weighting
stays; `features.regime_gate` stays `false`. Ledger:
`docs/STRATEGY_EVIDENCE_2026-07.md`; result JSON + manifest committed from
the VPS.

## Linked experiment
`scripts/H024_regime_gate_ab.py` (to be written — a two-arm runner over the
frozen pipeline on identical bars, toggling only `features.regime_gate`, using
`backtest_mode=True` per the 2026-07-19 offline-infra fixes).

## Linked result
`research/results/H024_regime_gate_ab.json`
