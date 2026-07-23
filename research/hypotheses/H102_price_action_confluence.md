# Hypothesis

## ID
H102

## Title
Price Action engine as a confluence input (governance closure — not new work)

## Statement
Like H101, this closes a governance gap rather than proposing new work:
`price_action` has been enabled in production since before the edge gate
existed and was carried in `EXEMPT_ENGINES` on the same "plain technical
read" rationale. It is weighted (`config.yaml`, weight ≈0.187) and votes
inside the scored confluence system, so it should carry a hypothesis like
every other engine.

The claim under test: **the Price Action engine (candlestick patterns +
RSI + Bollinger + momentum, rewritten once after a measured 0.975
correlation with NNFX) contributes non-negative — in fact, positive —
marginal value to the production 4-engine confluence set out-of-sample.**

## Why this might be true
Unlike H101 (SMC), the evidence here is not mixed.

## Existing evidence (not new — cited from the registry)
`H015` (Ablation Study — Minimum Engine Set, `RESOLVED`, 2026-07-10,
15-symbol OOS-confirmed): *"nnfx and price_action are load-bearing
(dropping either always hurts)"* — stated as stable across every subset
search run in that study (3-symbol and 15-symbol universes both). This is
the strongest positive OOS evidence any single engine in this codebase
carries short of the carrier-asset trend-capture edge itself.

## Data required (for a future dedicated test)
Same as H101 — full 20-symbol universe, H4/D1, chronological OOS split,
H008c method, ≥300 OOS trades.

## Falsification criteria
Reject (demote toward disable) only if a dedicated marginal-contribution
A/B under the current (post-Axis-6) voting system contradicts H015's
finding — i.e. TEST-slice mean ΔPF from removing price_action is
non-negative with OOS-win-fraction ≥ 60% favoring removal. Given H015's
existing evidence is unusually strong and consistent, this hypothesis is
the closest thing in the registry to a de facto `PASSED` engine, but is
kept at `RESEARCH` rather than promoted directly, because no run to date
isolated price_action's *marginal* contribution specifically (H015
answered "best subset," which is related but not the same question) and
because CLAUDE.md's promotion bar (`edge_gate.py PROMOTION_CRITERIA`) is
process, not opinion — a hypothesis doesn't get to skip its own bar just
because the evidence looks favorable.

## Status
`RESEARCH` — enabled for live (demo) paper-trading and evidence
accumulation, consistent with nnfx (H004) and wyckoff (H006). Superseded a
bare `EXEMPT` bypass that predated the edge gate's PROMOTION_CRITERIA
(2026-07-09).

## Linked experiment
None dedicated yet. Related existing evidence:
`scripts/engine_research.py` (H015).

## Linked result
`research/results/engine_subset_search.json`,
`research/results/engine_subset_search_15sym.json` (H015's artifacts).
