# Backtest Engine — Lookahead / Leakage Line-by-Line Audit

**Scope:** `backtesting/backtest_engine.py`'s `run_backtest()` main loop and
every function it calls per bar (engine `analyze()`/`decision_frame()`,
`build_multi_timeframe_view`/`resample`, `detect_regime`,
`assess_market_quality`, `check_mtf_confirmation`, `check_reversal_veto`,
indicator/context filter evaluation), checked specifically for the 16
leakage categories requested (future OHLC leakage, `shift(-1)` misuse,
entry using unknown High/Low, SL/TP intrabar ambiguity, in-bar event
ordering, indicators computed from future data, MTF alignment leakage,
resampling leakage, session/regime classification using later info,
signal-vs-execution timestamp, warmup contamination, last-bar handling,
close-to-open/open-to-close assumptions, survivorship/data-selection
effects, SL/TP/min_rr influencing signal selection itself).

**Method:** direct code reading + live reproduction (not unit-test-only
review) — every claim below either quotes the exact code checked or shows
a real script run against real/synthetic data. Per this session's own
forensic discipline: nothing here is reported as "clean" without having
actually been read/run, and nothing is reported as a bug without a
concrete reproduction.

## Confirmed bugs — found and fixed during this pass

Three real, reproduced, fixed bugs came directly out of this review
(full CLAIM/EVIDENCE/FIX/REGRESSION-TEST detail in
`reports/forensic/13_CONFIRMED_BUGS.md`):

- **BUG-002 (P0)** — the entry bar's own SL/TP was never checked (an
  off-by-one in the exit-check loop permanently skipped a trade's own
  entry bar). Not a lookahead bug per se (no future data was used), but a
  **missed-check** bug in the same execution-simulation family — flagged
  here because it was found by this exact line-by-line pass.
- **BUG-003 (P2)** — direct side-effect of the BUG-002 fix: the
  `equity_curve` point for a same-bar-exit trade was stale (balance
  update landed one bar late).
- **BUG-004 (P0)** — commission (and, dormant, swap) was never subtracted
  from `pnl_usd` for any non-forex asset class (XAUUSD, BTCUSD, ETHUSD,
  XAGUSD, USOIL, US30, NAS100, SPX500) — a real cost-modeling bug, not a
  lookahead bug, but directly relevant to CLAUDE.md's carrier-asset PF
  claims and found in the same pass.

None of these three involve a decision being made with data that wasn't
yet available — they are execution-simulation/cost-accounting bugs, not
lookahead. The lookahead-specific checklist below is a SEPARATE pass,
done after fixing those three, specifically targeting whether the engine
ever lets a decision see data from the future.

## 1. Future OHLC leakage

**Checked:** the per-bar decision window construction,
`backtest_engine.py` main loop:
```python
for i in range(config.warmup_bars, len(df) - 1):
    next_bar = df.iloc[i + 1]
    ...
    window = df.iloc[:i+1]        # bars 0..i INCLUSIVE — bar i's full OHLC
                                    # is known (its close has happened)
    mtf = build_multi_timeframe_view(window, timeframes)
    outputs = [e.safe_analyze(mtf) for e in engines_list]
    ...
    raw_entry = float(next_bar["open"])   # entry at i+1's OPEN only
```
`window = df.iloc[:i+1]` is a Python slice — it is structurally
IMPOSSIBLE for it to contain row `i+1` or later (`iloc[:i+1]` excludes
index `i+1`). Every engine, gate, and filter that runs during a decision
only ever receives `mtf` (derived from `window`) or `outputs`/`vote`/
`score` derived from `mtf` — none of them are ever handed `df` or
`next_bar` directly. **Verdict: no future-OHLC leak in the decision
path.** (Entry/exit-price usage of `next_bar` — the open for entry, the
full OHLC for the SL/TP exit check — is intentional and correct: it
represents what actually happens on the bar the trade executes on, not a
future bar relative to that trade.)

## 2. `shift(-N)` misuse

**Checked:** repo-wide grep for `shift(-` across `engines/`,
`confluence/`, `core/`, `regimes/`, `risk/`, `backtesting/`, `backtest/`.
```
$ grep -rn "shift(-" engines/ confluence/ core/ regimes/ risk/ backtesting/ backtest/
(zero matches)
```
**Verdict: zero occurrences anywhere in the decision path.** The only
`.shift(...)` calls found (`utils/indicators.py`'s `true_range`
(`close.shift(1)`), `rsi`/`efficiency_ratio`/`variance_ratio`/etc.) are
all POSITIVE shifts (look backward), which is the correct direction.

