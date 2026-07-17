# H022 — FX-cross universe expansion (USDCNH, GBPAUD, EURAUD)

**Status:** PLANNED · **Registered:** 2026-07-17 (rule written before any
deep-history result exists — CLAUDE.md rule 1)

## Claim

The frozen H4 confluence strategy (prod4 engines, untouched thresholds)
carries a cost-inclusive edge on three liquid FX crosses it does not
currently trade: **USDCNH, GBPAUD, EURAUD**.

## Where the candidates came from — and why only these three

The 2026-07-17 IC Markets full-universe re-cost sweep: for the first time
every candidate paid a **real measured spread** (`get_spot_by_name`,
spread timestamped). Of 60 nominal winners:

- **The alt-coin block disqualified itself on methodology.** Re-fetching a
  fresh 1200-bar window one day apart swung alt PFs by more than ±1.0 at
  n≈25–35 — ENAUSD 1.92→3.20 and AVXUSD 2.37→3.58 *despite adding cost*,
  which is impossible on identical data. Rankings that unstable are window
  noise, not signal. (They also mostly lack the ≥3y history the house
  standard needs.)
- **Three liquid crosses stayed stable across both runs, with tight
  measured spreads:**

| symbol | PF (costless, 07-17a) | PF (real spread, 07-17b) | spread | n |
|---|---|---|---|---|
| USDCNH | 1.797 | 1.787 | 2.3 pips | 30 |
| GBPAUD | 1.592 | 1.578 | 0.9 pips | 25 |
| EURAUD | — (in winners list) | 1.416 | 0.7 pips | 29 |

AUDJPY (1.536 @ 0.5p) and XAGUSD (1.555 @ 3.6p) also repeated — they are
already enabled, so they count as confirmations, not candidates.

**These screening PFs carry zero adoption weight.** They are in-sample,
short-window (~200 days), small-n. The 07-06 sweep lesson stands: the
deep chronological OOS split makes the call, never the screen.

## Method (H008c house standard)

Per symbol, independently:
1. Download the deepest available H4 history (broker trendbars and/or
   Twelve Data; ≥5y where it exists).
2. Run the **frozen** production strategy — no tuning, no threshold
   changes — with the measured real spread as commission.
3. Chronological TRAIN(65%)/TEST(35%) split; only TEST counts.
4. Yearly-stability read on TEST years (h4_yearly_stability style).

Nothing in the live config changes while this runs.

## Decision rule (pre-registered)

PER SYMBOL: **ADOPT-TO-DEMO** only if BOTH hold:
1. TEST-slice PF ≥ 1.2 with n ≥ 40 closed trades at real measured spread;
2. no TEST year's PF < 0.9.

Either fails → **REJECT**, documented in the ledger. History too short
for n ≥ 40 on TEST → **INSUFFICIENT DATA**, no enable.

ADOPT-TO-DEMO means: `enabled: true` in `config/symbols.yaml` with its own
outcome bucket. These trades **do not count toward D001 (FX) or D002
(carriers)** — those buckets' symbol sets stay frozen as registered. A new
rule **D003** covering adopted crosses may be registered at enable time,
before any of their outcomes exist.

## Out of scope

Alt-coin candidates from the sweep — excluded until one shows ≥3y history
AND cross-window rank stability. Any threshold/engine change — separate
hypotheses, never this one.

---

## Result (2026-07-17) — RESOLVED, no symbol enabled

Manifest: `research/results/h022_fx_cross_oos_20260717_manifest.json`
(reproducible, clean tree). The pre-registered rule was applied literally
by `research/experiments/H022_fx_cross_oos.py`.

| symbol | TRAIN PF (n) | TEST PF (n) | worst TEST year | verdict |
|---|---|---|---|---|
| USDCNH | 1.298 (128) | **1.926 (18)** | 2025: 1.054 | **INSUFFICIENT_DATA** — n=18 < 40 |
| GBPAUD | 0.802 (151) | 0.825 (80) | **2025: 0.337** | **REJECT** |
| EURAUD | 0.758 (126) | 0.887 (70) | **2025: 0.424** | **REJECT** |

**The screen-vs-OOS autopsy** — each candidate's ~200-day screen PF was,
almost to the decimal, its lucky 2026 TEST-year patch:

| symbol | screen PF (07-17) | TEST-2026 patch PF | 6.5y verdict |
|---|---|---|---|
| GBPAUD | 1.578 | 1.42 | loses both slices |
| EURAUD | 1.416 | 1.612 | loses both slices |

**Lesson (third confirmation of the H008b/H015 mirage class, now at the
symbol-universe level):** a short screen sits on whatever patch it
samples. The deep chronological split — applied by a rule written before
the data existed — did the refusing.

**USDCNH, honestly:** the one candidate profitable on BOTH chronological
slices at real cost. It signals rarely (managed float, low volatility —
18 TEST trades in 2.2 years), so it cannot clear the registered n≥40 bar
and is NOT enabled. Any future revisit needs its own pre-registered
entry; this one is closed.

`config/symbols.yaml` untouched · D001/D002 untouched · no D003 registered.
