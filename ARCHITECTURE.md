# IATIS Architecture — Complete System Design

## System Overview

IATIS (Institutional Adaptive Trading Intelligence System) is a **Market Intelligence Platform** that evaluates market conditions across a multi-gate pipeline before executing trades. The architecture is layered and deterministic, with each component having clear responsibilities. An optional AI explanation layer sits outside this pipeline entirely — it explains decisions after the fact, it never makes them.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    IATIS v0.4.5 — Decision Pipeline                  │
└─────────────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────────┐
        │  LIVE DATA (Multi-Provider with Failover)         │
        │  Twelve Data → Yahoo → Alpha Vantage → Finnhub    │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  DATA VALIDATION & TIMEFRAME SYNC                 │
        │  • No nulls, no lookahead bias                    │
        │  • Multi-timeframe building (H1/H4/D1)            │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  MARKET QUALITY SCORE (Gate 1)                    │
        │  • Session + ATR + Trend clarity scoring          │
        │  • Thresholds in config.yaml market_quality:      │
        │  • Feature-flagged: features.market_quality_gate  │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  REGIME DETECTION                                 │
        │  • TRENDING | RANGING | VOLATILE                  │
        │  • Feeds adaptive weights to engines              │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  STRATEGY ENGINES (config-gated, 4 of 9 enabled)  │
        │  ✅ SMC | Price Action | NNFX | Wyckoff           │
        │  ⏸ ICT | Quant | Divergence | Market Structure    │
        │     | Sentiment | Macro (all implemented, disabled)│
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  CONFLUENCE ENGINE (Gate 2)                       │
        │  • Majority vote + weighted score                 │
        │  • Contradiction check (standard + H013)          │
        │  • Multi-TF confirmation (D1/H1 alignment)         │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  CORRELATION FILTER (Gate 3)                      │
        │  • Max N EXECUTE per correlation group per run    │
        │  • Cap in config.yaml portfolio.max_per_group      │
        │  • Feature-flagged: features.correlation_filter    │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  RISK GATE (Gate 4 — sovereign veto)              │
        │  • Risk/Reward floor, position sizing              │
        │  • REAL drawdown/open-risk/correlated-exposure     │
        │    from risk/live_portfolio_state.py — not         │
        │    hardcoded placeholders                          │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  SYMBOL HEALTH INDEX (Gate 5)                     │
        │  • Win rate + profit factor over recent trades     │
        │  • Auto-pauses persistently underperforming symbols │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  NEWS INTELLIGENCE (Gate 6)                       │
        │  • NFP, FOMC, CPI detection                        │
        │  • Blackout window before high-impact events       │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  META DECISION LAYER (Gate 7)                     │
        │  • Confidence calibration                          │
        │  • Can downgrade EXECUTE → NO_TRADE on low conf.    │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  EXECUTION & PERSISTENCE                          │
        │  • Trade execution (dry_run | cTrader | OANDA)     │
        │  • Outcome tracking (auto-close on SL/TP)          │
        │  • Telegram alerts + Command Center dashboard      │
        │  • Cloudflare D1 + JSONL for audit trail            │
        └──────────────────────────────────────────────────┘
                              ↓
              (on demand, from the dashboard only)
        ┌──────────────────────────────────────────────────┐
        │  AI EXPLANATION LAYER (ai/ai_analyzer.py)         │
        │  • Explains a decision already made above          │
        │  • Never imported by main.py or scheduler.py       │
        │  • Opt-in: config.yaml ai.enabled (default false)   │
        └──────────────────────────────────────────────────┘