## 3. Entry using High/Low not yet known

**Checked:** entry construction (`backtest_engine.py`):
```python
raw_entry = float(next_bar["open"])
entry = raw_entry + slip if direction == "BULLISH" else raw_entry - slip
```
Entry uses `next_bar["open"]` only — never `next_bar["high"]`/`["low"]`.
SL/TP distance uses `atr_val` (computed from `atr_series.iloc[i]`, a
trailing rolling mean ending at bar `i`, see §6) and `entry`, both known
at the moment of order placement. **Verdict: no leak.**

## 4. SL/TP intrabar ambiguity

**Checked:** `check_exit()` (`backtest_engine.py:184-229`), already
documented in its own docstring and covered by
`tests/test_backtest_exits.py`'s existing gap/slippage/pessimistic-SL
tests (`test_both_touched_in_one_bar_sl_wins_pessimistic`, etc.) — SL is
checked before TP when a single bar's OHLC shows both were touched (the
true intrabar sequence is unknowable from OHLC alone, so the pessimistic
assumption is taken deliberately, not a bug). **Verdict: correct,
documented, already tested — not a leak, a modeling assumption stated
plainly in the code.**

## 5. In-bar event ordering

**Checked:** `check_exit()`'s branch order: gap-through-SL-at-open →
gap-through-TP-at-open → intrabar SL touch → intrabar TP touch. Gaps are
checked before intrabar touches (correct — if the bar opened beyond a
level, that's what actually happened first). **Verdict: correct
ordering, no leak.**

## 6. Indicator computed from future data

**Highest-risk item on the checklist** — checked in depth, including a
live reproduction (not just code reading):

- `atr_series = compute_atr(df, period=14)` is computed ONCE over the
  WHOLE `df` before the loop starts (`backtest_engine.py:544`), not
  per-window. This is only safe if `atr()`'s rolling window is purely
  backward-looking. Confirmed: `utils/indicators.py::atr()` →
  `true_range(df).rolling(window=period, min_periods=period).mean()` —
  a standard trailing `.rolling()` with no `center=True`. **A trailing
  rolling mean at position `i` only ever reads positions `i-period+1..i`
  — never `i+1` or later — so precomputing it over the whole `df` is
  mathematically identical to recomputing it fresh on `df.iloc[:i+1]`
  every iteration.** Confirmed no leak.
- `find_swings()` (`utils/indicators.py:115-125`, used by SMC/ICT/
  Wyckoff/market_structure/PriceAction-v2/Wyckoff-v2 per this session's
  Confluence Engine Overhaul unification) is the ONLY `center=True`
  usage anywhere in the codebase (confirmed by grep — see below). A
  centered rolling window inherently needs bars on BOTH sides of a
  position to confirm it as a local extreme, which sounds like a classic
  lookahead risk. **Verified empirically, not just reasoned about**: ran
  `find_swings()` on a truncated slice ending exactly at a bar that only
  becomes a real swing high once later (excluded) bars are visible —
  the truncated call correctly returns `False` (cannot confirm it),
  matching a live system's own inability to confirm an unconfirmed swing.
  For a bar well inside the truncated slice's boundary (all data needed
  for its centered window already ≤ the truncation point), the truncated
  call's verdict matched the full-series verdict exactly. **This proves
  `find_swings()` never reaches past whatever slice it's given — the
  "centered" character is entirely internal to the given slice, so as
  long as it's always called on `window` (bars 0..i, never i+1+), it
  cannot leak real future data.** Confirmed via `decision_frame()`
  (`engines/base_engine.py:100-109`) that every engine derives its `df`
  exclusively from `mtf_data[decision_tf]`, and `mtf_data` is built from
  `window` (never `df`/`next_bar` directly) — so the causal-truncation
  property holds at every real call site
  (`smc_engine.py`/`market_structure_engine.py`/`wyckoff_engine_v2.py`/
  `price_action_engine_v2.py`, confirmed by grep).
  ```
  $ grep -rn "center=True" engines/ confluence/ core/ regimes/ risk/ backtesting/ backtest/ utils/
  utils/indicators.py:123:    swing_high = highs == highs.rolling(window=2 * window + 1, center=True).max()
  utils/indicators.py:124:    swing_low = lows == lows.rolling(window=2 * window + 1, center=True).min()
  ```
**Verdict: no leak** — both the trailing-ATR precompute and the
centered-swing detector are causally safe, the latter confirmed by a
live reproduction, not assumed from reading the code alone.

## 7. MTF alignment leakage

