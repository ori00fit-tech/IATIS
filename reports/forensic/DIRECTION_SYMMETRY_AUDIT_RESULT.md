# Direction Symmetry Audit — First Real Run Result

**Tool:** `research/diagnostics/direction_symmetry.py` (Forensic System Audit
Phase 1, item B). Static, AST-based, advisory-only — see the module's own
docstring for the full caveat.

**Run:** `python3 -m research.diagnostics.direction_symmetry`, same
scan the `GET /research/diagnostics/direction-symmetry` endpoint and the
Mission Center panel perform live.

## Result

**27 files scanned, 0 findings.**

```
engines/base_engine.py, divergence_engine.py, ict_engine.py, macro_engine.py,
market_structure_engine.py, nnfx_engine.py, price_action_engine.py,
price_action_engine_v2.py, quant_engine.py, sentiment_engine.py, smc_engine.py,
wyckoff_engine.py, wyckoff_engine_v2.py
confluence/context_filters.py, contradiction_engine.py,
crypto_positioning_modulator.py, indicator_filters.py, meta_decision.py,
mtf_confirmation.py, regime_weights.py, reversal_veto.py,
score_calculator.py, voting_system.py
risk/correlation_engine.py, live_portfolio_state.py, portfolio_exposure.py,
risk_engine.py
```

Zero `MISSING_MIRROR` (a function referencing only one side of a BULLISH/
BEARISH or BUY/SELL pair) and zero `ASYMMETRIC_CONSTANT` (mirrored if/elif
branches assigning visibly different score magnitudes to the same variable)
findings anywhere in the scanned set.

## This closes the coverage gap the earlier manual audit flagged

The session's earlier manual, direct-quote symmetry check (before this tool
existed) covered `backtest_engine.py`'s entry/SL/TP/close paths,
`confluence/voting_system.py`'s `tally_votes()`, and 3 sampled engines
(price_action, nnfx, smc/market_structure) — and explicitly flagged 6
engines as genuinely unaudited: **ict, quant, wyckoff, divergence,
sentiment, macro**. All 6 are now covered by this tool's real run, with
zero findings.

**Sanity check that the scanner is genuinely reading these files, not
silently skipping them**: `ict_engine.py`, `sentiment_engine.py`, and
`wyckoff_engine.py` each contain 6-8 real occurrences of `BULLISH`/
`BEARISH`/`"BUY"`/`"SELL"` tokens (confirmed by grep), so the zero-finding
result reflects the scanner actually parsing real directional logic in
these files, not an empty/no-op scan.

## Context on why this result is plausible, not just "clean by luck"

- `divergence_engine.py`, `quant_engine.py`, `macro_engine.py` were fully
  rebuilt earlier this session (Confluence Engine Overhaul Phase 3a/3b/3c)
  onto a consistent `extract_features()`/`decide()` pattern with mirrored
  vote-and-classify logic — symmetric by construction, not by accident.
- `wyckoff_engine.py`/`wyckoff_engine_v2.py` were the subject of
  extensive testing this session (accumulation AND distribution schematics
  built and asserted symmetric end-to-end).
- `ict_engine.py`/`sentiment_engine.py` were NOT touched or rebuilt this
  session — this is the first real evidence of their symmetry, not a
  restatement of already-known work.

## Caveat (unchanged from the tool's own docstring)

This is a **static, heuristic** check — it proves the absence of two
specific code-shaped asymmetry patterns, not the absence of every possible
directional bug. A genuinely subtle asymmetry (e.g. two DIFFERENT
thresholds compared against the same directional signal via unrelated
variable names, or an asymmetry expressed through data rather than a
literal constant) would not be caught by this heuristic. This result is a
LEAD supporting "no obvious code-level asymmetry," not a certificate of
correctness — it complements, and does not replace, `backtest/
meta_analysis.py`'s separate statistical BUY-vs-SELL outcome comparison
(which checks the system's *measured behavior*, not its code shape).

## Status

CLOSED (no findings) for the 6 previously-unaudited engines listed in the
Forensic Audit Phase 1 plan's item B scope. No fix needed. This report
exists so a future audit pass doesn't have to re-derive that this check
was actually run, with what result.
