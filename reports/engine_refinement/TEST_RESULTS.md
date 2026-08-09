# IATIS Engine Refinement V1 — Final Test Results (#370)

Captured: 2026-08-09T00:20:52Z
Git state: `research/engine-refinement-v1` @ `9d3aee5` (HEAD after #369,
"event observability pass across all engines" — the last commit before
this document).

This document is the authoritative record that task #370 ("Full test
suite run + TEST_RESULTS.md") requires: a final, full-suite verification
run covering every change made across tasks #354–#369, a before/after
test-count comparison against `BASELINE.md`, and a summary of all 21
recorded changes with accurate production-impact framing.

## 1. Before / after test-count comparison

| | Baseline (`BASELINE.md`, pre-refinement) | Final (this document) | Delta |
|---|---|---|---|
| Tests collected | 2834 | 2953 | +119 |
| Passed | 2826 | 2945 | +119 |
| Skipped | 2 | 2 | 0 |
| Failed | 6 | 6 | 0 (same 6, unchanged identity) |

Full command and result, run fresh on the current HEAD:

```
$ python3 -m pytest -q --collect-only
2953 tests collected in 5.75s

$ python3 -m pytest -q
6 failed, 2945 passed, 2 skipped, 2 warnings in 888.17s (0:14:48)
```

The +119 net new tests come from the dedicated per-engine refinement test
files/additions made across #358–#369 (SMC, Price Action, NNFX, Wyckoff,
ICT, Market Structure, Divergence, Quant, Macro, Sentiment refinements,
the #368 indicator golden-test gap-fill, and the #369 observability-pass
additions) plus the pre-existing suites those same commits extended
in-place. This matches the per-task verification numbers reported in each
individual engine's own delivered report during this pass (e.g. 2938 →
2943 → 2945 as the Sentiment/#368/#369 tasks landed), now confirmed
identical on one final, single run against the complete, merged set of
changes.

## 2. The 6 failures are unchanged, pre-existing, and environment-caused

Every one of the 6 failures below appeared identically on the
pre-refinement baseline (`BASELINE.md` §2) and on every full-suite run
performed during this refinement pass (#358 through #369's own
verification runs). None are caused by any change recorded in
`research/engine_refinement/changes.json`.

```
FAILED tests/test_alpaca_provider.py::test_missing_credentials_fall_through
FAILED tests/test_api_server.py::test_ai_explain_trade_disabled_by_default
FAILED tests/test_api_server.py::test_ai_research_summary_disabled_by_default
FAILED tests/test_api_server.py::test_ai_research_question_disabled_by_default
FAILED tests/test_api_server.py::test_ai_suggest_hypothesis_disabled_by_default
FAILED tests/test_api_server.py::test_ai_suggest_hypothesis_works_with_no_body
```

Root causes, confirmed by the captured tracebacks (unchanged from every
prior run this session):

- `test_alpaca_provider.py::test_missing_credentials_fall_through` — this
  sandbox's outbound proxy returns an HTTP 403 (`ProxyError`) for the
  Alpaca request before the code under test ever gets to raise its own
  `DataFetchError("...ALPACA_API_KEY...")`, so the regex match on the
  expected error message fails. A sandbox network-policy artifact, not a
  code defect.
- The 5 `tests/test_api_server.py` AI-disabled-by-default tests — this
  sandbox's `.env` carries real AI-provider credentials, so these
  `/ai/*` endpoints attempt a genuine call to Gemini instead of
  short-circuiting on "no API key configured." That real call hits
  Gemini's live 429 rate limit, producing `status: "error"` instead of
  the test's expected `status: "disabled"`. Also a sandbox-`.env`
  artifact (real credentials present where the test assumes none), not a
  code defect.

Zero regressions: no test that passed on the pre-refinement baseline now
fails, and no new failure was introduced by any of the 21 recorded
changes.

## 3. Summary of all 21 recorded changes

Source of truth: `research/engine_refinement/changes.json` (21 entries,
schema `{change_id, type, engine, description, strategy_semantics_changed,
performance_driven, commit}`). `performance_driven` is `False` on every
entry — no change in this pass was made to alter measured performance;
every change is a bug fix, causality hardening, semantic correction, or
observability improvement, per the refinement pass's own governing rules.

