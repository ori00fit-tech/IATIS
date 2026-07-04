# IATIS — Institutional Adaptive Trading Intelligence System

> **Version 0.4.5 · 364 tests · Market Intelligence Platform**
> Config-driven decision pipeline + Command Center dashboard + optional AI explanation layer

---

## What This Is

IATIS is a **Market Intelligence Platform** — not just a signal generator.

Before any trade executes, the system evaluates:
- **Market Quality** (session, volatility, time of day)
- **Multi-Timeframe alignment** (D1 vs H1)
- **Portfolio correlation** (max N signals per correlated group, configurable)
- **Reversal engine consensus** (H013 — prevents trend-vs-reversal conflicts)
- **News blackout** (auto-blocks during NFP, FOMC, CPI)
- **Symbol health** (auto-pauses underperforming symbols)
- **Real portfolio risk state** (live drawdown / open risk / correlated exposure — not placeholders)

Only after all gates pass does the system generate an EXECUTE signal.

**Core philosophy:**
1. NO_TRADE is a valid (and often correct) output
2. Every rejection has a documented reason
3. Research before production — `research/edge_gate.py` enforces this in code
4. No lookahead in backtesting — bar N sees only bars 0..N
5. Asset-aware math — JPY pip ≠ EUR formula
6. Platform > Signal — explain, measure, improve
7. AI explains decisions, it never makes them — the confluence + risk engines are the sole authority for `final_verdict`

---

## Architecture

```
LIVE DATA (Twelve Data → Yahoo → Alpha Vantage → Finnhub failover)
    ↓
MARKET QUALITY SCORE (0-100, thresholds in config.yaml market_quality:)
    ↓ POOR → NO_TRADE immediately (feature-flagged: features.market_quality_gate)
DATA VALIDATION + REGIME DETECTION
    ↓ TRENDING | RANGING | VOLATILE
REGIME-AWARE WEIGHTS
    ↓
STRATEGY ENGINES (config-gated; 4 of 9 implemented engines currently enabled)
    ↓
CONFLUENCE (majority vote + weighted score)
    ↓
MTF CONFIRMATION (D1 trend vs H1 signal)
    ↓
CONTRADICTION CHECK (standard + H013 reversal-group veto)
    ↓
CORRELATION FILTER (feature-flagged: features.correlation_filter, cap in config.yaml portfolio:)
    ↓
RISK GATE (sovereign veto — real drawdown/open-risk/correlated-exposure state
           from risk/live_portfolio_state.py, not hardcoded zeros)
    ↓
NEWS GATE (blackout before NFP/FOMC/CPI)
    ↓
SYMBOL HEALTH CHECK (auto-pause underperformers)
    ↓
DECISION → Telegram + SQLite + JSONL + Outcome Tracker
    ↓
(optional, on demand) AI EXPLANATION LAYER → Command Center dashboard
```

The AI layer is drawn separately on purpose: nothing in `main.py` or `scheduler.py` imports `ai.ai_analyzer` — it only runs when a human clicks a button in the dashboard, after the decision above is already final.

---

## Engines (9 implemented — 4 currently enabled)

> `config.yaml`'s `engines.enabled` block currently turns on `smc` and
> `price_action` (EXEMPT — plain structure reads, no edge claim), plus
> `nnfx` and `wyckoff` (hypothesis status `RESEARCH`, i.e.
> paper-trading-only per `research/edge_gate.py`, not yet `PASSED`). The
> other five (ICT, Quant, Divergence, Market Structure, Sentiment) are
> implemented but disabled pending their own hypothesis validation. The
> table below describes all 9 as designed — check `config.yaml` for
> what's actually running.

