# Philosophy Audit — Addendum: Live-Behavior Analysis (8 Axes)

Companion to `docs/PHILOSOPHY_AUDIT_2026-07.md`. That document audited the
*philosophy against backtest evidence*; this one audits **whether the live
system actually behaves according to that philosophy**, prompted by the first
614 live decisions (10 EXECUTE / 604 NO_TRADE, 1.63%).

Executable version: **`scripts/philosophy_audit.py`** — 29 automated checks
across the 8 axes below, runnable on the VPS against the production D1
(`python -m scripts.philosophy_audit`). Verified end-to-end against a
schema-true synthetic database in this audit.

---

## The headline finding first: the live system is not the backtested system

Chasing the live statistics (NNFX agreement 18.9% avg score 13.1, Wyckoff
participation 1.85%) leads to the code, and the code contains the cause —
documented in its own docstring:

> `core/data_loader.py:270-279` — *"Twelve Data Free plan supports M1, M5,
> M15, H1 natively. **H4 and D1 return 403 Forbidden** … we fetch H1 and
> resample upward … **500 H1 bars ≈ 125 H4 bars**."*

Consequences, each verified against the module that consumes the data:

| Component | Needs | Gets live (bars_to_load=500) | Effect |
|---|---|---|---|
| NNFX engine | **210+ H4 bars** for EMA200 (`nnfx_engine.py:61`) | ~125 H4 bars | Returns NEUTRAL with reason *"Insufficient data for NNFX analysis"* — **permanently mute in live operation** |
| MTF D1 confirmation | **50+ D1 bars** (`mtf_confirmation.py:77`) | ~21 D1 bars | Returns `score_adjustment=0.0` — **the D1 gate is silently inert live** |
| Wyckoff | a formed trading range + a spring/upthrust event | thin window | Rare by design (32% vote rate even on full history); on a 125-bar window, ~2% |
| SMC / PriceAction | short lookbacks | enough | The only two engines actually fed |

Meanwhile **every backtest that validated the system ran on deep H4 CSVs**
where NNFX voted on 100% of bars and the MTF gate was active
(`h4_backtest_20260705`: "mtf_gate: ACTIVE").

**So the live deployment inverts the validated design:** the trend anchor
(NNFX) and the higher-timeframe confirmation — the two components the main
audit identified as the *only* evidence-carrying parts of the decision stack
— are dead in production, and the system is running on exactly the pair
(SMC + PriceAction) whose standalone value the ablation ranked
neutral-to-negative. The 614 live decisions are therefore **not evidence
about the philosophy at all**; they are evidence about a data-starvation
defect.

**Fix (one change, high confidence):** make the resample base deep enough —
raise the H1 fetch to ≥ 3,000 bars (Twelve Data free allows up to 5,000 per
request; `bars_to_load` currently truncates the Yahoo path to 500 via
`df.tail(outputsize)` too). 3,000 H1 → ~750 H4 bars (NNFX ✓) and ~125 D1
bars (MTF ✓). Alternative: use the cTrader trendbars already integrated for
the demo as the live bar source — the broker serves native H4/D1.
**Do not retune weights, quorum, or thresholds until this is fixed and
~2–4 weeks of decisions have accumulated on full data.**

---

## Axis 1 — Hierarchy audit (does the verdict respect its gates?)

**Verdict: SOUND in code.** `main.py:_final_verdict` composes strictly:

```
final_verdict = EXECUTE  iff  conf.passed AND risk_pass AND not news_blocked
```

then the per-symbol regime filter and the Meta layer can only *downgrade*
EXECUTE → NO_TRADE, never upgrade. The risk gate is only evaluated when
confluence passes (`_risk_gate` returns `None` otherwise), and news only when
confluence passes — so "Confluence FAIL + Risk PASS" cannot exist as a
decision path, and "News FAIL → EXECUTE" is unreachable.

**Correction to the proposed SQL:** `cf_score>=60 AND risk_passed=0` is a
*legitimate* state (confluence passed, risk vetoed → NO_TRADE), not a
violation. The true violation queries are:

