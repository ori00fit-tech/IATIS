# IATIS v2.0 — Vision Document

> **⚠️ This is a vision/roadmap document, not a specification of current
> system state.** It describes the long-term architecture IATIS is
> evolving toward. For what's actually implemented right now, see
> `/README.md`. Treating this file as "the current spec" would violate
> the project's own non-negotiable principle: *"No future phase
> functionality pretending to be complete."*

## Why this document exists separately from README.md

A detailed 14-layer architecture was proposed (Asset Profile Layer,
Session Context Engine, Memory Layer, Explainability Layer, etc.) as a
single "Master Specification v2.0." Adopting it directly as the live
spec would have created the exact problem the spec itself warns
against: layers described in full architectural detail, with concrete
example numbers (e.g. "Liquidity Sweep + London Session + HTF Bullish
→ 352 occurrences, 61% win rate"), before any of that data or logic
exists. A README that *implies* completeness this way is more dangerous
than a missing feature — it's a confident-sounding lie to whoever reads
it next, including future-us.

So: the ideas below are good and worth building. This document exists
to hold them at arm's length until each one has actually been built,
tested, and proven — at which point it gets promoted into the real
README.md and `engines/`/`research/` code, not before.

## Core philosophy (this part is already true today, not aspirational)

- IATIS is not a trading bot. It's a trading decision governance
  framework. Avoiding a bad trade is a successful output, not a
  non-event.
- The system is deterministic wherever possible.
- AI may explain and summarize. It may never invent signals, override
  risk, or modify engine outputs.

## Full proposed layer stack (target architecture)

```
DATA LAYER                    ✅ implemented (synthetic; CSV in progress)
VALIDATION LAYER               ✅ implemented
ASSET PROFILE LAYER            ❌ not started — needs real multi-asset data
SESSION CONTEXT LAYER          ❌ not started — needs precise session-time data
TIMEFRAME SYNCHRONIZATION      ✅ implemented
MARKET REGIME DETECTOR         🟡 partial — trend/range only, rest UNKNOWN by design
RESEARCH EDGE GATE             ✅ implemented
STRATEGY ENGINES               🟡 partial — SMC + Price Action only
CONTRADICTION ENGINE           ✅ implemented
CONFLUENCE COURT               ✅ implemented (re-normalized, transparent)
RISK GOVERNANCE ENGINE         ✅ implemented
DECISION LOG DATABASE          ✅ implemented (No-Trade Database)
MEMORY LAYER                   ❌ not started — needs real trade/decision history first
EXECUTION LAYER                ❌ not started — Phase 2+
AI EXPLANATION LAYER           ❌ not started — Phase 4+, explicitly explain-only
```

Legend: ✅ real and tested · 🟡 partially real, rest explicit stub · ❌ not started

## Deferred layers — why each one waits, and what unblocks it

### Asset Profile Layer
**Idea:** per-asset volatility/spread/session/risk/sizing profiles, so
the system never assumes XAUUSD behaves like EURUSD.
**Blocked on:** real multi-asset historical data. A profile built from
synthetic data would encode noise as if it were asset character —
worse than not having a profile.
**Unblocks when:** Phase 2 CSV data covers 2+ real assets with enough
history to compute real volatility/spread statistics.

### Session Context Engine
**Idea:** classify Asia/London/New York/Overlap sessions and report
session_volatility / session_confidence as context (not a trading
signal).
**Blocked on:** real timestamped data with accurate session boundaries.
Synthetic bars have no real session character.
**Unblocks when:** real intraday data (Phase 2/3) is available with
reliable timezone handling.

### Memory Layer
**Idea:** track historical setup performance, e.g. "Liquidity Sweep +
London Session + HTF Bullish → N occurrences, win rate, avg RR."
**Blocked on:** this is precisely what the research/ hypothesis-testing
loop produces — it cannot be built before real experiments generate
real results. Any number shown here today would be fabricated.
**Unblocks when:** at least one hypothesis (e.g. H001) reaches PASSED
status against real data with a real registry.json entry, and the
decision log has accumulated enough real history to query.
**Hard rule carried over from research/edge_gate.py:** the Memory Layer
must read from `research/results/registry.json` and `storage/decisions.jsonl`
only — never from hardcoded example statistics.

### Explainability Layer (as a dedicated module)
**Status:** the *principle* is already implemented today — every
`EngineOutput` carries `reasons`, every pipeline report carries a
`summary` field and `confluence.fail_reasons`, and `storage/decision_log.py`
persists full reasoning for every decision. What's deferred is only a
dedicated formatting/reporting layer (e.g. nicer human-readable report
generation) — not the underlying explainability guarantee.

### Execution Layer (Telegram / FastAPI / Cloudflare)
**Blocked on:** nothing technical — this is genuinely "next," but
intentionally sequenced after the data/research/testing work below so
we're not wiring delivery channels for a pipeline that's still
stabilizing.

### AI Explanation Layer
**Blocked on:** Phase 3 engine logic maturity. An AI explaining
SMC-swing-structure-only reasoning today wouldn't yet be explaining a
"real" institutional system — explaining a stub convincingly is itself
a form of the over-claiming this project is trying to avoid.
**Hard constraint carried forward:** AI may explain/summarize/document
only. It may never set bias, score, verdict, or risk result. This
constraint must be enforced in code (a type-level boundary, not just a
prompt instruction) when this layer is eventually built.

## Promotion rule

A layer moves from this document into the real README.md/codebase only
when:
1. It has real (not synthetic) data backing it, where relevant.
2. It has passing behavior tests, not just smoke tests.
3. Any claimed statistic or edge has a `PASSED` entry in
   `research/results/registry.json`.
4. README.md's status table is updated in the same change — the table
   and the code must never drift apart.