| Engine | Method | Hypothesis | Weight | Notes |
|---|---|---|---|---|
| SMC | Swing structure majority vote | EXEMPT | 0.202 | Enabled |
| Price Action | Sigmoid MA + breakout + candle patterns | EXEMPT | 0.187 | Enabled |
| NNFX | EMA200 + ADX | RESEARCH (H004) | 0.227 | Enabled — highest weight |
| Wyckoff | Spring/Upthrust + VSA | RESEARCH (H006) | 0.071 | Enabled |
| ICT | Killzones + Premium/Discount | RESEARCH (H003) | 0.066 | Disabled |
| Quant | RSI(14) + ROC(10) | RESEARCH (H005) | 0.071 | Disabled |
| Divergence | RSI/MACD divergence | RESEARCH (H010) | 0.061 | Disabled — reversal engine |
| Market Structure | BOS/CHoCH/MSS | RESEARCH (H011) | 0.086 | Disabled |
| Sentiment | COT + retail proxy | RESEARCH (H012) | 0.030 | Disabled |
| Macro | DXY + Risk-On/Off | — | 0.000 | Disabled, requires yfinance |

Weights live in `config.yaml`'s `confluence.weights` and are only ever applied to whichever engines are enabled — a disabled engine's weight is dead configuration until `research/edge_gate.py` allows it to be turned on.

---

## Config Control Plane

`config.yaml` is not a set of placeholders — every top-level section below maps to a real, already-wired conditional in the code (file:line noted inline in the YAML itself):

| Section | Controls |
|---|---|
| `data` | Source (`twelve_data`/`ctrader`/`injected`), symbols, timeframes. `system.mode=live` refuses `source: synthetic` at startup. |
| `engines.enabled` | Which of the 9 engines vote — gated by `research/edge_gate.py`. |
| `confluence` | Vote/score thresholds, per-engine weights. |
| `risk` | RR floor, exposure caps, drawdown thresholds, `starting_balance` (the equity-curve baseline `risk/live_portfolio_state.py` derives real drawdown from). |
| `features` | `market_quality_gate`, `correlation_filter`, `ai_weight_suggestions` — on/off switches for real gates, not roadmap items. |
| `market_quality` | MQS grade thresholds (GOOD/FAIR/POOR). |
| `monitoring` | RAM/disk warning thresholds for `GET /health/full`'s dashboard status (informational only — doesn't stop trading). |
| `portfolio` | Max concurrent EXECUTE signals per correlation group. |
| `ai` | Opt-in AI explanation layer — see below. Disabled by default. |
| `execution` | Broker selection, `dry_run` (defaults true), max open trades. |

---

## AI Explanation Layer (optional, disabled by default)

`ai/ai_analyzer.py` is an **explanation/reporting orchestrator**, not a decision-maker. It is never imported by `main.py` or `scheduler.py` — it only runs on demand, from the dashboard, after a decision has already been made by the confluence and risk engines.

```
ai/
├── ai_analyzer.py       # Orchestrator: config -> provider -> cache -> typed result
├── providers/           # Provider pattern — swap without touching the orchestrator
│   ├── base.py            # Common interface + prompt loading + JSON extraction
│   ├── perplexity.py       # Default provider (OpenAI-compatible chat completions)
│   ├── openai.py
│   └── anthropic.py
├── prompts/              # Externalized prompt templates (JSON-only output enforced)
├── cache.py               # TTL cache — news ~20min, macro ~60min; trade explanations
│                          # keyed by decision id, not blanket TTL (inputs never change)
├── models.py              # TradeExplanation / NewsAnalysis / MacroAnalysis result shapes
└── dynamic_weights.py     # Separate, older feature: Claude-based engine-weight suggestions
                           # (POST /ai/optimize-weights) — advisory only, dry_run by default
```

**Turning it on:**
```yaml
# config.yaml
ai:
  enabled: true
  provider: perplexity   # or openai / anthropic
  model: sonar
```
```bash
# .env — read from the environment, never from config.yaml
PERPLEXITY_API_KEY=...
# or OPENAI_API_KEY / ANTHROPIC_API_KEY, matching whichever provider is selected
```

**Endpoints (all dashboard-facing, all read-only w.r.t. trading decisions):**

