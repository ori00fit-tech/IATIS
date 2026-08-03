# P0/P1 Forensic Audit — Final Summary (2026-08-04)

Consolidated deliverable for the operator's exact requested format,
covering both the P0 (cTrader connection lifecycle) and P1 (research
mission engine evidence separation) audits. Full detail lives in the two
dedicated reports this summary draws from:
`16_CTRADER_LIFECYCLE_AUDIT.md`, `17_MISSION_EVIDENCE_PIPELINE_AUDIT.md`.

## Files inspected

**P0 (execution):**
- `execution/ctrader_client.py` (full file, 1732 lines — state machine,
  every auth/bootstrap handler, `connect()`, `_schedule_reconnect()`,
  `_on_error`/`_on_error_res`, `_stop_client`, `_acquire_process_lock`)
- `core/data_providers.py` (`get_shared_ctrader_client()`, the in-process
  singleton + its `threading.Lock`)
- `execution/trade_executor.py`, `execution/reconciliation.py` (every
  call site that could construct or use a `CTraderClient`)
- `scripts/{backtest_ic_symbols,ctrader_smoke_test,measure_ctrader_spread,
  download_ctrader_fx_history}.py` (every direct, non-singleton
  `CTraderClient()` construction in the repo — confirmed all are
  standalone operator-run tools, not live-path concurrent callers)
- `git log --oneline -- execution/ctrader_client.py` (full history)
- `tests/test_ctrader_client.py`, `tests/test_ctrader_message_handlers.py`,
  `tests/test_ctrader_execution_logic.py`, `tests/test_provider_chains.py`

**P1 (research):**
- `backtest/optimizer.py` (`classify_search_space_variation`,
  `search_space_has_signal_variation`, `MissionSearchSpace`)
- `execution/routes/missions.py` (mission-detail route,
  `search_space_kind` wiring)
- `backtest/mission_runner.py`, `backtest/mission_validator.py`,
  `backtest/meta_analysis.py` (grepped for auto-selection language and
  any `registry.json`/`config.yaml`/`config/engines.yaml` write)