**Checked:** `build_multi_timeframe_view(window, timeframes)`
(`core/timeframe_sync.py:59-79`) — `views[base_label] = df_base` (=
`window`, unmodified), coarser timeframes via `resample(df_base, tf)`.
`resample()` (`core/timeframe_sync.py:33-41`) is a plain
`df.resample(rule).agg(...)` call over `df_base` — structurally cannot
produce a row keyed to a timestamp beyond `df_base`'s own last index.
The LAST row of a resampled coarser frame (e.g. "today's" D1 candle when
`window`'s tail sits mid-day) will be a partially-formed candle — this
is NOT leakage (no future data used), and is the same thing a live
system watching the same coarser timeframe mid-formation would see at
that exact moment. **Verdict: no leak — MTF confirmation reads whatever
the coarser frame's most recent (possibly still-forming) state is,
consistent with live behavior, not a backtest-only artifact.**

## 8. Resampling leakage

Same function as §7 — `resample()` never reaches beyond `df_base`'s own
index range; pandas' `.resample()` groups existing rows into buckets, it
does not fabricate or reach outside the input. **Verdict: no leak.**

## 9. Session/regime classification using later info

**Checked:**
- `assess_market_quality(df=window, symbol=config.symbol, now=bar_time, timeframe=...)`
  — `bar_time` is explicitly `window.index[-1].to_pydatetime()` (the
  CURRENT bar's own timestamp), not wall-clock time. The comment at the
  call site already flags this as load-bearing: *"CRITICAL: pass the BAR
  time, not wall-clock now; session/Friday/Monday penalties must be
  evaluated at the data's timestamp or the whole gate is noise."*
  Confirmed correct.
- `detect_regime(window)` (`regimes/regime_detector.py`) —
  `_trend_strength()` uses `df["close"].tail(lookback)`, a purely
  backward-looking slice of whatever `window` it's given.
- Repo-wide grep for wall-clock leakage into the decision path:
  ```
  $ grep -rn "datetime.now()\|datetime.utcnow()\|pd.Timestamp.now()" engines/ confluence/ core/ regimes/ risk/ backtesting/backtest_engine.py
  core/data_manager.py:190:            end = datetime.now()
  ```
  The one hit (`core/data_manager.py`) is live data-fetching code, never
  called from inside `run_backtest()`'s loop (`run_backtest` receives an
  already-loaded `df`, it never calls `data_manager`). **Verdict: no
  wall-clock leakage into any backtest decision.**

## 10. Signal timestamp vs execution timestamp

Decision made using `window` ending at bar `i` (as of bar `i`'s close);
trade entered at `next_bar` (bar `i+1`)'s open. `bar_time` used for
session/regime gates is bar `i`'s own timestamp (the bar the DECISION is
based on), not bar `i+1`'s (the bar the trade EXECUTES on) — this is the
correct convention (session/day checks should reflect when the decision
was made, and next-bar-open execution is the realistic fill model this
module's own docstring states at the top of the file: *"Realistic:
entries on next-bar open, fixed risk sizing."*). **Verdict: correct, no
leak or mislabeling.**

## 11. Warmup contamination

`config.warmup_bars` (default 210) gates the loop's start
(`for i in range(config.warmup_bars, len(df) - 1)`), giving every
decision at least 211 bars of `window` history. Individual engines with
their OWN larger minimum-history requirements (e.g. Quant v2's Hurst
exponent wanting up to 500 bars) correctly abstain via their own
`min_bars` gates / NaN-propagation rather than producing a
false/leaked signal when `window` is shorter than they need — confirmed
by this session's own Quant v2 test suite
(`tests/test_quant_engine_v2.py`'s degeneracy-case tests). This does not
leak future data; it just means some engines contribute NEUTRAL/abstain
opinions during the earliest portion of a run until their own history
requirement is met within the causally-available `window`. **Verdict:
no leak — a correct, already-tested abstention behavior, not a bug.**

## 12. Last-bar handling

The loop's range (`range(config.warmup_bars, len(df) - 1)`) means the
LAST iteration's `next_bar` is `df.iloc[-1]` (the true final row) — a
trade CAN open on the very last bar. Its same-bar exit check (BUG-002's
fix) correctly checks that same final bar. If it doesn't exit there, the
post-loop "Force-close any position still open at the end of data" block
closes it at `df.iloc[-1]["close"]` with reason `FORCED_CLOSE` — using
the SAME last bar's close, not a bar beyond the data. **Verdict: no
leak, correct boundary handling.**

## 13. Close-to-open / open-to-close assumptions

Entry always at next-bar OPEN (not close). SL/TP intrabar checks use the
full bar's OHLC once that bar has occurred. No code path assumes a
trade can be entered "at the previous bar's close" or exited "at a
future bar's open before it's known." **Verdict: no leak.**

## 14. Survivorship / data-selection effects

Out of scope for `backtest_engine.py` itself — this is a property of
the historical OHLCV files under `data/` (are delisted/discontinued
symbols represented, is the crypto/FX universe survivorship-biased),
not of the execution-simulation code reviewed in this pass. Not
reproducible or fixable from this file; flagged as a separate,
data-layer forensic category (see the roadmap's "sandbox-tractable
future phases" bucket) rather than silently ignored.

## 15. SL/TP/min_rr influencing signal selection itself

**Checked directly** — the exact concern: could `sl_atr_multiplier`/
`min_rr` ever feed back into WHETHER a trade executes or WHICH direction
it takes (a circular-dependency leak, distinct from OHLC leakage)?
```python
direction = vote.winning_bias.value          # set from vote ONLY
...                                           # (the `ok` gate, decided ABOVE this line, already ran)
sl_dist = atr_val * config.sl_atr_multiplier  # computed AFTER direction/ok
tp_dist = sl_dist * config.min_rr
```
`sl_dist`/`tp_dist`/`min_rr` are computed strictly AFTER the `if not ok:
continue` gate has already decided whether/which-direction to trade —
they can only affect the SIZE of the trade's stop/target, never whether
one is taken or which side. **Verdict: confirmed NOT an issue** — no
influence of risk parameters on signal selection.

## Summary

| # | Category | Verdict |
|---|---|---|
| 1 | Future OHLC leakage | CLEAN — window slicing structurally excludes future rows |
| 2 | `shift(-N)` misuse | CLEAN — zero occurrences anywhere |
| 3 | Entry on unknown High/Low | CLEAN — entry uses open only |
| 4 | SL/TP intrabar ambiguity | CLEAN — documented pessimistic assumption, tested |
| 5 | In-bar event ordering | CLEAN — gaps checked before intrabar touches |
| 6 | Indicator from future data | CLEAN — verified live (ATR trailing-safe, `find_swings` empirically causal-safe) |
| 7 | MTF alignment leakage | CLEAN — resample cannot reach beyond `window` |
| 8 | Resampling leakage | CLEAN — same mechanism as #7 |
| 9 | Session/regime using later info | CLEAN — bar_time used, not wall-clock |
| 10 | Signal vs execution timestamp | CLEAN — correct next-bar-open convention |
| 11 | Warmup contamination | CLEAN — engines abstain correctly on insufficient history |
| 12 | Last-bar handling | CLEAN — same-bar check + forced close at true last bar |
| 13 | Close-to-open/open-to-close | CLEAN — next-bar-open entry throughout |
| 14 | Survivorship/data-selection | OUT OF SCOPE for this file — data-layer concern, not silently dropped |
| 15 | SL/TP/min_rr → signal selection | CLEAN — confirmed no feedback into direction/gate |

**Three real bugs were found and fixed in this same pass** (BUG-002,
BUG-003, BUG-004 — see `reports/forensic/13_CONFIRMED_BUGS.md`), none of
which are lookahead bugs (they are execution-simulation and
cost-accounting bugs). The lookahead-specific checklist above,
checked separately and afterward, found **zero confirmed lookahead
leaks** in `backtest_engine.py`'s decision path.

## Status and next steps

This report covers `backtesting/backtest_engine.py` and its direct
per-bar dependencies (`core/timeframe_sync.py`, `regimes/regime_detector.py`,
`core/market_quality.py`, `confluence/*`, `utils/indicators.py`'s shared
functions). It does NOT constitute a full audit of every individual
engine's internal indicator math (e.g. whether `engines/ict_engine.py`'s
own private helpers ever mishandle a boundary) — that level of
per-engine scrutiny is explicitly the next item in the forensic roadmap
(REAL/PARTIAL/STUB classification for ict, quant, wyckoff, divergence,
sentiment, macro), not duplicated here.

Per the operator's own explicit instruction: **no Validate action was
run on any Mission Center trial during this pass** — this report is
audit-only, and the one finding with genuine measurement-validity
implications (BUG-004, non-forex commission) is flagged in
`13_CONFIRMED_BUGS.md` as needing a re-run of the registered
carrier-asset evidence before any PF number from before this fix is
trusted at face value — that re-run is a separate, not-yet-performed
follow-up, consistent with every other bug fixed this session.