| Endpoint | Used by |
|---|---|
| `POST /ai/explain-trade` | Live Signals — "Explain" button per decision |
| `GET /ai/news-analysis` | Mission Control — AI Briefing panel |
| `GET /ai/macro-analysis` | Mission Control — AI Briefing panel |
| `GET /ai/daily-report` | Mission Control — AI Briefing panel |
| `POST /ai/research-summary` | Research & Backtests — AI Research Summary panel |
| `POST /ai/optimize-weights?dry_run=true` | Engine Monitor — AI Weight Suggestions panel (Claude, separate feature) |

Every call returns `status: ok | disabled | error` — a missing key or provider failure degrades to a clean error response, never a crash, and never changes what the pipeline already decided.

---

## Storage Backend (optional: Cloudflare D1)

`storage/decision_db.py`, `outcome_tracker.py`, `engine_tracker.py`, and `experience_db.py` default to local SQLite files, same as always. Setting `IATIS_STORAGE_BACKEND=d1` (plus `D1_WORKER_URL` / `D1_PROXY_TOKEN` in `.env`) switches all four to Cloudflare D1 instead — one centrally-managed database instead of four local files, accessed through a small authenticated proxy Worker (`cloudflare/worker.js`), since D1 is only reachable from inside a Worker, not directly from this VPS-hosted Python process:

```
Python storage/*.py  --HTTPS-->  cloudflare/worker.js  --D1 binding-->  D1
```

Full setup (creating the D1 database, deploying the Worker, setting secrets) is in `cloudflare/README.md` — it requires a Cloudflare account and can't be provisioned from this repo alone. The default (unset `IATIS_STORAGE_BACKEND`, local SQLite) needs no Cloudflare account at all and is what the test suite always exercises.

---

## Command Center Dashboard

`dashboard/frontend/` is a React + TypeScript SPA, built with Vite, served at `GET /app` once built (`cd dashboard/frontend && npm install && npm run build`). It talks to the same FastAPI backend as everything else — no separate server.

| Tab | Shows |
|---|---|
| Mission Control | System health, CPU/RAM/disk, symbol health, API budget, AI Briefing (news/macro/daily report) |
| Live Signals | Recent decisions, open paper-trading signals, per-decision AI explanation |
| Data Center | Per-symbol data cache health (OK/STALE/GAPS/MISSING) |
| Engine Monitor | Per-engine vote stats, rule-based suggested weights, AI (Claude) weight suggestions |
| Research & Backtests | Hypothesis registry, backtest results, regime performance matrix, AI research summary |
| Roadmap | Static project roadmap |

Auth is the same session-cookie/`X-API-Key` scheme as the rest of the API — see Security below.

---

## Backtesting & Research

Two packages, composed rather than duplicated:

- **`backtesting/backtest_engine.py`** — the only simulation engine (gap-aware exits, slippage, parameters aligned with the live pipeline).
- **`backtest/metrics.py` / `monte_carlo.py` / `report.py`** — the only metrics/reporting implementation (Sharpe, Sortino, Calmar, drawdown analysis, Monte Carlo robustness, HTML reports).
- **`backtest/runner.py`** — the entry point that ties them together via an explicit adapter, not a second copy of either model:
  ```bash
  python -m backtest.runner --symbols EURUSD GBPUSD --data-dir data
  ```
- **`backtest/walk_forward.py`** — out-of-sample walk-forward validation on top of the same engine:
  ```bash
  python -m backtest.walk_forward --symbols EURUSD GBPUSD
  ```

Historical performance numbers are intentionally not hardcoded in this README — the simulation engine has changed since any specific run, so a stale PF/WR table would be misleading. Run the commands above against current data for current numbers; results are written under `reports/` alongside the exact engine config used, so every run is reproducible.

`scripts/engine_ablation.py` and `scripts/verify_data_integrity.py` support this: the former measures per-engine marginal contribution (vote-independence matrix, leave-one-out), the latter validates the historical dataset against real market-hours calendars before it's trusted for a backtest.

---

## Research Hypotheses

