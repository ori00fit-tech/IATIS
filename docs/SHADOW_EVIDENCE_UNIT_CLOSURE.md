# SHADOW Evidence Unit + Catastrophic Divergence Contract — Closure Record

Companion record to `docs/PHASE_15D_SHADOW_OUTCOME_DIVERGENCE_EVIDENCE.md` (the
original pre-registration, which locked `n ≥ 40` and left the `0.65` ratio
RESERVED) and `docs/PHASE_15D_CLOSURE_RECORD.md` (which built the resolver
but explicitly left the translator to Phase 13's `shadow_record` shape out
of scope). This Design Gate was opened as Path A, step ①: lock the
catastrophic-divergence *definition* as a pure governance contract, with no
promotion code written in the same phase.

**Status at the time of writing this record:**

| Property | Status |
|---|---|
| DESIGNED | YES — negotiated and locked turn-by-turn in conversation, nine agenda items (B1–B9), before any code |
| IMPLEMENTED | **NO — not attempted, by design** (see §4) |
| TESTED | N/A — nothing was implemented |
| ACCEPTED | N/A |
| WIRED | NO |
| PROVEN | NO |
| Commit | NOT AUTHORIZED at time of writing |
| Push | NOT AUTHORIZED at time of writing |

---

## 1. Files delivered

**None, besides this record.** No production code, no tests, no schema
change. `backtest/promotion_gate.py` was read but never touched — its
existing `{"completed": bool, "diverged_catastrophically": bool}` consumer
contract (and its already-coded, already-reachable LIMITED branch) remains
exactly as it was before this Design Gate opened.

This absence of a producer function is itself the locked decision, not an
oversight: a `shadow_record` producer — even one that only ever returns
`{"completed": False, "diverged_catastrophically": False}` — was explicitly
rejected as something to build now, because its mere existence would imply
a working SHADOW evidence contract that does not currently exist. See §4.

---

## 2. Locked contract (B1–B4, B9)

### 2.1 Evidence unit (B1)
One `request_id`, assessed by its *current* (latest-pairwise)
`terminality_state` from `backtest/shadow_outcome_terminality.py::assess_terminality()`.
A unit counts iff that state is `TERMINAL_CONFIRMED`.

