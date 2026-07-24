# Hypothesis H023 — Wyckoff volume gating by asset class

**Status:** NULL · **Resolved:** 2026-07-24

## ID
H023

## Title
After the cTrader migration gave FX bars nonzero tick-volume, does forcing FX
back to price-only Wyckoff (vs the now-active tick-volume mode) change the
system's out-of-sample edge?

## Statement
`wyckoff_engine._volume_analysis` degrades to price-only Wyckoff only when
`window["volume"].sum() == 0`. Under the old Twelve Data Free feed, FX volume
was 0, so FX correctly ran the engine's *intended* price-only mode. cTrader —
now the primary provider for fx/metals/energy/indices — returns nonzero
tick-volume in the trendbar `v` field, so that gate no longer trips for FX and
Wyckoff now runs full volume-spread analysis on FX **tick-volume**, which the
engine's own docstring says it should not ("For FOREX: price-only Wyckoff …
only tick volume proxy").

Claim to test: gating Wyckoff's volume analysis by asset class (FX → price-only
regardless of nonzero tick-volume) changes the full pipeline's out-of-sample
profit factor on FX by a material, robust amount.

## Why this might be true
Tick-volume is a count of price updates, not traded size; on FX it correlates
with volatility more than with genuine accumulation/distribution. Feeding it
into Wyckoff's stopping-volume / climax / no-demand tests could inject
volatility-driven noise into an engine designed to read real volume — or it
could be immaterial, since Wyckoff carries only 0.0707 of the confluence weight.

## Data required
- Symbols: FX arms — EURUSD, GBPUSD, USDJPY, USDCHF, EURJPY, GBPJPY, AUDJPY.
  Controls (unchanged) — XAUUSD, XAGUSD, BTCUSD, ETHUSD.
- Timeframe: H4 decision TF (+ D1 for the MTF gate), production pipeline.
- Date range: deepest available H4 history; chronological TRAIN(65%)/TEST(35%).
- Minimum sample size (stated before running): pooled FX TEST n ≥ 100 closed
  trades at real measured spread.

## Falsification criteria
Decided before looking at any result. ADOPT the asset-class gate (arm B) only if
ALL hold on the TEST slice:
1. pooled FX `PF(B) − PF(A) ≥ 0.10`,
2. pooled FX TEST `n ≥ 100`,
3. per-symbol PF change ≥ 0 in ≥ 5 of the 7 FX symbols,
4. controls not degraded: metals+crypto pooled TEST `PF(B) ≥ PF(A) − 0.05`.

Any failure → **FAILED / NO CHANGE** (current tick-volume behavior stays,
logged in the rejected ledger). `|PF(B) − PF(A)| < 0.10` is a valid **NULL**
result — the silent change is immaterial at system level.

REGARDLESS of the OOS verdict this is measurement only: no live code change
until the forward-demo evidence counter reaches its milestone (CLAUDE.md
rule 6). H023 is **FROZEN** like H018 and can never reset the prospective
counter mid-sample.

## Status
`NULL`

## Result (2026-07-24, VPS — real cTrader-sourced FX volume)

A first attempt (2026-07-23) used FX history from `scripts/download_all_symbols.py`
(Yahoo Finance), which reports **zero volume for every FX pair** — confirmed
directly (`load_from_csv(...)['volume'].describe()` → max 0.0). That made
arm A and arm B identical by construction (both already price-only); the
`dPF=0.0` on all 7 symbols was a broken test, not a finding, and was
discarded (see `registry.json`'s `data_source_caveat`).

`scripts/download_ctrader_fx_history.py` was built to fetch real cTrader
trendbars instead (confirmed nonzero: EURUSD probe showed mean=3844,
min=71, max=14972). Re-run on that data:

| | arm A (tick-volume) | arm B (price-only) |
|---|---|---|
| pooled FX TEST PF | 0.981 | 0.981 |
| pooled FX TEST n | 1089 | 1089 |
| controls (metals+crypto) PF | 1.35 | 1.35 |

`dPF = 0.0` on all 7 FX symbols individually. Applying the pre-registered
rule: condition 1 (`dPF ≥ 0.10`) fails, so not ADOPT; `|dPF| < 0.10` with
controls unaffected → **NULL**, exactly the outcome the notes anticipated
given Wyckoff's small (0.0707) confluence weight.

This time the null is mechanistically explicable rather than a data
artifact: `engines/wyckoff_engine.py:228-250` shows volume analysis only
*adds score* on top of an already price-determined bias (spring/upthrust
or range position, lines 190-226) — it never flips the vote itself. The
maximum possible swing on the weighted confluence score from Wyckoff's
volume bonus is roughly `20 × 0.0707 ≈ 1.4` points, apparently never
enough to cross `min_score_to_trade` or flip `agree_count`/`winning_bias`
across 1089 real trades.

**Conclusion:** the cTrader-migration-induced silent behavior change (FX
Wyckoff running full volume analysis instead of its intended price-only
mode) is immaterial at system level. No live/config change — current
tick-volume behavior stays as-is; there was nothing to revert either way.

## Linked experiment
`research/experiments/H023_wyckoff_volume_gating.py`

## Linked result
`research/results/H023_wyckoff_volume_gating.json`
