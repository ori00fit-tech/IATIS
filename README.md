# IATIS

**Institutional Adaptive Trading Intelligence System**

![version](https://img.shields.io/badge/version-0.5.9-informational)
![python](https://img.shields.io/badge/python-3.11-blue)
![tests](https://img.shields.io/badge/tests-3%2C088%20functions-brightgreen)
![license](https://img.shields.io/badge/license-proprietary-lightgrey)

> A decision-governance framework for discretionary-grade trading research.
> Its output is a *verdict* — most often **NO_TRADE** — not a signal to buy or sell.

---

## What IATIS Is

IATIS evaluates whether a trade **should or should not be taken** on a fixed
universe of instruments, applies a chain of independent veto gates, and
records every decision (executed or rejected) with full provenance for later
measurement. Around that live core sits a much larger, explicitly
**exploratory** research layer — Mission Center's Optuna-driven search,
per-provider data-quality benchmarks, standalone engine ablation — whose
entire output is *leads*, never evidence, and which is structurally
incapable of touching the live decision path or the hypothesis registry.

**IATIS is not:**

- an AI trading bot,
- a signal seller,
- an automated profit engine.

It is an evidence-governed pipeline. Its measured edge is narrow and
documented: **disciplined trend-capture on carrier assets (XAUUSD, BTCUSD,
ETHUSD) at H4 with D1 confirmation, under hard risk rules (RR ≥ 2, ATR-based
stops, fractional sizing)**. The FX book is statistically indistinguishable
from breakeven across three independent confirmations. The ten-engine
confluence layer, seven-plus gates, and scoring machinery are *packaging*
whose measured marginal value is approximately zero — they exist to enforce
discipline and auditability, not because they add alpha.

Full evidence: `docs/STRATEGY_EVIDENCE_2026-07.md`,
`docs/PHILOSOPHY_AUDIT_2026-07.md`, `docs/PRODUCTION_AUDIT_2026-07.md`,
`reports/forensic/13_CONFIRMED_BUGS.md` (the running forensic bug ledger).

---

## Core Philosophy

> **"The best trade is often No Trade."**

`NO_TRADE` is a first-class, frequently-correct output. Every rejection carries
a documented reason. Every EXECUTE must survive all gates in order. Nothing
built on top of the live pipeline — Mission Center, the benchmark labs,
engine v2 variants — is allowed to weaken that discipline; each is designed
so its worst-case failure mode is "produces a misleading exploratory number,"
never "silently changes a live verdict."

---

## Key Principles

1. **NO_TRADE is a valid verdict.** Rejections are logged with their cause.
2. **Pre-registration before implementation.** A hypothesis with a decision
   rule is written into `research/results/registry.json` *before* any result
   exists (`research/edge_gate.py` enforces engine gating in code).
3. **Chronological out-of-sample or it didn't happen.** In-sample
   improvements are presumed mirages until they survive a forward slice.
4. **No lookahead.** In backtests, bar *N* sees only bars `0..N`
   (`research/guards/causal_guard.py`/`static_scan.py` enforce this
   structurally, not just by convention).
5. **Asset-aware math.** A JPY pip is not a EUR pip; carriers are not FX.
6. **AI explains, it never decides.** The confluence and risk engines are the
   sole authority for `final_verdict`. Nothing in `main.py` or `scheduler.py`
   imports the AI layer.
7. **Never change entries/exits/thresholds mid-sample.** The forward-demo
   counter is the only prospective evidence; altering the system resets it.
8. **Exploration is never evidence.** Mission Center, the Provider/Engine
   Benchmark labs, and every ad-hoc engine variant produce *leads* — a
   result only becomes evidence after a human writes a real hypothesis and
   it clears `research/edge_gate.py`'s `PROMOTION_CRITERIA`. No code path
   anywhere writes to `registry.json`, `config.yaml`, or `config/*.yaml`
   from an exploratory run; this is enforced with source-scan + live
   byte-identical-file regression tests, not just a comment.

---

## Architecture Overview

IATIS is a single Python process (pipeline + FastAPI server) running on a VPS,
backed by a Cloudflare D1 database reached through a thin authenticated Worker
proxy. There is **no local database fallback** and **no container runtime** —
deployment is systemd-based.

```
                 ┌─────────────────────────────────────────────┐
   Data layer →  │ asset-class provider chains (native-TF aware)│
                 │ crypto: ccxt/Binance → alpaca → twelve_data   │
                 │ fx/metals/indices/energy: cTrader → …         │
                 │ opt-in bridges: MT5, Dukascopy JForex          │
                 └───────────────────┬─────────────────────────┘
                                     ▼
             ┌───────────────────────────────────────────────┐
             │  DECISION PIPELINE (main.py / run_pipeline)    │
             │  Market Quality → Regime → Engines → Confluence│
             │  → MTF → Contradiction/Reversal → Correlation  │
             │  → Risk → News → Symbol Health → Meta → verdict │
             └───────────────────┬───────────────────────────┘
              scheduler.py drives │ this per-symbol on an interval
                                  ▼
     ┌───────────────┬────────────────────┬──────────────────────┐
     ▼               ▼                    ▼                      ▼
  Telegram     Cloudflare D1         JSONL audit          (on demand)
  alert     (decisions/outcomes/    trail on disk         AI explanation
  (one-way) engine votes/missions/                        layer (dashboard)
            benchmarks/etc.)
                                  ▲
                                  │ FastAPI (execution/api_server.py, ~120 routes)
                                  ▼
                    Command Center SPA (React + TS + Vite, GET /app)
                                  ▲
                                  │ operator-triggered only, no scheduler path
                                  │
        ┌─────────────────────────┴─────────────────────────┐
        │  RESEARCH & EXPLORATION LAYER (never a decision    │
        │  authority — every output is a LEAD, not evidence)  │
        │  Mission Center (Optuna search + validation +       │
        │  meta-analysis + feature mining) · Provider/Engine  │
        │  Benchmark labs · ad-hoc engine v2 variants          │
        └───────────────────────────────────────────────────┘
```

The AI layer and the research/exploration layer are both drawn off to the
side deliberately: the former runs only when a human requests it *after* a
verdict is already final; the latter runs only as an operator-triggered
background job through the whitelisted job executor, never on the
scheduler's own path, and never writes back into anything the scheduler
reads.

---

## Repository Structure

```
IATIS/
├── main.py                     # Decision pipeline entry point (run_pipeline)
├── scheduler.py                # Automated multi-symbol runner (stdlib sched)
├── config.yaml                 # Governance control plane (core)
├── config/                     # Split config: engines / symbols / risk / ai
│
├── core/                       # Data infra: provider chains, failover, MQS,
│                               #   timeframe sync, data confidence/validation
├── engines/                    # 10 strategy engines + base (4 enabled) +
│                               #   2 ad-hoc-only v2 variants (price_action, wyckoff)
├── confluence/                 # Voting, scoring, MTF, contradiction, reversal
│                               #   veto, regime weights, meta decision,
│                               #   indicator/context filters (research-only)
├── regimes/                    # TRENDING/RANGING/VOLATILE + session context
├── fundamentals/               # News calendar/blackout, news-risk, clients
│
├── risk/                       # Sovereign risk gate, live portfolio state,
│                               #   correlation + portfolio-exposure engines
│
├── storage/                    # Cloudflare D1 client + repositories, shadow
│                               #   book, calibration, audit log, migrations,
│                               #   mission/benchmark result tables
│
├── execution/                  # FastAPI server (execution/routes/, ~24
│                               #   route modules), cTrader + OANDA +
│                               #   Dukascopy JForex clients, trade executor,
│                               #   reconciliation, Telegram
│
├── integrations/ctrader/       # cTrader OAuth 2.0: authorize URL, token
│                               #   exchange/refresh, .env persistence
│
├── ai/                         # Optional AI explanation layer (providers,
│                               #   prompts, cache, dynamic weights, hypothesis
│                               #   suggestion → draft-only files)
│
├── backtest/                   # Metrics / Monte Carlo / walk-forward / runner
│                               #   + Mission Center (optimizer, mission_runner,
│                               #   mission_validator, meta_analysis, feature_mining,
│                               #   multiple_testing) + benchmark engines
│                               #   (price/news/macro/analytics/engine)
├── backtesting/                # The single simulation engine
│
├── research/                   # edge_gate.py, hypotheses (35 tracked),
│                               #   results/registry, guards (causal/static-scan),
│                               #   diagnostics (direction-symmetry scanner), manifests
│
├── cloudflare/                 # D1 Worker proxy, schema, migrations, wrangler
├── dashboard/frontend/         # Command Center SPA (React 19 + Vite), 26 tabs
├── scripts/                    # Data download (Dukascopy/cTrader/Twelve Data/
│                               #   MT5-bridge), backtests, audits, ops, backup
├── docs/                       # Audits, strategy evidence, gap analyses, roadmap
├── reports/forensic/           # Running forensic bug ledger + audit reports
├── tests/                      # 169 files, ~3,088 test functions (hermetic)
├── iatis-*.service / *.timer   # systemd units (scheduler, api, watchdog, backup,
│                               #   marketaux collector, orderflow collector)
└── requirements*.txt           # Pinned deps (+ separate cTrader requirements)
```

---

## Execution Flow

`scheduler.py` runs `main.run_pipeline` once per interval per enabled symbol
(default 60 min, `--interval` to change; `--once` for cron-style single runs).
It uses only Python's stdlib `sched` — no Celery, Redis, or external cron
dependency. Overlap protection skips a run if the previous one is still
executing. A startup message and low-credit budget warnings go to Telegram.
Pending D1 schema migrations are applied at boot on both the scheduler and
the API server, so a fresh deploy on either path never runs against a stale
schema.

On the VPS the process is supervised by systemd:

| Unit | Purpose |
|---|---|
| `iatis-scheduler.service` | Runs the scheduled pipeline |
| `iatis-api.service` | Serves the FastAPI app + dashboard |
| `iatis-watchdog.timer` (10 min) | Liveness watchdog (`scripts/watchdog.py`) |
| `iatis-d1-backup.timer` / `iatis-backup.timer` | Nightly D1 + JSONL backup |
| `iatis-marketaux-collect.timer` | Accumulates news-sentiment history for H021 (opt-in, not enabled by default) |
| `iatis-orderflow-collector.service` | Order-flow research collector (opt-in) |

---

## Decision Pipeline

Ordered stages in `main.run_pipeline`; any gate can force `NO_TRADE`:

1. **Market Quality Score** (`core/market_quality.py`) — session/volatility/
   day scoring. POOR → immediate NO_TRADE (feature-flagged).
2. **Data validation + regime detection** — TRENDING / RANGING / VOLATILE.
3. **Strategy engines** — only config-enabled engines vote.
4. **Confluence** — weighted vote + score. Three ordered score floors must be
   cleared: system-wide `min_score_to_trade` (58), per-symbol `min_score`,
   and `min_score_to_execute` (60). An **informative-weight-share gate** (0.6)
   rejects a quorum formed only because the rest of the panel was mute.
5. **MTF confirmation** — H4 signal vs D1 EMA/ADX trend.
6. **Contradiction + reversal-group veto** — prevents trend-vs-reversal
   conflicts.
7. **Correlation filter** — cap concurrent EXECUTEs per correlation group,
   seeded from *both* this run's new signals and any already-open positions
   from a prior run (so multi-symbol same-direction exposure can't slip
   through by spreading across two scheduler ticks).
8. **Risk gate** — sovereign veto: RR floor, exposure caps, real drawdown from
   `risk/live_portfolio_state.py` (a live equity curve, not hardcoded zeros).
9. **News gate** — blackout around high-impact events (NFP/FOMC/CPI).
10. **Symbol health** — auto-pause chronic underperformers.
11. **Meta decision layer** — confidence/stability check; can downgrade an
    EXECUTE to NO_TRADE (with an auditable `downgrade_reason`).

Only if every stage passes is the verdict `EXECUTE`, and only a *confirmed*
broker fill or dry-run simulation (never the raw verdict) is logged to the
outcome tracker — a declined execution attempt is never recorded as an
open position. The decision report is persisted with `provenance` (code
version, config hash, per-timeframe data version) so "never change mid-
sample" is verifiable, not just promised.

This exact 11-stage pipeline is what `backtesting/backtest_engine.py`
replays for every backtest and every Mission Center trial — the same code,
not a re-implementation — so a research finding is directly comparable to
what the live scheduler would have done, modulo whatever ad-hoc override
(timeframes/engines/indicators/context filters/risk params/confluence
quorum/engine variant) that particular research run requested.

---

## Live Decision Pipeline — Implemented Features

Verified present and wired into the running system:

- **Config-driven decision pipeline** (`main.py`) with all 11 gates above.
- **Stdlib scheduler** (`scheduler.py`) with overlap protection, budget
  awareness, and cross-run correlation-filter seeding.
- **Ten strategy engines** implemented; **four enabled** (SMC, Price Action,
  NNFX, Wyckoff — the frozen `prod4` set). All ten share a common
  Feature-Extraction / Decision-Logic split, a unified indicator library
  (`utils/indicators.py`), and every scoring threshold externalized into
  `config/engines.yaml`'s `thresholds:` block at the code's own prior
  hardcoded values — a refactor proven bit-identical to the pre-refactor
  engines via golden-value regression tests, not just asserted.
- **Confluence subsystem**: weighted voting, scoring, MTF, contradiction and
  reversal-veto, regime-aware weights, meta-decision layer.
- **Sovereign risk engine** with a *real* portfolio state (drawdown, open
  risk, correlated exposure derived from trade history).
- **Asset-class provider chains** with native-timeframe-aware failover
  (ccxt/Binance, Alpaca, cTrader, Twelve Data, FCS, Alpha Vantage, Finnhub;
  MT5 and Dukascopy JForex as opt-in, unofficial bridges — see below).
- **Cloudflare D1 storage** via an authenticated Worker proxy — decisions,
  engine votes, outcomes, engine performance, experience DB, shadow book,
  journal, execution quality, mission/benchmark result tables; atomic
  multi-statement batches; 14-version schema-migration chain.
- **FastAPI server** (`execution/api_server.py`) exposing ~120 endpoints
  across 24 route modules.
- **Command Center dashboard** — React 19 + TypeScript SPA (Vite), 26
  deep-linkable tabs across 7 sections, served at `GET /app` after build,
  with a dedicated mobile nav shell below 1024px viewport width.
- **cTrader broker client** with real OAuth 2.0 web authorization flow
  (`integrations/ctrader/`, "Connect cTrader" dashboard tab), automatic
  token refresh with an expiry margin, and a deterministic self-heal retry
  when the broker rejects a stale token (`CH_ACCESS_TOKEN_INVALID`) instead
  of blindly re-sending it on the next reconnect. Live symbol-spec fetch,
  bounded-backoff auto-reconnect, and position reconciliation on every
  (re)connect. **OANDA client** as a fallback path. **Dukascopy JForex
  bridge** as a third, opt-in, fixed-quantity-only broker path.
- **Broker reconciliation** (`execution/reconciliation.py`) — self-gated to
  live-order mode, with an actual repair endpoint (not detection-only) for
  broker-vs-internal position mismatches.
- **One-way Telegram alerts** (`execution/telegram_bot.py`) — signal/verdict
  and ops notifications; flood-protected. (No inbound command interface.)
- **Backtesting stack**: single simulation engine (`backtesting/`) composed
  with metrics/Monte Carlo/walk-forward/robustness/report modules
  (`backtest/`), the same code Mission Center's exploratory trials run
  through.
- **Research governance**: `edge_gate.py` blocks unproven engines at boot;
  `registry.json` (35 hypotheses tracked) is the single source of hypothesis
  truth; manifest and survivorship checkers enforce clean-tree provenance;
  a forensic bug ledger (`reports/forensic/13_CONFIRMED_BUGS.md`) records
  every confirmed defect with reproduction, fix, and regression test.
- **Nightly D1 + JSONL backup** with re-load verification and rotation.
- **Rotating file logging** (`utils/logger.py`), `IATIS_LOG_LEVEL` override.
- **Hermetic test suite** — ~3,088 test functions across 169 files;
  `conftest.py` blocks real sockets and strips real credentials, faking D1
  with in-memory SQLite.
- **CI** (GitHub Actions): ruff (E9/F821 gate), full pytest suite, pip-audit.

---

## Research & Exploration Layer

A separate, much larger set of tools sits *around* the live pipeline for
generating and stress-testing hypotheses — none of it is a decision
authority, all of it is reachable only through an operator-triggered
whitelisted background job, and none of it can write to `config.yaml`,
`config/*.yaml`, or `research/results/registry.json`. Every phase of this
layer was built with a hard-block guarantee — a source-scan for any write
call plus a live byte-identical-file test — because that safety property is
the entire point.

### Mission Center — AI Research Lab

`backtest/mission_runner.py` + `backtest/optimizer.py`, driven from the
"Mission Center" dashboard tab and `POST /research/missions`. A *mission*
runs an Optuna-sampled search (Grid, Random, TPE/Bayesian, or NSGA-II
genetic) across a joint space of timeframes, enabled engines, indicator
filters, context filters (session/day-of-week/volatility regime/market
regime/direction), ad-hoc risk-parameter ranges, and — on the two live
engines with a v2 variant — which engine version to run, all via the same
ephemeral `build_engine_config_override()` merge point the backtest engine
already exposes. A mission can also search across **named hypothesis
bundles** the operator defines up front (e.g. "SMC only", "ICT + London +
Trending"), so the sampler explores genuinely different signal
configurations instead of only varying risk parameters around one fixed
combo. Every trial is resumable (crash-safe, D1-backed, per-trial
reproducibility fingerprint) and duplicate-configuration-aware (an
identical resolved config is never re-backtested).

Nothing here is a verdict. Every mission report leads with a real,
Bonferroni-corrected significance banner — *"Of N trials, X would look
significant by chance alone; only Y survive correction"* — computed by
`backtest/multiple_testing.py`, the same discipline this repo already
required of every hand-run hypothesis. A promising trial can be turned into
a Validation run (`backtest/mission_validator.py`): re-evaluated,
Monte-Carlo'd, walk-forward'd, and robustness-swept around its own chosen
parameters, either as a `SAME_SYMBOL` self-check or a `CROSS_SYMBOL`
generalization test against ≥2 *other* symbols (a single-symbol validation
is refused outright — it can't distinguish an edge from curve-fitting).
Additional diagnostics — Effective Sample Size significance, regime
robustness, a stability score, and a cost-stress test — attach to each
validation. `backtest/meta_analysis.py` pools an entire mission's trials
for cross-trial consensus claims (with a real binomial sign test, not a
guess) and a ranked, never-auto-selected "unexplored opportunities" list.
`backtest/feature_mining.py` does the same for ~28-34 decision-time
features (regime, MTF alignment, reversal-veto state, per-engine
structural facts) that the backtest engine already computes and used to
discard. A "Suggest hypothesis from this trial" action grounds the AI
Copilot's `/ai/suggest-hypothesis` in the mission's own real numbers and
can save a `## DRAFT` markdown file into `research/hypotheses/drafts/` —
never a registry write, and the file's own banner says so.

A dedicated `research/diagnostics/direction_symmetry.py` static scanner
(AST-based, advisory-only) checks every engine/confluence/risk file for a
one-sided BULLISH/BEARISH branch with no mirror — a systemic-bug class
this whole layer exists partly to catch.

### Provider Benchmark & Data Quality Lab

Four domain benchmarks (`backtest/price_benchmark.py`, `news_benchmark.py`,
`macro_benchmark.py`, `analytics_benchmark.py`), each fetching the *same*
symbol/window from every configured provider and scoring real dimensions —
completeness (with market-closure vs. genuine-gap classification),
per-field correctness against a cross-provider median consensus (not a
naive two-provider agreement check — the lab explicitly measures "does a
provider agree with the *group*," not just its nearest neighbor),
timestamp-boundary alignment, OHLC integrity, freshness, latency, and
(News/Analytics only) source diversity, duplicate-rate, and — for
MarketAux specifically — a real repeat-query determinism check. Every
failed fetch is still recorded as a row, never silently dropped. A
`GET /research/provider-scorecard` + `GET /research/best-provider` surface
combines all four domains into an advisory-only, per-symbol/per-series
routing recommendation an operator reviews and applies manually via
`config.yaml`'s `provider_chains` — the lab never reorders a chain itself.

### Engine Benchmark

`backtest/engine_benchmark.py` — standalone, single-engine ablation
backtests (explicitly exploratory, never evidence) plus live trade
attribution, surfaced as its own "Engine Benchmark" dashboard tab.

### Trusted Data Center

A pre-flight data-readiness layer (`storage/market_bars.py`,
`scripts/push_bars_to_d1.py`) that checks a symbol/timeframe actually has
enough real bars in the warehouse before a Mission Center trial is allowed
to run against it, and prefers a genuine native-timeframe CSV over an
H1-resampled substitute whenever one exists — closing a class of trial
where a "H4 trial" was silently computed from resampled H1 data.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| API | FastAPI + Uvicorn |
| Data | pandas, numpy, pydantic |
| Market data | ccxt, Twelve Data, cTrader Open API, FCS, Alpha Vantage, Finnhub, Alpaca, FRED, MarketAux, Dukascopy (historical + JForex bridge), MT5 bridge, yfinance (offline diffs only) |
| Storage | Cloudflare D1 via Worker proxy (no local DB) |
| Research | Optuna (TPE/NSGA-II/Grid/Random samplers), statsmodels (ADF stationarity test) |
| Frontend | React 19, TypeScript, Vite, Tailwind v4, TanStack Table/Query, Zustand, Radix UI, cmdk, Framer Motion, ECharts, lightweight-charts |
| Alerts | Telegram Bot API (outbound only) |
| Scheduling | Python stdlib `sched` + systemd timers |
| CI | GitHub Actions (ruff, pytest, pip-audit) |

There is no Docker/compose file and no `pyproject.toml`/`setup.py` — the
project is run directly from source with a venv.

---

## Installation

```bash
git clone <repo-url> IATIS && cd IATIS
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# cTrader broker support (live VPS only; safely omitted for CI/tests):
pip install -r requirements-ctrader.txt
cp .env.example .env   # then fill in secrets
```

Cloudflare D1 is **required** (no local fallback). Provision it before first
run — see `cloudflare/README.md`:

```bash
cd cloudflare
wrangler d1 create iatis                     # copy database_id into wrangler.toml
wrangler d1 execute iatis --remote --file=schema.sql
wrangler secret put D1_PROXY_TOKEN
wrangler deploy
```

---

## Configuration

`config.yaml` is a live control plane — every section maps to a real
conditional in code. The symbol universe, engine activation, risk limits, and
AI layer are split into `config/{symbols,engines,risk,ai}.yaml` and merged back
into one effective dict by `utils/helpers.py::load_config()`.

| File / section | Controls |
|---|---|
| `config.yaml` → `data` | Source, symbols, timeframes, provider chains, bar depth |
| `config.yaml` → `confluence` | Quorum, score floor, informative-weight-share, per-engine weights |
| `config.yaml` → `execution` | Broker, `dry_run`, `allow_live_trading`, max trades, execute floor |
| `config.yaml` → `features` | Real on/off gates (market quality, correlation, reconciliation, …) |
| `config.yaml` → `market_quality` / `monitoring` / `portfolio` | MQS grades, health thresholds, correlation cap |
| `config/engines.yaml` | `enabled.<name>`, `thresholds.<name>` (every engine's scoring constants, incl. `_v2` blocks), `smc_full_spec`, version metadata |
| `config/symbols.yaml` | Per-symbol `enabled`, `min_score`, `rr` + governance record |
| `config/risk.yaml` | RR floor, exposure caps, drawdown thresholds, `starting_balance` (**frozen**) |
| `config/ai.yaml` | AI provider order, model, cache TTLs |

Every research-layer override (Mission Center's timeframes/engines/
indicators/context-filters/risk-params/confluence-quorum/engine-variant
knobs) is an **ephemeral, in-memory merge** over a `load_config()` snapshot,
never a write to any file above — the live config is the same file whether
or not a mission is running.

> **Version marker (v0.5.9):** the release version is unified across
> `config.yaml` (`system.version`), `cloudflare/package.json`, and
> `dashboard/frontend/package.json` (+ its lockfile). The string is
> documentation only — it is not read by the pipeline.

---

## Environment Variables

Secrets live in `.env` only — never in config, chat, issues, or commits.

| Variable | Required | Purpose |
|---|---|---|
| `D1_WORKER_URL`, `D1_PROXY_TOKEN` | **Yes** | Cloudflare D1 storage proxy |
| `API_SERVER_KEY` | **Yes (prod)** | FastAPI/dashboard auth |
| `TWELVE_DATA_API_KEY` | Recommended | Primary FX/metals/indices data |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional | Outbound alerts |
| `CTRADER_*` (`CLIENT_ID`/`CLIENT_SECRET`/`REDIRECT_URI`/`ACCESS_TOKEN`/`REFRESH_TOKEN`/`ACCESS_TOKEN_EXPIRY`/`ENVIRONMENT`/`OAUTH_SCOPE`) | Optional | Broker — OAuth 2.0 web flow, see `.env.example`'s full block + the "cTrader Connection" dashboard tab / `integrations/ctrader/` |
| `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, `OANDA_ENVIRONMENT` | Optional | Fallback broker |
| `DUKASCOPY_JFOREX_BRIDGE_URL`, `DUKASCOPY_JFOREX_ENVIRONMENT` | Optional | Opt-in, unofficial data/execution bridge — see `docs/DUKASCOPY_JFOREX_BRIDGE_SETUP.md` |
| `MT5_BRIDGE_URL`, `MT5_BRIDGE_TOKEN` | Optional | Opt-in, unofficial data bridge — see `docs/MT5_BRIDGE_SETUP.md` |
| `ALPACA_API_KEY/_SECRET`, `FCS_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY` | Optional | Failover / macro data |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Optional | AI layer (only the selected provider's key) |
| `MARKETAUX_API_KEY`, `TAAPI_API_KEY` | Optional | Research/news infrastructure (economic calendar itself is keyless — Forex Factory) |
| `ENV` | Optional | `development` enables Swagger + relaxes auth |
| `IATIS_LOG_LEVEL` | Optional | Log-level override |

Unset optional keys fall through cleanly via the provider chains.

---

## Running Locally

```bash
# Single pipeline run for the default symbol
python main.py

# Scheduled multi-symbol runner
python scheduler.py --interval 60
python scheduler.py --once --symbols EUR/USD XAU/USD

# API server + dashboard
uvicorn execution.api_server:app --host 0.0.0.0 --port 8000
```

Tests are hermetic and need no secrets:

```bash
pytest tests/ -q
```

---

## Docker

Not provided. The project ships no `Dockerfile` or `docker-compose.yml`;
deployment is systemd-based (see below). Containerization is a possible future
task, not a current capability.

---

## Cloudflare Deployment

Two Cloudflare pieces are involved:

- **D1 + Worker proxy** (`cloudflare/`) — the system-of-record for all
  decisions/outcomes/missions/benchmarks. The Worker (`worker.js`) exposes
  `POST /d1/exec` and `POST /d1/batch`, both guarded by a
  `Bearer D1_PROXY_TOKEN` header, and is the *only* path from the VPS
  Python process to D1.
- **Cloudflare Tunnel** (optional, `scripts/setup_cloudflare_tunnel.sh`) —
  exposes the dashboard/API without opening VPS ports.

R2/S3/B2 object storage is **not** a native integration. It is only reachable
as an optional off-site backup target via an rclone remote in
`scripts/backup_d1.sh` (`BACKUP_RCLONE_REMOTE`); by default backups stay on the
VPS disk.

---

## Dashboard (Command Center)

`dashboard/frontend/` is a React 19 + TypeScript SPA built with Vite, served at
`GET /app` once built:

```bash
cd dashboard/frontend && npm install && npm run build   # dist/ is gitignored
```

26 deep-linkable tabs (⌘K palette, per-tab error isolation, smart polling,
route-level code splitting), grouped into 7 sidebar sections. A dedicated
bottom-nav-and-drawer shell replaces the sidebar below a 1024px viewport,
tables collapse into cards below 640px, and every Mission Center panel
carries its own "collapsible, N active" section state:

| Section | Tabs |
|---|---|
| Overview | Mission Control, Live Signals, Alert Center |
| Live Ops | Forward Demo, Trade Journal, Risk Center, Execution Quality, Portfolio Reconciliation |
| Research & Backtests | Research & Backtests, Backtesting Lab, Backtesting Charts, Experiment Runner, Mission Center |
| Data & Providers | Data Center, Provider Eval, cTrader Connection |
| Engines & AI | Engine Monitor, Engine Benchmark, AI Decision Center, AI Settings |
| System & Audit | System Audit, Live Logs, File Explorer, Reports, VPS Operations |
| Meta | Roadmap |

Auth uses the same session-cookie / `X-API-Key` scheme as the API. The
cTrader OAuth callback route is the one exception, authenticated instead by
a short-lived CSRF `state` token plus the existing session cookie, since the
browser is mid-redirect from cTrader's own domain when it lands there.

---

## REST API

~120 endpoints across 24 route modules under one FastAPI app
(`execution/api_server.py`). Selected groups:

| Group | Endpoints |
|---|---|
| Core pipeline | `GET /health`, `GET /health/full`, `POST /analyze/{symbol}`, `GET /candles/{symbol}` |
| Decisions & outcomes | `GET /decisions`, `GET /outcomes`, `POST /outcomes/{id}/close`, `GET /stats`, `GET /journal`, `POST /journal/{id}/annotate` |
| Health & data | `GET /symbol-health`, `GET /engine-stats`, `GET /data-health`, `GET /data-confidence`, `GET /reconciliation`, `POST /reconciliation/repair` |
| Research & audit | `GET /research`, `GET /research/{id}`, `GET /research/compare`, `GET /research/integrity`, `GET /research/edge-library`, `GET /backtest-results`, `GET /meta-analysis`, `GET /philosophy-audit`, `GET /forward-review`, `GET /research/diagnostics/direction-symmetry` |
| Mission Center | `POST/GET /research/missions`, `GET /research/missions/{id}`, `POST /research/missions/{id}/validate`, `GET .../meta-analysis`, `GET .../feature-mining` |
| Provider/Engine benchmarks | `POST/GET /research/provider-benchmark`, `/research/news-benchmark`, `/research/macro-benchmark`, `/research/analytics-benchmark`, `/research/engine-benchmark`, `GET /research/provider-scorecard`, `GET /research/best-provider` |
| Governance ledgers | `GET /shadow-book`, `GET /audit-log`, `GET /provider-chains`, `GET /execution-quality`, `GET /metrics` |
| Experience DB | `GET /experience/summary`, `GET /experience/query`, `GET /experience/pattern` |
| Ops | `GET /budget`, `GET /alerts`, `POST /ops/reload-config`, `GET /reports/{kind}`, `GET /files/*`, `GET /logs*`, experiment-runner routes |
| AI layer | `POST /ai/explain-trade`, `GET /ai/explain/{id}`, `GET /ai/news-analysis`, `GET /ai/macro-analysis`, `GET /ai/daily-report`, `POST /ai/research-summary`, `POST /ai/suggest-hypothesis`, `POST /ai/save-hypothesis-draft`, `GET/POST /ai/settings`, `POST /ai/optimize-weights` |
| cTrader OAuth | `GET /ctrader/status`, `GET /ctrader/authorize`, `GET /ctrader/callback` |
| Auth & UI | `POST/GET /login`, `GET /logout`, `GET /dashboard` (legacy SSR), `GET /app` (SPA) |

Swagger/OpenAPI docs are disabled unless `ENV=development`.

---

## Command Center

The Command Center is the browser control surface: the SPA above plus its
read-only/whitelisted ops endpoints (config reload, diagnostics, log tailing,
file browsing, subprocess experiment/mission/benchmark jobs). It is a
*window into* the system — it does not, and cannot, alter a trading verdict
or the hypothesis registry.

---

## Research & Backtesting

Two composed packages, not duplicates:

- **`backtesting/backtest_engine.py`** — the single simulation engine
  (gap-aware exits, measured spreads/slippage, pipeline-aligned parameters,
  the exact 11-stage decision logic above). Every override channel — engine
  toggle, timeframe, indicator/context filter, risk override, confluence
  quorum, engine variant — merges into one ephemeral `engine_config` dict
  this function accepts; the live pipeline never passes one.
- **`backtest/`** — metrics (Sharpe/Sortino/Calmar/SQN/Ulcer/Kelly/VaR/CVaR,
  drawdown), Monte Carlo, HTML reports, walk-forward, robustness sweeps,
  and the `runner.py` adapter — plus Mission Center's orchestration
  (`optimizer.py`, `mission_runner.py`, `mission_validator.py`,
  `meta_analysis.py`, `feature_mining.py`, `multiple_testing.py`) and the
  four benchmark-lab engines:

```bash
python -m backtest.runner --symbols EURUSD GBPUSD --data-dir data
python -m backtest.walk_forward --symbols EURUSD GBPUSD
python -m backtest.mission_runner --symbols EURUSD --sampler tpe --n-trials-per-symbol 50
```

Historical PF/WR figures are deliberately **not** hardcoded here — the engine
evolves, and a stale table would mislead. Runs write to `reports/` alongside
the exact config used.

**Research governance.** No engine may be enabled without a `registry.json`
entry at `RESEARCH` or better, enforced by `research/edge_gate.py`. The
hypothesis ledger currently tracks **35 entries** (11 FAILED, 10 RESEARCH,
5 PLANNED, 3 RESOLVED, 3 NULL, 2 PASSED, 1 ABANDONED); the full, current
table lives in `research/results/registry.json` rather than duplicated here
(a stale copy would itself violate the project's own evidence discipline).
Selected highlights:

| ID | Title | Status |
|---|---|---|
| H001/H002/H002b | Liquidity-sweep entries | FAILED |
| H008/H008b/H008c | BOS + FVG confluence | FAILED / ABANDONED |
| **H009** | 6-engine confluence as signal | **PASSED** *(under-evidenced, flagged at boot)* |
| **H013** | Reversal-group counter-signal | **PASSED** |
| H017 | SMC full-spec internal confluence | FAILED |
| H018 | Structure-based stops | PLANNED (frozen until ~100 closed demo trades) |
| H019 | Crypto positioning/sentiment | PLANNED |
| H021 | MarketAux news sentiment A/B | PLANNED (waiting on `iatis-marketaux-collect.timer` accumulation) |
| H020, H024, H025, H033 | Info-weight-share sensitivity, hard regime gate, compression-as-predictability, meta-model self-confidence gating | FAILED — see CLAUDE.md's dead list; none of these are to be rebuilt without a new, distinctly-argued hypothesis |

> **Note on H009:** `edge_gate.py` flags H009's `PASSED` at boot because its
> evidence block does not meet `PROMOTION_CRITERIA` (≥300 OOS trades, OOS
> PF ≥ 1.2, walk-forward, Monte Carlo). Per the gate's own rule, an
> under-evidenced `PASSED` must be treated as `RESEARCH`. This is by design —
> the promotion bar is code, not prose.

---

## Risk Management

- **Sovereign risk gate** (`risk/risk_engine.py`): RR floor (`min_risk_reward`
  2.0), max exposure (5%), drawdown reduce/stop (10% / 15%), fractional sizing
  (0.25%–1% per trade), ATR×2.5 stops.
- **Live portfolio state** (`risk/live_portfolio_state.py`): drawdown, open
  risk, and correlated exposure derived from a real equity curve based on
  `starting_balance`, not placeholders.
- **Correlation + exposure engines**: cap concurrent EXECUTEs per correlation
  group across a run *and* against already-open positions from prior runs;
  per-run portfolio exposure accounting.
- **Money-safety gates**: `dry_run` and `allow_live_trading` default to safe;
  the executor hard-refuses real-money orders on a non-demo account unless
  explicitly enabled, on every broker path (cTrader/OANDA/Dukascopy JForex).
  `risk/risk.yaml` is **frozen** until the shadow book reaches ~50 samples
  per gate.

---

## Logging

`utils/logger.py` provides a root logger writing to stderr (captured by
journald under systemd) and, when `logging.file` is set, an additional
rotating file handler (10 MB × 5 backups). Level is `INFO` by default,
overridable via `IATIS_LOG_LEVEL`. Every decision also produces a structured
JSONL audit record and a D1 row with full provenance.

---

## Decision Database

All durable state lives in **Cloudflare D1**, accessed only through the Worker
proxy (`storage/d1_client.py` → `cloudflare/worker.js` → D1 binding). Core
tables: `decisions`, `engine_votes`, `outcomes`, `engine_performance`,
`experiences`, `shadow_signals`, plus the research/exploration layer's own
tables (`research_missions`, `research_mission_trials_v2`,
`research_mission_validations`, `provider_benchmark_*`, `news_benchmark_*`,
`macro_benchmark_*`, `analytics_benchmark_*`, `engine_benchmark_*`).
Multi-statement writes (a decision plus its engine votes) are committed
atomically via D1's `batch()` API. Migrations live in `storage/migrations.py`
(14 versions, additive-only — no destructive `ALTER`) and
`cloudflare/migrations/`. Nightly backups (`scripts/backup_d1.py`) dump,
gzip, verify-reload, and rotate every table, with a JSONL copy alongside.

---

## Testing

- **~3,088 test functions across 169 files.** Fully hermetic: `tests/conftest.py`
  blocks real sockets, strips real credentials, and fakes the D1 Worker with a
  per-test in-memory SQLite connection.
- Coverage spans data providers, all ten engines (incl. v2 variants), the
  Feature-Extraction/Decision-Logic split, confluence/meta-decision, risk,
  storage resilience, migrations, API contract, execution logic
  (cTrader OAuth/OANDA/Dukascopy JForex), reconciliation, the research layer
  (Mission Center's optimizer/validator/meta-analysis/feature-mining, all
  four benchmark labs), and causal/static guards — including a recurring
  class of hard-block test (source-scan + live byte-identical-file check)
  proving the research layer can never write to `config.yaml`,
  `config/*.yaml`, or `registry.json`.

```bash
pytest tests/ -q
```

---

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) on every PR and push to `main`:

1. **ruff** — gated on `E9` (syntax) + `F821` (undefined names) only; the
   wider style backlog is intentionally not yet gated.
2. **pytest** — the full hermetic suite.
3. **pip-audit** — known-vulnerability scan of the installed environment.

Deployment is manual/scripted on the VPS (`scripts/deploy_vps.sh`): venv +
deps, a sanity compile/test slice, systemd unit refresh, restart, and an
optional read-only security review. There is no automated CD to production.

---

## Security

- Session cookie holds a rotating `session_id`, never the raw API key;
  `HttpOnly + Secure + SameSite=Lax`.
- Dashboard session tokens (legacy SSR path) expire on the same TTL-purge
  precedent as the main auth store — no unbounded in-memory session growth.
- `hmac.compare_digest` for key comparison (timing-attack resistant); the D1
  Worker uses a constant-time token comparison.
- Symbol input validated against `^[A-Z]{2,6}(/[A-Z]{2,6})?$`.
- Swagger/OpenAPI disabled unless `ENV=development`.
- Secrets confined to `.env`; `EnvironmentFile` keeps tokens out of systemd
  units and journald. cTrader's OAuth client secret is exchanged server-side
  only — the ~1-minute authorization code never reaches the frontend.
- Dependency hygiene: pinned requirements (including a real, verified
  ceiling on `ctrader-open-api` after a yanked-release incident), security-
  driven floors, pip-audit in CI.
- systemd units run sandboxed (`NoNewPrivileges`, `PrivateTmp`,
  `ProtectSystem=full`, resource limits).
- Long-running background job history (`_jobs` in the experiment/mission/
  benchmark executor) is bounded by a lazy prune-on-access eviction, not
  left to grow unbounded across server uptime.
- **Known gap:** units still run as `User=root` pending the service-user
  migration (`scripts/setup_service_user.sh` exists; not yet executed).

---

## Performance

No performance benchmarks are published here on purpose. The pipeline is
I/O-bound on market-data fetches; runtime is dominated by provider latency, not
computation. Data-budget math (Twelve Data free plan) is documented in
`scheduler.py`. Any strategy performance figure must be reproduced from a
current `backtest.runner` run against current data — this project does not ship
fixed PF/WR numbers, and treats stale ones as misleading.

---

## Known Limitations

- **Narrow measured edge.** Only carrier trend-capture (XAUUSD/BTCUSD/ETHUSD,
  H4/D1) is evidenced; the FX book is ~breakeven; the engine/gate machinery's
  marginal value is ≈ 0.
- **Cloudflare-coupled.** D1 is mandatory with no local fallback; the system
  cannot run fully offline.
- **Single-operator auth.** No multi-user store, RBAC, or JWT.
- **root systemd units.** Service-user migration not yet executed.
- **MT5 and Dukascopy JForex bridges are unofficial and unsupported** — both
  route through a community/third-party local HTTP bridge rather than a real
  network protocol (unlike cTrader's genuine Protobuf/OAuth API), are opt-in
  only, and are excluded from every default provider chain.
- **No Docker / no packaging** (`pyproject.toml` absent).
- **Backups stay on-box by default** unless an rclone remote is configured.
- **H009 `PASSED` is under-evidenced** and flagged as such at every boot.
- **The Provider/Engine Benchmark labs and Mission Center are advisory-only
  by design** — a high score or a promising trial is a starting point for a
  human-written hypothesis, never a routing decision or an edge on its own.

---

## Roadmap

The direction is to grow IATIS from a trading-decision engine into a full
**Institutional Trading Intelligence Platform (ITIP)** — where every release
adds real production value, not just features, and the deterministic core stays
the sole decision authority. Full detail, including the engine maturity model
and per-version exit criteria, is in [`docs/ROADMAP.md`](docs/ROADMAP.md).

Near-term work (evidence-gated, not feature-toggled):

1. **Complete the forward-demo sample** (~100 closed cTrader-demo trades) and
   apply the pre-registered D001/D002 rules via `scripts/forward_review.py`.
2. **Service-user migration** — move all `iatis-*.service` units off root.
3. **H018** (structure-based stops) once the sample threshold is reached.
4. **H021** (MarketAux sentiment) once `iatis-marketaux-collect.timer` has
   accumulated enough live history for a valid A/B.
5. Off-site backups by default (documented rclone/R2 remote).
6. Continue triaging the remaining forensic-audit findings recorded in
   `reports/forensic/` one confirmed bug at a time.

> Every roadmap item that touches trading behavior (enabling an engine,
> changing a threshold, adaptive weighting, promoting a Mission Center
> finding) is **measurement work gated by a pre-registered hypothesis
> clearing the OOS bar** — not a feature toggle. See `CLAUDE.md` and the
> dead list.

---

## Contributing

This is a single-operator research repository governed by strict evidence
rules (`CLAUDE.md`). Before changing anything:

1. Read `CLAUDE.md` and the **dead list** — measured-and-buried ideas are not
   to be rebuilt.
2. Pre-register any new strategy hypothesis in `research/results/registry.json`
   with a decision rule *before* producing results.
3. Keep negative results — they are committed with the same care as positive
   ones.
4. Never alter entries/exits/thresholds while the forward-evidence counter is
   open.
5. A promising Mission Center trial or benchmark-lab score is a lead, not a
   promotion — it still needs a full, pre-registered hypothesis before it
   can change anything live.
6. Run `pytest tests/ -q` and the philosophy audit
   (`scripts/philosophy_audit.py`) before proposing changes.

---

## License

Proprietary. No license file is present in the repository; all rights reserved
by the owner unless a `LICENSE` is added.

---

## Disclaimer

IATIS is a research and paper-trading platform. It is **not** financial advice,
**not** a signal service, and **not** an automated money-making system. Live
order placement exists but defaults to safe (`dry_run: true`,
`allow_live_trading: false`), and forward trading currently runs only on a
demo account for evidence collection. Trading carries substantial risk of loss.
Nothing here is a promise of profitability; the maintainers document what has
been *measured*, including that most of the system's cleverness adds no
measured edge. Use at your own risk.
