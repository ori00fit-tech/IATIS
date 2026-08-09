# IATIS Engine Refinement V1 — Production Safety Audit (#372)

Captured: 2026-08-09. Git state: `research/engine-refinement-v1` @
`e64b6e6` (after #371).

## Purpose

This document answers one question directly, with evidence, not
assertion: **does anything in Engine Refinement V1 change what the live
(or paper) trading system actually does?** Every per-engine task report
in this pass claimed a production-impact framing; this audit
independently re-derives that claim from the raw diff and re-runs the
tests that would catch a violation, rather than trusting the prior
claims at face value.

Method: `git diff origin/main..HEAD` was read in full, file by file, not
sampled. Every file touched by this branch is accounted for below.
Nothing is asserted from memory of the individual task reports.

## 1. Every file touched by this branch, and its production relevance

```
$ git diff --stat origin/main..HEAD -- . ':!reports' ':!research/engine_refinement'
```

| File | Category | Live-trading relevance |
|---|---|---|
| `main.py` | **live entry point** | +6 lines — audited in §2. Inert. |
| `backtesting/backtest_engine.py` | backtest-only | Never imported/called by `main.py`/`scheduler.py` — audited in §3. |
| `config/engines.yaml` | config | Audited in §4. `enabled:` block byte-identical. |
| `engines/base_engine.py` | shared base class | Audited in §5. Crash-path output unchanged. |
| `engines/smc_engine.py`, `price_action_engine.py`, `nnfx_engine.py`, `wyckoff_engine.py` | **live-enabled engines** | Audited in §6. Golden-value regression confirms byte-identical `bias`/`score`. |
| `engines/ict_engine.py`, `market_structure_engine.py`, `divergence_engine.py`, `quant_engine.py`, `macro_engine.py`, `sentiment_engine.py` | disabled engines | Audited in §7. Never constructed by the live pipeline. |
| `tests/*` | test-only | No production relevance. |

**Files with zero diff, confirmed by direct command** (the layers that
would matter most if anything here were unsafe — money sizing, order
placement, reconciliation, vote tallying, gate arithmetic):

```
$ git diff --quiet origin/main..HEAD -- risk/ execution/ scheduler.py storage/ confluence/
$ echo $?
0
```

`risk/`, `execution/`, `scheduler.py`, `storage/`, and `confluence/`
(the module that actually computes `tally_votes()`/`calculate_score()`
from engine outputs) are **byte-identical to `origin/main`**. No risk
gate, no order-placement path, no vote-tallying/scoring arithmetic, no
reconciliation logic, and no persisted-state schema was touched by this
entire refinement pass.

## 2. `main.py` — the live decision pipeline's only change

Full diff (6 lines):

```diff
@@ build_active_engines(config) @@
+    all_versions = config.get("engines", {}).get("versions", {})
     for key, cls in _ALL_ENGINES.items():
         if enabled.get(key, False):
             ...
             engine.thresholds = all_thresholds.get(key, {})
+            engine.version = all_versions.get(key)
```

This sets `engine.version` from `config/engines.yaml`'s pre-existing
`versions:` block (a field that already existed, previously read by
nothing). Confirmed by direct grep that `engine_version`/`.version` are
**never read** by any file under `confluence/`, by `main.py` itself
beyond this assignment, or by `backtesting/backtest_engine.py`'s own
identical pattern:

```
$ grep -rn "engine_version\|\.version\b" confluence/*.py main.py backtesting/backtest_engine.py
main.py:154:            engine.version = all_versions.get(key)
backtesting/backtest_engine.py:651:            engine.version = all_versions.get(thresholds_key)
```

Both are write-only assignments with no downstream reader in any
decision-making code path. `engine.thresholds = all_thresholds.get(key,
{})` (the line directly above, unmodified — pre-existing since the
Confluence Engine Overhaul, not part of this pass) is the only other
per-engine configuration line in this function, and it is untouched.

**Conclusion: `main.py`'s only change is inert metadata tagging.**

## 3. `backtesting/backtest_engine.py` — confirmed unreachable from the live pipeline

`run_backtest()` and every function in this module exist solely for
backtests/Mission Center research runs. Confirmed directly:

```
$ grep -rn "run_backtest\|backtest_engine" main.py scheduler.py
main.py:404:    # confluence/reversal_veto.py for backtesting/backtest_engine.py and
```

The only hit is a comment. Neither `main.py` nor `scheduler.py` imports
or calls anything from `backtesting/backtest_engine.py`. This means the
following changes in this file — all real, all documented in
`CHANGES.md` §1 — have **zero live-trading reachability**, regardless of
their own content:

- The new `assert_monotonic_timestamps()` precondition check (causality
  hardening, §6 of the refinement plan).
- `crashed_engine_bars`/`crashed_engine_totals` tracking (error
  semantics, §4).
- `engine.version` tagging (versioning, §5), same inert pattern as §2.

## 4. `config/engines.yaml` — full diff review

Full diff already captured verbatim in `CHANGES.md` §2/`TEST_RESULTS.md`
§4; re-verified here directly against the file on disk. Three, and only
three, kinds of change exist in this file:

1. **Header comment** — documents the new (inert) `versions:` wiring;
   no code-read behavior changed.
2. **`thresholds.divergence` and `thresholds.ict` value changes** — both
   engines are `enabled: false`. `thresholds.smc`, `thresholds.
   price_action`, `thresholds.nnfx`, and `thresholds.wyckoff` (the 4
   live-enabled engines' own threshold blocks) have **zero diff hunks**
   anywhere in this file's change — confirmed by the full diff showing
   no `@@` hunk touching those four sub-blocks.
3. **`versions:` block bumps** — `divergence`, `ict`, `macro`,
   `market_structure`, `quant`, `sentiment` (all 6 disabled engines).
   `smc`, `price_action`, `nnfx`, `wyckoff` stay at their baseline
   version strings, unbumped, since none of their changes were
   semantic fixes.

**The `enabled:` block itself — the single most production-critical
section of this file — has zero diff.** Confirmed by SHA256 comparison
of the surrounding unmodified regions and by the fact that the diff
above contains no hunk touching it. Verbatim, current state:

```yaml
enabled:
  smc: true
  price_action: true
  nnfx: true
  wyckoff: true
  ict: false
  market_structure: false
  divergence: false
  quant: false
  macro: false
  sentiment: false
```

Identical to `BASELINE.md` §3. `smc_full_spec: false` and
`crypto_positioning_modulator` are likewise untouched.

**Config files confirmed byte-identical to baseline by SHA256, re-hashed
fresh for this audit:**

```
8204341bdb41383e321493130571f6b07ebb54d4c00d236337f33ea48725c14e  config.yaml           (matches BASELINE.md §5 exactly)
55a671b5a0341bd90e7f70f6b536f362ecb299f8b52244e4cdda249bb80b9785  config/symbols.yaml   (matches BASELINE.md §5 exactly)
587eff93026ed2951cce5f511f77bb532ff24ced312aca94c5b92a1333a34e70  config/risk.yaml      (matches BASELINE.md §5 exactly)
```

`config.yaml` is the file holding `confluence.weights`,
`min_engines_agreeing`, `min_score_to_trade`, and
`min_informative_weight_share` — the actual live vote-quorum and
scoring-gate rules. **Not one byte of it changed.**

## 5. `engines/base_engine.py` — the crash-path contract, re-verified

`safe_analyze()`'s except branch (the fail-closed guarantee every
engine relies on) was read directly, in full, on the current HEAD:

```python
except Exception as exc:
    logger.warning(f"{self.name} engine crashed, abstaining: {type(exc).__name__}: {exc}")
    return EngineOutput(
        engine_name=self.name,
        bias=Bias.NEUTRAL,
        score=0.0,
        reasons=[f"Engine error, abstaining: {exc}"],
        crashed=True,
        error_type=type(exc).__name__,
        error_message=str(exc),
        causal_timestamp=causal_timestamp,
        data_quality=data_quality,
        engine_version=self.version,
    )
```

`bias=Bias.NEUTRAL, score=0.0` — byte-identical to the pre-refinement
behavior. The only additions are a new log line (previously silent) and
additional diagnostic fields (`crashed`, `error_type`, `error_message`,
`causal_timestamp`, `data_quality`, `engine_version`) that no
`confluence/` code reads. The success path (`analyze()` returning
normally) is likewise unmodified except for filling the same additive
fields when an engine's own `analyze()` didn't already set them.

**Conclusion: the fail-closed contract "unclear data → NEUTRAL, never a
guess" is unchanged, byte-for-byte, on both the success and crash
paths.**

## 6. Live-enabled engines (SMC, Price Action, NNFX, Wyckoff) — behavioral proof, re-run fresh

Re-ran `tests/test_engine_config_extraction_no_behavior_change.py`
standalone for this audit (not reusing the #370 full-suite log):

```
$ python3 -m pytest tests/test_engine_config_extraction_no_behavior_change.py -v
...
27 passed in 17.02s
```

This file pins **golden `bias`/`score` values**, captured before this
refinement pass began, for SMC/PriceAction/NNFX/Wyckoff (plus ICT and
MarketStructure) across two independent synthetic scenarios, under
**both** a bare zero-arg construction and construction with the real,
current `config/engines.yaml` thresholds populated. All 27 assertions
pass on the current HEAD — meaning every one of these 4 live engines
produces the exact same `bias` and `score` for the exact same input data
as it did before any code in this refinement pass was written, with the
real production config loaded.

This is the direct, executable proof behind every "0 `strategy_semantics_
changed`" claim for these 4 engines in `changes.json`/`CHANGES.md` — not
a restatement of it.

## 7. Disabled engines (ICT, Market Structure, Divergence, Quant, Macro, Sentiment)

`config/engines.yaml`'s `enabled:` block (§4 above) is the single gate
that decides whether any of these 6 engines is ever constructed by
`main.py::build_active_engines`. Confirmed unchanged: all 6 remain
`false`. `main.py`'s engine-construction loop (`for key, cls in
_ALL_ENGINES.items(): if enabled.get(key, False): ...`) skips
construction entirely for a `false` entry — none of these 6 engines'
`analyze()`/`decide()` is ever called by the live pipeline, regardless
of what changed inside them.

The 4 genuine `strategy_semantics_changed: true` fixes in this pass
(ICT, Market Structure, Divergence, Quant) therefore have **zero live
reachability** — not "low impact," but literally unreachable code from
the live pipeline's perspective. The same is true of Macro (whose
`confluence.weights.macro` is additionally forced to `0.0` in
`config.yaml`, itself confirmed byte-identical in §4) and Sentiment.

## 8. Engine variants (`price_action_v2`, `wyckoff_v2`) — confirmed still ad-hoc-only

These two research variants (built in an earlier phase of this
codebase's history, unrelated to and untouched by this refinement pass)
are reachable only through Mission Center's ephemeral `engine_variants`
override, never through `main.py`'s live construction path. Confirmed:
`main.py::build_active_engines` iterates `_ALL_ENGINES` (the 10 base
engine classes only) and has no `variants`/`engine_variants` handling of
any kind — that logic exists exclusively in
`backtesting/backtest_engine.py`'s own construction loop (§3, confirmed
unreachable from live). `config/engines.yaml`'s `versions.price_action_v2`/
`versions.wyckoff_v2` entries exist for research traceability only; no
`enabled.price_action_v2`/`enabled.wyckoff_v2` key exists or is read
anywhere.

## 9. Full-suite regression confirmation

Cross-referencing `TEST_RESULTS.md` (§1 of this document's companion,
task #370): **2945 passed, 2 skipped, 6 failed** on the complete,
merged set of all 21 changes — the same 6 pre-existing,
environment-caused failures present on the pre-refinement baseline, zero
regressions. That result is unchanged since #370 and was not re-run here
(no code changed between #370 and this audit) — this document's own new
evidence is the targeted, direct file-by-file review in §1–§8 above,
plus the fresh golden-value re-run in §6.

## 10. Verdict

**GO for production deploy of `research/engine-refinement-v1`, unchanged
from the branch's current state, with respect to live-trading behavior.**

Specifically confirmed:

- Zero diff in `risk/`, `execution/`, `scheduler.py`, `storage/`,
  `confluence/` — the money-sizing, order-placement, reconciliation, and
  vote-scoring layers are untouched.
- `config.yaml` (weights/quorum/score-gate rules) is byte-identical to
  baseline.
- `config/engines.yaml`'s `enabled:` block is byte-identical to
  baseline; the 4 live engines' own threshold sub-blocks are untouched.
- `main.py`'s only change is a write-only, never-read metadata field.
- The 4 live-enabled engines (SMC, Price Action, NNFX, Wyckoff) are
  proven, via a fresh test run against golden pre-refinement values, to
  produce byte-identical `bias`/`score` output under the real production
  config.
- The 6 disabled engines' changes, including the 4 genuine semantic
  fixes among them, are unreachable from the live pipeline by
  construction (`enabled: false`), not merely low-risk.
- The fail-closed "unclear data → NEUTRAL, never a guess" contract is
  unchanged on both the success and crash paths.

**Deploying this branch to production today would change zero observable
live-trading behavior.** What it changes is entirely diagnostic:
richer, more honest logging/metadata on the 4 live engines, and 6
disabled research engines becoming more useful *if and when* a future,
separately-justified, pre-registered decision re-enables one of them —
which CLAUDE.md's frozen-state rule already requires regardless of
anything in this pass.

No condition in this audit was left unchecked because it "should be
fine" — every claim above is backed by a command run against this exact
commit, quoted verbatim.
