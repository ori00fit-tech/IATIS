# IATIS Engine Refinement V1 — Engine Inventory

No engine is ranked here. Every engine's `VALIDATION_STATUS = UNKNOWN`.
This document is a factual inventory only, captured against the
`BASELINE.md` snapshot (`research/engine-refinement-v1` @ `ac60e71`).

Per-engine structure: **Inputs / Features & Events / Context / Score
logic / Thresholds / External data / Timestamps / Known assumptions /
Tests / Production status**.

---

## SMC (`engines/smc_engine.py`, 413 lines)

- **Inputs**: `mtf_data[decision_tf]` OHLC only.
- **Features/Events**: `find_swing_points` (rolling-window swing highs/
  lows) → `extract_structural_features`/`decide_structural_bias` (HH/HL
  vs LH/LL vote count → bullish/bearish/mixed structural bias). Separate,
  gated-off-by-default full-spec path: `detect_fair_value_gaps`,
  `detect_order_blocks`, `detect_bos_choch`, `extract_full_spec_features`
  — all behind `config/engines.yaml`'s `smc_full_spec: false` (H017
  FAILED, frozen off per CLAUDE.md).
- **Context**: none distinct from structural bias itself.
- **Score logic**: swing-direction vote count → base score, full-spec
  components (when enabled) modulate as internal score inputs, never
  entries (per H001/H002/H008c "no standalone entry edge" finding baked
  into the module docstring).
- **Thresholds**: `config/engines.yaml` `thresholds.smc` (swing window,
  base_score, mixed_score, neutral_floor, etc.).
- **External data**: none.
- **Timestamps**: causal by construction — swing/full-spec detectors
  documented as "a pattern at bar i is only usable once every ... " (full
  causal claim not yet independently verified by an automated test; see
  `CAUSAL_AUDIT.md`).
- **Known assumptions**: BOS/CHoCH detection (`detect_bos_choch`) exists
  as a full-spec-only, disabled-by-default function — the live structural
  bias path does NOT currently distinguish `structure_state` from a
  distinct `event` the way the refinement plan's §7 wants; HH/HL/LH/LL
  votes ARE the bias today, not a separate event layer.
- **Tests**: `tests/test_h101_smc_structural_bias_ab.py`,
  `tests/test_smc_fullspec.py`, plus golden-value pins in
  `tests/test_engine_config_extraction_no_behavior_change.py`. No
  dedicated `test_smc_engine.py` for the base structural-bias path.
- **Production status**: **LIVE** (prod4), `enabled.smc: true`,
  weight 0.202.

## Price Action v1 (`engines/price_action_engine.py`, 280 lines)

- **Inputs**: `mtf_data[decision_tf]` OHLC.
- **Features/Events**: `_candle_pattern` (bullish/bearish engulfing,
  hammer, etc.), RSI (SMA-smoothed, `utils.indicators.rsi`), Bollinger
  Bands, `detect_breakout`. `extract_features()`/`decide()` split exists
  (Confluence Overhaul Phase 2).
- **Context**: BB position, RSI level — currently folded directly into
  score contributions inside `decide()`, not exposed as separate context
  fields on `EngineOutput.features`/`raw` distinct from the score
  rationale.
- **Score logic**: independent additive contributions from candle
  pattern + RSI + BB + breakout — the refinement plan's §8 concern
  ("upper BB = bullish" assumed independently) applies directly here;
  needs the audit, not necessarily a rewrite.
- **Thresholds**: `config/engines.yaml` `thresholds.price_action`.
- **External data**: none.
- **Known assumptions**: RSI/BB/momentum/pattern treated as
  quasi-independent evidence sources with additive scores — flagged by
  §8 as needing verification, not automatic removal.
- **Tests**: `tests/test_h102_price_action_confluence_ab.py` + golden
  pins in the shared config-extraction/feature-split files. No dedicated
  `test_price_action_engine.py`.
- **Production status**: **LIVE** (prod4), `enabled.price_action: true`,
  weight 0.1869.

## Price Action v2 (`engines/price_action_engine_v2.py`, 397 lines)

- **Inputs**: OHLC only — explicitly **no RSI, no Bollinger Bands**
  (absence pinned by a source-scan test).