`TERMINAL_CONFIRMED` is **not sticky** — a request can move
`TERMINAL_CONFIRMED → CONTRADICTED → TERMINAL_CONFIRMED` again across
further re-invocations (module docstring, lines 29–32: "an earlier
revision is NOT permanently disqualifying"). `classify_cross_invocation_relationship()`
(`backtest/shadow_outcome_revision.py`) is the sole authority for the
`OUTCOME_REPRODUCED`/`OUTCOME_REVISED`/`LEGITIMATE_PROGRESSION` judgment
that terminality delegates to, never re-implemented.

### 2.2 Re-evaluation discipline (B2)
`shadow_record` is a pure, **momentary** computation — recomputed in full
from caller-supplied, current observations on every invocation. No stored
snapshot of a prior evidence set is ever treated as still valid; no
eligibility at time T implies eligibility at T+1 without recomputation.
Consistent with every other module in this chain being pure and
caller-supplied, and with `terminality_state` itself being non-sticky.

### 2.3 Sample definition (B3)
```
n_T(H) = | { request_id : hypothesis_id == H
                          ∧ terminality_state(request_id, at T) == TERMINAL_CONFIRMED } |
```
Pooled across **all symbols belonging to the hypothesis** — not per-symbol.
`cross_symbol_confirmed` in `promotion_gate.py` is a separate, independent
condition; `n ≥ 40` must not be silently reinterpreted as "40 per symbol."

`TIMEOUT` / `DATA_GAP` / `NOT_YET_ASSESSABLE` outcomes can **never** enter
`n_T` — structurally, not by policy choice: `assess_terminality()` requires
`walk_ohlc is not None` for `verification_attempted` to be true at all, and
`walk_ohlc` is never captured for non-causal outcomes
(`shadow_outcome_revision.py` docstring, lines 29–31: "TIMEOUT... never
gets `walk_ohlc` captured at all"). Such requests are permanently
`NOT_YET_ASSESSABLE` with no path to `TERMINAL_CONFIRMED`.

### 2.4 `completed` (B3, final form)
```
completed(H, T) = ( n_T(H) ≥ 40 )  AND  ( divergence_statistic_valid )
```
Both conjuncts are required. Sample-size sufficiency and statistic
validity are independent facts; `n_T(H) ≥ 40` alone must never be read as
"ready for LIMITED."

### 2.5 `diverged_catastrophically` (B9)
Never defaults to `False` as a claim of "no catastrophic divergence." When
`completed` is `False`, this field is inert — `promotion_gate.py`'s
`if not shadow_record.get("completed"): ... elif shadow_record.get("diverged_catastrophically"): ...`
never reaches the `elif` once `completed` is `False`. Any value placed
there in that state is a structural placeholder, not a governance claim.

### 2.6 Current resolved value
Under current architecture, `divergence_statistic_valid` is **always
`False`** (see §3) — therefore `completed(H, T)` is always `False`,
regardless of `n_T(H)`, for every hypothesis, right now. LIMITED stays
fail-closed for a documented, real reason, not a fabricated one.

---

## 3. The blocking finding (B4A) — why no comparable statistic exists today

`docs/PHASE_15D_SHADOW_OUTCOME_DIVERGENCE_EVIDENCE.md` §9 proposed
`win_rate = TP_HIT_count / (TP_HIT_count + SL_HIT_count + TIMEOUT_count)` as
the comparison statistic, with `n ≥ 40` locked and `0.65` left RESERVED.
Direct source verification (read-only, this Design Gate) found that
**neither side of the comparison can actually be computed that way**:

- **Observed side** (§2.3 above): `TIMEOUT` can never reach
  `TERMINAL_CONFIRMED`, so it can never appear in `n_T` at all — not "a
  small share," literally zero, always.
- **Baseline side** (`backtest/metrics.py::calculate_metrics()`, lines
  219–226): `win_rate = winning_trades / total_trades`, where
  `total_trades = len(closed)` (every trade with a non-`None` exit_price,
  regardless of `exit_reason`), and `is_win` (set in
  `backtesting/backtest_engine.py:468`) is `trade.pnl_usd > 0` — a
  **profitability** classification, not an exit-mechanism classification.
  A `TIMEOUT` trade that happened to be profitable at forced closure counts
  as a win in the baseline; its SHADOW counterpart never enters the sample
  under any outcome.

These are two different metrics by construction, not the same metric with
a parameter difference. Three repair paths were identified and all three
rejected for *this* Design Gate (none foreclosed permanently — see §5):

1. **Outcome-equivalent population** — filter the baseline to
   `TP_HIT`/`SL_HIT`-only trades. **Not constructible from persisted data**:
   `storage/research_missions.py`'s `research_mission_trials_v2` stores
   only an integer `trades` count and the aggregate `metrics_json` — no
   per-trade `exit_reason` breakdown survives anywhere past the in-memory
   `TradeRecord` list inside a single `run_backtest()` call. Building this
   requires either re-running backtests or a schema/`metrics.py` change —
   both out of this gate's scope.
2. **Common financial closure population** — redefine "closed" symmetrically
   on both sides. Structurally blocked already by §2.3: SHADOW cannot
   represent `TIMEOUT` closure at all without changing `walk_ohlc` capture
   in `backtest/shadow_outcome_resolver.py`, itself a documented, deferred,
   out-of-scope fix (`shadow_outcome_revision.py` docstring: "OBSERVED,
   DEFERRED, NOT FIXED HERE").
3. **Reject the comparison** — do not treat `win_rate` as this contract's
   divergence statistic at all. **Selected.** No existing or newly-invented
   replacement statistic was designed in this gate — inventing one after
   seeing this result would be exactly the "choose the metric after seeing
   the data" failure mode CLAUDE.md's pre-registration discipline exists to
   prevent.

---

## 4. What this Design Gate proved, and what it explicitly did not

**Proved:**
- Phase 13's `shadow_record` consumer contract and its already-coded
  LIMITED branch are mechanically reachable in principle (confirmed via
  `tests/test_promotion_gate.py`'s `_SHADOW_OK` fixture) — nothing on the
  consumer side needs building.
- A complete, internally consistent evidence-unit and sample-size contract
  can be defined using only already-existing, pure building blocks
  (`assess_terminality()`, `classify_cross_invocation_relationship()`),
  with no new storage, no new schema, no wiring.
- The one statistic §9 ever proposed (`win_rate`) is **not** computable
  identically on both sides of the comparison under the current
  architecture — a precise, source-verified finding, not a suspicion.

**Explicitly NOT proved or built:**
- No `shadow_record` producer function exists, in any form, including a
  trivial constant one — deliberately, per §1.
- No replacement divergence statistic was designed or chosen.
- The `0.65` ratio (or any alternative threshold) remains exactly as
  RESERVED as `docs/PHASE_15D_CLOSURE_RECORD.md` left it — now additionally
  blocked by the absence of any statistic to apply a threshold to.
- No TRUE/FALSE/NOT_YET_ASSESSABLE state machine for
  `diverged_catastrophically` was designed — moot until a statistic exists.
- `backtest/promotion_gate.py` was not touched and remains fully
  independent of this gate's findings.

---

## 5. Next phase boundary (not started, not designed here)

Explicitly deferred, each its own future Design Gate:

1. **SHADOW COMPARABLE STATISTIC DESIGN** — the immediate next step per
   Path A: design a divergence statistic (new, or one of the two repair
   paths below) whose population and semantics are provably identical on
   both the observed (SHADOW) and baseline (backtest) sides.
2. **Outcome-equivalent population** (§3, path 1) — would require extending
   `backtest/metrics.py`/`backtest/mission_runner.py` to persist a
   per-exit-reason trade breakdown, not just the aggregate `win_rate`.
3. **Common financial closure population** (§3, path 2) — would require
   revisiting `walk_ohlc`/composition capture in
   `backtest/shadow_outcome_resolver.py` for non-causal (`TIMEOUT`/`DATA_GAP`)
   outcomes — itself a materially bigger governance question than an
   additive classifier, per that module's own existing deferral.
4. The `0.65` threshold's meaning (or a replacement) — cannot be
   meaningfully opened until (1) produces a valid statistic.
5. The actual `shadow_record` producer implementation — blocked until (1)
   and (4) both close.

Commit/push for this record is a separate decision, to be made after it is
reviewed.