```

---

## Core Modules

### 1. **core/** — Data Infrastructure

| File | Purpose | Key Classes |
|------|---------|-------------|
| `data_providers.py` | Multi-provider failover (Twelve Data → Yahoo → Alpha Vantage → Finnhub) | `fetch_with_failover()` |
| `data_loader.py` | CSV/synthetic data loading, timeframe building | `load_data()` |
| `data_manager.py` | Caching, retry logic | `DataManager` |
| `data_validator.py` | OHLCV validation (no nulls, monotonic) | `validate_ohlcv()` |
| `market_quality.py` | Market Quality Score (0-100), thresholds from `config.yaml market_quality:` | `assess_market_quality()` |
| `timeframe_sync.py` | Multi-timeframe building and resampling | `build_multi_timeframe_view()` |
| `asset_profiles.py` | Per-asset settings (pip size, session hours, spreads) | `get_profile()` |
| `twelve_data_client.py` | Twelve Data API client with rate limiter + cache | `TwelveDataClient` |
| `ccxt_provider.py` | Crypto data via CCXT | `get_ccxt_data()` |
| `alt_data_loader.py` | Alternate/offline data loading path | — |

**Flow:**
```
load_multi_timeframe_with_failover()
  ↓ (try Twelve Data)
  ↓ (cached? yes → return)
  ↓ (empty? try Yahoo)
  ↓ (empty? try Alpha Vantage)
  ↓ (empty? try Finnhub)
  ↓
validate_ohlcv(df)
build_multi_timeframe_view(df, ["H1","H4","D1"])
```

### 2. **engines/** — 9 Strategy Engines (4 currently enabled)

> `config.yaml`'s `engines.enabled` block has only `smc`, `price_action`,
> `nnfx`, and `wyckoff` set to `true`. The other five are implemented and
> edge-gated but disabled — enabling one requires its hypothesis in
> `research/results/registry.json` to reach at least `RESEARCH` status
> (see `research/edge_gate.py`).

Each engine returns `EngineOutput(bias, score, reasons, raw)` where:
- `bias`: BULLISH | BEARISH | NEUTRAL
- `score`: 0-100 (how confident is this engine?)
- `reasons`: List of human-readable logic strings
- `raw`: Raw indicators used

| Engine | Weight | Enabled | Method |
|--------|--------|---------|--------|
| NNFX | 0.227 | ✅ | EMA200 + ADX |
| SMC | 0.202 | ✅ | Swing point majority vote |
| Price Action | 0.187 | ✅ | Sigmoid MA + breakout + candle patterns |
| Wyckoff | 0.071 | ✅ | Spring/Upthrust detection |
| Quant | 0.071 | ⏸ | RSI(14) + ROC(10) |
| Market Structure | 0.086 | ⏸ | BOS/CHoCH (H011) |
| ICT | 0.066 | ⏸ | Killzones + trend filter |
| Divergence | 0.061 | ⏸ | RSI/MACD divergence (H010) |
| Sentiment | 0.030 | ⏸ | COT proxy (H012) |
| Macro | 0.000 | ⏸ | Requires yfinance |

**All engines inherit from `BaseEngine`:**
```python
class BaseEngine:
    name: str
    def analyze(mtf_data: dict) -> EngineOutput
    def safe_analyze(mtf_data: dict) -> EngineOutput  # with exception handling
