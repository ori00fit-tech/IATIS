# IATIS Philosophy Validation Audit — July 2026

**Committee:** Senior Quantitative Researcher · Institutional Portfolio Manager · Trading Systems Architect · Data Scientist · Bayesian Statistician · Software Systems Auditor.

**Scope:** the *decision philosophy* of IATIS — not code quality (covered by `docs/PRODUCTION_AUDIT_2026-07.md`). Every claim below is grounded in artifacts committed to this repository: `research/results/*.json` manifests, the hypothesis registry, config history, and the confluence/risk source code. Where the repository contains no evidence, the finding says **NO EVIDENCE** — it is never treated as support.

**Method note:** raw price CSVs and per-trade logs are gitignored, so this audit works from the git-tracked result manifests (each carries commit hash + dataset SHA256). The two most information-dense artifacts are:

- `ablation_20260703.json` — 10-symbol H1 leave-one-out ablation + engine vote-independence matrix (1,511 decision samples/symbol).
- `h4_yearly_stability_20260705_manifest.json` — the frozen production config over 6.4 years × 15 symbols, 4,240 closed simulated trades, bucketed by year.

All significance calculations in this document were recomputed by the committee from those artifacts, not taken from prose claims.

---

## 0. Answer to the Primary Question

> **"Is the philosophy of IATIS capable of producing a sustainable statistical edge in real markets?"**

**The philosophy as stated — "many engines × confluence × many gates = edge" — is NOT what produces the measured edge, and several of its central assumptions are contradicted by the system's own experiments.**

What actually survives the evidence is much smaller and simpler:

1. **A modest trend-following edge on persistently trending assets** (XAUUSD, BTCUSD, ETHUSD) at H4 with D1 confirmation. In-sample over 6.4y: WR 42.9% at RR ≈ 2.5, z = +8.6 vs breakeven, expectancy ≈ +0.44R/trade, PF 1.21–1.52 **after real measured broker spreads**. This is real evidence — but it is in-sample relative to system development and regime-exposed (2020–2026 was a secular bull in both gold and crypto, though PF 1.55 in the 2022 crypto bear is a genuine robustness point in its favor).
2. **Structural risk discipline** — RR floor ≥ 2, ATR stops, 0.25–1% risk per trade. This is arithmetic, not statistics: at WR 36%, the system is only profitable *because* R ≥ 2. It is load-bearing.

What does **not** survive:

3. **The FX book has no statistically demonstrable edge.** Across the unselected 12-pair universe: n = 3,328 trades, WR 34.5% vs breakeven 33.3%, z = +1.42, **p = 0.078 one-sided — not significant even in-sample**, and correlated trades across USD pairs make the effective n smaller than 3,328, so the true p is worse. The "7 kept FX pairs" (p = 0.006) were selected *after* observing the full-history results — that p-value is void by construction (selection bias).
4. **The multi-engine confluence apparatus is largely redundant packaging around one trend factor** (Section 4). The system's own `engine_activation` study proves that adding engines *reduces* PF (1.27 → 1.11 with all 9).

**Verdict: the philosophy is partially wrong but honestly instrumented.** The measured edge is a two-ingredient system (trend rule + risk discipline) on three carrier assets, wearing a nine-engine, seven-gate costume. The costume has measurable cost (dilution, dead gates, false precision, decision starvation on FX) and no measured benefit beyond the minimal core. Sustainability is **unproven**: 5 closed live outcomes exist; the 100-trade forward demo test currently running is the only path to a defensible claim, and the repository is correct to treat it as the top priority.

---

## 1. Audit Objectives — the Eleven Assumptions