```sql
-- must always return zero rows:
SELECT id, ts, symbol FROM decisions WHERE verdict='EXECUTE' AND risk_passed=0;
SELECT id, ts, symbol FROM decisions WHERE verdict='EXECUTE'
  AND fail_reason IS NOT NULL AND fail_reason<>'';
-- risk verdicts fabricated where the gate never ran:
SELECT COUNT(*) FROM decisions WHERE risk_passed IS NOT NULL
  AND fail_reason LIKE '%engine(s) agree%';
```

All three are automated as Axis-1 checks in `scripts/philosophy_audit.py`.
One real auditability gap found: a Meta-layer BLOCK downgrades EXECUTE →
NO_TRADE **without writing a `fail_reason`** (only the log line records it) —
flagged as check 1.3.

## Axis 2 — Engine independence

Backtest ground truth already exists (`ablation_20260703.json`, pooled over
10 symbols, both-voting decisions): **SMC|PriceAction agree 49.7%** — at the
vote level they are *not* clones (the clone pairs are wyckoff|sentiment
100%, ict|wyckoff 99.5%, and the anti-clones nnfx|wyckoff 1.2%). The live
65.5%/56.1% figures measure something different — agreement *with the final
verdict*, which is confounded by the verdict being derived from them.
Check 2.x computes true pairwise vote agreement from `engine_votes`
(threshold: FAIL at ≥80% or ≤20%). Note the ~50% SMC|PA agreement cuts the
other way too: when the only two live engines co-sign at coin-flip rates,
their co-signature carries little confirmation value (see Axis 8).

## Axis 3 — Phantom engines

**Confirmed — but the root cause reverses the recommendation.** NNFX and
Wyckoff are phantoms live (mute on ~81–98% of decisions), yet NNFX is not
"incomplete" and does not need its weight reduced: it is **starved** (see
headline). Reducing its weight or disabling it would permanently codify the
defect and delete the one engine family that carried the validated edge
(the ablation's only consistently *positive* removals were trend engines;
NNFX removal cost up to −0.445 PF on USDCAD). Wyckoff is a genuine
rare-event engine (2–32% participation by design) — the main audit already
recommends disabling it on redundancy grounds (100% agreement with the
sentiment proxy), which stands.

Checks 3.x measure participation per engine and count the literal
`"Insufficient data for NNFX"` string in `raw_json` — making the starvation
directly observable in the decision records.

## Axis 4 — Per-engine decision impact (counterfactual replay)

`scripts/philosophy_audit.py` replays every stored decision's votes with one
engine deleted and counts changed outcomes (majority direction or quorum
state). Deletion-changes-nothing (< ~2%) ⇒ the engine is decoration: remove
it or fix its inputs. On live data today this will show NNFX ≈ 0% (mute
engines change nothing when deleted — consistent with starvation, not with
value). The backtest LOO remains the authority on *P&L* impact: no engine
significant, SMC removal *improved* 7/10 symbols.

## Axis 5 — Gate bottleneck (who actually blocks?)

The live ranking (122 × "Only 1 engine agrees" ≫ 39 × exposure ≫ score) is
the *starved* ranking. Decomposition matters more than the count: for every
quorum rejection, check 5.2/5.3 splits the blockers into **mute** (NEUTRAL /
score < 20) vs **dissenting** (voted against). Mute ≫ dissent — which is
what NNFX+Wyckoff silence guarantees — means the bottleneck is missing
information, not disagreement, and the fix is data, not thresholds. A
genuine "SMC bullish vs PriceAction bearish" split is the healthy case: two
fed engines disagreeing *should* produce NO_TRADE under this philosophy.

The 39 exposure rejections deserve separate attention: with
`risk_per_trade_max=0.01` and `max_exposure=0.05`, five open paper positions
saturate the book, and `outcome_tracker.auto_close_outcomes()` only resolves
SL/TP at scheduler-tick close prices — stale open outcomes can block new
signals for hours. Check 7.2 counts these; verify open-outcome hygiene
before reading them as intended risk behavior.