### 3.1 Live-enabled engines (SMC, Price Action, NNFX, Wyckoff) — real production impact

These 4 engines are `enabled: true` in `config/engines.yaml` (prod4, per
CLAUDE.md's frozen state) and are the ones actually voting in live/paper
decisions today. Changes here have **real production impact** — they are
not cosmetic for the live system, even where `strategy_semantics_changed`
is `False`.

| change_id | type | semantics changed | summary |
|---|---|---|---|
| SMC-REFINE-001 | OBSERVABILITY | No | Swing pivot/confirmation timing (`last_swing_high_bar`, `..._confirmation_bar`, `swing_confirmation_delay`, etc.) now reported in `extract_structural_features()`'s output, including in the `insufficient=True` abstention branch. `decide_structural_bias()` proven (regression test) to ignore the new keys entirely. |
| SMC-REFINE-002 | OBSERVABILITY | No | `detect_bos_choch()` now reports `reference_bar`/`bars_since_reference`/`confirmation_delay` on firing branches; the `event="none"` branch does not fabricate these fields. |
| SMC-REFINE-003 | SEMANTIC_FIX | No | `SMCEngine.analyze()`'s `raw` output reorganized into `structure_state`/`swing_timing`/`structural_events`/`zones` grouped keys, additive only — every pre-existing flat `raw` key preserved byte-for-byte; `bias`/`score`/`reasons` computation untouched. |
| PA-REFINE-001 | OBSERVABILITY | No | `rsi_undefined` flag added so a genuinely-undefined RSI (zero avg gain/loss) is distinguishable from a real ~50 reading. Fallback *value* (50.0) deliberately left unchanged — correcting it would shift `decide()`'s RSI-threshold branches, out of this pass's scope; flagged for a future evidence-driven decision. |
| PA-REFINE-002 | OBSERVABILITY | No | `momentum_bars_used`/`momentum_bars_configured` surfaced so the pre-existing `min(momentum_bars, len(df)-1)` clamp is now visible instead of silent. |
| PA-REFINE-003 | BUG_FIX | No | Removed two dead locals (`c2`, `body1`) in `_candle_pattern()` that were computed but never read by any pattern check. Byte-identical pattern detection confirmed by dedicated regression test. |
| NNFX-REFINE-001 | OBSERVABILITY | No | `adx_undefined`/`rsi_undefined` flags added for the same "silently-defaulted-but-truly-undefined" reason as PA-REFINE-001, applied to NNFX's own ADX/RSI. Fallback values unchanged; threshold-branch correction flagged, not made. |
| NNFX-REFINE-002 | BUG_FIX | No | `_adx()`'s internal ATR now reuses `utils.indicators.atr()` instead of a duplicated `tr.rolling(period).mean()`. Formula-identical, confirmed by dedicated regression test. |
| NNFX-REFINE-003 | BUG_FIX | No | Removed a dead `close` local in `_adx()` (only high/low are actually used). Behavior-identical, confirmed by regression test. |
| WYCKOFF-REFINE-001 | OBSERVABILITY | No | `range_atr_zero` surfaced via a new standalone helper (deliberately not folded into `_identify_trading_range()`'s own 3-tuple return, to avoid silently breaking `wyckoff_engine_v2.py`'s fixed-arity unpacking). Confirms the pre-existing 99-sentinel fallback for a flat/stale instrument was already correct, just silent. |
| WYCKOFF-REFINE-002 | BUG_FIX | No | Removed two dead locals (`close` in `_identify_trading_range()`, `last` in `_detect_spring_upthrust()`), neither part of any return contract. Behavior-identical, confirmed by regression tests. |

**Net production impact of this pass on the live system**: every SMC/
Price Action/NNFX/Wyckoff change is additive-observability or dead-code
removal, each individually proven behavior-identical by a dedicated
regression test plus the unchanged golden-value suite
(`tests/test_engine_config_extraction_no_behavior_change.py`). No
`bias`/`score` computation for any live-enabled engine changed as a
result of this refinement pass. This is a real claim about the live
system, not a "zero impact, nothing to see" dismissal — these 4 engines
now expose materially more diagnostic surface (timing metadata,
undefined-vs-neutral disambiguation, resolved dead-code ambiguity) that
a live operator can use, even though the trading decision itself is
unchanged.

### 3.2 Disabled engines (ICT, Market Structure, Divergence, Quant, Macro, Sentiment) — zero live-trading impact

All 6 are `enabled: false` in `config/engines.yaml` and carry no live
confluence weight in the running system (`quant`'s Sentiment/Macro/etc.
weights exist in `config.yaml` but are never consulted since these
engines are never constructed by `main.py`'s live `build_active_engines`
path when disabled). Changes here, including the 4 genuine
`strategy_semantics_changed: true` fixes, have **zero live-trading
impact** — they only affect what an operator sees if/when these engines
are manually re-enabled or exercised through Mission Center's ad-hoc,
ephemeral research sandbox.

| change_id | type | semantics changed | summary |
|---|---|---|---|
| ICT-REFINE-001 | SEMANTIC_FIX | **Yes** | Removed automatic bias/score assignment from premium/discount zone position and killzone timing (context, not evidence). A real Judas-swing detection is now the sole bias/score source; zone/killzone/H1-trend became confirmation-only modifiers. Golden values recaptured. Operator-pre-approved. |
| ICT-REFINE-002 | BUG_FIX | No | `raw["is_killzone"]` corrected to the same London/NewYork/Overlap-only definition `decide()` already used internally, instead of the broader "any session's first two hours" reading that included Asia. |
| ICT-REFINE-003 | OBSERVABILITY | No | `raw` gains grouped `context`/`event` keys mirroring SMC's structure/event separation, making the context-vs-evidence distinction from ICT-REFINE-001 visible in the engine's own output. |
| MARKET-STRUCTURE-REFINE-001 | SEMANTIC_FIX | **Yes** | `last_event` (BOS/CHoCH/MSS) now requires a real close-beyond-level break, not just a geometric swing-value comparison — matching this engine's own documented design intent versus its actual (looser) prior implementation. `trend` (HH/HL/LH/LL classification) is unchanged. Golden value recaptured. |
| MARKET-STRUCTURE-REFINE-002 | OBSERVABILITY | No | `raw` gains `h1_broke_level`/`h1_break_direction`/`h1_break_price` and H4 equivalents, surfacing the break fact behind REFINE-001's fix even when no event fires. |
| MARKET-STRUCTURE-REFINE-003 | OBSERVABILITY | No | `raw` gains `h4_event`/`h4_event_direction` (H1's own event was already surfaced; H4's was silently discarded despite being computed identically). Proven never-scored by dedicated test. |
| DIVERGENCE-REFINE-001 | SEMANTIC_FIX | **Yes** | Removed fixed automatic score bonuses (triple/MACD/MTF confirmation) from `decide()`'s arithmetic — these now report as observability-only reasons, never modifying `score`. Only the base pattern's own price-move magnitude sets score beyond the flat base. Dead now-unused threshold keys removed from config. Operator-pre-approved. |
| QUANT-REFINE-001 | SEMANTIC_FIX | **Yes** | `_classify_regime()` no longer conflates data-insufficiency (`UNKNOWN`, new) with a genuine no-majority statistical finding (`RANDOM`). Both still abstain identically (`NEUTRAL`/0.0) — only the reported classification/reasoning changed, never the live decision. `versions.quant` 2.0 → 2.1. |
| MACRO-REFINE-001 | OBSERVABILITY | No | `risk_vote_detail` added, reporting each risk-on/off vote's own `as_of` date and cadence (daily/weekly), plus a new reason string stating the vote-independence-assumption caveat. Vote-counting arithmetic proven byte-identical by dedicated invariance test. `versions.macro` 2.0 → 2.1. |
| SENTIMENT-REFINE-001 | CAUSALITY_FIX | **Yes** | A COT snapshot dated *after* the bar's own timestamp is now discarded (defense-in-depth alongside the pre-existing `_bar_time_is_live` gate from BUG-005), rather than silently used. A snapshot with no parseable `report_date` is not discarded (unknown-vintage ≠ future-vintage). `versions.sentiment` 1.0 → 1.1. |

**Net production impact of this pass on the live system**: exactly zero.
None of these 6 engines vote in any live or paper decision today
(`enabled: false`); their `confluence.weights` entries are dead metadata
consulted only if an engine is re-enabled, which CLAUDE.md's frozen-state
rule requires a fresh pre-registered hypothesis to do. The 4 genuine
semantic fixes among them (ICT-001, Market-Structure-001, Divergence-001,
Quant-001) were each explicitly operator-pre-approved as within this
pass's scope precisely *because* they carry zero live risk — they correct
a confirmed context/evidence conflation or a data-insufficiency/no-signal
conflation, findings this session's own audits (`ENGINE_INVENTORY.md`)
identified before any code changed.

## 4. Config-file drift check

SHA256 hashes, compared against `BASELINE.md` §5:

```
8204341bdb41383e321493130571f6b07ebb54d4c00d236337f33ea48725c14e  config.yaml            (UNCHANGED)
5f0617a53a652d4f073c1e30c6e6ccc9b0e023b5fa2a510c27ed8b5b0fe64b8b  config/engines.yaml    (CHANGED — see below)
55a671b5a0341bd90e7f70f6b536f362ecb299f8b52244e4cdda249bb80b9785  config/symbols.yaml    (UNCHANGED)
587eff93026ed2951cce5f511f77bb532ff24ced312aca94c5b92a1333a34e70  config/risk.yaml       (UNCHANGED)
```

`config.yaml` — the file holding `confluence.weights`,
`min_engines_agreeing`, `min_score_to_trade`, `min_informative_weight_share`
— is **byte-identical** to the pre-refinement baseline. No weight, no
threshold, no quorum rule was touched by this refinement pass, live or
otherwise.

`config/engines.yaml` changed only in the ways already described in
`changes.json` and reflected in the table above: `versions:` bumps for
divergence/ict/macro/market_structure/quant/sentiment (baseline `2.0`/
`1.0` → current `2.1`/`1.1`/`1.2` per engine, see §3), plus
`thresholds.divergence`'s now-dead `triple_bonus`/`macd_confirm_bonus`/
`mtf_confirm_bonus`/`mtf_conflict_penalty` keys removed (DIVERGENCE-REFINE-001).
The `enabled:` block itself — the only part of this file with live
production consequence — is unchanged: still exactly `smc, price_action,
nnfx, wyckoff: true`; `ict, market_structure, divergence, quant, macro,
sentiment: false`.

## 5. Per-engine REFINED verdict

Every engine touched in this pass reached a **REFINED** verdict in its
own individually-delivered task report (#358–#369) — no engine was left
BLOCKED. Restated here for a single-document summary:

| Engine | Verdict | Live-enabled |
|---|---|---|
| SMC | REFINED | Yes |
| Price Action | REFINED | Yes |
| NNFX | REFINED | Yes |
| Wyckoff | REFINED | Yes |
| ICT | REFINED | No |
| Market Structure | REFINED | No |
| Divergence | REFINED | No |
| Quant | REFINED | No |
| Macro | REFINED | No |
| Sentiment | REFINED | No |

No unresolved/ambiguous strategy-behavior questions were left unflagged;
the two deliberately-deferred fallback-value corrections (PA-REFINE-001,
NNFX-REFINE-001 — RSI/ADX undefined-value semantics) are explicitly
recorded as flagged-for-operator-decision in their own change
descriptions (§3.1), not silently resolved.

## 6. Conclusion

The full test suite, run once on the final, complete HEAD of this
refinement pass (all 21 changes applied), produces **2945 passed, 2
skipped, 6 failed** out of **2953 collected** — the same 6 pre-existing,
environment-caused failures present on the pre-refinement baseline, with
zero regressions and +119 new tests added across the pass. `config.yaml`
is byte-identical to baseline; `config/engines.yaml`'s live `enabled:`
set is unchanged. Task #370 is complete.
