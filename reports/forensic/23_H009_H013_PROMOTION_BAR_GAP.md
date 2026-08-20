# H009 / H013 — Why They Fail `research/edge_gate.py::PROMOTION_CRITERIA`

**Scope note (Data Integrity Core, Slice 2 — Fingerprint Binding).** Per the
operator's explicit instruction, this is a **read-only diagnostic report**.
Nothing here modifies `research/results/registry.json` — no status change,
no reclassification, no deletion. Registry hygiene (what to actually do
about H009/H013) is a separate, human-made decision. This report exists so
that decision can be made from an accurate, itemized picture instead of
re-deriving it from scratch each time.

`audit_passed_hypotheses()` already flags both entries at every boot
(non-fatal). This report is the itemized "why," checked against the code
that defines the bar, not just against the registry's own prose notes.

## The bar (verbatim from `research/edge_gate.py::PROMOTION_CRITERIA`)

```python
PROMOTION_CRITERIA = {
    "min_trades": 300,          # ev["oos_trades"] must be >= this
    "min_oos_pf": 1.2,          # ev["oos_pf"] must be >= this
    "require_walk_forward": True,   # ev["walk_forward"] must be truthy
    "require_monte_carlo": True,    # ev["monte_carlo"] must be truthy
}
```

`_promotion_criteria_unmet(h)` reads exactly one place: `h["evidence"]`
(a dict with keys `oos_trades`, `oos_pf`, `walk_forward`, `monte_carlo`).
Nothing else in the registry entry — `result`, `addendum_*`, `notes`,
`status_caveat` — is consulted by the gate. This is the single fact that
explains both entries' failures below: each has real, honestly-recorded
data elsewhere in its entry, but none of it lives in the one place the
gate actually reads.

## H009 — "IATIS 6-engine confluence as systematic trading signal"

**Status in registry:** `PASSED` (registry's own `notes` field already
states it fails `PROMOTION_CRITERIA` and should be read as flagged).

**`evidence` block:** **absent entirely.** The entry stores its results
under `result` (`{symbols: 6, windows: 18, pass_rate: "18/18", min_pf: 1.5,
avg_pf: 3.08}`) and a separate `addendum_2026-07-13` block — neither key is
named `evidence`, so `_promotion_criteria_unmet()` reads an empty dict for
every field:

| Criterion | Required | What the gate sees | Real data elsewhere in the entry |
|---|---|---|---|
| `oos_trades >= 300` | 300 | `missing` (→ `0`) | `result.windows = 18`, not a trade count at all — the entry never records a summed OOS trade count anywhere |
| `oos_pf >= 1.2` | 1.2 | `missing` (→ `0`) | `result.avg_pf = 3.08`, `result.min_pf = 1.50` — real numbers, just under `result`, not `evidence` |
| `walk_forward` truthy | required | `missing` | The whole entry IS a walk-forward result (18 windows, 6 symbols) — but no `evidence.walk_forward` key exists to say so |
| `monte_carlo` truthy | required | `missing` | Not run for this entry at all — genuinely absent, not just misfiled |

**Root cause:** a schema mismatch, not a data quality problem. H009 stores
real results in a `result`/`addendum_*` shape that predates the `evidence`
schema `PROMOTION_CRITERIA` was written against (2026-07-09, per
`edge_gate.py`'s own comment) — H009 was last updated 2026-06-25/07-13,
before that schema existed.

**Secondary problem, independent of the schema mismatch** (already
correctly self-flagged in the registry's own `notes` and the
`addendum_2026-07-13.fx_regression_flagged` block): even if the `result`/
`addendum` numbers were copied verbatim into an `evidence` block, they
would still not cleanly clear the bar as *current* evidence — the
`addendum_2026-07-13` re-run under the frozen production config shows the
7 tested FX pairs at PF 0.907–1.008 (below breakeven), a real regression
against the numbers `docs/STRATEGY_EVIDENCE_2026-07.md` published, with
its root cause explicitly logged as **unexplained** (H020's own A/B
ruled out the one config-diff hypothesis that was tested). Carriers
(BTCUSD/ETHUSD/XAUUSD) still show PF 1.31–1.47 at n=290–347 in that same
addendum. So H009's true current state is carrier-only, not "6-engine
confluence" broadly — which is exactly what CLAUDE.md's own "what this
system is" paragraph already states as the measured edge.

## H013 — "Reversal Engine Group Agreement as Counter-Signal"

**Status in registry:** `PASSED` (registry's own `status_caveat` field
already states this is deliberate — n=1 does not justify a live veto and
the entry is meant to stay flagged until properly measured).

**`evidence` block:** present, but structurally a single case, not a
sample:

| Criterion | Required | What the gate sees | Real data elsewhere in the entry |
|---|---|---|---|
| `oos_trades >= 300` | 300 | `evidence` has no `oos_trades` key → `0` | `evidence.case_1` describes exactly 1 observed instance (2026-06-26, ETHUSD+BTCUSD, "5 trades hit SL") — the entry's own richest number is 5, not 300 |
| `oos_pf >= 1.2` | 1.2 | `evidence` has no `oos_pf` key → `0` | No PF is computed anywhere in the entry — `case_1.trade_result` is a plain-text "loss (5 trades hit SL)", not a ratio |
| `walk_forward` truthy | required | `missing` | Never run — this hypothesis has never been walk-forward tested at all |
| `monte_carlo` truthy | required | `missing` | Never run |

**Root cause:** genuinely insufficient evidence, not a schema mismatch —
unlike H009, H013's own `evidence` key exists and is read correctly by the
gate; it simply doesn't contain enough (n=1, no PF, no OOS test) to clear
any of the four criteria. The registry's `status_caveat` already says this
plainly and explains why it was marked `PASSED` anyway: the underlying
mechanism (`confluence/reversal_veto.py`) has been live in production
since 2026-06-27, and the label was corrected from a stale `RESEARCH` to
reflect that live-deployment fact — not to claim the *hypothesis* (as
opposed to the *mechanism*) has been validated.

## Summary

| | H009 | H013 |
|---|---|---|
| Failure type | Schema mismatch — real results exist, stored under `result`/`addendum_*`, never mirrored into `evidence` | Genuine evidence gap — n=1 observed case, no PF, no OOS/walk-forward/Monte Carlo test |
| Real underlying data quality | Carriers (BTC/ETH/XAU) still real and strong (PF 1.31–1.47, n=290–347); FX materially regressed and unexplained | One anecdote; mechanism live in prod since 2026-06-27, hypothesis itself untested |
| What would close the gap | Populate a real `evidence` block from the already-computed `result`/`addendum` numbers, AND resolve the open FX-regression question before trusting a fresh `oos_pf` | Actually run this as a pre-registered, OOS-tested hypothesis — n≥300, a real `oos_pf`, walk-forward, Monte Carlo — before the `evidence` block can hold anything but the current n=1 |
| Registry action taken by this report | **None** — read-only, per the operator's explicit instruction | **None** |

Both entries already self-document their own gap honestly (`notes` /
`status_caveat` fields) — this report exists to make that gap itemized and
checkable against the actual gate code, not to add a new finding neither
entry already knew about. Any decision to re-label, backfill an `evidence`
block, or re-run either hypothesis is a separate, human-made research
decision, per CLAUDE.md's rule that registry hygiene is distinct from code
hardening.
