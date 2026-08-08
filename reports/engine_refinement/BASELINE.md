# IATIS Engine Refinement V1 — Baseline Snapshot

**This document is immutable once committed.** It records the exact
state of the codebase at the moment `research/engine-refinement-v1` was
created, so every later claim in this refinement pass ("X changed",
"Y was broken before") can be checked against a fixed reference point.

Captured: 2026-08-08T04:07:15Z

## 1. Git state

- Branch: `research/engine-refinement-v1`
- Created from: `origin/main` @ `9138bb9` ("Merge pull request #225 from
  ori00fit-tech/claude/iatis-full-audit-350sic")
- Confirmed via `git diff origin/main claude/iatis-full-audit-350sic`:
  **zero-diff, byte-identical** to the tip of the long-running
  `claude/iatis-full-audit-350sic` working branch (`0c23a70`) — every
  commit from that branch has already been merged into `main` through a
  continuous series of PRs (#1 through #225). `main` is *not* a stale or
  separate foundation; it is the current state of the whole system.

## 2. Test count

`python3 -m pytest -q --collect-only` → **2834 tests collected**.

Last known full-suite result on this exact code (from the prior session,
confirmed unchanged since): **2826 passed, 2 skipped, 6 failed** — the 6
failures are pre-existing and environment-caused (5 Gemini 429
rate-limiting tests under `tests/test_api_server.py`'s AI-disabled-by-
default suite, 1 Alpaca credentials test under
`tests/test_alpaca_provider.py`), both artifacts of this sandbox's own
`.env`, not code defects.

## 3. Production engine configuration (`config/engines.yaml`)

```
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

versions:
  smc: "1.0"
  price_action: "1.0"
  price_action_v2: "2.0"
  nnfx: "1.0"
  wyckoff: "1.0"
  wyckoff_v2: "2.0"
  ict: "1.0"
  market_structure: "1.0"
  divergence: "2.0"
  quant: "2.0"
  macro: "2.0"
  sentiment: "1.0"

smc_full_spec: false
```

Live-production set (prod4, per CLAUDE.md): **smc, price_action, nnfx,
wyckoff** — exactly matches `enabled`. `price_action_v2`/`wyckoff_v2`
exist only as ephemeral, ad-hoc Mission Center `engine_variants` — never
loaded by `main.py`'s live construction path, never in `enabled`.

## 4. `config.yaml` confluence block

```
confluence:
  min_engines_agreeing: 2
  min_score_to_trade: 58
  min_informative_weight_share: 0.6
  weights:
    smc: 0.202
    price_action: 0.1869
    nnfx: 0.2273
    wyckoff: 0.0707
    ict: 0.0657
    market_structure: 0.0859
    divergence: 0.0606
    quant: 0.0707
    macro: 0.0
    sentiment: 0.0303
```

## 5. Config file hashes (SHA256, for later drift detection)

```
8204341bdb41383e321493130571f6b07ebb54d4c00d236337f33ea48725c14e  config.yaml
c08e2e4ae9764d8f792121217e680bc992cda57cedd69acf561719ef190135a6  config/engines.yaml
55a671b5a0341bd90e7f70f6b536f362ecb299f8b52244e4cdda249bb80b9785  config/symbols.yaml
587eff93026ed2951cce5f511f77bb532ff24ced312aca94c5b92a1333a34e70  config/risk.yaml
```

## 6. Current `EngineOutput` schema (`engines/base_engine.py`)

```python
@dataclass
class EngineOutput:
    engine_name: str
    bias: Bias                       # BULLISH | BEARISH | NEUTRAL
    score: float                     # 0-100
    reasons: list[str]
    raw: dict
    features: dict                   # Feature-Extraction-layer snapshot (Confluence Overhaul Phase 2)
    probability: float | None = None
    confidence_interval: tuple[float, float] | None = None
    expected_return: float | None = None
    expected_drawdown: float | None = None
    sample_size: int | None = None
    evidence_level: str = "HEURISTIC"  # HEURISTIC | MEASURED (no engine is MEASURED yet)
    crashed: bool = False            # set True only by safe_analyze()'s except branch
```

**Confirmed finding, load-bearing for this refinement pass**: `crashed`
is set by `BaseEngine.safe_analyze()` but is **never read anywhere
downstream** (`grep -rn "\.crashed\b"` outside `tests/`/`base_engine.py`
returns zero hits). A crashed engine today produces an ordinary
`NEUTRAL, score=0.0` vote that flows into `tally_votes()`/backtest
statistics indistinguishably from a genuine "no opinion" abstention —
this is the exact gap the refinement plan's §4 (Error Semantics) targets.

Missing relative to the refinement plan's target contract (§3):
`score_type`, `causal_timestamp`, `data_quality`, `error_type`,
`error_message`, and a distinct `engine_version` field (currently only
tracked externally in `config/engines.yaml`'s `versions:` block, never
on the `EngineOutput` instance itself).

## 7. Backtest configuration entry point

`backtesting/backtest_engine.py::run_backtest(df, config, engine_config=None)`
— `engine_config=None` resolves to a real `utils.helpers.load_config()`
snapshot; every Mission Center override (`engines.enabled`,
`data.timeframes`, `indicators.filters`, `context_filters.filters`,
`engines.variants`, `confluence`) merges over that snapshot via
`build_engine_config_override()`. This function is never touched by this
refinement pass except where §6 (causality) requires wiring
`research/guards/causal_guard.py` into the per-bar decision loop.

## 8. Research registry state (`research/results/registry.json`)

Top-level keys: `_comment`, `hypotheses`, `walk_forward_validation`,
`walk_forward_validation_full_universe_20260719` (5 keys total,
including `_comment`). This refinement pass will **not** write hypothesis
entries into this file — see `reports/engine_refinement/CHANGES.md`'s own
header for why (schema mismatch: `registry.json` is reserved for
statistically-tested trading hypotheses with pre-registered falsification
criteria per CLAUDE.md rule 1; a BUG_FIX/CAUSALITY_FIX/SEMANTIC_FIX entry
does not fit that schema and would pollute it).

## 9. Causality guard modules — present but unwired

`research/guards/causal_guard.py` and `research/guards/static_scan.py`
both exist on disk, confirmed real (not stubs), but confirmed **zero**
references to either from `backtesting/backtest_engine.py` — the
"Diagnostic Infrastructure" work earlier this session explicitly deferred
wiring them into the hot path as its own separate, risk-assessed change.
This refinement pass (§6) is that deferred change.

## 10. What this baseline does NOT claim

This snapshot is a factual record of the current state, not a judgment
about which engines are "good." Per the refinement plan's own rule,
every engine's `VALIDATION_STATUS` stays `UNKNOWN` throughout this
branch — nothing here ranks or promotes anything.
