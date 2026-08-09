# IATIS Engine Refinement V1 — Research Governance Changelog

Captured: 2026-08-09 (task #371). Covers `research/engine-refinement-v1`
from branch creation through `aa826ce` (#370).

## What this document is, and is not

This is a **human-readable narrative record** of every code change made
during Engine Refinement V1. It is deliberately kept **separate from
`research/results/registry.json`**, which is reserved by CLAUDE.md rule 1
for pre-registered trading hypotheses with falsification criteria written
before any result exists. Nothing in this refinement pass is a
hypothesis — every change here is a bug fix, causality hardening, semantic
correction, or observability improvement, made under an explicit rule
that forbids strategy/threshold/weight/symbol/timeframe optimization and
engine ranking. Writing these entries into `registry.json` would corrupt
that file's schema and its purpose as the single source of truth for
tested trading edges.

The **machine-readable source of truth** for the 21 code-level changes is
`research/engine_refinement/changes.json`, appended to via
`research.engine_refinement.changes.append_change()` as each change was
made. This document is a narrative companion to it — organized
chronologically and by phase, written for a human reviewer rather than a
script — and does not supersede it. Where the two could ever disagree,
`changes.json` is authoritative.

Companion documents, each with a distinct role:

| Document | Role |
|---|---|
| `reports/engine_refinement/BASELINE.md` | Immutable pre-refinement snapshot (git state, test count, config hashes) |
| `reports/engine_refinement/ENGINE_INVENTORY.md` | Per-engine audit: inputs/features/score-logic/thresholds/confirmed conflicts, all `VALIDATION_STATUS = UNKNOWN` |
| `research/engine_refinement/changes.json` | Machine-readable, schema'd change log (21 entries) |
| `reports/engine_refinement/TEST_RESULTS.md` | Final full-suite verification (#370): before/after counts, per-change production-impact table |
| This document | Narrative changelog, chronological, for human review |

## Governing rules for this whole pass

1. Only `BUG_FIX`, `CAUSALITY_FIX`, `SEMANTIC_FIX`, and `OBSERVABILITY`
   changes are in scope — never strategy/threshold/weight/symbol/
   timeframe optimization, never engine ranking, never a production
   config change made for a performance reason.
2. Work proceeds one engine at a time; each engine's work stops for
   operator review before the next begins.
3. Every code change is recorded in `changes.json`, never
   `registry.json`. `performance_driven` is `False` on every one of the
   21 entries — confirmed, not asserted, by the field itself.
4. Any genuinely ambiguous strategy-behavior question found during an
   audit is flagged in the delivering report, not silently resolved.
   Two such flags exist in this pass (§3.1 below: the RSI/ADX
   "undefined vs. neutral" fallback-value question for Price Action and
   NNFX) and remain open for the operator's own future decision.

## 1. Foundation phase — branch, baseline, and base-contract hardening (pre-#358)

Before any engine's own logic was touched, six commits established the
ground this whole pass stands on:

- **Branch + baseline** (`ac60e71`) — `research/engine-refinement-v1`
  created from `origin/main`, confirmed zero-diff to the tip of the
  long-running `claude/iatis-full-audit-350sic` branch it succeeds.
  `BASELINE.md` captured as an immutable reference: 2834 tests collected,
  2826 passed / 2 skipped / 6 failed (the same 6 environment-caused
  failures that persist, unchanged, through every run in this pass — see
  `TEST_RESULTS.md` §2), exact config file hashes, exact `EngineOutput`
  schema.
- **Engine inventory** (`26ace8b`) — every one of the 12 engines (10 base
  + `price_action_v2`/`wyckoff_v2` research variants) documented in
  `ENGINE_INVENTORY.md`: inputs, features, events, context, score logic,
  thresholds, external-data dependencies, timestamp handling,
  assumptions, existing test coverage, and production status. This audit
  is what surfaced all four "CONFIRMED CONFLICT" findings later fixed in
  §3.1 below (ICT, Divergence, Quant, Macro) — every fix in this pass
  traces back to a finding written down here, before any code changed.
  Every engine's `VALIDATION_STATUS` is `UNKNOWN` — this pass ranks
  nothing.
- **`EngineOutput` base contract hardened** (`de718f8`) — five additive
  fields (`score_type`, `causal_timestamp`, `data_quality`, `error_type`,
  `error_message`) so a research consumer can tell what kind of number
  `score` is, which bar a decision was computed against, what data was
  actually used, and why a crash happened, without parsing free-text
  `reasons` strings. All defaults preserve exact current behavior.
- **Crashed engines distinguished from honest NEUTRAL votes** (`bdaa411`)
  — `BacktestResult` gained `crashed_engine_bars`/`crashed_engine_totals`,
  tracked observationally. A crashed engine still votes NEUTRAL/0 exactly
  as before (zero change to gating/voting) — this only makes that fact
  countable and loggable after the fact, closing the gap
  `BASELINE.md` §6 flagged: `EngineOutput.crashed` was set but never read
  anywhere downstream.
- **Engine versioning wired onto `EngineOutput`** (`8373d4b`) —
  `EngineOutput.engine_version`/`BaseEngine.version` added, filled from
  `config/engines.yaml`'s pre-existing `versions:` block (previously
  documented as "metadata for humans only," now actually reaches the
  output object) at both the live (`main.py`) and backtest
  (`backtesting/backtest_engine.py`) construction sites, with the same
  gate/vote-parity discipline `thresholds` already used. Purely
  observational — never consulted by any scoring logic.
- **Causality hardening wired into the hot path** (`a8d222b`) —
  `research/guards/causal_guard.py` and `static_scan.py` existed on disk
  but had zero references from `backtest_engine.py`, per `BASELINE.md`
  §9. `run_backtest()` now asserts strictly-increasing input timestamps
  before its main loop (a real, cheap precondition check — every real
  caller already sorts its data, so this only ever fires on genuinely
  broken upstream input). A new 6-test file formally proves no engine's
  `mtf_data` ever contains a bar timestamped after the decision
  timeframe's own current bar, across a real run, and pins
  `static_scan.py`'s zero-HIGH-severity baseline across the whole
  backtest hot path as a permanent regression guard.

## 2. Per-engine refinement phase (#358–#367)

Each engine below was worked one at a time, in the order shown, each
stopping for review before the next began. Full technical descriptions
are in `changes.json`; this section is the narrative summary.

### 2.1 SMC (`0762705`, `784b8f8` — live-enabled)

Three changes, all additive/observability-shaped, zero strategy change:
swing pivot/confirmation timing surfaced (`SMC-REFINE-001`), BOS/CHoCH
reference-bar staleness surfaced (`SMC-REFINE-002`), and the engine's raw
output reorganized into grouped `structure_state`/`swing_timing`/
`structural_events`/`zones` keys while preserving every pre-existing flat
key byte-for-byte (`SMC-REFINE-003`). `decide_structural_bias()` proven
by regression test to ignore every new key.

### 2.2 Price Action (`c05cac9`, `91d0cda` — live-enabled)

Two observability additions (`rsi_undefined` flag distinguishing a
genuinely-undefined RSI from a real ~50 reading; `momentum_bars_used`/
`momentum_bars_configured` surfacing the pre-existing history-length
clamp) plus one dead-code removal (`PA-REFINE-003`: two unread locals in
`_candle_pattern()`). **Flagged, not fixed**: correcting the RSI
undefined-fallback value itself would shift `decide()`'s threshold
branching — a strategy-semantics change explicitly out of this pass's
scope, left for the operator's own evidence-driven decision.

### 2.3 NNFX (`1391bf3`, `79a4397` — live-enabled)

The same "undefined vs. neutral" observability pattern applied to ADX and
RSI (`NNFX-REFINE-001`, same flagged-not-fixed caveat as Price Action),
plus two bug fixes: `_adx()`'s internal ATR deduplicated to reuse
`utils.indicators.atr()` instead of a locally duplicated rolling mean
(`NNFX-REFINE-002`, formula-identity confirmed by regression test), and a
dead `close` local removed from `_adx()` (`NNFX-REFINE-003`).

### 2.4 Wyckoff (`024cb64`, `8525ba0` — live-enabled)

`range_atr_zero` surfaced via a new standalone helper, deliberately kept
out of `_identify_trading_range()`'s own return tuple since
`wyckoff_engine_v2.py` (an untouched research variant) unpacks that
function as a fixed 3-tuple — changing its arity would have silently
broken v2 (`WYCKOFF-REFINE-001`). Two dead locals removed
(`WYCKOFF-REFINE-002`), neither part of any return contract, confirmed
safe for v2's dependency on the same functions.

### 2.5 ICT (`314fad2`, `f1a4e44` — disabled)

The pass's first genuine `strategy_semantics_changed: true` fix
(`ICT-REFINE-001`), operator-pre-approved: `decide()` previously let
premium/discount zone position and killzone session timing auto-set
bias/score directly — conflating statistical price context with actual
trading-event evidence. Fixed so the only real event this engine detects
(a Judas swing) is the sole bias/score source; zone/killzone/H1-trend
became confirmation-only modifiers. Golden values recaptured
(`tests/test_engine_config_extraction_no_behavior_change.py`). A related
bug fix corrected `raw["is_killzone"]`'s definition to match what
`decide()` actually used (`ICT-REFINE-002`), and an observability pass
grouped the context/event distinction into the raw output explicitly
(`ICT-REFINE-003`). Because `ict` is `enabled: false`, this real semantic
fix carries zero live-trading impact.

### 2.6 Market Structure (`0cf68ca` — disabled)

A second genuine semantic fix (`MARKET-STRUCTURE-REFINE-001`,
`strategy_semantics_changed: true`): `last_event` (BOS/CHoCH/MSS) was
assigned from a pure geometric comparison of swing-pivot values, with no
requirement that price ever actually broke a level — directly
contradicting this engine's own header docstring, which claims its whole
advantage over plain SMC is detecting real structural *events*, not
counting swing pairs. Fixed to require a real close-beyond-level break,
mirroring `smc_engine.detect_bos_choch`'s own established convention.
`trend` (the HH/HL/LH/LL classification) is unchanged — it's a legitimate
geometric fact independent of any break. Golden value recaptured. Zero
live-trading impact (`enabled: false`).

### 2.7 Divergence (`8f32af2` — disabled)

Third semantic fix (`DIVERGENCE-REFINE-001`, operator-pre-approved):
removed fixed automatic score bonuses for triple-swing/MACD/MTF
confirmation from `decide()`'s own arithmetic — the same underlying facts
now report as observability-only reasons ("context only, not scored").
Only the base pattern's own price-move magnitude drives score beyond the
flat base. Mirrors the "informational only, provably never scored"
convention already established elsewhere in this codebase (Macro's
commodity trends, Quant's volatility clustering) and pinned by the same
kind of dedicated invariance test. Now-dead threshold keys removed from
config. Zero live-trading impact.

### 2.8 Quant (`3cd7c44` — disabled)

Fourth semantic fix (`QUANT-REFINE-001`): `_classify_regime()` previously
returned `RANDOM` both for genuine data-insufficiency (too few
diagnostics could vote) and for a real statistical no-majority finding —
two structurally different situations reported identically. Fixed: case
(a) is now `UNKNOWN`, a distinct classification with its own reason
string. Both states still abstain identically (`NEUTRAL`/0.0) — only the
reported classification/reasoning changed, never the live decision.
`versions.quant` bumped 2.0 → 2.1. Zero live-trading impact.

### 2.9 Macro (`60f819e` — disabled)

Pure observability fix (`MACRO-REFINE-001`): the 6 risk-on/off votes were
summed as if independent and simultaneous despite updating on genuinely
different real-world cadences (daily vs. weekly), with no per-vote
timestamp anywhere. `extract_features()` gained `risk_vote_detail`
(each vote's own `as_of` date and cadence); `decide()` gained one new
caveat reason string. Vote-counting arithmetic proven byte-identical by a
dedicated invariance test — this is explicitly *not* one of the four
`strategy_semantics_changed: true` fixes. `versions.macro` 2.0 → 2.1.
Already carries zero live-trading impact twice over: `enabled: false`
*and* its `confluence.weights.macro` is already forced to 0.0.

### 2.10 Sentiment (`bf2d49d` — disabled)

The pass's only `CAUSALITY_FIX`-typed change (`SENTIMENT-REFINE-001`):
zero references to `report_date`/`publication_date`/`available_at`
existed anywhere in `sentiment_engine.py`, even though the COT cache has
always written a real `report_date`. BUG-005's bar-time gate (from an
earlier, separate audit this session) already prevented most lookahead,
but never checked the loaded snapshot's own vintage against the bar being
evaluated. Fixed: a COT snapshot dated after the bar's own timestamp is
now discarded as defense-in-depth. A snapshot with unparseable/missing
`report_date` (unknown vintage, not future vintage) is *not* discarded —
matching this pass's "no data → no opinion changed" convention, verified
by 3 dedicated tests. `versions.sentiment` 1.0 → 1.1. Zero live-trading
impact.

## 3. Wrap-up phase (#368–#370)

- **Indicator golden-test gap fill** (`2a311ae`, #368) — a systematic
  cross-check of every indicator function against its dedicated test
  file found two genuine zero-coverage gaps: NNFX's `_adx()` had no
  golden-value test pinning its exact formula, and SMC's
  `_count_swing_direction()` had never been tested directly, only
  indirectly through higher-level functions. Both closed with hand-
  computed golden-value tests. Test-only — no engine file touched, so no
  `changes.json` entry was recorded for this task (a deliberate,
  disclosed scoping decision, not an omission).
- **Event observability pass across all engines** (`9d3aee5`, #369) — a
  systematic `raw`-dict audit across every remaining engine found one
  genuine gap: Market Structure's `_classify_structure()` computed
  `last_event`/`last_event_bias` identically for H1 and H4, but only
  H1's ever reached `raw` — H4's was silently discarded even though
  `decide()` never reads either (`MARKET-STRUCTURE-REFINE-003`). Fixed,
  proven never-scored by a dedicated test varying H4's event across
  none/BOS/CHoCH/MSS. `versions.market_structure` 1.1 → 1.2. One
  candidate gap (an SMC full-spec BOS/CHoCH-style event field) was
  investigated and confirmed *not* a real gap — the base (non-full-spec)
  path structurally has no separate discrete-event concept to expose.
- **Full test suite run + `TEST_RESULTS.md`** (`aa826ce`, #370) — the
  authoritative final verification: 2953 tests collected (baseline 2834,
  +119), 2945 passed / 2 skipped / 6 failed on the complete, merged set
  of all 21 changes — the same 6 pre-existing, environment-caused
  failures present on the baseline, zero regressions. Full detail,
  including the config-file drift check and the per-engine REFINED
  verdict table, is in `TEST_RESULTS.md`.

## 4. Summary statistics

By type (21 total):

| Type | Count |
|---|---|
| OBSERVABILITY | 12 |
| BUG_FIX | 4 |
| SEMANTIC_FIX | 4 |
| CAUSALITY_FIX | 1 |

By engine:

| Engine | Changes | Live-enabled | `strategy_semantics_changed` |
|---|---|---|---|
| SMC | 3 | Yes | 0 |
| Price Action | 3 | Yes | 0 |
| NNFX | 3 | Yes | 0 |
| Wyckoff | 2 | Yes | 0 |
| ICT | 3 | No | 1 |
| Market Structure | 3 | No | 1 |
| Divergence | 1 | No | 1 |
| Quant | 1 | No | 1 |
| Macro | 1 | No | 0 |
| Sentiment | 1 | No | 1 |

`performance_driven`: `False` on all 21 entries, without exception — no
change in this pass was motivated by, or measured against, trading
performance. Four changes (ICT, Market Structure, Divergence, Quant) are
genuine `strategy_semantics_changed: true` fixes; all four are on
`enabled: false` engines, so none carry any live-trading consequence
today, and all four were operator-pre-approved or directly traced to a
"CONFIRMED CONFLICT" finding recorded in `ENGINE_INVENTORY.md` before any
code changed.

## 5. Open items carried forward (not silently resolved)

- **Price Action / NNFX RSI & ADX "undefined vs. neutral" fallback
  values** (`PA-REFINE-001`, `NNFX-REFINE-001`): both engines now
  *disambiguate* a genuinely-undefined RSI/ADX reading from a real
  neutral one via a new flag, but the fallback *value* itself (50.0 for
  RSI, 0.0 for ADX) is unchanged. Correcting it to the mathematically
  true value (100/0 for RSI; 0 is already correct for ADX) would shift
  `decide()`'s threshold branching on two live-enabled engines — a real
  strategy-semantics change, explicitly out of this pass's scope. Left
  for the operator's own evidence-driven decision, not decided here.

No other ambiguous strategy-behavior question was left unresolved during
this pass.