Registry: `research/results/registry.json`. No engine may be enabled in `config.yaml` without an entry here at `RESEARCH` status or better — enforced by `research/edge_gate.py`, not just documented.

| ID | Title | Status |
|---|---|---|
| H001 | Liquidity sweep + HTF trend | FAILED |
| H002 | Qualified sweep (ATR+regime) | FAILED |
| H002b | Qualified sweep, multi-symbol | FAILED |
| H003 | ICT killzone + premium/discount | RESEARCH |
| H004 | NNFX EMA200 + ADX | RESEARCH |
| H005 | Quant RSI + ROC | RESEARCH |
| H006 | Wyckoff Spring/Upthrust | RESEARCH |
| H007 | Macro DXY + Risk-On/Off | RESEARCH |
| H008 | BOS + FVG confluence | NEEDS_MORE_DATA |
| H008b | BOS+FVG + London session + ATR | ABANDONED |
| **H009** | **6-engine confluence as primary edge** | **PASSED** |
| H010 | RSI/MACD Divergence engine | RESEARCH |
| H011 | BOS/CHoCH/MSS Market Structure | RESEARCH |
| H012 | COT + Retail Sentiment | RESEARCH |
| H013 | Reversal engine group agreement | RESEARCH |
| H014 | Engine orthogonality test | RESEARCH |
| H015 | Ablation study — minimum engine set | RESEARCH |
| H016 | Engine pair synergy analysis | RESEARCH |

---

## Broker Integration

### IC Markets / cTrader (primary)
```
API:    execution/ctrader_client.py
Config: execution.broker: ctrader, execution.ctrader_enabled, execution.dry_run

Add to .env:
  CTRADER_CLIENT_ID=...
  CTRADER_CLIENT_SECRET=...
  CTRADER_ACCOUNT_ID=...
  CTRADER_ACCESS_TOKEN=...
  CTRADER_ENVIRONMENT=demo   # or live
```
Implements: app/account auth verified against actual server responses (not assumed), live symbol-spec fetch (no guessed lot sizes/volumes), relative SL/TP derived from live spot, bounded exponential-backoff auto-reconnect on unplanned disconnect, and `ProtoOAReconcileReq` on every (re)connect so open positions reflect broker truth rather than only events seen since process start.

### OANDA (backup)
```
execution/oanda_client.py — kept as a fallback broker path.
```

`dry_run` defaults to `true` (`config.yaml` `execution.dry_run`) — `TradeExecutor` never places a real order unless this is explicitly turned off.

---

## API Endpoints

~30 endpoints on the single FastAPI app (`execution/api_server.py`). Grouped by purpose:

| Group | Endpoints |
|---|---|
| Core pipeline | `GET /health`, `GET /health/full`, `POST /analyze/{symbol}` |
| Decisions & outcomes | `GET /decisions`, `GET /outcomes`, `POST /outcomes/{id}/close`, `GET /stats` |
| Symbol/engine health | `GET /symbol-health`, `GET /engine-stats`, `GET /data-health` |
| Research & backtests | `GET /research`, `GET /backtest-results`, `GET /meta-analysis` |
| Experience DB (Market Memory) | `GET /experience/summary`, `GET /experience/query`, `GET /experience/pattern` |
| AI explanation layer | `POST /ai/explain-trade`, `GET /ai/explain/{decision_id}`, `GET /ai/news-analysis`, `GET /ai/macro-analysis`, `GET /ai/daily-report`, `POST /ai/research-summary`, `POST /ai/optimize-weights` |
| Budget/ops | `GET /budget` |
| Auth & dashboard | `GET/POST /login`, `GET /logout`, `GET /dashboard` (legacy SSR page), `GET /app` (Command Center SPA, once built) |

---

## Project Structure

