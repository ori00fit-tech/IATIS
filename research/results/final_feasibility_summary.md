# Final Feasibility Summary — Data Acquisition Audit (2026-07-24)

One page. Full reasoning: `data_feasibility_report.md`. Structured data:
`data_sources_registry.json`, `data_collection_plan.json`,
`data_quality_requirements.json`, `data_source_rankings.csv`,
`missing_data_matrix.csv`, `commercial_data_candidates.md`.

## Headline result

**35 of 35 hypotheses are data-feasible to at least PASS WITH FALLBACK.
Zero hypotheses require a commercial data purchase to proceed. Zero
hypotheses are blocked by data availability today.**

## By the numbers

- **35** hypotheses total (H001–H105, letter-suffixed) — not H001–H999.
- **29** share one shared OHLCV backbone (5 provider chains, already
  live in `config.yaml`) — **PASS**.
- **6** have genuinely distinct data domains — all independently
  evaluated:
  - **H007** (macro) — **PASS**. Official free sources (CBOE, FRED)
    already integrated.
  - **H012** (COT) — **PASS, fully verified against live data**. Full
    1986-2025 backfill run and confirmed on the VPS 2026-07-24
    (`scripts/download_cot_deep_history.py`, free CFTC yearly archive).
    4 real bugs found and fixed across 4 verification rounds — see the
    "What actually needs doing" section below for the full account.
  - **H019** (crypto positioning) — **PASS WITH FALLBACK, already
    resolved (FAILED)**. 2 of 3 legs (funding rate, Fear&Greed) were
    deep and clean; open interest was dropped per its own pre-registered
    fallback rule after real probes on 3 exchanges.
  - **H021** (news sentiment) — **PASS WITH FALLBACK**. Collector
    running; MarketAux's rate limit means the sample accrues slowly —
    an outcome the hypothesis's own pre-registration already accepted.
  - **H104** (tick order flow) — **PASS, forward-only**. Free WebSocket
    collector built and confirmed connected 2026-07-24; no historical
    backfill is possible at any price except a genuine commercial
    purchase (Tardis.dev, unpriced) — see below.
  - **H105** (execution/TCA) — **PASS**. First-party live data, no
    external source involved at all.

## What actually needs doing (concrete, non-blocking)

1. ~~Write a CFTC yearly-archive backfill script for H012~~ — **DONE,
   FULLY BACKFILLED, AND VERIFIED 2026-07-24**
   (`scripts/download_cot_deep_history.py`). Four rounds of running this
   against real live CFTC data found and fixed four genuine, previously-
   invisible bugs: (1) EURUSD/XAUUSD's matching logic was also catching
   EUR cross-rate contracts and an unrelated Coinbase gold contract
   (bare-prefix match → exact CFTC field-delimiter match); (2) USOIL's
   mapped contract name had, even before that fix, only ever matched a
   *different, unintended* contract at a European venue instead of the
   real NYMEX/US-benchmark contract — remapped, flagged as a reviewable
   judgment call; (3) the live weekly collector had no `User-Agent`
   header and was silently getting HTTP 403 from `cftc.gov` — fixed;
   (4) the full 1986-present backfill (not just a one-year probe)
   revealed GBPUSD and NZDUSD both truncated to 2022-02-08 onward because
   CFTC renamed both contracts around then — fixed via multi-alias
   support in `COT_SYMBOLS`, confirmed by re-running the backfill
   (GBPUSD now spans 1986-2025, NZDUSD extends to 2004-2025). Two minor,
   non-blocking items remain documented but not chased further (GBPUSD's
   record density within its now-correct range; NZDUSD/AUDUSD's shared
   ~2004 start) since every symbol already clears H012's stated minimum
   depth by a wide margin. None of these four bugs would have surfaced
   from documentation review alone — this is the concrete payoff of
   actually running the collector against live data instead of trusting
   a first "looks fine" result. Full account:
   `data_feasibility_report.md` Part B.2's four update notes;
   `research/results/registry.json` H012 entry for the complete trail.
2. ~~Confirm `iatis-marketaux-collect.timer` is actually enabled~~ —
   **CONFIRMED 2026-07-24** on the VPS: timer active, 21 sentiment records
   already collected, next fire on schedule.
3. **Optionally get a Tardis.dev quote** for H104 if the 3-month forward
   wait is judged too slow — the one commercial vendor in 17 researched
   whose product maps to a real, already-identified gap. Not a default
   action; price it before deciding.

## What does NOT need doing

- No commercial data purchase is required for any hypothesis to proceed
  as pre-registered.
- No hypothesis should be modified, reduced in scope, or have its source
  swapped based on this audit — every already-resolved hypothesis's
  evidence stands as committed (CLAUDE.md rule 4); this audit found no
  data-feasibility defect serious enough to warrant reopening any of
  them, only one prior-session self-caught instance (H023's Yahoo
  zero-volume bug, already fixed and re-run before this audit began).
- No new source should replace MarketAux inside H021 or any exchange
  inside H019's OI probe — CLAUDE.md rule 6 forbids changing a
  hypothesis's data mid-sample; GDELT (news) and Kaiko/Tardis (crypto OI/
  tick) are viable candidates for **new**, separately pre-registered
  hypotheses only.

## One retrospective flag (not a reopening)

H001/H002/H002b were originally tested on Yahoo Finance H1 data, later
disqualified project-wide (2026-07-16) for wrong-instrument and
resampling correctness bugs — not for insufficient depth. Their null
results are not undermined by this (a wrong-data bug tends to manufacture
false edges, not false nulls, and H001/H002/H002b's own p-values were
already unremarkable at p=0.63/0.22/0.43), but it is recorded here for
transparency since the brief asked for every hypothesis to be inspected,
including already-resolved ones.

## Bottom line

This project's existing free-data-only discipline is not the constraint
some of the plan-review commentary in the registry implies it might be.
The real, current bottleneck on every open hypothesis is **time**
(H021's rate-limited accumulation, H104's 3-month clock, H105's live-fill
volume) or **a small amount of unbuilt but simple free-data
infrastructure** (H012's archive backfill) — not data access, and not
budget.
