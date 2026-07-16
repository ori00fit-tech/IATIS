# IATIS

**Institutional Adaptive Trading Intelligence System**

![version](https://img.shields.io/badge/version-0.5.9-informational)
![python](https://img.shields.io/badge/python-3.11-blue)
![tests](https://img.shields.io/badge/tests-869%20functions-brightgreen)
![license](https://img.shields.io/badge/license-proprietary-lightgrey)

> A decision-governance framework for discretionary-grade trading research.
> Its output is a *verdict* — most often **NO_TRADE** — not a signal to buy or sell.

---

## What IATIS Is

IATIS evaluates whether a trade **should or should not be taken** on a fixed
universe of instruments, applies a chain of independent veto gates, and
records every decision (executed or rejected) with full provenance for later
measurement.

**IATIS is not:**

- an AI trading bot,
- a signal seller,
- an automated profit engine.

It is an evidence-governed pipeline. Its measured edge is narrow and
documented: **disciplined trend-capture on carrier assets (XAUUSD, BTCUSD,
ETHUSD) at H4 with D1 confirmation, under hard risk rules (RR ≥ 2, ATR-based
stops, fractional sizing)**. The FX book is statistically indistinguishable
from breakeven across three independent confirmations. The nine-engine
confluence layer, seven gates, and scoring machinery are *packaging* whose
measured marginal value is approximately zero — they exist to enforce
discipline and auditability, not because they add alpha.

Full evidence: `docs/STRATEGY_EVIDENCE_2026-07.md`,
`docs/PHILOSOPHY_AUDIT_2026-07.md`, `docs/PRODUCTION_AUDIT_2026-07.md`.

---

## Core Philosophy

> **"The best trade is often No Trade."**

`NO_TRADE` is a first-class, frequently-correct output. Every rejection carries
a documented reason. Every EXECUTE must survive all gates in order.

---

## Key Principles

1. **NO_TRADE is a valid verdict.** Rejections are logged with their cause.
2. **Pre-registration before implementation.** A hypothesis with a decision
   rule is written into `research/results/registry.json` *before* any result
   exists (`research/edge_gate.py` enforces engine gating in code).
3. **Chronological out-of-sample or it didn't happen.** In-sample
   improvements are presumed mirages until they survive a forward slice.
4. **No lookahead.** In backtests, bar *N* sees only bars `0..N`.
5. **Asset-aware math.** A JPY pip is not a EUR pip; carriers are not FX.
6. **AI explains, it never decides.** The confluence and risk engines are the
   sole authority for `final_verdict`. Nothing in `main.py` or `scheduler.py`
   imports the AI layer.
7. **Never change entries/exits/thresholds mid-sample.** The forward-demo
   counter is the only prospective evidence; altering the system resets it.

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
                 └───────────────────┬─────────────────────────┘
                                     ▼
             ┌───────────────────────────────────────────────┐
             │  DECISION PIPELINE (main.py / run_pipeline)    │
             │  Market Quality → Regime → Engines → Confluence│
             │  → MTF → Contradiction/Reversal → Risk → News  │
             │  → Symbol Health → Meta Decision → verdict      │
             └───────────────────┬───────────────────────────┘
              scheduler.py drives │ this per-symbol on an interval
                                  ▼
     ┌───────────────┬────────────────────┬──────────────────────┐
     ▼               ▼                    ▼                      ▼
  Telegram     Cloudflare D1         JSONL audit          (on demand)
  alert     (decisions/outcomes/    trail on disk         AI explanation
  (one-way) engine votes/etc.)                            layer (dashboard)
                                  ▲
                                  │ FastAPI (execution/api_server.py, ~50 routes)
                                  ▼
                    Command Center SPA (React + TS + Vite, GET /app)
```

The AI layer is drawn off to the side deliberately: it runs only when a human
requests it from the dashboard, *after* a verdict is already final.

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
├── engines/                    # 10 strategy engines + base (4 enabled)
├── confluence/                 # Voting, scoring, MTF, contradiction, reversal
│                               #   veto, regime weights, meta decision
├── regimes/                    # TRENDING/RANGING/VOLATILE + session context
├── fundamentals/               # News calendar/blackout, news-risk, clients
│
├── risk/                       # Sovereign risk gate, live portfolio state,
│                               #   correlation + portfolio-exposure engines
│
├── storage/                    # Cloudflare D1 client + repositories, shadow
│                               #   book, calibration, audit log, migrations
│
├── execution/                  # FastAPI server, cTrader + OANDA clients,
│                               #   trade executor, reconciliation, Telegram
│
├── ai/                         # Optional AI explanation layer (providers,
│                               #   prompts, cache, dynamic weights)
│
├── backtest/                   # Metrics / Monte Carlo / walk-forward / runner
├── backtesting/                # The single simulation engine
│
├── research/                   # edge_gate.py, hypotheses, results/registry,
│                               #   guards (causal/static-scan), manifests
│
├── cloudflare/                 # D1 Worker proxy, schema, migrations, wrangler
├── dashboard/frontend/         # Command Center SPA (React 19 + Vite)
├── scripts/                    # Data download, backtests, audits, ops, backup
├── docs/                       # Audits, strategy evidence, gap analyses
├── tests/                      # 74 files, ~869 test functions (hermetic)
├── iatis-*.service / *.timer   # systemd units (scheduler, api, watchdog, backup)
└── requirements*.txt           # Pinned deps (+ separate cTrader requirements)
```

---

## Execution Flow

`scheduler.py` runs `main.run_pipeline` once per interval per enabled symbol
(default 60 min, `--interval` to change; `--once` for cron-style single runs).
It uses only Python's stdlib `sched` — no Celery, Redis, or external cron
dependency. Overlap protection skips a run if the previous one is still
executing. A startup message and low-credit budget warnings go to Telegram.

On the VPS the process is supervised by systemd:

| Unit | Purpose |
|---|---|
| `iatis-scheduler.service` | Runs the scheduled pipeline |
| `iatis-api.service` | Serves the FastAPI app + dashboard |
| `iatis-watchdog.timer` (10 min) | Liveness watchdog (`scripts/watchdog.py`) |
| `iatis-d1-backup.timer` / `iatis-backup.timer` | Nightly D1 + JSONL backup |

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
7. **Correlation filter** — cap concurrent EXECUTEs per correlation group.
8. **Risk gate** — sovereign veto: RR floor, exposure caps, real drawdown from
   `risk/live_portfolio_state.py` (a live equity curve, not hardcoded zeros).
9. **News gate** — blackout around high-impact events (NFP/FOMC/CPI).
10. **Symbol health** — auto-pause chronic underperformers.
11. **Meta decision layer** — confidence/stability check; can downgrade an
    EXECUTE to NO_TRADE (with an auditable `downgrade_reason`).

Only if every stage passes is the verdict `EXECUTE`. The decision report is
persisted with `provenance` (code version, config hash, per-timeframe data
version) so "never change mid-sample" is verifiable, not just promised.

---

## Implemented Features

Verified present and wired into the running system:

- **Config-driven decision pipeline** (`main.py`) with all gates above.
- **Stdlib scheduler** (`scheduler.py`) with overlap protection and budget
  awareness.
- **Ten strategy engines** implemented; **four enabled** (SMC, Price Action,
  NNFX, Wyckoff — the frozen `prod4` set).
- **Confluence subsystem**: weighted voting, scoring, MTF, contradiction and
  reversal-veto, regime-aware weights, meta-decision layer.
- **Sovereign risk engine** with a *real* portfolio state (drawdown, open
  risk, correlated exposure derived from trade history).
- **Asset-class provider chains** with native-timeframe-aware failover
  (ccxt/Binance, Alpaca, cTrader, Twelve Data, FCS, Alpha Vantage, Finnhub).
- **Cloudflare D1 storage** via an authenticated Worker proxy — decisions,
  engine votes, outcomes, engine performance, experience DB, shadow book;
  atomic multi-statement batches; schema migrations.
- **FastAPI server** (`execution/api_server.py`) exposing ~50 endpoints.
- **Command Center dashboard** — React 19 + TypeScript SPA (Vite), 15 tabs,
  served at `GET /app` after build.
- **cTrader broker client** with verified app/account auth, live symbol-spec
  fetch, bounded-backoff auto-reconnect, and position reconciliation on every
  (re)connect. **OANDA client** as a fallback path.
- **Broker reconciliation** (`execution/reconciliation.py`) — self-gated to
  live-order mode.
- **One-way Telegram alerts** (`execution/telegram_bot.py`) — signal/verdict
  and ops notifications; flood-protected. (No inbound command interface.)
- **Backtesting stack**: single simulation engine (`backtesting/`) composed
  with metrics/Monte Carlo/walk-forward/report modules (`backtest/`).
- **Research governance**: `edge_gate.py` blocks unproven engines at boot;
  `registry.json` is the single source of hypothesis truth; manifest and
  survivorship checkers enforce clean-tree provenance.
- **Nightly D1 + JSONL backup** with re-load verification and rotation.
- **Rotating file logging** (`utils/logger.py`), `IATIS_LOG_LEVEL` override.
- **Hermetic test suite** — ~869 test functions; `conftest.py` blocks real
  sockets and strips real credentials, faking D1 with in-memory SQLite.
- **CI** (GitHub Actions): ruff (E9/F821 gate), full pytest suite, pip-audit.

---

## Experimental Features

Present in the codebase but not part of the measured live edge:

- **AI explanation layer** (`ai/`) — enabled by default in `config/ai.yaml`
  (Gemini), but strictly explanation/reporting: it never affects
  `final_verdict`. Degrades cleanly to `status: disabled|error`.
- **AI dynamic weight suggestions** (`ai/dynamic_weights.py`,
  `POST /ai/optimize-weights`) — advisory only, dry-run by default.
- **Experiment runner** (`POST /experiments/run`) — whitelisted subprocess
  research jobs surfaced in the dashboard.
- **Data-confidence cross-provider check** (`core/data_confidence.py`) —
  monitoring only, never a gate; off by default.
- **Live-on-demo forward trading** — cTrader demo account with `dry_run:false`
  and `allow_live_trading:false`, feeding a 100-closed-trade forward-evidence
  counter. This is prospective data collection, not a proven live edge.
- **Six disabled engines** (ICT, Quant, Divergence, Market Structure,
  Sentiment, Macro) — implemented, gated off pending their own hypotheses.
- **News-sentiment / positioning research** (MarketAux, TAAPI clients) — built
  as infrastructure for pre-registered hypotheses H019/H021, not wired live.

---

## Planned Roadmap

Pre-registered but not yet implemented (`registry.json` status `PLANNED`):

- **H018** — structure-based stop placement (SL at order-block/swing). Frozen
  until ~100 closed demo trades exist.
- **H019** — crypto positioning/sentiment as internal confluence.
- **H021** — MarketAux news sentiment fed into the Sentiment engine (A/B).

Operational roadmap:

- Migrate `iatis-*.service` off `User=root` to a dedicated service account
  (`scripts/setup_service_user.sh` exists; migration not yet executed).
- Live/demo soak test of the cTrader reconnect path under real conditions.
- Confidence calibration + regime performance matrix maturing as closed
  trades accumulate.
- Filling `data/swap_rates.json` with real cTrader rates and re-running the
  FX stability check (currently ships all-zeros / off).

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| API | FastAPI + Uvicorn |
| Data | pandas, numpy, pydantic |
| Market data | ccxt, Twelve Data, cTrader Open API, FCS, Alpha Vantage, Finnhub, Alpaca, yfinance (offline diffs only) |
| Storage | Cloudflare D1 via Worker proxy (no local DB) |
| Frontend | React 19, TypeScript, Vite, Tailwind v4, lightweight-charts |
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
| `config/engines.yaml` | `enabled.<name>`, `smc_full_spec`, version metadata |
| `config/symbols.yaml` | Per-symbol `enabled`, `min_score`, `rr` + governance record |
| `config/risk.yaml` | RR floor, exposure caps, drawdown thresholds, `starting_balance` (**frozen**) |
| `config/ai.yaml` | AI provider order, model, cache TTLs |

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
| `CTRADER_*` | Optional | Broker (client id/secret, account, token, environment) |
| `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, `OANDA_ENVIRONMENT` | Optional | Fallback broker |
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
  decisions/outcomes. The Worker (`worker.js`) exposes `POST /d1/exec` and
  `POST /d1/batch`, both guarded by a `Bearer D1_PROXY_TOKEN` header, and is
  the *only* path from the VPS Python process to D1.
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

Fifteen deep-linkable tabs (⌘K palette, per-tab error isolation, smart
polling):

| Tab | Shows |
|---|---|
| Mission Control | System health, credits, paper-trading evidence |
| Live Signals | Recent decisions + open paper signals, per-decision AI explain |
| Forward Demo | D001/D002 forward rules + shadow-book gate ledger |
| Execution Quality | Real fills, slippage, TCA |
| Data Center | OHLCV cache completeness, provider chains |
| Engine Monitor | Per-engine votes, accuracy, suggested/AI weights |
| Research & Backtests | Hypothesis registry, backtest runs, regime matrix |
| System Audit | Philosophy audit + research-integrity checks |
| Live Logs | Whitelisted journalctl / file-log tail |
| File Explorer | Read-only, repo-confined file browser |
| Alert Center | Aggregated signals from other endpoints |
| Reports | Markdown/JSON snapshots of computed state |
| Experiment Runner | Whitelisted subprocess research jobs |
| VPS Operations | Config reload, diagnostics, backups |
| Roadmap | Planned modules and phases |

Auth uses the same session-cookie / `X-API-Key` scheme as the API.

---

## REST API

~50 endpoints on a single FastAPI app (`execution/api_server.py`). Selected
groups:

| Group | Endpoints |
|---|---|
| Core pipeline | `GET /health`, `GET /health/full`, `POST /analyze/{symbol}`, `GET /candles/{symbol}` |
| Decisions & outcomes | `GET /decisions`, `GET /outcomes`, `POST /outcomes/{id}/close`, `GET /stats` |
| Health & data | `GET /symbol-health`, `GET /engine-stats`, `GET /data-health`, `GET /data-confidence`, `GET /reconciliation` |
| Research & audit | `GET /research`, `GET /research/{id}`, `GET /research/integrity`, `GET /backtest-results`, `GET /meta-analysis`, `GET /philosophy-audit`, `GET /forward-review` |
| Governance ledgers | `GET /shadow-book`, `GET /audit-log`, `GET /provider-chains`, `GET /execution-quality`, `GET /metrics` |
| Experience DB | `GET /experience/summary`, `GET /experience/query`, `GET /experience/pattern` |
| Ops | `GET /budget`, `GET /alerts`, `POST /ops/reload-config`, `GET /reports/{kind}`, `GET /files/*`, `GET /logs*`, experiment-runner routes |
| AI layer | `POST /ai/explain-trade`, `GET /ai/explain/{id}`, `GET /ai/news-analysis`, `GET /ai/macro-analysis`, `GET /ai/daily-report`, `POST /ai/research-summary`, `POST /ai/optimize-weights` |
| Auth & UI | `POST/GET /login`, `GET /logout`, `GET /dashboard` (legacy SSR), `GET /app` (SPA) |

Swagger/OpenAPI docs are disabled unless `ENV=development`.

---

## Command Center

The Command Center is the browser control surface: the SPA above plus its
read-only/whitelisted ops endpoints (config reload, diagnostics, log tailing,
file browsing, subprocess experiment jobs). It is a *window into* the system —
it does not, and cannot, alter a trading verdict.

---

## Research & Backtesting

Two composed packages, not duplicates:

- **`backtesting/backtest_engine.py`** — the single simulation engine
  (gap-aware exits, measured spreads/slippage, pipeline-aligned parameters).
- **`backtest/`** — metrics (Sharpe/Sortino/Calmar/drawdown), Monte Carlo,
  HTML reports, walk-forward, and the `runner.py` adapter:

```bash
python -m backtest.runner --symbols EURUSD GBPUSD --data-dir data
python -m backtest.walk_forward --symbols EURUSD GBPUSD
```

Historical PF/WR figures are deliberately **not** hardcoded here — the engine
evolves, and a stale table would mislead. Runs write to `reports/` alongside
the exact config used.

**Research governance.** No engine may be enabled without a `registry.json`
entry at `RESEARCH` or better, enforced by `research/edge_gate.py`. Current
hypothesis ledger:

| ID | Title | Status |
|---|---|---|
| H001/H002/H002b | Liquidity-sweep entries | FAILED |
| H003 | ICT killzone + premium/discount | RESEARCH |
| H004 | NNFX EMA200 + ADX | RESEARCH |
| H005 | Quant RSI + ROC | RESEARCH |
| H006 | Wyckoff Spring/Upthrust | RESEARCH |
| H007 | Macro DXY + Risk-On/Off | RESEARCH |
| H008/H008b/H008c | BOS + FVG confluence | FAILED / ABANDONED |
| **H009** | 6-engine confluence as signal | **PASSED** *(see note)* |
| H010 | RSI/MACD divergence | RESEARCH |
| H011 | Market Structure BOS/CHoCH/MSS | RESEARCH |
| H012 | COT + retail sentiment | RESEARCH |
| **H013** | Reversal-group counter-signal | **PASSED** |
| H014/H015/H016 | Orthogonality / ablation / synergy | RESOLVED |
| H017 | SMC full-spec internal confluence | FAILED |
| H018 | Structure-based stops | PLANNED |
| H019 | Crypto positioning/sentiment | PLANNED |
| H020 | Informative-weight-share sensitivity | FAILED |
| H021 | MarketAux news sentiment A/B | PLANNED |

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
  group; per-run portfolio exposure accounting.
- **Money-safety gates**: `dry_run` and `allow_live_trading` default to safe;
  the executor hard-refuses real-money orders on a non-demo account unless
  explicitly enabled. `risk/risk.yaml` is **frozen** until the shadow book
  reaches ~50 samples per gate.

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
`experiences`, `shadow_signals`. Multi-statement writes (a decision plus its
engine votes) are committed atomically via D1's `batch()` API. Migrations live
in `storage/migrations.py` and `cloudflare/migrations/`. Nightly backups
(`scripts/backup_d1.py`) dump, gzip, verify-reload, and rotate every table,
with a JSONL copy alongside.

---

## Testing

- **~869 test functions across 74 files.** Fully hermetic: `tests/conftest.py`
  blocks real sockets, strips real credentials, and fakes the D1 Worker with a
  per-test in-memory SQLite connection.
- Coverage spans data providers, engines, confluence/meta-decision, risk,
  storage resilience, migrations, API contract, execution logic
  (cTrader/OANDA), reconciliation, research layer, and causal/static guards.

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
- `hmac.compare_digest` for key comparison (timing-attack resistant); the D1
  Worker uses a constant-time token comparison.
- Symbol input validated against `^[A-Z]{2,6}(/[A-Z]{2,6})?$`.
- Swagger/OpenAPI disabled unless `ENV=development`.
- Secrets confined to `.env`; `EnvironmentFile` keeps tokens out of systemd
  units and journald.
- Dependency hygiene: pinned requirements, security-driven floors, pip-audit
  in CI.
- systemd units run sandboxed (`NoNewPrivileges`, `PrivateTmp`,
  `ProtectSystem=full`, resource limits).
- **Known gap:** units still run as `User=root` pending the service-user
  migration (`scripts/setup_service_user.sh`).

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
- **cTrader reconnect** has not had a real-network soak test.
- **No Docker / no packaging** (`pyproject.toml` absent).
- **Backups stay on-box by default** unless an rclone remote is configured.
- **H009 `PASSED` is under-evidenced** and flagged as such at every boot.

---

## Roadmap

The direction is to grow IATIS from a trading-decision engine into a full
**Institutional Trading Intelligence Platform (ITIP)** — where every release
adds real production value, not just features, and the deterministic core stays
the sole decision authority. Full detail, including the engine maturity model
and per-version exit criteria, is in [`docs/ROADMAP.md`](docs/ROADMAP.md).

| Version | Theme | Focus |
|---|---|---|
| **v0.6** | Institutional Foundation | Engine maturity docs, per-engine scoring, evidence-based dynamic weighting, risk hardening (daily-risk/exposure/correlation), API expansion, daily/weekly reports, ops closure |
| **v0.7** | Quant Research Platform | Walk-forward, Monte Carlo, parameter optimization, strategy comparison, feature engineering, data-quality scoring, data catalog, research workspace |
| **v0.8** | Institutional Data Platform | Unified data lake, OHLCV/news/econ management, research-result storage, data-quality monitoring, multi-source sync, full decision archival |
| **v0.9** | AI Decision Intelligence | Decision explanation, session summaries, engine-improvement suggestions, loss analysis, developer copilot — assistant only, never deciding |
| **v1.0** | ITIP (first stable) | Integrated Command Center, decision governance, portfolio & institutional risk, multi-channel alerts, professional reports, stable API, multi-user auth/RBAC, backup & restore |
| **Beyond 1.0** | Product modules | Research Studio · Portfolio Manager · Market Intelligence · Execution Hub · Data Center · AI Copilot · Enterprise Dashboard · Audit & Compliance |

**Near-term v0.6 work items** (evidence-gated):

1. **Complete the forward-demo sample** (~100 closed cTrader-demo trades) and
   apply the pre-registered D001/D002 rules via `scripts/forward_review.py`.
2. **Service-user migration** — move all `iatis-*.service` units off root.
3. **cTrader reconnect + reconciliation soak test** under real conditions.
4. **H018** (structure-based stops) once the sample threshold is reached.
5. **Off-site backups by default** (documented rclone/R2 remote).
6. **Git release tag + `CHANGELOG.md`** to anchor the unified version marker.

> Every roadmap item that touches trading behavior (enabling an engine,
> changing a threshold, adaptive weighting) is **measurement work gated by a
> pre-registered hypothesis clearing the OOS bar** — not a feature toggle. See
> `CLAUDE.md` and the dead list.

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
5. Run `pytest tests/ -q` and the philosophy audit
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
