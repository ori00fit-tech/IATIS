# IATIS Engine Refinement V1 — Final Status

Captured: 2026-08-09. Final commit before freeze: `c162734` (#372).
Freeze tag: `engine-refinement-v1-freeze` (created immediately after this
document is committed — see §7).

## 1. Executive summary

Engine Refinement V1 is **complete**. Across 26 commits on
`research/engine-refinement-v1` (branched from `origin/main` @ `9138bb9`),
every one of the 10 confluence engines was audited and refined one at a
time under a strict, explicitly-bounded mandate: **bug fixes, causality
hardening, semantic corrections, and observability improvements only —
never strategy/threshold/weight/symbol/timeframe optimization, never
engine ranking, never a production config change made for a performance
reason.**

21 code-level changes were made and recorded in
`research/engine_refinement/changes.json`. Every engine reached a
**REFINED** verdict; none was left BLOCKED. The full test suite grew
from 2834 to 2953 collected tests (+119) with **zero regressions**: the
same 6 pre-existing, environment-caused failures appear on both the
pre-refinement baseline and the final HEAD. An independent production
safety audit (#372), backed by direct commands re-run against the final
commit, confirms **zero observable live/paper-trading behavior change**
— `risk/`, `execution/`, `scheduler.py`, `storage/`, and `confluence/`
are byte-identical to `origin/main`, `config.yaml` is byte-identical to
baseline, and `config/engines.yaml`'s `enabled:` block and the 4 live
engines' own threshold blocks are untouched.

## 2. Scope and governing rules (as executed, not just as planned)

1. Only `BUG_FIX`, `CAUSALITY_FIX`, `SEMANTIC_FIX`, `OBSERVABILITY` — held
   throughout; verified in `changes.json` (all 21 entries carry one of
   these 4 types, `performance_driven: False` on every one).
2. One engine at a time, each stopping for operator review before the
   next began — held throughout (10 sequential per-engine tasks, #358–
   #367, each individually reviewed and approved via the operator's own
   "اكمل" signal before the next started).
3. Every change recorded in `changes.json`, never
   `research/results/registry.json` — held; `registry.json` shows an
   empty `git diff` against `origin/main` for this entire branch.
4. Genuinely ambiguous strategy-behavior questions flagged, not silently
   resolved — held; one open item remains (§6 below), carried forward
   deliberately across #367, #370, #371, and restated here.

## 3. Timeline

| Task(s) | Phase | Commits |
|---|---|---|
| #352 | Branch + baseline snapshot | `ac60e71` |
| #353 | Engine inventory (12 engines, 4 confirmed conflicts found) | `26ace8b` |
| #354 | `EngineOutput` base contract hardened (§3) | `de718f8` |
| #355 | Error semantics: crashed vs. honest NEUTRAL (§4) | `bdaa411` |
| #356 | Engine versioning wired (§5) | `8373d4b` |
| #357 | Causality guards wired into backtest hot path (§6) | `a8d222b` |
| #358 | SMC refinement | `0762705`, `784b8f8` |
| #359 | Price Action refinement | `c05cac9`, `91d0cda` |
| #360 | NNFX refinement | `1391bf3`, `79a4397` |
| #361 | Wyckoff refinement | `024cb64`, `8525ba0` |
| #362 | ICT refinement (confirmed conflict fixed) | `314fad2`, `f1a4e44` |
| #363 | Market Structure refinement (confirmed conflict fixed) | `0cf68ca` |
| #364 | Divergence refinement (confirmed conflict fixed) | `8f32af2` |
| #365 | Quant refinement (confirmed conflict fixed) | `3cd7c44` |
| #366 | Macro refinement | `60f819e` |
| #367 | Sentiment refinement (causality fix) | `bf2d49d` |
| #368 | Indicator golden-test gap fill (test-only) | `2a311ae` |
| #369 | Event observability pass across all engines | `9d3aee5` |
| #370 | Full test suite run + `TEST_RESULTS.md` | `aa826ce` |
| #371 | Research governance changelog (`CHANGES.md`) | `e64b6e6` |
| #372 | Production safety audit (`PRODUCTION_CONFIG_AUDIT.md`) | `c162734` |
| #373 | This document + freeze | (this commit) |

26 commits total, `ac60e71`..`c162734` plus this task's own commit.

## 4. Per-engine final verdict

| Engine | Verdict | Live-enabled | Changes | Semantics changed |
|---|---|---|---|---|
| SMC | REFINED | Yes | 3 | No |
| Price Action | REFINED | Yes | 3 | No |
| NNFX | REFINED | Yes | 3 | No |
| Wyckoff | REFINED | Yes | 2 | No |
| ICT | REFINED | No | 3 | Yes (1 of 3) |
| Market Structure | REFINED | No | 3 | Yes (1 of 3) |
| Divergence | REFINED | No | 1 | Yes |
| Quant | REFINED | No | 1 | Yes |
| Macro | REFINED | No | 1 | No |
| Sentiment | REFINED | No | 1 | Yes |

10 of 10 engines REFINED. 0 BLOCKED. 21 total changes (12
OBSERVABILITY, 4 BUG_FIX, 4 SEMANTIC_FIX, 1 CAUSALITY_FIX — see
`CHANGES.md` §4 for the full breakdown).

## 5. Test results (final, from #370)

| | Baseline | Final | Delta |
|---|---|---|---|
| Collected | 2834 | 2953 | +119 |
| Passed | 2826 | 2945 | +119 |
| Skipped | 2 | 2 | 0 |
| Failed | 6 | 6 | 0 (same 6, identical failures) |

The 6 failures are pre-existing, environment-caused (this sandbox's
`.env` carrying real AI-provider credentials that hit a live Gemini
429 rate limit, and this sandbox's outbound proxy returning a 403 for an
Alpaca credentials test) — present identically on the pre-refinement
baseline and unchanged by any of the 21 recorded changes. Full detail in
`TEST_RESULTS.md`.

## 6. Open items carried forward (not resolved by this pass)

**Price Action / NNFX RSI & ADX "undefined vs. neutral" fallback
values** (`PA-REFINE-001`, `NNFX-REFINE-001`): both live-enabled engines
now *disambiguate* a genuinely-undefined RSI/ADX reading from a real
neutral one via a new flag (`rsi_undefined`/`adx_undefined`), but the
fallback *value* itself (50.0 for RSI, 0.0 for ADX) is unchanged.
Correcting it to the mathematically true value would shift `decide()`'s
threshold branching on two live-enabled engines — a real
strategy-semantics change, explicitly out of this pass's scope by rule.
**This is a decision for the operator to make separately**, backed by
its own evidence (e.g., a Mission Center ablation comparing current vs.
corrected fallback behavior), not something this refinement pass
decided on its own authority.

No other ambiguous strategy-behavior question was left unresolved.

## 7. Production impact — final statement

Restated from `PRODUCTION_CONFIG_AUDIT.md`'s verdict, the authoritative
source: **GO for production deploy, zero observable live-trading
behavior change.** `risk/`, `execution/`, `scheduler.py`, `storage/`,
`confluence/` are byte-identical to `origin/main`. `config.yaml` is
byte-identical to the pre-refinement baseline. `config/engines.yaml`'s
`enabled:` block and the 4 live engines' own threshold sub-blocks are
untouched. The 4 live-enabled engines (SMC, Price Action, NNFX, Wyckoff)
are proven, by a fresh golden-value test run against the real production
config, to produce byte-identical `bias`/`score` output. The 6 disabled
engines' changes — including the 4 genuine semantic fixes among them —
are unreachable from the live pipeline by construction
(`enabled: false`), not merely low-risk.

## 8. Document index

| Document | Purpose |
|---|---|
| `reports/engine_refinement/BASELINE.md` | Immutable pre-refinement snapshot |
| `reports/engine_refinement/ENGINE_INVENTORY.md` | Per-engine audit, all `VALIDATION_STATUS = UNKNOWN` |
| `research/engine_refinement/changes.json` | Machine-readable, schema'd 21-entry change log |
| `reports/engine_refinement/CHANGES.md` | Narrative, chronological changelog |
| `reports/engine_refinement/TEST_RESULTS.md` | Final full-suite verification |
| `reports/engine_refinement/PRODUCTION_CONFIG_AUDIT.md` | Independent production-safety re-verification |
| `reports/engine_refinement/FINAL_REFINEMENT_STATUS.md` | This document |

## 9. Freeze

This branch is frozen at this document's commit as the reference point
for Engine Refinement V1. Tag: `engine-refinement-v1-freeze`, applied to
the commit that adds this document. The tag marks a completed, fully
verified, production-safe reference state — not a merge to `main` (that
remains the operator's own decision, made outside this session, per this
project's ops runbook).

**Nothing further is planned on this branch under the Engine Refinement
V1 mandate.** Any follow-on work — including the two items explicitly
carried forward (§6's RSI/ADX fallback-value question, and the deferred
Engine Benchmark page tracked separately as task #374) — is new,
separately-scoped work, not a continuation of this refinement pass.