```

### 3. **confluence/** — Voting & Decision Logic

| File | Purpose |
|------|---------|
| `voting_system.py` | Tally votes: majority wins, breakdown recorded |
| `score_calculator.py` | Weighted average of AGREEING engines only |
| `contradiction_engine.py` | Detect conflicting signals (standard + H013 reversal veto) |
| `mtf_confirmation.py` | D1 trend must align with H1 signal |
| `regime_weights.py` | Adjust engine weights based on market regime |
| `reversal_veto.py` | H013: when 2+ reversal engines unanimously oppose trend |
| `meta_decision.py` | Confidence calibration + engine stability analysis |

**Confluence Flow:**
```
1. Each engine votes: BULLISH | BEARISH | NEUTRAL
2. Find majority bias
3. Calculate weighted score (majority engines only)
4. Check contradictions (can block trade)
5. Check MTF confirmation (D1 aligns with H1?)
6. Check H013 reversal veto (reversal consensus vs trend)
7. Meta Decision: is confidence enough to execute?
```

### 4. **risk/** — Risk Management (Sovereign Layer)

| File | Purpose |
|------|---------|
| `risk_engine.py` | Hard-gate checks: RR floor, exposure cap, drawdown-stop/-reduce thresholds. Any single failure blocks the trade. |
| `live_portfolio_state.py` | Derives **real** account balance, drawdown-from-peak, open risk, and correlated exposure from the outcomes history — feeds `risk_engine.py`'s `RiskInputs`. Fail-safe: a storage read error returns the configured starting balance with zero derived risk rather than crashing or silently zeroing balance. |
| `correlation_engine.py` | Pre-filter used by `scheduler.py`: max N EXECUTE signals from the same correlation group per run (count-based, cheap early skip). |
| `portfolio_exposure.py` | Earlier in-memory-only exposure tracker; largely superseded by `live_portfolio_state.py`'s persisted equity-curve approach. |

**Correlation Groups** (`risk/correlation_engine.py`):
```
USD_MAJORS:   EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCHF, USDCAD
JPY_CROSSES:  USDJPY, EURJPY, GBPJPY, AUDJPY
EUR_CROSSES:  EURUSD, EURJPY, EURGBP, EURCHF
METALS:       XAUUSD, XAGUSD
RISK_ASSETS:  BTCUSD, ETHUSD, NAS100, SPX500, US30
```
Max signals per group per run set by `config.yaml`'s `portfolio.max_per_group`.

### 5. **storage/** — Persistence & Analytics

| File | Purpose |
|------|---------|
| `decision_db.py` | All decisions, queryable for analytics |
| `decision_log.py` | JSONL: append-only decision audit trail (always local — not part of D1) |
| `engine_tracker.py` | Per-engine live vote performance |
| `outcome_tracker.py` | Trade results: entry/exit/SL/TP/P&L |
| `symbol_health.py` | Symbol Health Index (SHI) auto-pause logic — reads `outcome_tracker`'s table directly, no table of its own |
| `calibration.py` | Confidence calibration + regime performance matrix |
| `experience_db.py` | Market Memory: similar historical setups, historical win rate |
| `d1_client.py` | The only storage backend for the modules above — see **Cloudflare D1 Backend** below |

`decision_db.py`, `outcome_tracker.py`, `engine_tracker.py`, `experience_db.py`, `symbol_health.py`, and `calibration.py` all store their data in Cloudflare D1 — there is no local SQLite fallback. Each one's `_conn()` routes through `d1_client.D1Connection`, whose `.execute()/.fetchone()/.fetchall()` shape mirrors `sqlite3` closely enough that none of their SQL query strings needed to change when they moved off local SQLite files.

### 6. **execution/** — Delivery & Broker Integration

| File | Purpose |
|------|---------|
| `api_server.py` | FastAPI server (~30 endpoints, session + API-key auth) |
| `telegram_bot.py` | Telegram alerts (EXECUTE signals only) |
| `trade_executor.py` | Execution bridge: `dry_run` / cTrader / OANDA — `dry_run` defaults `true` |
| `ctrader_client.py` | IC Markets cTrader integration: app/account auth verified against real server responses, live symbol-spec fetch (no guessed volumes), relative SL/TP from live spot, bounded exponential-backoff auto-reconnect, `ProtoOAReconcileReq` position reconciliation on every (re)connect |
| `oanda_client.py` | OANDA REST API (backup broker path) |
| `tradingview_webhook.py` | TradingView webhook stub |

### 7. **backtesting/** + **backtest/** — Simulation, Metrics & Reporting

Two packages, composed rather than duplicated:

| Package | Role |
|---|---|
| `backtesting/backtest_engine.py` | The one simulation engine — gap-aware exits, slippage, parameters aligned with the live pipeline |
| `backtest/metrics.py` | The one metrics implementation — Sharpe, Sortino, Calmar, drawdown analysis, trade statistics |
| `backtest/monte_carlo.py` | Monte Carlo robustness analysis (risk of ruin, return distribution) |
| `backtest/report.py` | HTML report generation |
| `backtest/runner.py` | Entry point composing the two via an explicit adapter — `python -m backtest.runner --symbols EURUSD GBPUSD --data-dir data` |
| `backtest/walk_forward.py` | Out-of-sample walk-forward validation on top of the same engine — `python -m backtest.walk_forward --symbols EURUSD GBPUSD` |

No hardcoded historical PF/WR figures are kept in this document — the simulation engine has changed since any specific run, so a stale table would misrepresent current behavior. Run the commands above for current numbers; results are written under `reports/` alongside the exact engine config used.

### 8. **research/** — Edge Gate & Hypothesis Tracking

| File | Purpose |
|------|---------|
| `edge_gate.py` | Blocks any engine without a registered, at-least-`RESEARCH`-status hypothesis at boot time |
| `hypotheses/` | H001-H016: engine claims written before code |
| `experiments/` | Validation scripts for the sweep/BOS-FVG hypotheses |
| `results/registry.json` | Single source of truth for hypothesis status |

**Key Rule:** No engine enabled in `config.yaml` without at least a `RESEARCH` (paper-trading-only) entry in `registry.json` — `PASSED` is not required to enable an engine, only to trust it. See `ai/` below and README for the full H001-H016 table.

### 9. **ai/** — Optional AI Explanation Layer

Not part of the decision pipeline. Verified: no import of `ai.ai_analyzer` anywhere in `main.py`, `scheduler.py`, `confluence/`, or `risk/`. It only runs when a human clicks a button in the Command Center dashboard, after `final_verdict` is already set.

| File | Purpose |
|------|---------|
| `ai_analyzer.py` | Orchestrator: selects a provider from `config.yaml`'s `ai:` section, applies caching, always returns `status: ok\|disabled\|error` |
| `providers/base.py` | Common `AIProvider` interface + prompt-template loading + JSON extraction |
| `providers/perplexity.py` | Default provider (OpenAI-compatible chat completions API) |
| `providers/openai.py` | Alternate provider |
| `providers/anthropic.py` | Alternate provider |
| `prompts/*.txt` | Externalized templates — explicitly forbid fabricated data and price prediction, enforce JSON-only output |
| `cache.py` | TTL cache: news ~20min, macro ~60min; trade explanations keyed by decision id instead |
| `models.py` | `TradeExplanation` / `NewsAnalysis` / `MacroAnalysis` result dataclasses |
| `dynamic_weights.py` | Separate, older feature — Claude-based engine-weight suggestions (`POST /ai/optimize-weights`), advisory-only and `dry_run` by default |

API keys are read from the environment (`PERPLEXITY_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEY` for `dynamic_weights.py` too) — never stored in `config.yaml`, matching every other credential in this codebase.

### 10. **dashboard/frontend/** — Command Center SPA

React + TypeScript + Vite, served at `GET /app` once built (`npm install && npm run build`), talking to the same FastAPI backend as everything else.

| Module | Shows |
|---|---|
| `mission-control/` | System health, symbol health, API budget, AI Briefing (news/macro/daily report) |
| `live-signals/` | Recent decisions, open paper-trading signals, per-decision AI explanation |
| `data-center/` | Per-symbol data cache health |
| `engine-monitor/` | Per-engine vote stats, rule-based + AI (Claude) suggested weights |
| `research-backtests/` | Hypothesis registry, backtest results, regime performance matrix, AI research summary |
| `roadmap/` | Static project roadmap |

### 11. **cloudflare/** — D1 Storage Backend

D1 databases are only reachable from inside a Cloudflare Worker via a binding — not directly from this VPS-hosted Python process. This folder holds the bridge every storage module requires (there is no local SQLite fallback — `D1_WORKER_URL`/`D1_PROXY_TOKEN` must be set):

| File | Purpose |
|---|---|
| `worker.js` | Authenticated D1 proxy — `POST /d1/exec` (one parameterized statement) and `POST /d1/batch` (multiple statements, atomic via D1's own `env.DB.batch()`) |
| `schema.sql` | Convenience one-time schema for `wrangler d1 execute` — combines the modules' `CREATE TABLE` statements (`decisions`+`engine_votes`, `outcomes`, `engine_performance`, `experiences`) |
| `wrangler.toml` | Worker config + D1 binding declaration |
| `README.md` | Full setup: `wrangler d1 create`, applying the schema, setting the shared-secret, deploying |

```
Python storage/*.py  --HTTPS (Bearer token)-->  cloudflare/worker.js  --D1 binding-->  D1
```

`storage/d1_client.py` is the Python side: `D1Connection`/`D1Cursor`/`D1Row` mimic `sqlite3`'s connection/cursor/row interface (`.execute()`, `.fetchone()`/`.fetchall()`, `.lastrowid`, row access by both name and position) closely enough that `decision_db.py`, `outcome_tracker.py`, `engine_tracker.py`, `experience_db.py`, `symbol_health.py`, and `calibration.py` read the same SQL they did when they used local SQLite. The one place cross-statement atomicity matters — `decision_db.log_decision_db()` writing one `decisions` row plus N `engine_votes` rows — uses `d1_batch()` (the Worker's `/d1/batch`); every other call site is a single statement, already atomic on its own.

Not migrated: `storage/decision_log.py`'s JSONL audit trail stays local-file-only — it's an append-only log, not a queryable store, and moving it to D1 would gain nothing.

The test suite never touches a real Cloudflare account: `tests/conftest.py`'s `fake_d1` autouse fixture fakes the Worker with a private in-memory `sqlite3` connection per test — real SQL semantics stay under test, only the HTTPS transport is faked.

---

## Configuration

`config.yaml` is a control plane, not a set of placeholders — every top-level section maps to a real, already-wired conditional (each documented inline in the YAML with the file:line it controls):

```yaml
data:
  source: twelve_data   # twelve_data | ctrader | injected (synthetic blocked under system.mode=live)
  symbol: EURUSD
  timeframes: [H1, H4, D1]
  bars_to_load: 500
  twelve_data_symbols:   # 20 configured, 7 currently enabled
    - internal: EURUSD
      symbol: EUR/USD
      min_score: 60
      rr: 2.0
      enabled: true

engines:
  enabled:
    smc: true
    price_action: true
    nnfx: true
    wyckoff: true
    ict: false          # RESEARCH — not yet enabled
    divergence: false   # RESEARCH — H010

confluence:
  min_engines_agreeing: 2
  min_score_to_trade: 58
  weights: {smc: 0.202, nnfx: 0.2273, price_action: 0.1869, "...": "..."}

risk:
  min_risk_reward: 2.0
  max_exposure: 0.05        # 5% of account at once
  max_drawdown_stop: 0.15   # > 15% → halt all trading
  risk_per_trade_max: 0.01
  starting_balance: 10000.0 # equity-curve baseline for risk/live_portfolio_state.py

features:
  market_quality_gate: true
  correlation_filter: true
  ai_weight_suggestions: true

market_quality:
  threshold_good: 60
  threshold_fair: 40

monitoring:
  ram_warn_pct: 85
  disk_warn_pct: 80

portfolio:
  max_per_group: 2

ai:
  enabled: false            # opt-in — no external AI call unless turned on
  provider: perplexity
  model: sonar

fundamentals:
  news_filter_enabled: true
  blackout_look_ahead_min: 60
```

---

## Key Design Principles

### 1. **NO_TRADE is Valid Output**
The system correctly identifies when **not** to trade. This is modeled as a feature, not a bug.

### 2. **Research Before Production (Edge Gate)**
No engine logic runs in production until:
- A hypothesis is written in `research/hypotheses/`
- An experiment in `research/experiments/` validates it against real data
- At least a `RESEARCH` entry exists in `research/results/registry.json` (paper-trading-only; `PASSED` is the higher bar for trusting the result)

### 3. **No Lookahead Bias**
At bar N, the pipeline only sees bars 0..N. Entry is next-bar open.

### 4. **Asset-Aware Math**
JPY pip ≠ EUR pip. Each asset has a profile with pip size, session hours, spread proxy, and min pip move.

### 5. **Sovereign Risk Layer, Fed Real State**
Risk gate is separate from confluence voting, and its hard stops now operate on `risk/live_portfolio_state.py`'s real drawdown/open-risk/correlated-exposure derivation — not hardcoded zeros.

### 6. **Multi-Provider Failover**
Twelve Data → Yahoo Finance → Alpha Vantage → Finnhub, in that order.

### 7. **Transparent Reasoning**
Every decision includes which engines voted which way, why, which gates passed/failed, and — on demand — an AI-generated plain-English explanation that never overrides the decision itself.

---

## Live Deployment

**Infrastructure:**
- VPS: Linux, Python 3.11+
- FastAPI server (uvicorn, port 8000)
- Cloudflare D1 (via `cloudflare/worker.js` proxy — see storage section above)
- Cloudflare tunnel for HTTPS (deployment-specific, not verified from this repo)

**Systemd Services:**
- `iatis-api.service` → FastAPI server
- `iatis-scheduler.service` → Scheduler

Both units run sandboxed (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, `ProtectHome`, `MemoryMax`, `TasksMax`) even though they still run as `User=root` — a TODO comment in each `.service` file documents the VPS-side steps (`useradd` + `chown`) needed before switching to a dedicated service account, since flipping that blind would break the live deploy.

**Scheduler Logic (`scheduler.py`):**
```
Per symbol, per run:
1. Correlation pre-filter → skip if a correlated symbol already EXECUTE this run
2. Symbol health check → skip if PAUSED
3. Run the full decision pipeline
4. If EXECUTE: attempt trade execution (dry_run gated)
5. Auto-close open outcomes when SL/TP hit
6. Send Telegram alert (EXECUTE only)
```
One symbol's exception doesn't kill the run for the rest — isolated per-symbol try/except with a 30-minute error-alert cooldown per symbol.

---

## API Endpoints

`execution/api_server.py`, ~30 endpoints on one FastAPI app:

| Group | Endpoints | Auth |
|---|---|---|
| Core pipeline | `GET /health`, `GET /health/full`, `POST /analyze/{symbol}` | Public / ✅ / ✅ |
| Decisions & outcomes | `GET /decisions`, `GET /outcomes`, `POST /outcomes/{id}/close`, `GET /stats` | ✅ |
| Symbol/engine/data health | `GET /symbol-health`, `GET /engine-stats`, `GET /data-health` | ✅ |
| Research & backtests | `GET /research`, `GET /backtest-results`, `GET /meta-analysis` | ✅ |
| Experience DB | `GET /experience/summary`, `GET /experience/query`, `GET /experience/pattern` | ✅ |
| AI explanation layer | `POST /ai/explain-trade`, `GET /ai/explain/{decision_id}`, `GET /ai/news-analysis`, `GET /ai/macro-analysis`, `GET /ai/daily-report`, `POST /ai/research-summary`, `POST /ai/optimize-weights` | ✅ |
| Budget | `GET /budget` | ✅ |
| Auth & dashboard | `GET/POST /login`, `GET /logout`, `GET /dashboard` (legacy SSR), `GET /app` (Command Center SPA) | Public/Cookie |

---

## Security

- Session rotation: cookie holds `session_id`, never the raw API key
- `HttpOnly + Secure + SameSite=Lax` cookies (Lax, not Strict — Strict blocks the cross-origin redirect Cloudflare's tunnel performs on login)
- `hmac.compare_digest` for key comparison
- Dashboard values escaped consistently client-side (data reaches the page via JSON fetch + DOM injection, not server-side string interpolation)
- Symbol validation regex: `^[A-Z]{2,6}(/[A-Z]{2,6})?$`
- Session store: `chmod 0o600` (storage is Cloudflare D1, no local DB files to protect)
- Telegram flood protection: 30min cooldown per error key
- Swagger/OpenAPI docs disabled unless `ENV=development`
- systemd sandboxing directives (see Live Deployment above)

---

## File Structure (Complete)

```
IATIS/
├── main.py                           # Decision pipeline entry point
├── scheduler.py                      # Automated multi-symbol runner
├── config.yaml                       # Control plane — see Configuration above
├── requirements.txt                  # Dependencies (pinned)
├── README.md                         # Public documentation
│
├── core/                             # Data infrastructure
│   ├── data_providers.py
│   ├── data_loader.py
│   ├── data_manager.py
│   ├── data_validator.py
│   ├── market_quality.py
│   ├── timeframe_sync.py
│   ├── asset_profiles.py
│   ├── twelve_data_client.py
│   ├── ccxt_provider.py
│   └── alt_data_loader.py
│
├── engines/                          # 9 strategy engines (4 enabled)
│   ├── base_engine.py
│   ├── smc_engine.py
│   ├── price_action_engine.py
│   ├── nnfx_engine.py
│   ├── ict_engine.py
│   ├── quant_engine.py
│   ├── wyckoff_engine.py
│   ├── divergence_engine.py
│   ├── market_structure_engine.py
│   ├── sentiment_engine.py
│   └── macro_engine.py
│
├── confluence/                       # Voting & decision logic
│   ├── voting_system.py
│   ├── score_calculator.py
│   ├── contradiction_engine.py
│   ├── mtf_confirmation.py
│   ├── regime_weights.py
│   ├── reversal_veto.py
│   └── meta_decision.py
│
├── risk/                             # Risk management (sovereign layer)
│   ├── risk_engine.py
│   ├── live_portfolio_state.py       # Real drawdown/open-risk/correlated-exposure
│   ├── correlation_engine.py
│   └── portfolio_exposure.py         # Legacy in-memory tracker
│
├── fundamentals/                     # News & calendar
│   ├── news_calendar.py
│   └── news_risk.py
│
├── regimes/                          # Market regime detection
│   ├── regime_detector.py
│   └── volatility_classifier.py
│
├── storage/                          # Persistence & analytics
│   ├── decision_db.py
│   ├── decision_log.py               # JSONL — always local, never D1
│   ├── engine_tracker.py
│   ├── outcome_tracker.py
│   ├── symbol_health.py
│   ├── calibration.py
│   ├── experience_db.py
│   └── d1_client.py                  # Optional D1 backend for the 4 DB modules above
│
├── cloudflare/                        # Optional D1 storage backend (opt-in)
│   ├── worker.js                      # Authenticated D1 proxy (exec + batch)
│   ├── schema.sql                     # One-time convenience schema
│   ├── wrangler.toml
│   └── README.md                      # Full setup — requires a Cloudflare account
│
├── execution/                        # Delivery & brokers
│   ├── api_server.py                 # FastAPI, ~30 endpoints
│   ├── telegram_bot.py
│   ├── trade_executor.py
│   ├── ctrader_client.py             # Reconnect + reconciliation
│   ├── oanda_client.py
│   └── tradingview_webhook.py
│
├── ai/                                # Optional AI explanation layer
│   ├── ai_analyzer.py
│   ├── providers/ (base, perplexity, openai, anthropic)
│   ├── prompts/*.txt
│   ├── cache.py
│   ├── models.py
│   └── dynamic_weights.py
│
├── backtesting/                      # The one simulation engine
│   └── backtest_engine.py
│
├── backtest/                         # Metrics/Monte Carlo/reports + entry points
│   ├── metrics.py
│   ├── monte_carlo.py
│   ├── report.py
│   ├── runner.py
│   └── walk_forward.py
│
├── research/                         # Edge gate & hypotheses
│   ├── edge_gate.py
│   ├── hypotheses/                   # H001-H016
│   ├── experiments/
│   └── results/registry.json
│
├── dashboard/frontend/               # Command Center SPA (React + TS + Vite)
│   └── src/modules/ (mission-control, live-signals, data-center,
│                      engine-monitor, research-backtests, roadmap)
│
├── utils/
│   ├── helpers.py
│   ├── logger.py
│   └── feature_def.py
│
├── tests/                            # 374 tests
│
├── scripts/                          # Data, backtests, ablation, integrity checks
│   ├── full_pipeline_backtest.py
│   ├── walk_forward_validation.py
│   ├── engine_ablation.py            # Per-engine marginal contribution
│   ├── verify_data_integrity.py      # Validates data against real market-hours calendars
│   ├── download_all_symbols.py
│   └── cache_calendar.py
│
├── data/                              # Historical datasets
├── docs/VISION_v2.md                  # Longer-form roadmap notes
│
├── storage/                           # Runtime data (gitignored)
│   ├── decisions.db / decisions.jsonl
│   ├── outcomes.db
│   ├── experience.db
│   └── news_history/                  # Cached news calendars (committed seed data)
│
├── iatis-api.service                  # systemd unit (sandboxed, see Security)
├── iatis-scheduler.service            # systemd unit (sandboxed, see Security)
└── .env                               # Secrets (never committed)
```

---

## Dependency Tree

```
main.py
├── core/ (data loading & validation)
├── engines/ (9 engines, only enabled ones instantiated — research/edge_gate.py gates this)
├── confluence/ (voting, scoring, contradiction/MTF/reversal-veto)
├── risk/
│   ├── live_portfolio_state.py (real balance/drawdown/exposure)
│   └── risk_engine.py (hard-gate checks against that state)
├── regimes/ (regime detection)
├── fundamentals/ (news blackout)
├── storage/ (persistence)
├── research/edge_gate.py (engine gating)
└── execution/telegram_bot.py (alerts)

scheduler.py
├── main.py (the pipeline, per symbol)
├── risk/correlation_engine.py (pre-filter)
├── storage/symbol_health.py (SHI check)
├── storage/outcome_tracker.py (auto-close)
├── execution/trade_executor.py (dry_run-gated execution)
└── execution/telegram_bot.py (alerts)

execution/api_server.py (FastAPI)
├── main.py (on-demand /analyze)
├── storage/ (most endpoints)
├── backtesting/ + backtest/ (research endpoints)
├── execution/ctrader_client.py
└── ai/ai_analyzer.py, ai/dynamic_weights.py (AI endpoints only — not the pipeline above)

dashboard/frontend/ (React SPA)
└── talks to execution/api_server.py exclusively, no direct Python imports
```

Note what's deliberately **not** in `main.py`'s tree: `ai/` is reachable only from `execution/api_server.py`'s AI-specific endpoints, confirmed by grep — there is no path from the decision pipeline into the AI layer.

---

## Phase Roadmap

### ✅ Done
- Core decision pipeline: edge-gated engines, sovereign risk layer, correlation/news/symbol-health gates
- Real portfolio risk state (`risk/live_portfolio_state.py`) — replaced hardcoded zeros
- Config control plane (`features`/`monitoring`/`portfolio`/`market_quality` as real toggles, not placeholders)
- Command Center dashboard (React SPA, 6 tabs)
- AI explanation layer (Perplexity/OpenAI/Anthropic), wired into 4 dashboard tabs, structurally isolated from the decision path
- cTrader auto-reconnect + position reconciliation
- Engine ablation harness (vote-independence, leave-one-out), historical data integrity verifier
- systemd sandboxing

### ⏳ Next
- Migrate `iatis-*.service` off `User=root` to a dedicated service account
- Live/demo soak test of the cTrader reconnect path under real network conditions
- Confidence calibration + regime performance matrix maturing as more closed trades accumulate
- Multi-user auth if this stops being single-operator

---

## Codebase

```
158 Python files (excluding dashboard/frontend) | ~31,700 lines
374 tests
~30 API endpoints
9 strategy engines (4 enabled) | 16 research hypotheses tracked (H001-H016)
```

This system is a research and paper-trading platform first. Live order placement exists but defaults to `dry_run: true` everywhere, and `research/edge_gate.py` keeps unproven engines out of the vote regardless of what any document claims.