> **IMPLEMENTED (2026-07-09):** two hygiene mechanisms in
> `auto_close_outcomes()` — (1) intrabar TP/SL detection: every report now
> carries the decision bar's `bar_high`/`bar_low` (including MQS-blocked
> weekend runs) and the scheduler passes them through, so a level touched
> inside the bar and retraced closes the signal instead of lingering; when
> both levels sit inside one bar, SL is assumed first (same conservative
> convention as `backtest_engine.check_exit()`). (2) time stop:
> `execution.max_open_trade_hours` (168h = 7 days ≈ 42 H4 bars) force-closes
> stale signals at market, labeled by realized R (±0.1R breakeven band), so
> the paper book can no longer stay saturated indefinitely.
> Regression-locked by `tests/test_outcome_hygiene.py`.

## Axis 6 — Logic discontinuity (the BTC-39 vs ETH-80 case)

**Real, and there are four documented mechanisms in the code:**

1. **Two competing definitions of "majority".** `voting_system.tally_votes`
   picks `winning_bias` by *weighted conviction*; `score_calculator
   .calculate_score` independently picks the score side by *raw count*, with
   ties broken by higher average. They can select **opposite sides** — the
   stored `cf_score` can describe the direction the verdict didn't take.
2. **The count-tie flip.** At 1-vote-vs-1-vote (the common live state with
   only SMC+PA fed), the reported score is whichever side's average is
   higher: SMC bearish 45 / PA bullish 80 → 80; nudge a third voice or one
   point and the same inputs report ~45 (or a bear-side ~39). This is
   exactly the observed BTC/ETH class of jump — not a bug in either
   function, but an inconsistency *between* them.
3. **The conviction cliff at 20.** `voting_system` silences votes with
   score < 20; `score_calculator` **does not apply the threshold** (it reads
   raw bias). An engine at 19 is NEUTRAL for the quorum yet still steers the
   score — two layers disagree about whether it voted.
4. **Multiplicative/additive jumps**: H013 soft veto ×0.5 and MTF ±8 apply
   *after* the above, compounding threshold effects.

**Fix:** one definition of majority (weighted conviction, since that decides
the verdict), the conviction threshold applied identically in both modules,
and the tie-break removed (a 1-1 tie between two coin-flip-correlated
engines is *no information* — it should score 0, not "the louder side's
average"). Checks 6.1–6.3 quantify how often each mechanism fires.

> **IMPLEMENTED (2026-07-09):** mechanisms 1–3 are closed on this branch —
> `calculate_score()` now takes `winning_bias` from `tally_votes()` (and
> derives it via the same function when omitted), imports the conviction
> threshold + `effective_bias()` from `voting_system` instead of re-reading
> raw biases, and both modules resolve exact conviction ties to NEUTRAL /
> score 0. Both production call sites (`main.py`, `backtest_engine.py`)
> pass the vote's winner explicitly. Regression-locked by
> `tests/test_axis6_consistency.py`. Mechanism 4 (H013 ×0.5, MTF ±8)
> remains — by design until the shadow-book calibration says otherwise.

## Axis 7 — 98.4% rejection: philosophy or bug?

**Mostly bug, calibrated by the system's own evidence.** The identical
config on full-depth data (`h4_backtest_20260705`) executes on ≈15% of
evaluated decision points. Live is 1.63% — an order of magnitude below the
system's *own validated selectivity*, and the gap decomposes exactly into
the starved quorum (Axis 3/5) plus exposure saturation. The 5–15% target
proposed in the review is right — but it should be *reached by feeding the
engines*, not by loosening `min_engines_agreeing` or `min_score` while the
panel is half-mute (that would combine starved inputs with weakened gates:
the worst of both).

## Axis 8 — "No-information confluence"

**The sharpest philosophical catch in the review, and it is correct.**
Today every EXECUTE is carried by exactly 2 informative engines while 2
enabled engines sit mute — `min_engines_agreeing=2` was calibrated for a
4-voice panel and has silently degenerated into *"SMC and PriceAction both
said so"*, where those two agree ~50% of the time when both vote (backtest,
pooled). A quorum that ignores how many voters *could* vote is not measuring
confluence. Two remedies, in order:

