# Engine REAL / PARTIAL / STUB / MISLEADINGLY_NAMED Classification

**Scope:** the 6 engines flagged as genuinely unaudited by this
session's earlier Direction Symmetry pass (`ict`, `quant`, `wyckoff`,
`divergence`, `sentiment`, `macro`) — `smc`, `price_action`, `nnfx`,
`market_structure` are the live prod4-adjacent engines, already
extensively read this session in other contexts and not repeated here.

**Method:** direct code reading of `extract_features()`/`decide()` (or
the equivalent), tied to what the module's own docstring/name claims —
not the docstring alone. Where this session already built and ran real
tests against a rebuilt engine (Quant/Divergence/Macro v2), those tests
were re-run here to confirm they still pass, rather than re-deriving
every formula from scratch. Wyckoff v1 got the same fresh, first-time
read as ICT/Sentiment (neither rebuilt this session).

## Classification

| Engine | Verdict | Basis |
|---|---|---|
| ICT | **REAL** | Genuine killzone timing (`regimes/session_context.py`, real session detection), real dealing-range premium/discount math, a legitimate Judas-swing false-breakout check, real HTF EMA trend filter. Honestly discloses in its own docstring that order blocks/FVG/market structure shift are NOT implemented ("Phase 4 will add"). One minor dead-variable note (`session_bars` computed, unused) — cosmetic, no behavior impact. |
| Quant (v2) | **REAL** | Full statistical rebuild (Confluence Engine Overhaul Phase 3a): Hurst exponent (R/S analysis), ADF stationarity (via `statsmodels`), variance ratio, autocorrelation, efficiency ratio, half-life, Shannon entropy — all genuine formulas, independently verified in the earlier lookahead audit (no lookahead, no placeholder math). 45/45 tests pass (`tests/test_indicators_quant_stats.py`, `tests/test_quant_engine_v2.py`). Disabled by default. |
| Wyckoff (v1, live-enabled) | **REAL, with one confirmed dead-feature gap** | Trading-range identification, spring/upthrust detection, and volume-based stopping-volume/climax/no-demand/no-supply are all genuinely implemented and wired into `decide()`. **Confirmed by grep**: `_effort_vs_result()` — the module's own docstring lists "Effort vs Result (price bar size vs expected direction)" as feature #4 of the usable price-only concepts — is defined but **never called anywhere** in `extract_features()` or `decide()`. This is real dead code overstated by the docstring, not a decision-correctness bug (the feature simply isn't active, doesn't produce wrong output). Already fixed in the ad-hoc-only, non-default `WyckoffEngineV2` (Track C, this session), which wires `_effort_vs_result` into a real Composite-Operator-footprint heuristic — but v1 (the version actually live in prod4) still has it dead. |
| Divergence (v2) | **REAL** | Full rebuild (Phase 3b): real ZigZag pivot detection (magnitude + spacing filtered, empirically verified causally-safe in the lookahead audit), real Wilder RSI/MACD divergence detection (Regular/Hidden/Triple/MTF-confirmed), no stale/fabricated "Killzone bonus" claim (the old, never-implemented docstring claim was removed as part of the rebuild). 95/95 combined tests pass. Disabled by default. |
| Sentiment | **REAL implementation, had a confirmed severe bug (BUG-005, fixed)** | Real COT consumption (`scripts/download_cot.py`'s weekly cron → `data/cot/{SYMBOL}.json`), real retail price-position contrarian proxy, real MarketAux news-sentiment integration (live HTTP, real API). Not a stub — genuinely computes real signals when data is available. Had a severe lookahead vulnerability (COT/MarketAux both answer with TODAY's data regardless of which historical bar is being analyzed) — found and fixed this session (BUG-005, `reports/forensic/13_CONFIRMED_BUGS.md`). Disabled by default. |
| Macro | **REAL** | Full rebuild (Phase 3c): real FRED-sourced yield curve (US10Y/US02Y), credit spread (BAA10Y), Fed balance sheet (WALCL), oil/copper/natgas alongside the pre-existing DXY/VIX/GLD/SPY risk-on/off vote system. Commodity trends correctly kept informational-only (never scored — verified by this session's own dedicated test proving two otherwise-identical feature dicts differing only in commodity direction produce identical scores). 95/95 combined tests pass (shared run with Divergence above). Disabled by default. |

## Summary

**Zero STUB or MISLEADINGLY_NAMED engines found.** Every one of the 6
engines audited genuinely computes real signals from real data using
real formulas — none is a hollow placeholder dressed up with a
convincing name. The two real findings from this pass:

1. **Wyckoff v1's dead `_effort_vs_result`** — a docstring overstatement
   (claims a feature that isn't wired in), not a bug. No fix applied to
   v1 (it's the live-enabled version; wiring in a new scoring
   contribution to a live-frozen prod4 engine would itself violate
   CLAUDE.md rule 6 without a pre-registered hypothesis) — the gap is
   already correctly addressed in the ad-hoc-only `WyckoffEngineV2`.
2. **Sentiment's BUG-005** — already found, fixed, tested, and
   documented in this same forensic pass (`13_CONFIRMED_BUGS.md`).

This result is broadly consistent with, and independently confirms,
the direction the Confluence Engine Overhaul's own plan history took
(Quant/Divergence/Macro rebuilt with real statistics this session;
Wyckoff explicitly called "the best engine currently" and extended
rather than replaced) — but this pass verified it fresh, via direct
code reading and re-running the real tests, rather than taking the
prior self-report on faith.

## Status

CLOSED for the 6 engines in this pass's scope. `smc`, `price_action`,
`nnfx`, `market_structure` were not re-classified here (already
extensively read in other contexts this session — Phase 1/2 of the
Confluence Engine Overhaul, the golden-value regression suite, and the
Direction Symmetry Audit's own manual pre-check) and are all confirmed
REAL implementations of real technical concepts, not stubs.