| # | Assumption | Verdict | Evidence |
|---|---|---|---|
| 1 | Multiple engines improve accuracy | **DISPROVED** | `engine_activation_20260705`: baseline-4 portfolio PF 1.27; every add-one-in variant lower; all-9 = 1.108. H1 LOO: no engine's removal significantly hurts (max \|t\| = 1.46 across 10 symbols); removing SMC *improves* PF on 7/10 symbols (mean ΔPF +0.054). |
| 2 | More confluence ⇒ higher probability | **UNSUPPORTED** | No score-vs-outcome calibration data exists (5 live outcomes; backtest manifests don't bucket by score). Structurally, the score is a *weighted average of agreeing engines* — adding a lukewarm agreeing engine **lowers** the score, so the metric doesn't even measure confluence breadth. Direct evidence (assumption 1) points the other way. |
| 3 | More filters reduce risk | **DISPROVED as stated** | H1 LOO `−sentiment` variant: **more** trades (211 vs 166 on EURUSD), **higher** PF (1.078 vs 1.057), **lower** max DD (12.2% vs 19.5%). A filter was rejecting good trades while adding drawdown. No A/B exists for MQS, news, or correlation gates at all. |
| 4 | Rejecting trades improves expectancy | **UNPROVEN** | The score gate rejects ~4.3× more candidates than are taken (3,853 vs 902, H4 production config) but the counterfactual P&L of rejected signals is **never recorded**. The system literally cannot answer whether its NO_TRADEs are correct. This is the single largest missing measurement in the platform. |
| 5 | Risk Engine improves long-term returns | **KEEP (structural), throttles unproven** | RR floor 2.0 is arithmetically load-bearing (WR 36% × R2 ⇒ +0.08R; at R1 the same signals lose). Drawdown-tiered sizing and the 15% halt have never fired in any committed backtest artifact and have no A/B. They are cheap insurance — but they are *policy*, not proven alpha. |
| 6 | Dynamic thresholds are beneficial | **UNSUPPORTED** | `regime_weights.py` docstring admits it: *"hand-crafted domain knowledge … can be validated once enough engine_tracker data accumulates."* Multipliers (1.3, 0.7, 1.4 …) have no empirical derivation. No A/B static-vs-regime weights exists. |
| 7 | Engine weighting is justified | **DISPROVED as precision** | Weights carry 4 decimals (nnfx 0.2273, divergence 0.0606) but their derivation is absent from the repo (production audit Phase 4: "provenance NOT ENOUGH EVIDENCE"). LOO shows per-engine contributions statistically indistinguishable from noise — the data cannot support 4-digit weights; it can barely support signs. |
| 8 | Majority voting is statistically superior | **UNSUPPORTED** | Voting theory (Condorcet) requires *independent* voters. The measured vote matrix shows the voters are not independent (Section 4): wyckoff↔sentiment agree **100.0%** (n = 3,389), ict↔wyckoff **99.5%**, nnfx↔sentiment **0.1%**. A majority among clones is one vote counted several times. |
| 9 | Market regime filtering adds value | **NO EVIDENCE** | Regime detection feeds the weight multipliers (assumption 6) and MQS. No experiment isolates its contribution. |
| 10 | MTF confirmation increases edge | **PLAUSIBLE, unproven** | Indirect support: D1-primary on the same 3y window shows higher PF than H4-primary (e.g. ETHUSD 1.97 vs 1.59; GBPUSD 1.90 vs 1.05) at far fewer trades — higher-timeframe alignment correlates with quality. But no MTF-gate on/off A/B exists; the bonus/penalty magnitudes are hand-set constants. |
| 11 | No-Trade decisions create value | **PHILOSOPHICALLY SOUND, EMPIRICALLY UNMEASURED** | Same gap as assumption 4. NO_TRADE-as-valid-output is good discipline, but its value is asserted, never measured. A shadow book of rejected signals would settle it in one release. |

**Score: of eleven foundational assumptions, zero are validated to a statistical standard, two are structurally sound (5, 11), three are directly contradicted by the system's own experiments (1, 3, 8/7).**

---

## 2. Scientific Validation — Rule by Rule

The platform's own hypothesis registry is the right instrument, and its honesty is exceptional (six FAILED/ABANDONED entries retained). But applied to the *production rule set*, the registry reveals a governance hole:

| Production rule | Hypothesis | Status | Committee reading |
|---|---|---|---|
| SMC engine enabled | none — **EXEMPT** | bypassed | The edge gate's own bar ("no engine without a hypothesis") is waived by labeling. LOO evidence: removal improves 7/10 symbols. **The exemption label is a loophole in the system's central scientific control.** |
| Price Action enabled | none — **EXEMPT** | bypassed | Same loophole. Was rewritten once after a measured 0.975 correlation with NNFX; still agrees 82.6% with Quant, 64.2% with NNFX. |
| NNFX enabled | H004 | RESEARCH | Never individually validated — but it is the de facto core: votes on **100% of bars** (EMA200 always yields a side). It is the trend prior wearing an engine costume. |
| Wyckoff enabled | H006 | RESEARCH | Never validated. Vote behavior: 32% vote rate, 89% counter-trend, **100% agreement with the sentiment placeholder**. |
| 6-engine confluence | **H009** | **PASSED** | **The only PASSED entry in the registry is its least reliable.** The PF 3.08 / 18-18 walk-forward was shown by the production audit to be (a) not reproducible, (b) contaminated by development lookahead, (c) run on an engine since changed. The honest re-measurement (`h4_yearly_stability`) of the *same philosophy* gives portfolio PF **1.01–1.19** by year — roughly one third of the claim. **H009's status was never downgraded.** Every enabled engine ultimately shelters under this stale PASSED. |
| Liquidity sweeps, BOS+FVG | H001/2/2b/8/8b/8c | FAILED ×6 | Model rigor. H008c in particular (look-ahead removed, chronological OOS, pooled test WR 0.489, p = 0.83) is exactly how the *positive* claims should also be tested — and haven't been. |
| min_score_to_trade = 58, per-symbol 55/58/60 | none | — | No provenance anywhere in the repo. These thresholds do 87% of all rejecting (Section 5). False precision on the most powerful knob in the system. |
| Regime weight multipliers | none | — | Self-declared unvalidated. |
| News blackout, symbol health, correlation cap | none | — | Never simulated — the backtest engine models none of them, so their effect on 6.4y of results is unknown even in-sample. |

**Rejection rule applied ("reject every rule that lacks evidence"):** by the audit's own standard, the rules that survive are — the trend core (NNFX-style baseline + D1 confirmation), the RR ≥ 2 floor, ATR-based stops, fixed fractional sizing, and the carrier symbol selection *conditional on forward confirmation*. Everything else currently lacks supporting evidence.

---

## 3. Architecture Review — Failure Modes Found

| Failure mode | Present? | Concrete example |
|---|---|---|
| **Overfitting** | YES — moderate | 4-decimal weights with no derivation; min_score 58 vs symbol-specific 55/58/60; H008b's London+ATR filter collapsing from 63.6% (train, n=11) to 35.5% (test) shows the *team knows* this failure mode, yet production thresholds were never given the same OOS test. |
| **Survivorship / selection bias** | YES — explicit | `config.yaml` 2026-07-06: AUDUSD/USDCAD/NZDUSD/EURGBP/EURCHF disabled *because* their 6.4y in-sample PF < 1.0. Dropping measured losers after the fact mechanically raises the reported aggregate. The comment is honest about it ("re-enable if forward paper trading proves otherwise") but the resulting "portfolio PF" is a post-selection statistic. |
| **Confirmation bias** | PARTIAL | The negative-result ledger is a strong antidote and deserves credit. But it is applied asymmetrically: negative hypotheses got OOS re-tests (H008c); the positive core claim (H009) never got one. |
| **Redundant confirmation / double counting** | YES — structural | (a) wyckoff–sentiment 100% vote agreement; ict–wyckoff 99.5% — three "independent confirmations" are one signal. (b) `meta_decision.py` recombines the confluence score (already gated) with agree_count (already gated by min_engines_agreeing) into a "confidence" that gates again — the same information vetoes twice. (c) The H013 reversal veto requires "2+ reversal engines unanimous" — but the reversal group members agree 97–100% pairwise, so the "2+" quorum is one engine counted twice. |
| **Circular reasoning** | YES | The engines are enabled because H009 PASSED; H009's PASSED is a measurement *of those engines* made with a discredited method; the registry never re-closed the loop. |
| **Correlated indicators pretending independence** | YES | Trend cluster: smc–nnfx 73.6%, price_action–quant 82.6%, nnfx votes 100% of bars. Anti-trend cluster: nnfx–wyckoff 1.2%, nnfx–sentiment 0.1% (perfect anti-correlation = same information, sign-flipped). Nine engines ≈ **two factors**: "trend" and "not-trend". |
| **Excessive filtering / decision starvation** | YES on FX | EURUSD H4: 566 score-rejections vs 141 trades over 3y ⇒ ~47 trades/year, WR 34.8%, PF 1.03. After real (lower!) FX spreads the book is ~breakeven: enormous machinery to harvest ≈ nothing. On carriers the same filtering yields PF 1.3–1.6 — the filters aren't creating that edge, the assets are. |
| **Rule explosion** | YES | 7 gates + meta layer + veto + regime multipliers + per-symbol thresholds. One gate (`reversal_veto`) fires **exactly zero times** in the production configuration (its input engines are disabled) — dead machinery presented as governance. |
| **False precision** | YES | Weights (0.2273), MTF bonus/penalty constants, meta-confidence formula (30/30/20/20 mix, +10 "data quality" for using Twelve Data, penalty constants 15 and 5) — none derived from data. |
| **Complexity without edge** | YES — proven | This is the rare case where the system *measured it itself*: every sophistication tested in `docs/STRATEGY_EVIDENCE_2026-07.md` (more engines, volume, sweeps, pairs trading, managed exits, currency strength) failed to add PF. The conclusion drawn there ("the system is at the edge frontier") is correct but incomplete — the same logic runs in reverse: if additions add nothing, most of the *existing* additions likely add nothing either, and the LOO data supports that. |

---

## 4. Confluence Philosophy

**Is the Confluence Score scientifically justified? On current evidence: NO — with one nuance.**

- *Does higher confluence increase win rate / PF / reduce drawdown?* **Unknown.** The decisions DB stores the score per decision, but only 5 closed live outcomes exist and no backtest manifest buckets outcomes by score. The claim is untested after ~13 months of development. The nuance: selectivity-in-general has indirect support (D1-primary, fewer/better trades, higher PF), but nothing ties *the score specifically* to outcome quality.
- *Diminishing returns?* The `engine_activation` study answers a stronger version: returns are **negative** past the 4-engine baseline — and LOO suggests negative past ~2 engines.
- *Is the threshold (58) optimal?* Unknown; no sensitivity sweep is committed. The per-symbol 55/58/60 variations have no recorded justification.
- **Construction flaw:** the score is the weighted mean of *agreeing* engines only. Two engines agreeing at 60 score higher than four agreeing at 55. It measures the *average conviction of the agreeing clique*, not confluence. `participating_weight_share` — the one number that does measure breadth — is computed and then not used as a gate.

**Recommendation: SIMPLIFY, then validate.**
1. Replace the vote+score+contradiction+meta stack with one number: **directional weighted conviction** (bull_conviction − bear_conviction, already computed in `voting_system.py`) with a single threshold.
2. Log score/conviction on every decision *including rejections*, plus a shadow-book counterfactual outcome. After ~300 forward decisions, plot WR and expectancy by conviction decile. Keep the threshold only if the curve is monotone; else remove it.
3. Do not "redesign" confluence upward (more engines, more layers) — that direction is measured and dead.

---

## 5. Engine Evaluation

Vote behavior pooled across 10 symbols (H1 ablation, 1,511 samples/symbol); ΔPF = mean leave-one-out effect (negative = removal hurts = engine helps); no ΔPF is statistically significant (all |t| ≤ 1.46, n = 10 symbols).

| Engine | Votes | Counter-trend | Mean ΔPF on removal | Helps/hurts (sym) | Redundancy | Verdict |
|---|---|---|---|---|---|---|
| NNFX | 100% | 25% | −0.010 | 6/4 | = the trend factor itself | **KEEP** — it *is* the system. Rename honestly: "trend baseline". Enormous per-symbol variance (−0.445 USDCAD … +0.482 USOIL) means even this is regime/asset-dependent. |
| Market Structure | 72% | 37% | **−0.050 (largest helper)** | 6/4 | 60% w/ nnfx | **TEST-ENABLE** — the only engine whose removal consistently hurt, yet it is *disabled*. The enabled set contradicts the committed ablation. Run the H4 A/B (baseline-4 vs baseline-4+MS, and MS-for-SMC swap) before promoting. |
| Price Action | 74% | 42% | −0.020 | 4/6 | 82.6% w/ quant, 64.2% w/ nnfx | **MODIFY/MERGE** — already rewritten once for 0.975 corr with NNFX; still substantially the same factor. Merge its candle/breakout residual into the trend core or prove marginal value at H4. |
| Quant | 65% | 47% | −0.024 | 6/3 | 82.6% w/ price_action | **MERGE** with Price Action — they are one oscillator-flavored engine in two files. |
| SMC | 76% | 43% | **+0.054 (largest drag)** | 3/7 | 73.6% w/ nnfx | **DISABLE pending evidence** — enabled in production, yet removal improved 7/10 symbols; implements ~30% of its spec (swing structure only); mostly a lagged trend copy. |
| Wyckoff | 32% | 89% | +0.020 | 3/7 | **100% w/ sentiment, 99.5% w/ ict** | **DISABLE** — a counter-trend flag identical to two other engines; volume-based method running on volume-less FX data; weight 0.07 so it mostly adds veto noise. |
| ICT | 22% | 55% | +0.016 | 3/6 | 99.5% w/ wyckoff | **REMOVE from roadmap** as a separate engine — its concepts (killzones aside) are the same anti-trend flag. Session/killzone logic, if wanted, belongs in MQS. |
| Sentiment | 40% | 90% | +0.027 | 2/8 | 100% w/ wyckoff | **REMOVE** — it is a *price-derived proxy*, not sentiment (COT never wired). Removal improved 8/10 symbols. Keeping it labeled "sentiment" is self-deception risk. |
| Divergence | 57% | 53% | +0.019 | 5/5 | — | **KEEP DORMANT** — coin-flip contribution; only re-test as a *veto input* if reversal logic is ever revisited. |
| Macro | 0% (weight 0) | — | ±0.000 | 0/0 | — | **REMOVE** — weight 0.0, requires an uninstalled dependency, contributes exactly nothing. Dead config. |

**Structural conclusion:** the nine engines collapse into **one trend factor + one anti-trend flag + noise**. Recall/precision per engine against trade outcomes cannot be computed from committed artifacts (per-engine vote-vs-outcome tables live only in the production D1, n≈208 votes / 5 outcomes) — another measurement the platform is instrumented for but hasn't accumulated.

---

## 6. Governance Analysis — the Gate Pipeline

Pooled gate rejections, H4 production config (902 trades taken):

| Gate | Rejections | Share | Committee finding |
|---|---|---|---|
| Confluence score < threshold | 3,853 | **75.3%** | The dominant decision-maker of the entire system — and the least evidenced (no threshold provenance, no counterfactual tracking). *Which gate rejects the most profitable trades?* Unknowable today; by volume alone, this is where they'd be. |
| Contradiction check | 914 | 17.9% | Second-biggest rejecter. With only 4 engines (2 of them near-clones and 1 anti-trend flag), "contradiction" often means "wyckoff disagrees with nnfx" — which the vote matrix shows is wyckoff's *permanent state* (1.2% agreement). Needs an A/B before it keeps veto power. |
| Vote quorum (min 2) | 242 | 4.7% | Cheap, harmless, largely redundant with the score gate. Merge into score. |
| MQS | 153 | 3.0% | Small effect; unvalidated; plausible as session hygiene. Keep only with an A/B. |
| Reversal veto (H013) | **0** | 0% | **Fires zero times in the production configuration** (its input engines are disabled). Dead gate. Remove until reversal engines pass research; note that when it *was* active (H1 all-engine runs) it rejected 1,477 candidates based on a quorum of engines that agree 97–100% with each other — a duplicated-signal veto. |
| Risk gate | not in manifests | — | RR floor binds at signal construction (SL/TP are ATR-derived, RR fixed per symbol), so it rarely rejects; drawdown throttle untested. Keep — structural. |
| News / correlation / symbol health | not simulated | — | Zero backtest coverage; live n too small. Unknown value, plausible as cheap insurance, must be measured (event-window study on the 6.4y trade set would take a day). |

**Ordering:** since all gates are AND-composed vetoes, order doesn't change outcomes — only attribution and CPU. Fine as is. **Merging:** vote-quorum → score; meta-decision layer → delete (it re-gates on a recombination of score + agreement + a +10 bonus for *which data vendor was used* — information already used upstream, plus noise). **Disappearing:** reversal veto (dead), macro engine (dead), meta layer (double-counting).

**The largest governance gap:** IATIS tracks every rejection *reason* but no rejection *outcome*. Add a shadow book (paper-fill every gate-rejected signal at its hypothetical SL/TP) and within months the platform will know, per gate, its saved-loss vs forgone-profit ledger. Nothing else in this section can be settled without it.

---

## 7. Statistical Analysis

Recomputed from committed manifests:

**Distribution / significance (6.4y frozen config, 4,240 trades):**

| Book | n | WR | Breakeven WR | z | p (1-sided) | Expectancy |
|---|---|---|---|---|---|---|
| FX-12 (unselected) | 3,328 | 34.5% | 33.3% (R=2) | +1.42 | 0.078 | +0.035R |
| Carriers (XAU/BTC/ETH) | 912 | 42.9% | ~29.8% (R≈2.5) | +8.60 | <10⁻¹⁵ | +0.441R |
| All 15 | 4,240 | 36.3% | 32.6% | +5.16 | <10⁻⁶ | +0.122R |
| "Kept 7" FX | 2,051 | 36.0% | 33.3% | +2.54 | 0.006 | — **void: post-hoc selection** |

Correlation caveat: simultaneous signals across USD-correlated pairs violate independence; effective n < nominal n; FX significance is *worse* than shown. The all-15 significance is carried almost entirely by the carriers.

**Yearly regime analysis (portfolio, frozen config):** PF 1.01 / 1.07 / 1.17 / 1.19 / 1.15 / 1.06 / 1.12 (2020→2026). Never below 1.0 — genuinely the strongest robustness fact in the repo — but the band 1.01–1.19 is thin; a 0.1 PF haircut from any unmodeled friction (swap/rollover costs are *not* modeled anywhere) erases several years.

**Sensitivity:** no committed sweep of `min_score_to_trade`, MTF constants, or SL multiplier. Threshold optimization: not performable from committed artifacts — flagged as required work, *to be done walk-forward, not in-sample*.

**Feature importance:** the LOO ablation is the right harness and its answer is "no engine matters detectably except possibly market_structure (positive) and smc (negative), both sub-significant."

**Bayesian view of the carrier edge:** prior that any free-data H4 technical system has a real edge: low (base rates; six of this repo's own eight tested entry ideas failed). The 6.4y in-sample carrier result (z = 8.6) is strong likelihood but development-contaminated; discount heavily. The 2022-bear PF 1.55 and the live-spread recost survive as genuine partial evidence. Posterior: **plausible modest real edge on trending carriers, P(edge > costs) ≈ 0.5–0.65; the 100-trade forward demo run is precisely the experiment that resolves it.** No probability mass should move on FX until forward data says otherwise.

**Monte Carlo:** `backtest/monte_carlo.py` exists but no committed manifest carries CI bands on the headline numbers. At PF 1.1 / 4,240 trades, bootstrap CIs would very likely straddle 1.0 for the FX book — worth committing as a standard artifact.

---

## 8. Simplicity Challenge — the Minimal System

Direct evidence says the minimal form is:

```
IATIS-minimal:
  Universe:   XAUUSD, BTCUSD, ETHUSD  (+XAGUSD as forward candidate)
  Signal:     H4 trend state (EMA200 side + ADX strength — today's NNFX)
  Confirm:    D1 trend agreement (EMA20/50 + ADX — today's MTF gate)
  Entry:      next H4 open;  Stop: 2.5×ATR;  Target: RR 2.0–2.5
  Size:       0.5–1.0% risk, halve after 10% drawdown, halt at 15%
  Hygiene:    news blackout (cheap insurance), max concurrent positions
```

Everything else is removable **on the evidence committed to this repo**: five dormant engines (LOO: none significantly positive), SMC and Wyckoff (LOO: negative-to-neutral, redundant), the sentiment proxy, the reversal veto (fires 0×), the meta-decision layer (double-counts), regime weight multipliers (unvalidated), the FX-12 book (p = 0.078), the index/oil symbols (enabled with **no committed backtest evidence at all** — US30/NAS100/SPX500 appear in no result manifest, and USOIL's only appearance is PF 0.87 in the H1 ablation, *losing*, yet it is `enabled: true`).

Estimated reduction: **~70–80% of decision-pipeline complexity, at zero measured edge cost** — with the honest caveat that the estimate is bounded by the same in-sample data, so the right procedure is to run IATIS-minimal *alongside* the full system on the demo account and let the forward evidence counter arbitrate. The platform (data failover, outcome tracker, D1 storage, dashboards, edge gate) is worth keeping in full — the *platform* is genuinely good; it's the *decision stack* that's overweight.

---

## 9. Devil's Advocate Review

Attempts to destroy the remaining positive claim (carrier trend edge), and what survived:

| Attack | Result |
|---|---|
| **It's randomness.** | Partially survives the attack: z = 8.6 on 912 trades is not luck *within the sample*. But the sample itself was development data — see next row. |
| **It's curve fitting.** | The strongest attack. Thresholds, weights, symbol set, timeframe choice (H4 over D1/H1), and RR values were all chosen with full access to 2020–2026 data. The 6.4y "stability" run is a *consistency* check of one frozen config, not out-of-sample proof. The H008b precedent (63.6% train → 35.5% test) shows exactly how such numbers die OOS. **Nothing in the repo rules this out for the core system.** |
| **It's regime beta.** | Substantially true and should be owned: gold + crypto trended for most of 2020–2026, and a trend system on trending assets earns trend beta, not alpha. Counter-evidence: BTC PF 1.55 in the 2022 bear (trend capture works both directions) and gold's flat-2021 PF 0.94 (system correctly earned ~nothing when the asset didn't trend). The edge should be described honestly as **"disciplined trend capture on assets that trend"** — it will underperform in extended ranging regimes, and no committed artifact quantifies how badly. |
| **It's data leakage.** | The team found and killed its own look-ahead bugs twice (trade-management +100% mirage; H008c causal-swing fix). Bar-level lookahead is credibly controlled. Development-level lookahead (previous row) is not. |
| **It's selection bias.** | Confirmed for the FX-7 and for any headline that excludes the disabled pairs; confirmed-in-spirit for the IC-sweep "77 winners" (72 never paid a real spread — the repo itself says so). The carrier selection (XAU/BTC/ETH) is *also* post-hoc: they are the three best performers of the 15 tested. The honest portfolio number for the philosophy as-of-2020 is the all-15 PF ≈ 1.1, not the carrier PF ≈ 1.4. |
| **The 5-trade live record proves nothing.** | Correct. n = 5. No claim of live edge is currently possible in either direction. |

**Surviving conclusions:** (1) real broker costs do not kill the backtested carrier edge (measured, live spreads); (2) the system was profitable in-sample in every year including a crypto bear; (3) every *negative* finding in the repo is trustworthy — the platform's capacity for honest self-measurement is its most valuable asset; (4) all *positive* findings remain in-sample and await the forward demo counter.

---

## 10. Final Verdict

| Category | Score /10 | Basis |
|---|---|---|
| Scientific validity | **4** | World-class negative-result honesty; but the production set contradicts its own ablation, the only PASSED hypothesis is stale, and EXEMPT labels bypass the edge gate. |
| Statistical robustness | **3** | FX book non-significant; carrier result in-sample; no CIs on headline claims; correlated-trade inflation unaddressed. |
| Logical consistency | **4** | Sound layering, but double-counted signals (veto quorum of clones, meta re-gating) and circular H009 authorization. |
| Maintainability | **6** | (From production audit) strong tests/docs; CC-71 pipeline function, dead modules, dead gates. |
| Generalization ability | **3** | Edge concentrated in 3 post-hoc assets; failed on 5/12 FX; unknown on the enabled-without-evidence indices. |
| Overfitting risk | **7 (high)** | Development lookahead pervades every positive number; fitted constants everywhere; post-hoc symbol curation. |
| Practical usefulness | **6** | As a measurement platform + paper-trading lab: genuinely useful today. As an alpha source: unproven. |
| Institutional readiness | **2** | Per production audit: credential exposure, no CI/DR, n = 5 live outcomes. Philosophy aside, no institution allocates to this today. |
| Expected long-term edge | **3** | FX ≈ 0. Carriers: plausible PF 1.1–1.3 forward if the trend-regime persists; regime-dependent by construction. |

### Answers to the Six Questions

**1. Is IATIS philosophically sound?**
Half of it. The *epistemic* philosophy (research before production, NO_TRADE valid, negative results retained, no lookahead, costs measured live) is sound and rare — keep it. The *decision* philosophy (edge emerges from many engines, confluence scoring, and gate stacking) is contradicted by the system's own measurements: engines dilute, voters are clones, the biggest gate is unvalidated, one gate is dead, and the meta layer double-counts.

**2. Which assumptions are scientifically validated?**
None to a full statistical standard. Best-supported: RR ≥ 2 discipline (arithmetic), trend capture on trending carriers (strong in-sample, cost-verified, regime-caveated), higher-timeframe selectivity (indirect), and the platform's negative results (H001–H008c — fully credible).

**3. Which assumptions are unsupported?**
Multi-engine accuracy (disproved), confluence-score→probability (unmeasured), more-filters-less-risk (counter-evidenced), engine weights at 4 decimals (no provenance), regime multipliers (self-declared unvalidated), majority voting among correlated engines (structurally invalid), news/correlation/symbol-health gates (never simulated), FX edge (p = 0.078), index/oil symbol enablement (no evidence at all).

**4. Which components should be removed immediately?**
(a) Macro engine (weight 0, dead). (b) Reversal veto in production (fires 0 times). (c) Meta-decision layer (double-counts; fold its position multiplier into the score gate if sizing granularity is wanted). (d) Sentiment engine (a mislabeled price proxy). (e) USOIL/US30/NAS100/SPX500 from the live universe until any evidence exists. (f) H009's PASSED status — downgrade to RESEARCH until re-validated forward; this is a registry edit, not code. (g) The EXEMPT loophole: every enabled engine gets a hypothesis, no exceptions.

**5. Simplest architecture with equal-or-better performance?**
Section 8's IATIS-minimal: one trend rule + one HTF confirmation + ATR/RR/fractional-risk discipline + news hygiene, on XAU/BTC/ETH(+XAG). On the committed evidence it matches the full system on the assets that make all the money and sheds the FX book, whose measured contribution is ≈ 0 at 4× the operational surface. Validate by running it side-by-side on the demo counter.

**6. Redesign from scratch, evidence-only?**
Keep the platform, rebuild the brain:
- **Layer 1 — one causal trend model per asset class** (EMA/ADX state machine; today's NNFX), D1-confirmed, H4-executed.
- **Layer 2 — risk as the only sovereign gate:** RR floor, ATR stops, fractional sizing, drawdown throttle, correlation cap, news blackout. No score, no vote.
- **Layer 3 — measurement as the product:** shadow book for every rejected signal; conviction-decile calibration; per-gate saved-loss/forgone-profit ledger; Monte Carlo CIs on every published number; walk-forward with config frozen at a tagged commit *before* the test window opens.
- **Expansion rule:** a new signal enters only with a pre-registered hypothesis, chronological OOS split, and a *marginal* portfolio-level improvement with CI excluding zero — the bar H008c already established. On today's evidence the first candidates are market_structure (the one positive LOO) and a real order-flow engine on crypto (the one data-rich avenue) — nothing else in the current roadmap clears the bar.

**Final sentence, undiplomatically:** IATIS is an excellent laboratory wrapped around a mediocre committee of clones voting on one idea; fire the committee, keep the idea, keep the laboratory — and let the forward demo counter, not this document or any backtest, issue the only verdict that counts.

---

*Audit produced 2026-07-09 on branch `claude/iatis-philosophy-audit-945ofz`. All numbers recomputed from git-tracked manifests; recomputation scripts are one-liners over `research/results/*.json` and are reproducible from this document's tables.*
