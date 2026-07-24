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
  - **H012** (COT) — **PASS**. Live/forward data is real; the deep-history
    backfill script (`scripts/download_cot_deep_history.py`, free CFTC
    yearly archive) was built 2026-07-24, pending a one-time `--probe`
    verification run on the VPS (sandbox has no egress to `cftc.gov`).
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

1. ~~Write a CFTC yearly-archive backfill script for H012~~ — **DONE AND
   PROBED 2026-07-24** (`scripts/download_cot_deep_history.py`). The
   `--probe` run against the real VPS network caught a genuine bug:
   EURUSD returned 120 rows/year instead of ~52 because the shared
   contract-matching logic (used by BOTH this script and the already-live
   weekly collector) was also catching CFTC's EUR cross-rate contracts
   and an unrelated Coinbase gold contract. **Fixed same day** (exact
   CFTC field-delimiter match instead of a bare prefix), 3 regression
   tests added reproducing the real market names found. The production
   `data/cot/*.json` cache should be rebuilt fresh after the fix deploys.
   Separately unresolved: NZDUSD isn't present under its mapped name in
   the 2025 archive at all — flagged, not a matching bug, not investigated
   further. This is exactly the kind of silent data-corruption class this
   audit exists to catch, and it would not have surfaced without the
   live VPS probe — see `data_feasibility_report.md` Part B.2's second
   update for the full account.
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
