# IATIS — Institutional Adaptive Trading Intelligence System

> **Phase 2 status: live data, Telegram notifications, automated scheduler.**
> Running on real market data via Twelve Data API. See the status table below
> for what's fully implemented vs. what's still stubbed.

## What's real vs. stubbed

| Component | Status |
|---|---|
| Data loader (synthetic) | ✅ Working |
| Data loader (real CSV) | ✅ Working — generic + MT4/MT5 formats, headerless + tab-separated |
| **Data loader (Twelve Data live)** | ✅ Working — rate limiting, per-interval cache, multi-symbol |
| Data validator | ✅ Working |
| Multi-timeframe sync (resample + native fetch) | ✅ Working |
| Regime detector (trend/range + ATR volatility) | ✅ Working |
| SMC engine | ✅ Working (swing structure bias; order blocks/FVG/BOS-CHOCH = Phase 3) |
| Price Action engine | ✅ Working (sigmoid-scaled MA trend + breakout detection) |
| ICT / NNFX / Quant / Macro engines | ⏳ Stub — gated behind `edge_gate.py` until hypotheses proven |
| Confluence (voting, re-normalized score, contradiction) | ✅ Working |
| Risk engine (sovereign authority) | ✅ Working — RR floor, drawdown halt, exposure cap |
| Research layer / edge gate | ✅ Working — H001 FAILED, H002 PENDING (qualified sweep) |
| No-Trade Database (JSONL) | ✅ Working — streaming append log |
| **No-Trade Database (SQLite)** | ✅ Working — queryable analytics: regime performance, engine breakdown |
| Behavior tests | ✅ Working — hand-crafted OHLCV scenarios |
| **Telegram notifications** | ✅ Working — HTML formatted, full report per decision |
| **Scheduler** | ✅ Working — multi-symbol, overlap protection, budget awareness |
| FastAPI server | ⏳ Stub — Phase 3 |
| Cloudflare webhook | ⏳ Stub — Phase 3 |
| Correlation engine | ⏳ Stub — needs multi-symbol data |
| AI explanation layer | ⏳ Stub — Phase 4 |
| Backtesting | ⏳ Stub — Phase 5 |

## Project structure

```
IATIS/
├── main.py                  # pipeline entry point
├── config.yaml               # all tunable parameters
├── .env.example               # copy to .env for Phase 2+ (API keys)
│
├── core/                     # data loading, validation, timeframe sync
├── regimes/                  # market regime + volatility classification
├── engines/                  # SMC, ICT, NNFX, Price Action, Quant (independent voters)
├── confluence/                # voting, weighted scoring, contradiction checks
├── risk/                     # risk gate, portfolio exposure, correlation (stub)
├── research/                  # edge research: hypotheses, experiments, results, edge_gate.py
├── ai/                       # explanation-only AI layer (stub, Phase 4)
├── execution/                 # Telegram bot, webhook, API server (stub, Phase 2)
├── cloudflare/                # webhook gateway worker (stub, Phase 2)
├── backtesting/               # backtest engine + metrics (stub, Phase 5)
├── storage/                   # runtime logs, decision_log.py (No-Trade Database)
└── tests/                     # smoke tests for every component above
```

## Research layer — proving an edge before trusting it

`research/` enforces a hard rule, checked in code (`research/edge_gate.py`,
called from `main.py` before any engine is activated): **no engine may be
enabled in `config.yaml` unless its backing hypothesis has a `PASSED`
status in `research/results/registry.json`.** SMC and Price Action are
exempt — they're plain technical structure/trend reads, not edge claims.
ICT, NNFX, and Quant are blocked by default until a hypothesis is written
(`research/hypotheses/`), tested (`research/experiments/`), and proven on
real historical data. See `research/README.md` for the full flow, and
`research/hypotheses/H001_liquidity_sweep_htf.md` for a worked example.

## Decision log — the No-Trade Database