1. Fix the starvation (headline) so the panel is actually 4-voiced.
2. Make the quorum *information-aware*: require `agree_count ≥ 2` **of the
   engines that produced a non-NEUTRAL, above-conviction output**, and
   refuse EXECUTE when informative engines < 2 (i.e., a mute panel can never
   satisfy confluence by default). `participating_weight_share` is already
   computed and stored — gate on it (e.g., ≥ 0.5 of enabled weight must be
   informative) instead of leaving it decorative.

> **IMPLEMENTED (2026-07-09):** `confluence.min_informative_weight_share`
> gate, wired identically in `main.py` and `backtest_engine.py` (new
> `info_share` gate-rejection bucket), computed by
> `voting_system.informative_weight_share()` — informative = any effective
> vote, agreeing *or* dissenting, over the weight of the engines that ran.
> Calibrated at **0.6**, not the 0.5 sketched above: with production
> weights, SMC+PriceAction alone are ≈56.6% of enabled weight, so a 0.5
> gate would not have caught the exact live failure mode. At 0.6 the
> starved panel fails with an explicit *"panel mostly mute"* fail_reason,
> any mix including a speaking NNFX passes, and NNFX-NEUTRAL (no trend)
> correctly refuses trend trades. The share is also persisted per decision
> (`confluence.informative_weight_share` in the report). Meta/regime-filter
> downgrades now write a `downgrade_reason` persisted as `fail_reason`
> (closing check 1.3), and news blackouts get the same treatment.
> Regression-locked by `tests/test_axis8_and_downgrade.py`.

Also answered here: the per-symbol confluence table (US30 71.2 vs EURUSD
47.0) does **not** mean "the system suits indices". US30/NAS100/SPX500 ride
the Yahoo-only data path and have **zero committed backtest evidence** (main
audit §8 recommends removing them from the live universe); their higher
average score is an artifact of which engines happen to fire on that data
path, not measured edge. ETHUSD-vs-BTCUSD inconsistency is Axis 6.

---

## Revised priorities (supersedes the four in the review where they conflict)

| # | Action | Replaces |
|---|---|---|
| 1 | **Fix data depth** (H1 fetch ≥ 3,000 bars or cTrader native H4/D1) — revives NNFX, Wyckoff-range detection, and the MTF gate in one change | "disable/downweight NNFX & Wyckoff" — that would codify the defect |
| 2 | **Unify the majority/threshold logic** (Axis 6 fix: one majority definition, consistent conviction threshold, no tie-break average) | "review the Confluence algorithm" — now with the three exact mechanisms |
| 3 | **Make the quorum information-aware** (Axis 8) and write a `fail_reason` on Meta downgrades (Axis 1.3) | "re-evaluate the 2-engine condition" — keep 2, count only informative voices |
| 4 | **Open-outcome hygiene** before judging the exposure cap (Axis 7.2) | "improve exposure management" |
| 5 | Re-run `scripts/philosophy_audit.py` after #1–#4 and let 2–4 weeks of full-data decisions accumulate; only then touch thresholds — and only via the shadow-book calibration the main audit specifies | — |

**The four hypotheses posed in the review, answered:**
1. *Independent engines?* At the vote level SMC/PA/NNFX are distinct (49–74%
   agreement); the true clones are in the dormant set (wyckoff–sentiment
   100%). Independence is not the live problem — starvation is.
2. *Real influence per engine?* Two of four enabled engines currently
   influence nothing, for a mechanical reason with a one-line fix; the
   backtest says no engine's influence is statistically distinguishable
   anyway.
3. *Is confluence mathematically consistent?* No — three specific
   discontinuity mechanisms, now enumerated and checkable.
4. *Conservative by intent or over-constrained?* Over-constrained by defect:
   1.63% live vs ≈15% validated for the same philosophy on full data.

*Addendum produced 2026-07-09. Automated checks: `scripts/philosophy_audit.py`
(29 checks, exit 1 on any FAIL) — wire it into the scheduler as a weekly
self-audit once credentials allow.*