- **Features/Events**: Inside Bar, Outside Bar, NR4/NR7, Fakey, Three Bar
  Play, Micro Trend, Compression/VCP, Failed Breakout, Opening Drive,
  Closing Strength — 11 pure bar-shape/structure detectors, each its own
  function.
- **Context**: compression/volatility-contraction score used as a
  "setup" modifier, not standalone evidence.
- **Score logic**: additive across the 11 detectors + a compression
  bonus.
- **Thresholds**: `config/engines.yaml` `thresholds.price_action_v2`.
- **Tests**: `tests/test_price_action_engine_v2.py` (31 tests).
- **Production status**: **NOT LIVE** — ad-hoc only, reachable via
  Mission Center's `engine_variants` override. Never in
  `config/engines.yaml`'s `enabled` block.

## NNFX (`engines/nnfx_engine.py`, 190 lines)

- **Inputs**: OHLC.
- **Features/Events**: EMA200 trend, ADX(14) trend strength, RSI(14).
  `extract_features()`/`decide()` split exists.
- **Context**: none distinct from the trend/strength/momentum stack
  itself.
- **Score logic**: EMA200 alignment + ADX strength + RSI-vs-50 additive.
  Per §9, EMA50/100/200 is currently effectively a single EMA200 check,
  not a full 3-EMA trend-stack feature (`BULL_ALIGNED`/etc.) — smaller
  gap than the plan assumed.
- **Thresholds**: `config/engines.yaml` `thresholds.nnfx`.
- **External data**: none.
- **Known assumptions**: ADX implementation has never been checked
  against an independent, deterministic golden vector — real gap.
- **Tests**: only shared golden/feature-split files (`test_engine_
  config_extraction_no_behavior_change.py`, `test_engine_feature_
  decision_split.py`, `test_phase3_engines.py`). No dedicated
  `test_nnfx_engine.py`, no ADX golden test.
- **Production status**: **LIVE** (prod4), `enabled.nnfx: true`, weight
  0.2273 (largest live weight).

## Wyckoff v1 (`engines/wyckoff_engine.py`, 324 lines)

- **Inputs**: OHLC (+ volume where available, metals/indices/crypto
  only).