Every pipeline run — `EXECUTE` and `NO_TRADE` alike — is appended to
`storage/decisions.jsonl` via `storage/decision_log.py`, with full reasons
attached (data validation failures, confluence score/vote shortfalls,
contradiction triggers, risk rejections). `summarize_decisions()` gives a
quick breakdown of how often the system trades vs. abstains and why —
this is often more diagnostic than the trade log alone, especially while
tuning thresholds in `config.yaml`.

## Running it

```bash
pip install -r requirements.txt
python main.py
```

This runs the full pipeline on synthetic data and prints a JSON report:
regime state, each engine's vote, confluence score, risk evaluation, and
the final verdict (`EXECUTE` or `NO_TRADE`).

Run the test suite:

```bash
python -m pytest tests/ -v
```

## Configuration

All tunables live in `config.yaml`: which engines are enabled, confluence
weights/thresholds, and risk parameters (per-trade risk %, max exposure,
min risk/reward, drawdown thresholds). Nothing is hardcoded in the
pipeline logic — change behavior by editing this file, not the code.

## Roadmap

- **Phase 1 (done)** — architecture skeleton, synthetic data, SMC +
  Price Action real logic, full risk gate, confluence system.
- **Phase 1.5 (done)** — research layer hardening (`edge_gate.py`
  enforced in code), No-Trade Database (`storage/decision_log.py`),
  real CSV historical data loader (`core/data_loader.py::load_from_csv`,
  generic + MT4/MT5-style formats), behavior tests with hand-crafted
  OHLCV scenarios (`tests/test_behavior.py`), and a fix to a real
  mathematical bug where `EXECUTE` was unreachable with fewer than ~4
  active engines (see `confluence/score_calculator.py` re-normalization
  and `validate_confluence_config()`).
- **Phase 2 (next)** — Telegram bot, FastAPI server, Cloudflare webhook
  gateway, live data providers (Twelve Data).
- **Phase 3** — ICT, NNFX, Quant engines with real logic (each gated
  behind a `PASSED` hypothesis — see `research/edge_gate.py`); SMC order
  blocks / FVG / BOS-CHOCH; correlation engine.
- **Phase 4** — Macro/news engine, AI explanation layer (explanation
  only — never decides the trade).
- **Phase 5** — Backtesting against real historical data once Phase 3
  engines are validated.

For the longer-term architecture (Asset Profile Layer, Session Context
Engine, Memory Layer, and more) see **[`docs/VISION_v2.md`](docs/VISION_v2.md)**
— a deliberately separate document so aspirational design never gets
mistaken for current system state. Nothing in that document is live
until it's reflected in this README's status table above.

## Design notes for contributors

- Every strategy engine must subclass `engines/base_engine.py::BaseEngine`
  and return an `EngineOutput`. Use `safe_analyze()` (not `analyze()`
  directly) when calling engines from the pipeline — it guarantees a
  crashing engine abstains instead of taking down the run.
- Don't make a stub "look done." If real logic isn't implemented yet,
  return `NEUTRAL` with a clear reason and a `TODO` docstring pointing
  to the phase where it'll be built — see `engines/ict_engine.py` for
  the pattern.
- Risk checks in `risk/risk_engine.py` are pure math with no market
  judgment calls — keep it that way. Market-judgment logic belongs in
  the strategy engines or regime detector, not in risk.
- **Never trust a hand-designed OHLCV fixture without running it
  through the actual engine first.** `find_swing_points()`'s centered
  rolling window means swing points placed too close together can fall
  inside each other's comparison window and silently fail to register.
  Three early attempts at `tests/fixtures/manual_ohlcv.py`'s contradiction
  fixture produced the wrong bias for exactly this reason before being
  corrected by actually executing them. When adding a new behavior-test
  fixture, print the engine's intermediate output (swing points, raw
  scores) and confirm it matches your intent before asserting on it.
- `confluence/score_calculator.py` re-normalizes the confluence score
  over only the engines that voted (non-`NEUTRAL`), not the full
  six-engine weight table. If you change `config.yaml`'s
  `min_engines_agreeing`, make sure it stays `<=` the number of enabled
  engines — `validate_confluence_config()` will raise at startup if not,
  but it's better to not hit that in the first place.
