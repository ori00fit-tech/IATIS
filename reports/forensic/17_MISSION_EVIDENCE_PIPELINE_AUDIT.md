# Research Mission Engine — DISCOVERY/VALIDATION/EVIDENCE/PRODUCTION Separation Audit (2026-08-04)

## Trigger

Following up on the operator's report that a real mission (`4ec00ac3e6ed`)
showed "Risk-only variation — signal fixed across all trials" with best PF
≈ 1.0 (noise, not evidence), the operator requested a P1 audit verifying
the research mission engine genuinely enforces
`DISCOVERY != VALIDATION != EVIDENCE != PRODUCTION` and cannot let a
high-in-sample-PF candidate become production merely by ranking first.

## Method

Direct code reading (not assumed from prior session notes), checking each
claim against the real source, following this session's own established
discipline: reproduce before calling anything confirmed.

## Findings

1. **`classify_search_space_variation()` is real, wired, and correctly
   implemented** (`backtest/optimizer.py`) — the exact mechanism meant to
   proactively label a mission like `4ec00ac3e6ed` as risk-only, instead
   of an operator discovering it after the fact in Meta-Analysis. Wired
   into `GET /research/missions/{id}`'s `search_space_kind` field
   (`execution/routes/missions.py:269-272`).

2. **Real, confirmed test-coverage gap, now closed**: despite being live
   in production code, `classify_search_space_variation()` had ZERO tests
   anywhere in the suite, and the API route that surfaces it
   (`search_space_kind` in the mission-detail response) also had zero
   tests. Added:
   - `tests/test_optimizer.py` (+6 tests): `RISK_ONLY_VARIATION` /
     `SIGNAL_VARIATION` / `MIXED` / `NONE` classification, both in flat
     mode and hypothesis-bundle mode.
   - `tests/test_missions.py` (+2 tests): real end-to-end HTTP round-trip
     — a real mission row seeded via `research_missions.upsert_mission()`
     with a risk-only search space, then `GET /research/missions/{id}`
     confirmed to return `search_space_kind: "RISK_ONLY_VARIATION"`; same
     for a genuinely signal-varying space returning `"SIGNAL_VARIATION"`.

3. **No auto-selection of a "winner" exists anywhere in the pipeline**
   (confirmed by grep across `mission_runner.py`, `mission_validator.py`,
   `meta_analysis.py`, `robustness.py`, `execution/routes/missions.py` for
   `winner`/`best_point`/`auto_select`): the only two matches are explicit
   comments stating the opposite — `meta_analysis.py`'s opportunity-ranking
   functions are documented "A ranked SUGGESTION only — never
   auto-selected, never auto-run."

4. **No code path writes to `research/results/registry.json`** from any
   mission/validation/AI-copilot-draft code (confirmed by grep across
   `execution/routes/missions.py`, `execution/routes/ai.py`,
   `backtest/mission_runner.py`, `backtest/mission_validator.py` — every
   mention of `registry.json` is a comment stating it must NOT be
   touched). Backed by existing, passing hard-block tests
   (`test_registry_json_byte_identical_before_and_after_mission_run`,
   `test_never_touches_config_yaml_or_engines_yaml` in both
   `tests/test_mission_runner.py` and `tests/test_mission_validator.py`)
   — re-run and confirmed still passing, not newly written.

5. **The promotion bar is code, not prose**: `research/edge_gate.py`'s
   `PROMOTION_CRITERIA = {"min_trades": 300, "min_oos_pf": 1.2,
   "require_walk_forward": True, "require_monte_carlo": True}` is a real,
   enforced dict — `check_edge_gate()` treats it as a FATAL hard gate when
   `allow_live_trading=True` (GOVERNANCE-001, already fixed earlier this
   session). A hypothesis cannot reach live-tradeable status without
   satisfying it, regardless of how good its raw PF looks.

6. **`DISCOVERY != VALIDATION` is a real, code-enforced distinction, not
   just labeling**: `backtest/mission_validator.py`'s
   `SAME_SYMBOL_CONFIRMED`/`SAME_SYMBOL_NOT_CONFIRMED` verdicts (own-symbol
   confirmation only) are structurally distinct from
   `NO_EDGE`/`WEAK_LEAD`/`STRONG_LEAD` (cross-symbol generalization,
   requiring ≥3 validated symbols for `STRONG_LEAD`) — a mission trial
   (discovery) can never itself produce either verdict; only a completed
   validation run can, and neither verdict is "evidence" per
   `PROMOTION_CRITERIA` above.

## No new code defect found

Unlike the cTrader lifecycle audit (`16_CTRADER_LIFECYCLE_AUDIT.md`),
this pass found the evidence-separation architecture to be substantively
sound and already correctly enforced in code — the one concrete, fixable
gap was test coverage (item 2 above), now closed. No weakening of any
statistical gate, no new "evidence" shortcut, and no auto-promotion path
was introduced or found.

## Verification

`tests/test_optimizer.py -k classify` (6/6), `tests/test_missions.py -k
search_space_kind` (2/2) — both pass in isolation. Full suite:
2209 passed, 2 skipped (6 pre-existing, unrelated credential-dependent
failures noted in BUG-007/BUG-008, unchanged) — zero regressions from
this pass's 8 new tests.

## Status

CLOSED. Real coverage gap found and fixed (8 new tests). No auto-selection,
no evidence-gate weakening, no discovery-to-production shortcut found
anywhere in the audited pipeline.
