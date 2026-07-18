# Hypothesis H023 — Wyckoff volume gating by asset class

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
`PLANNED`

## Linked experiment
`experiments/H023_wyckoff_volume_gating.py` (to be written — a two-arm runner
toggling only the Wyckoff FX volume mode on identical data)

## Linked result
`results/H023_result.json`
