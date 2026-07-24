# Hypothesis H037 — Decision delay (enter N bars after the signal)

## ID
H037

## Title
Delaying entry by N ∈ {1, 2, 3} H4 bars after an EXECUTE signal — same
signals, same stop distance, same RR geometry, only the entry timestamp
and price move — changes the pooled out-of-sample profit factor of the
frozen prod4 pipeline by a material, robust amount.

## Statement
Operator-proposed (2026-07-21 catalog, c-H059): "the best trades come
after waiting a certain number of candles after the signal appears."
Formalized: let arm A be the current system (entry at the signal bar's
computed entry, simulation from the next bar). Arm B(N) takes the SAME
decision set and, for each signal at bar `i`, enters at the close of bar
`i+N` with the trade geometry re-anchored to the new entry:

- `sl_dist` and `tp_dist` are the ORIGINAL distances computed at signal
  time (ATR-based, frozen system) — only the anchor moves:
  `SL = entry_new − dir·sl_dist`, `TP = entry_new + dir·tp_dist`.
- Simulation starts at bar `i+N+1`, same no-overlap rule as arm A: a
  signal whose delayed entry bar falls inside the previous trade's
  occupancy is dropped (that loss is what the retention guard measures).
- No invalidation filter, no momentum condition, no re-check at the
  delayed bar — ONE mechanism (the wait), so the verdict is attributable.

Re-anchoring (not keeping absolute levels) is the registered choice: it
isolates "does waiting buy a better entry?" from "same trade, different
fill", and keeps risk geometry identical across arms.

## Why this might be true (and the honest reason it might not)
Mechanism if real: H4 signals often fire mid-impulse; a 1–3 bar wait
(4–12 hours) lets the post-signal pullback provide a better anchor, and
filters signals that die immediately (their delayed entry is never
reached profitably — though we deliberately do NOT add that filter;
the delay alone must carry the effect).

**Prior honestly against it:** this is an entry-timing tweak, first
cousin of the managed-exits family whose OOS value measured ≈ 0, and
H033 just showed decision-time context carries no outcome information —
if *which* trades win is unpredictable, *shifting all entries uniformly*
must work through price mechanics alone (systematic pullback), which the
trend-following literature does not robustly support at H4. Expected
outcome is NULL or worse; it runs because it is cheap (no new pipeline
pass beyond signal capture) and the mechanism is genuinely distinct from
everything measured so far.

## Distinct from prior kills
- **Not managed exits (dead):** exits untouched — SL/TP distances and the
  simulation rule are byte-identical; only the entry anchor shifts.
- **Not an entry pattern (H008 family, dead):** no new pattern, no new
  condition; the signal set is exactly arm A's.
- **Not a gate (H024/H033):** nothing is skipped by intent; trades drop
  only via the mechanical no-overlap rule, and guard 2 punishes that.
- **Not threshold/weight tuning:** min_score, quorum, weights, engines
  untouched in all arms.

## Pre-registered parameters (frozen NOW)
- Delays tested: N ∈ {1, 2, 3}. Nothing else, ever, under this ID.
- Entry price for delayed arms: the CLOSE of bar `i+N` (closed-bar
  discipline; no intrabar fills).
- Same data discovery, universe, step, warmup, TRAIN(65%)/TEST(35%)
  split arithmetic, and no-overlap rule as H024/H033
  (`data/{SYM}_H1_{2y|5y}.csv`, ACTIVE_SYMBOLS, step 8, warmup 220).
- Signals are captured in ONE pipeline pass (arm A's decision set) and
  persisted; delay arms are pure geometry replays on the same bars.
  A delay-0 replay MUST reproduce arm A's trades exactly (built-in
  harness check; a mismatch invalidates the run).

## Falsification criteria
Decided before any result exists. Verdict on the pooled TEST slice.

**ADOPT delay N\* (the smallest N passing) ONLY if ALL hold for that N:**
1. pooled `PF(B_N) − PF(A) ≥ 0.15`;
2. arm B(N) retains ≥ **80%** of arm A's TEST trade count (stricter than
   the gate hypotheses' 50%: the mechanism is unconditional, so heavy
   trade loss means the delay is destroying the book, not timing it);
3. improvement in ≥ 60% of symbols individually (H015 cherry-pick guard);
4. carriers (XAUUSD/BTCUSD/ETHUSD) pooled `PF(B_N) ≥ PF(A) − 0.05`;
5. **family-consistency guard (anti-cherry-pick across N):** pooled
   `dPF > 0` for at least 2 of the 3 delays. One N passing while its
   neighbors degrade is treated as noise, not signal.

Any failure → **FAILED / NO CHANGE**. All three `|dPF| < 0.15` with
retention held → **NULL** (entry timing is immaterial at H4 — itself a
useful, committable fact). Pre-registered minimum: pooled arm-A TEST
`n ≥ 300` closed trades.

## Data required
Same as H024/H033: full ACTIVE_SYMBOLS universe on the H1 CSVs, H4+D1
frozen pipeline via `run_pipeline`, real measured spread via the house
`calc_pnl`. No new data.

## Live-safety (non-negotiable)
Measurement only. No feature flag is created unless the verdict is ADOPT
(and even then, nothing live changes until the forward-demo milestone —
CLAUDE.md rule 6; an adopted delay would reset the prospective counter
and therefore waits for it like H018). FROZEN like every open hypothesis.

## Status
`NULL`

## Result (2026-07-24, VPS run)
Validity check passed on every symbol (delay-0 replay reproduced arm A's
captured trades exactly). Arm A pooled TEST PF=1.12, n=1187 — identical
to H024's and H033's arm-A baseline on the same shared harness, a third
independent confirmation of harness fidelity.

| Delay | Test PF | n | dPF | Retention | Symbol win frac | Carriers PF |
|---|---|---|---|---|---|---|
| B(1) | 1.083 | 1060 | −0.037 | 0.893 | 0.579 | 1.230 |
| B(2) | 1.041 | 1029 | −0.079 | 0.867 | 0.263 | 1.188 |
| B(3) | 1.027 | 1006 | −0.093 | 0.848 | 0.316 | 1.085 |

Applying the pre-registered rule literally: no delay reaches dPF ≥ +0.15
(all three are negative), so none qualifies for ADOPT. All three
|dPF| < 0.15 with retention ≥ 80% held on every delay → **NULL**: entry
timing is immaterial at H4. Consistent with the direction predicted
before running ("expected NULL or worse") — the null was not a surprise
reframed after the fact. Note the *direction* is monotonically negative
and worsening with longer delays (−0.037 → −0.079 → −0.093), and the
symbol win fraction collapses well below the 60% cherry-pick guard at
every delay (0.579/0.263/0.316) — waiting does not "buy a better entry"
at H4; if anything the drift is mildly adverse, just not large enough to
cross the ADOPT-or-FAILED threshold in either direction beyond NULL.

Live/config unaffected: no feature flag was ever created (none is
created unless ADOPT). Regardless of verdict this was measurement only
(CLAUDE.md rule 6).

## Linked experiment
`research/experiments/H037_decision_delay.py`

## Linked result
`research/results/H037_decision_delay.json`