- **Features/Events**: `_identify_trading_range`, `_detect_spring_
  upthrust`, `_effort_vs_result` (computed but confirmed **not consumed**
  by v1's own `decide()` — dead diagnostic, exactly as the refinement
  plan's §10 already suspected), `_volume_analysis`.
  `extract_features()`/`decide()` split exists.
- **Context**: range position (low/mid/high of trading range).
- **Score logic**: spring/upthrust detection + range-position + stopping
  volume/climax + no-demand/no-supply, additive.
- **Thresholds**: `config/engines.yaml` `thresholds.wyckoff`.
- **External data**: volume, where the provider chain supplies it.
- **Known assumptions**: `_effort_vs_result`'s result is computed every
  call and thrown away — confirmed dead code, real gap for §10.
- **Tests**: `tests/test_h023_wyckoff_volume_gating.py` + shared golden/
  feature-split files. No dedicated `test_wyckoff_engine.py`.
- **Production status**: **LIVE** (prod4), `enabled.wyckoff: true`,
  weight 0.0707.

## Wyckoff v2 (`engines/wyckoff_engine_v2.py`, 445 lines)

- **Inputs**: OHLC, reuses v1's `_identify_trading_range`/`_detect_
  spring_upthrust`/`_volume_analysis` directly (imported, not
  duplicated).
- **Features/Events**: full Phase A→E schematic — `_find_climax` (SC/
  BC), `_find_automatic_reaction`, `_find_secondary_test`, `_find_sos_
  lps`, `_find_sow_lpsy`, `_composite_operator_footprint` (finally
  consumes v1's `_effort_vs_result`), `_detect_phase`.
- **Score logic**: v1's `decide()` result as a base layer, then phase-
  event bonuses layered on top (can upgrade a v1-NEUTRAL result when
  SOS+LPS/SOW+LPSY fire without a spring/upthrust).
- **Thresholds**: `config/engines.yaml` `thresholds.wyckoff_v2`
  (superset of v1's 14 keys + new phase-machine keys).
- **Tests**: `tests/test_wyckoff_engine_v2.py` (20 tests, includes a
  pre-climax-range regression pin for a real bug found during
  construction).
- **Production status**: **NOT LIVE** — ad-hoc only via `engine_
  variants`.

## ICT (`engines/ict_engine.py`, 291 lines)

- **Inputs**: `mtf_data` (H1 for session/killzone timing + decision TF).
- **Features/Events**: `_dealing_range`, `_premium_discount_zone`,
  `_detect_judas_swing`, session/killzone context via
  `regimes.session_context`. `extract_features()`/`decide()` split
  exists.
- **Context**: `zone` (premium/discount), `zone_pct`, `is_killzone`,
  `primary_session`.
- **Score logic — CONFIRMED CONFLICT with refinement plan §11**: `decide()`
  (lines ~158-176) directly converts `discount`→bullish-candidate /
  `premium`→bearish-candidate (gated by trend context, not automatic in
  ALL cases, but still zone→bias, not zone-as-context). `killzone_score`
  (default 20.0) is **added directly to score** when `is_killzone` is
  true (line ~193-195) — a direct automatic bonus, not metadata.
- **Thresholds**: `config/engines.yaml` `thresholds.ict`.
- **External data**: none beyond OHLC + session-clock context.
- **Timestamps**: Judas Swing detection currently has no explicit
  `timezone`/`pre_session_interval`/`confirmation_timestamp` fields on
  its output — only implicit via `regimes.session_context`.
- **Tests**: `tests/test_phase3_engines.py` (ICT section). No dedicated
  `test_ict_engine.py`.
- **Production status**: **NOT LIVE**, `enabled.ict: false`,
  weight 0.0657 (dead weight — never reaches vote tallying while
  disabled).

## Market Structure (`engines/market_structure_engine.py`, 335 lines)

- **Inputs**: `mtf_data["H1"]`+`mtf_data["H4"]` (dual-timeframe).
- **Features/Events**: `_swing_points` (reused from `utils.indicators.
  find_swings`, unified with SMC in Confluence Overhaul Phase 1),
  `_classify_structure` → real `BOS`/`CHoCH`/`MSS`/`NONE` event labels
  with a `trend`/`strength`/`last_event`/`last_event_bias` shape (NOT a
  naive HH/HL/LH/LL≡BOS/CHoCH equivalence — `_classify_structure` is
  already a distinct function from raw swing labeling). `extract_
  features()`/`decide()` split exists.
- **Context**: H1 vs H4 trend agreement/disagreement.
- **Score logic**: H1+H4 agreement → high-confidence continuation;
  H1 CHoCH/MSS against H4 trend → lower-confidence early-reversal;
  H1-only structure → lowest confidence. Read directly from `decide()`
  — genuinely tiered by event type, not a flat additive score.
- **Thresholds**: `config/engines.yaml` `thresholds.market_structure`.
- **Known assumptions**: `_classify_structure`'s own BOS-must-break-a-
  prior-level guarantee has not yet been independently re-verified by a
  dedicated causal/structural test in this refinement pass — flagged for
  the per-engine task, likely mostly-compliant already based on the
  function separation, needs final confirmation not a rewrite.
- **Tests**: only shared golden/feature-split files. No dedicated
  `test_market_structure_engine.py`.
- **Production status**: **NOT LIVE**, `enabled.market_structure: false`,
  weight 0.0859.

## Divergence (`engines/divergence_engine.py`, 332 lines, v2 rebuild)

- **Inputs**: `mtf_data[decision_tf]` + one coarser MTF frame for
  confirmation.
- **Features/Events**: `utils.indicators.zigzag_pivots` (magnitude+
  spacing-filtered, not naive local extrema), RSI (Wilder-smoothed) and
  MACD histogram divergence detection via `_detect_pattern`, `_check_
  triple` (3rd-swing confirmation), `_mtf_regular_direction`.
- **Context**: RSI overbought/oversold context, MTF timeframe checked.
- **Score logic — CONFIRMED CONFLICT with refinement plan §13**:
  `decide()` applies fixed automatic bonuses — `triple_bonus` (default
  15.0) when a 3rd swing confirms, `macd_confirm_bonus` (15.0) when MACD
  agrees, `mtf_confirm_bonus` (10.0)/`mtf_conflict_penalty` (5.0) for
  cross-timeframe agreement/disagreement — all directly added/subtracted
  from `score`, not exposed as independent unscored features the way
  §13 requires.
- **Thresholds**: `config/engines.yaml` `thresholds.divergence`.
- **Tests**: `tests/test_divergence_engine_v2.py` (35), `tests/test_
  indicators_divergence.py` (11).
- **Production status**: **NOT LIVE**, `enabled.divergence: false`,
  weight 0.0606. H010 stays `research/results/registry.json` status
  RESEARCH.

## Quant (`engines/quant_engine.py`, 468 lines, v2 rebuild)

- **Inputs**: `mtf_data[decision_tf]`, symbol name (for bars-per-year/
  24-7-asset classification).
- **Features/Events**: Hurst exponent, variance ratio, ADF stationarity,
  return autocorrelation, efficiency ratio, half-life, entropy — each
  computed independently in `extract_features()`, each casting a vote
  (or abstaining) in `_classify_regime()`.
- **Context**: `regime` (TRENDING/MEAN_REVERTING/RANDOM),
  `regime_confidence`, ATR-percentile volatility bucket.
- **Score logic — CONFIRMED CONFLICT with refinement plan §14**:
  `_classify_regime()` (lines 217-227): `min_votes = t.get("regime_min_
  votes", 2); if total_votes_cast < min_votes: return "RANDOM", votes,
  0.0` — insufficient evidence (too few diagnostics able to vote, e.g.
  short history) is currently indistinguishable from a genuine "this
  market really is statistically random" classification. §14 requires a
  distinct `UNKNOWN` state.
- **Thresholds**: `config/engines.yaml` `thresholds.quant`.
- **External data**: none (pure price-series statistics).
- **Tests**: `tests/test_quant_engine_v2.py` (20), `tests/test_
  indicators_quant_stats.py` (25).
- **Production status**: **NOT LIVE**, `enabled.quant: false`,
  weight 0.0707.

## Macro (`engines/macro_engine.py`, 356 lines, v2 rebuild)

- **Inputs**: `core.alt_data_loader.load_macro_snapshot()` — DXY, SPY,
  VIX, GLD, US10Y, US02Y, OIL_WTI, COPPER, NATGAS, CREDIT_SPREAD,
  FED_BALANCE_SHEET (11-series snapshot, D1-cadence). `mtf_data` itself
  is confirmed genuinely unused (proven by a dedicated test).
- **Features/Events**: DXY EMA10/EMA20 trend, SPY-vs-MA20, VIX bucket,
  Gold-vs-SPY divergence, yield-curve inversion, credit-spread widening/
  narrowing, Fed-balance-sheet expansion/contraction, commodity trend
  labels (informational only, provably never scored — pinned by a
  dedicated test).
- **Context**: `yield_curve` (spread, inverted bool), `data_loaded`/
  `load_errors` transparency fields.
- **Score logic — CONFIRMED CONFLICT with refinement plan §15**: 6
  risk-on/off votes (SPY-vs-MA20, VIX bucket, Gold-vs-SPY, yield-curve
  inversion, credit-spread direction, balance-sheet direction) are
  summed as if independent, then combined with a separately-weighted DXY
  bias — no per-observation `observation_timestamp`/`publication_
  timestamp`/`available_at` distinction exists anywhere in this engine.
- **External data**: FRED (primary) / CBOE (VIX only) via
  `core/alt_data_loader.py`, D1-cadence snapshots — confirmed no
  vintage/revision tracking exists for any series (Provider Benchmark
  Phase 3's own research finding, reconfirmed here).
- **Tests**: `tests/test_macro_engine.py` (36).
- **Production status**: **NOT LIVE**, `enabled.macro: false`,
  weight 0.0 (forced to zero even if enabled — frozen per CLAUDE.md).

## Sentiment (`engines/sentiment_engine.py`, 369 lines)

- **Inputs**: `_load_cot_data` (CFTC COT, weekly), `_retail_sentiment_
  proxy` (price-position-derived), `_marketaux_sentiment_signal`
  (MarketAux news sentiment, H021 pre-registered — not yet enabled on
  the VPS per prior session notes).
- **Features/Events**: COT net-long trend, retail-proxy contrarian
  signal, MarketAux sentiment score.
- **Context**: `_bar_time_is_live` (BUG-005 fix — bar-time gate already
  wired, confirmed earlier this session).
- **Score logic — CONFIRMED GAP for refinement plan §16**: zero hits for
  `report_date`/`publication_date`/`available_at` anywhere in this file
  or `fundamentals/*.py`. No mechanism exists to prevent a backtest bar
  from reading a COT snapshot that was published AFTER that bar's
  timestamp — the "historical sentiment must use historical snapshots"
  requirement is entirely unimplemented. The retail proxy's real name
  (`_retail_sentiment_proxy`) is already reasonably honest but is not yet
  literally `price_position_contrarian_proxy` as §16 suggests.
- **Thresholds**: `config/engines.yaml` `thresholds.sentiment` (implicit,
  not yet audited in this pass).
- **External data**: CFTC COT (weekly), MarketAux (H021, not yet live).
- **Tests**: `tests/test_sentiment_engine.py`, `tests/test_collect_
  marketaux_sentiment.py`.
- **Production status**: **NOT LIVE**, `enabled.sentiment: false`,
  weight 0.0303. H012 status RESEARCH.

---

## Cross-cutting infrastructure

- **`engines/base_engine.py`**: see `BASELINE.md` §6 for the current
  `EngineOutput` schema and the confirmed dead `crashed` field.
- **`confluence/`**: `voting_system.py` (`tally_votes`, reads only
  `bias`/`score`), `score_calculator.py` (`_engine_key` weight-lookup,
  includes the `PriceActionV2`→`price_action`/`WyckoffV2`→`wyckoff`
  mapping fix from the earlier engine-variants phase), `regime_
  weights.py`, `reversal_veto.py`, `mtf_confirmation.py`,
  `indicator_filters.py`, `context_filters.py`.
- **`backtesting/backtest_engine.py`**: single `run_backtest()` entry
  point, `build_engine_config_override()` merge function, `ENGINE_KEYS`/
  `ENGINE_VARIANT_KEYS`/`_ENGINE_VARIANT_CLASS_MAP` registries.
- **`research/guards/causal_guard.py` + `static_scan.py`**: exist,
  confirmed real (not stubs), confirmed **unwired** from
  `backtest_engine.py` — see `BASELINE.md` §9.
- **`research/results/registry.json`**: hypothesis-ID-keyed schema
  (H001...), reserved for statistically-tested trading hypotheses per
  CLAUDE.md rule 1 — this refinement pass writes to a separate
  `CHANGES.md` instead (see task/§21 rationale).

## Summary table

| Engine | Live? | Weight | Dedicated tests | Confirmed conflict this pass |
|---|---|---|---|---|
| SMC | ✅ | 0.202 | partial | structure_state/event not fully separated |
| Price Action v1 | ✅ | 0.1869 | partial | RSI/BB independence unverified |
| Price Action v2 | ad-hoc | — | ✅ | none found |
| NNFX | ✅ | 0.2273 | none | ADX ungolden-tested |
| Wyckoff v1 | ✅ | 0.0707 | partial | effort/result dead code |
| Wyckoff v2 | ad-hoc | — | ✅ | none found |
| ICT | off | 0.0657 | partial | **zone→bias, killzone→bonus** |
| Market Structure | off | 0.0859 | none | needs final BOS-level-break audit |
| Divergence | off | 0.0606 | ✅ | **automatic bonus assignment** |
| Quant | off | 0.0707 | ✅ | **RANDOM/UNKNOWN conflation** |
| Macro | off | 0.0 (forced) | ✅ | **vote-independence assumption, no available_at** |
| Sentiment | off | 0.0303 | partial | **no COT vintage/availability handling** |

VALIDATION_STATUS for all 12: **UNKNOWN**.