```
IATIS/
├── main.py                       # Decision pipeline entry point
├── scheduler.py                  # Automated multi-symbol runner
├── config.yaml                   # Control plane — see table above
├── .env                          # Secrets (never committed)
│
├── core/                         # Data infrastructure, multi-provider failover, MQS
├── engines/                      # 9 strategy engines (4 enabled)
├── confluence/                   # Voting, scoring, contradiction/MTF/reversal-veto checks
├── regimes/                      # TRENDING/RANGING/VOLATILE detection
├── fundamentals/                 # News calendar + blackout gate
│
├── risk/
│   ├── risk_engine.py            # Sovereign risk gate (RR floor, exposure caps, drawdown)
│   ├── live_portfolio_state.py   # Real drawdown/open-risk/correlated-exposure from history
│   └── correlation_engine.py     # Portfolio correlation filter
│
├── storage/                      # SQLite decisions/outcomes/experience DBs, JSONL audit trail
│
├── execution/
│   ├── api_server.py             # FastAPI — ~30 endpoints incl. AI + dashboard support
│   ├── ctrader_client.py         # IC Markets broker (reconnect + reconciliation)
│   ├── oanda_client.py           # Backup broker
│   ├── trade_executor.py         # dry_run-gated execution bridge
│   └── telegram_bot.py
│
├── ai/                           # Optional AI explanation layer — see section above
│
├── backtest/                     # Metrics/Monte Carlo/reports + runner.py/walk_forward.py
├── backtesting/                  # The one simulation engine backtest/ composes with
│
├── research/
│   ├── edge_gate.py              # Blocks unproven engines at boot, in code
│   ├── hypotheses/                # H001-H016 documented before code
│   └── results/registry.json     # Single source of truth for hypothesis status
│
├── dashboard/frontend/           # Command Center SPA (React + TS + Vite)
├── scripts/                      # Data download, backtests, ablation, integrity checks
├── docs/
└── tests/                        # 364 tests
```

---

## Security

- Session rotation: cookie holds `session_id`, never the raw API key
- `HttpOnly + Secure + SameSite=Lax` cookies (Lax, not Strict — Strict blocks the cross-origin redirect Cloudflare's tunnel performs on login; see comment in `execution/api_server.py`)
- `hmac.compare_digest` for key comparison (timing-attack resistant)
- Dashboard values escaped client-side consistently (all dynamic content reaches the page via JSON fetch + DOM injection, not server-side string interpolation)
- Symbol validation regex: `^[A-Z]{2,6}(/[A-Z]{2,6})?$`
- SQLite DB files and the session store: `chmod 0o600`
- Telegram flood protection: 30min cooldown per error key
- Swagger/OpenAPI docs disabled unless `ENV=development`
- systemd units run sandboxed (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, resource limits) even though they still run as `root` pending a dedicated service-user migration (see `iatis-*.service` comments)

---

## Roadmap

### ✅ Done
- Core 7-gate decision pipeline, edge-gated engines, sovereign risk layer
- **Real** portfolio risk state (drawdown/open-risk/correlated-exposure) — no longer hardcoded zeros
- Config control plane (`features`/`monitoring`/`portfolio`/`market_quality` as real toggles)
- Command Center dashboard (React SPA, 6 tabs)
- AI explanation layer (Perplexity/OpenAI/Anthropic provider pattern), wired into 4 dashboard tabs
- cTrader auto-reconnect + position reconciliation
- Engine ablation harness, historical data integrity verifier
- systemd sandboxing

### ⏳ Next
- Migrate `iatis-*.service` off `User=root` to a dedicated service account
- Live/demo soak test of the cTrader reconnect path (no substitute for real network conditions)
- Confidence calibration + regime performance matrix maturing as more closed trades accumulate
- Multi-user auth (JWT + a real user store) if this stops being single-operator

---

## Codebase

```
158 Python files (excluding dashboard/frontend) | ~31,700 lines
364 tests
~30 API endpoints
9 strategy engines (4 enabled) | 16 research hypotheses tracked
```

This system is a research and paper-trading platform first. Live order placement exists (`execution/trade_executor.py`, `execution/ctrader_client.py`) but defaults to `dry_run: true` everywhere, and `research/edge_gate.py` keeps unproven engines out of the vote regardless of what's in this README.