- `execution/routes/ai.py` (draft-save endpoint's write target)
- `research/edge_gate.py` (`PROMOTION_CRITERIA`, `check_edge_gate`)
- `tests/test_optimizer.py`, `tests/test_missions.py`,
  `tests/test_mission_runner.py`, `tests/test_mission_validator.py`,
  `tests/test_promotion_criteria.py`

## Exact defects found

| # | Area | Defect | Severity |
|---|---|---|---|
| 1 | P0 execution | **None in current code.** The exact symptom class in the operator's live logs (ALREADY_LOGGED_IN storm, socket leak, overlapping connections, reconnect-storm) matches four commits already merged 2026-07-17→07-23. No live-code defect confirmed this pass. | N/A — pre-existing, already fixed |
| 2 | P0 execution | Test-coverage gap: `_on_error_res`'s ALREADY_LOGGED_IN bootstrap-continuation logic had zero tests | Medium (correctness untestable, not a live defect) |
| 3 | P0 execution | Test-coverage gap: `_schedule_reconnect`'s single-flight guarantee, terminal-failure recovery, and intentional-disconnect cancellation had zero tests | Medium |
| 4 | P0 execution | Test-coverage gap: `get_shared_ctrader_client()`'s in-process thread-safety had never been exercised under real concurrent access | Medium |
| 5 | P1 research | Test-coverage gap: `classify_search_space_variation()` and its API surface (`search_space_kind`) — the exact mechanism meant to flag a mission like `4ec00ac3e6ed` as risk-only — had zero tests despite being live production code | Medium |
| 6 | P1 research | **None found.** No auto-selection, no registry.json write path, no evidence-gate weakening anywhere in the audited pipeline. | N/A |

**No P0 or P1 severity ("live-money-affecting" or "evidence-integrity-
breaking") code defect was found or introduced this pass.** Every finding
was a test-coverage gap on already-correct code, now closed.

## Fixes applied

**None required for the P0/P1 audits themselves** — the mechanisms
audited were already correct. One additional fix was applied as part of
this session, closing a gap this same summary's "remaining risks"
section had flagged:

1. `engines/base_engine.py` — `EngineOutput` gains `crashed: bool =
   False`, set `True` only inside `safe_analyze()`'s except branch.
   Closes the confirmed part of the operator's "safe_analyze conflates
   'no opinion' with 'broken'" claim: `tally_votes()`/the live gate are
   completely unchanged (still read only `bias`/`score`), but a human or
   a future monitoring metric can now distinguish a real crash from an
   honest abstention by reading this one field — already flowing into
   `main.py`'s existing `report["engine_outputs"]` with zero extra
   plumbing, since `to_dict()` already serializes it. New test:
   `tests/test_phase1.py::test_crashed_flag_distinguishes_engine_error_from_honest_abstention`.
2. `backtest/walk_forward.py` docstring correction (unrelated, carried
   over from the prior turn's audit — see `13_CONFIRMED_BUGS.md`).
3. `engines/macro_engine.py` — BUG-008 fix (unrelated, from the prior
   turn — DXY-to-bias mapping made symbol-aware for USD-base pairs).
4. `backtest/monte_carlo.py` — BUG-007 fix (unrelated, from the prior
   turn — `risk_of_ruin` now uses path-dependent `max_dd`).

(Items 2-4 predate this P0/P1 request but are included for a complete
picture of this session's total code changes.)

## Tests added (this P0/P1 pass)

| File | New tests | What they prove |
|---|---|---|
| `tests/test_ctrader_message_handlers.py` | 4 | `_on_error_res` correctly continues the bootstrap chain on ALREADY_LOGGED_IN from both `TCP_CONNECTED` and `APP_AUTH_OK`; a stray occurrence at an unhandled state is a no-op not a crash; a real error still sets `ERROR` |
| `tests/test_ctrader_client.py` | 3 | `_schedule_reconnect` is single-flight; the loop resets `_reconnecting` and is NOT wedged after exhausting `RECONNECT_MAX_ATTEMPTS`; an intentional disconnect cancels a sleeping reconnect loop immediately |
| `tests/test_provider_chains.py` | 1 | `get_shared_ctrader_client()` constructs exactly once and returns the identical instance to 8 concurrently-racing threads |
| `tests/test_optimizer.py` | 6 | `classify_search_space_variation` returns the correct one of `RISK_ONLY_VARIATION`/`SIGNAL_VARIATION`/`MIXED`/`NONE` in both flat and hypothesis-bundle modes |
| `tests/test_missions.py` | 2 | `GET /research/missions/{id}` returns the correct `search_space_kind` for a real seeded mission row, both risk-only and signal-varying |
| **Total** | **16** | |

## Tests executed

- Targeted: `tests/test_provider_chains.py tests/test_ctrader_client.py
  tests/test_ctrader_message_handlers.py tests/test_ctrader_execution_logic.py`
  → **141/141 passed**.
- Targeted: `tests/test_optimizer.py -k classify`, `tests/test_missions.py
  -k search_space_kind` → **8/8 passed**.
- Full suite (`python3 -m pytest -q`), twice (once per commit) →
  **2209 passed, 2 skipped**, zero regressions. The only failures present
  in either run are 6 pre-existing, unrelated tests
  (`test_alpaca_provider.py::test_missing_credentials_fall_through`,
  5× `test_api_server.py::test_ai_*_disabled_by_default`) that assert
  "disabled without credentials" behavior — they fail only because this
  session's `.env` was populated with real Alpaca/Gemini credentials for
  separate, unrelated manual-verification purposes (documented in
  `13_CONFIRMED_BUGS.md`'s BUG-007 note), not because of anything in this
  P0/P1 pass.

## Remaining risks

1. **Cannot verify the VPS's actual deployed commit from this sandbox.**
   If the live process predates `9b039ab` (2026-07-23), the operator will
   keep seeing the exact symptoms reported until a `git pull` +
   `systemctl restart` (as two separate commands) is run. This is an
   operational risk, not a code risk — but it is the single most likely
   explanation for what was reported, and is unverified pending operator
   action.
2. **No new observability was added** (connection-attempt counter, a
   startup-time git-commit-hash log line, an ALREADY_LOGGED_IN occurrence
   metric) — explicitly scoped out of this pass (audit + tests, not new
   surface). Recommended in `16_CTRADER_LIFECYCLE_AUDIT.md` §7 for a
   future, separately-scoped slice; would have made risk #1 immediately
   diagnosable from a live log line instead of requiring this forensic
   pass.
3. **The "gives up after 10 attempts, then reconnects on a later
   disconnect" behavior is a design choice, not a bug** (§6 of the
   cTrader report) — flagged for the operator's awareness in case a hard
   stop (require literal manual `connect()`) is actually the preferred
   operational posture. Not changed unilaterally.
4. **The broader mission-engine finding from the prior turn stands
   unchanged by this audit**: mission `4ec00ac3e6ed` itself (PF≈1.0,
   risk-only variation) remains what it was assessed as — noise, not
   evidence. This P1 pass verified the *labeling machinery* is sound and
   now tested; it does not and cannot manufacture an edge that isn't
   there. A genuine signal-search mission (hypothesis-bundle mode, 2+
   named hypotheses) is still the operator's own next research step, not
   something this audit performs.
5. **44-claim external audit from earlier in this session remains
   ~95% unverified.** Of the large "Round 1 + Round 2" list the operator
   pasted, only the Macro DXY-inversion claim (BUG-008, fixed) and one
   systemic claim (`safe_analyze`'s error/abstention conflation — confirmed
   real and fixed this pass: `EngineOutput` gained a `crashed: bool = False`
   field, set `True` only by `safe_analyze()`'s except branch, flowing
   automatically into `main.py`'s existing `report["engine_outputs"]`
   with zero extra plumbing; `tally_votes()`/the live gate are unchanged,
   still reading only `bias`/`score`) were checked against real code. The rest
   (5-engines-use-RSI correlation, Wyckoff footprint/range issues, Quant
   365-vs-261-day annualization, ICT open-candle Judas Swing, Divergence
   MACD lag, MarketStructure CHoCH noise, Sentiment/PriceAction
   double-voting, ATR-normalization blindness) are still unverified
   claims, not confirmed defects — flagged here so they aren't
   mistakenly treated as closed by this P0/P1 pass, which was scoped
   narrowly to execution lifecycle + evidence-pipeline separation only.

## Final verdict

| Question | Answer |
|---|---|
| Is the cTrader execution layer's CODE safe for the described symptoms? | **YES** — the fixes exist, are correct, and are now better test-covered. |
| Is the deployed VPS confirmed to be running that code? | **UNKNOWN — operator action required.** Cannot verify from this sandbox. |
| Does the research mission engine correctly separate DISCOVERY / VALIDATION / EVIDENCE / PRODUCTION? | **YES**, confirmed by direct code read + new tests. No auto-promotion path exists. |
| Has a real, statistically valid Edge been found for any symbol? | **NO** — unchanged from the operator's own prior assessment. Mission `4ec00ac3e6ed` is noise (PF≈1.0, risk-only variation), not evidence. |

### GO / NO-GO

- **Research (Mission Center, ad-hoc backtesting, hypothesis-bundle
  discovery): GO.** The evidence-pipeline safety rails are real,
  code-enforced, and now test-covered. Proceed with a genuine
  signal-search mission (2+ named hypotheses) as the next research step
  — per the operator's own correct diagnosis, re-running risk-only sweeps
  on mission `4ec00ac3e6ed`'s shape would not be a productive use of
  compute.
- **Live or paper execution via cTrader: NO-GO until the operator
  confirms the deployed VPS commit is current** (≥ `9b039ab`,
  2026-07-23) and redeploys if not. The code itself is not blocking —
  the unverified deployment state is. Once confirmed/redeployed, watch
  the scheduler logs for one full reconnect cycle (if any real disconnect
  occurs) to confirm the fixed behavior live, per
  `16_CTRADER_LIFECYCLE_AUDIT.md`'s own verification checklist.
- **No code changes are pending from this P0/P1 pass** — both commits
  (`150062e`, `e229f50`) are already pushed to
  `claude/iatis-full-audit-350sic`.
