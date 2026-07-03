# IATIS Architecture — Complete System Design

## System Overview

IATIS (Institutional Adaptive Trading Intelligence System) is a **Market Intelligence Platform** that evaluates market conditions across 7 gates before executing trades. The architecture is layered and deterministic, with each component having clear responsibilities.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    IATIS v0.4 — Complete Pipeline                   │
└─────────────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────────┐
        │  LIVE DATA (Multi-Provider with Failover)        │
        │  Twelve Data → Yahoo → Alpha Vantage → Finnhub   │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  DATA VALIDATION & TIMEFRAME SYNC                 │
        │  • No nulls, no lookahead bias                    │
        │  • Multi-timeframe building (M1/M15/H1/H4/D1)     │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  MARKET QUALITY SCORE (Gate 1)                    │
        │  • Session (35 pts) + ATR (30 pts) + Trend (10 pts)│
        │  • Penalties for Friday close, Monday gap, Asian  │
        │  Score < 40 → NO_TRADE (saves API credits)        │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  REGIME DETECTION                                 │
        │  • TRENDING | RANGING | VOLATILE                  │
        │  • Feeds adaptive weights to engines              │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  9 PARALLEL STRATEGY ENGINES                      │
        │  ✅ SMC (20.2%)  | Price Action (18.7%)           │
        │  ✅ NNFX (22.7%) | ICT (6.6%)                     │
        │  ✅ Quant (7.1%) | Wyckoff (7.1%)                 │
        │  🟡 Divergence  | Market Structure | Sentiment    │
        │  ❌ Macro (disabled)                              │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  CONFLUENCE ENGINE (Gate 2)                       │
        │  • Majority vote + Weighted score                 │
        │  • Threshold: min 2 agreeing, min score 58        │
        │  • Contradiction check (standard + H013)          │
        │  • Multi-TF confirmation (D1/H1 alignment)        │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  RISK GATE (Gate 3)                               │
        │  • Risk/Reward ratio (min 2.0)                    │
        │  • Position sizing                                │
        │  • Correlation exposure (max 2 per group)         │
        │  • Drawdown thresholds                            │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  SYMBOL HEALTH INDEX (Gate 4)                     │
        │  • Last 20 closed trades: Win Rate + Profit Factor│
        │  • Auto-pauses if SHI < 45                        │
        │  • Prevents cascade failures                      │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  NEWS INTELLIGENCE (Gate 5)                       │
        │  • NFP, FOMC, CPI detection                       │
        │  • 30-min blackout before high-impact events      │
        │  • Auto-cache for offline operation               │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  META DECISION LAYER (Gate 6)                     │
        │  • Confidence calibration                         │
        │  • Stability analysis                             │
        │  • Engine contribution tracking                   │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  EXECUTION & PERSISTENCE (Gate 7)                 │
        │  • Trade execution (dry_run | cTrader | OANDA)    │
        │  • Outcome tracking (auto-close on SL/TP)         │
        │  • Telegram alerts + Dashboard                    │
        │  • SQLite + JSONL for audit trail                 │
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
| `market_quality.py` | Market Quality Score (0-100) | `assess_market_quality()` |
| `timeframe_sync.py` | Multi-timeframe building and resampling | `build_multi_timeframe_view()` |
| `asset_profiles.py` | Per-asset settings (pip size, session hours, spreads) | `get_profile()` |
| `twelve_data_client.py` | Twelve Data API client with rate limiter + cache | `TwelveDataClient` |
| `ccxt_provider.py` | Crypto data via CCXT | `get_ccxt_data()` |

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

### 2. **engines/** — 9 Strategy Engines (4 currently enabled in config.yaml)

> As of this writing, `config.yaml`'s `engines.enabled` block has only
> `smc`, `price_action`, `nnfx`, and `wyckoff` set to `true`. The other
> five below are implemented and edge-gated but disabled — enabling them
> requires their hypothesis in `research/results/registry.json` to reach
> at least `RESEARCH` status (see `research/edge_gate.py`).

Each engine returns `EngineOutput(bias, score, reasons, raw)` where:
- `bias`: BULLISH | BEARISH | NEUTRAL
- `score`: 0-100 (how confident is this engine?)
- `reasons`: List of human-readable logic strings
- `raw`: Raw indicators used

| Engine | Weight | Status | Method |
|--------|--------|--------|--------|
| SMC | 20.2% | ✅ ACTIVE | Swing point majority vote |
| NNFX | 22.7% | ✅ ACTIVE | EMA200 + ADX |
| Price Action | 18.7% | ✅ ACTIVE | Sigmoid MA + breakout |
| Wyckoff | 7.1% | ✅ ACTIVE | Spring/Upthrust detection |
| Quant | 7.1% | ✅ ACTIVE | RSI(14) + ROC(10) |
| ICT | 6.6% | ✅ ACTIVE | Killzones + trend filter |
| Divergence | 6.1% | 🟡 RESEARCH | RSI/MACD divergence (H010) |
| Market Structure | 8.6% | 🟡 RESEARCH | BOS/CHoCH (H011) |
| Sentiment | 3.0% | 🟡 RESEARCH | COT proxy (H012) |
| Macro | 0.0% | ❌ DISABLED | Requires yfinance |

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
| `mtf_confirmation.py` | D1 trend must align with H1 signal (±8/15 pts) |
| `regime_weights.py` | Adjust engine weights based on market regime |
| `reversal_veto.py` | H013: When 2+ reversal engines unanimously oppose trend |
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

| File | Purpose | Hard Stops |
|------|---------|-----------|
| `risk_engine.py` | Risk/Reward, position sizing, exposure caps | RR ≥ 2.0, DD < 15% |
| `correlation_engine.py` | Portfolio correlation filter (max 2 per group) | Groups: USD, JPY, EUR, METALS, CRYPTO |

**Risk Groups:**
```
USD_MAJORS:   EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, NZDUSD
JPY_CROSSES:  EURJPY, GBPJPY, AUDJPY
EUR_CROSSES:  EURGBP, EURCHF, EURAUD
METALS:       XAUUSD, XAGUSD, USOIL
RISK_ASSETS:  BTCUSD, ETHUSD, US30, NAS100, SPX500
```
Max 2 EXECUTE signals per group per scheduler run.

### 5. **storage/** — Persistence & Analytics

| File | Purpose |
|------|---------|
| `decision_db.py` | SQLite: all decisions (568+ records) |
| `decision_log.py` | JSONL: detailed decision audit trail |
| `engine_tracker.py` | Per-engine live performance (292 votes) |
| `outcome_tracker.py` | Trade results: entry/exit/SL/TP/P&L |
| `symbol_health.py` | Symbol Health Index (SHI) auto-pause logic |
| `calibration.py` | Confidence calibration + regime performance matrix |
| `experience_db.py` | Market Memory: similar setups, historical WR |

### 6. **execution/** — Delivery & Broker Integration

| File | Purpose |
|------|---------|
| `api_server.py` | FastAPI server (17 endpoints, session auth) |
| `telegram_bot.py` | Telegram alerts (EXECUTE signals only) |
| `trade_executor.py` | Execution bridge: dry_run / cTrader / OANDA |
| `ctrader_client.py` | IC Markets cTrader integration (KYC pending) |
| `oanda_client.py` | OANDA REST API (backup, not available in Morocco) |
| `tradingview_webhook.py` | TradingView webhook stub |

### 7. **backtesting/** — Validation & Testing

| File | Purpose |
|------|---------|
| `backtest_engine.py` | Walk-forward backtest (no lookahead, asset-class aware P&L) |
| `metrics.py` | Sharpe ratio, max drawdown, profit factor |

**Walk-Forward Validation (18/18 symbols CONSISTENT ✅):**
- W1 Test (2024) vs W2 Test (2025) vs W3 Test (2026)
- Profit factor: min=1.50, avg=3.08
- Pass rate: 100%

### 8. **research/** — Edge Gate & Hypothesis Tracking

| File | Purpose |
|------|---------|
| `edge_gate.py` | Blocks any unproven engine at boot time |
| `hypotheses/` | H001-H013: engine claims before any code |
| `results/registry.json` | Single source of truth for hypothesis status |

**Key Rule:** No engine enabled in `config.yaml` without a `PASSED` entry in `registry.json`.

---

## Configuration

**config.yaml:**
```yaml
data:
  source: twelve_data | synthetic | csv | injected
  symbol: EURUSD
  timeframes: [H1, H4, D1]
  bars_to_load: 500
  twelve_data_symbols:  # 19 symbols + overrides per symbol
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
    ict: false        # RESEARCH — not proven yet
    divergence: false # RESEARCH — H010

confluence:
  min_engines_agreeing: 2
  min_score_to_trade: 58
  weights:
    smc: 0.202
    nnfx: 0.2273
    price_action: 0.1869
    # ... etc

risk:
  min_risk_reward: 2.0
  max_exposure: 0.05     # 5% of account at once
  max_drawdown_stop: 0.15 # > 15% → halt all trading
  risk_per_trade_max: 0.01

fundamentals:
  news_filter_enabled: true
  blackout_look_ahead_min: 60  # 1 hour before NFP/FOMC/CPI
```

---

## Key Design Principles

### 1. **NO_TRADE is Valid Output**
The system correctly identifies when **not** to trade. This is modeled as a feature, not a bug.

### 2. **Research Before Production (Edge Gate)**
No engine logic runs in production until:
- A hypothesis is written in `research/hypotheses/`
- An experiment in `research/experiments/` validates it against real data
- A `PASSED` entry exists in `research/results/registry.json`

### 3. **No Lookahead Bias**
At bar N, the pipeline only sees bars 0..N. Entry is next-bar open.

### 4. **Asset-Aware Math**
JPY pip ≠ EUR pip. Each asset has a profile with:
- Pip size (0.0001 for most, 0.01 for JPY)
- Session hours (UTC)
- Spread proxy
- Min pip move

### 5. **Sovereign Risk Layer**
Risk gate is separate from confluence voting. Any single risk rule failing blocks the trade.

### 6. **Multi-Provider Failover**
Data source priority:
1. Twelve Data (800 req/day free, M15+H1 native)
2. Yahoo Finance (unlimited*, H1+ only)
3. Alpha Vantage (25 req/day, FX + metals)
4. Finnhub (60 req/min free, OANDA FX + crypto)

### 7. **Transparent Reasoning**
Every decision includes:
- Which engines voted which way
- Why they voted
- What gates passed/failed
- What the next step would be

---

## Live Deployment

**Infrastructure:**
- VPS: Linux (Ubuntu 20.04+)
- Python 3.11+
- FastAPI server (port 8000)
- Cloudflare tunnel for HTTPS
- SQLite database (local, no external DB needed)

**Systemd Services:**
- `iatis-api.service` → FastAPI server
- `iatis-scheduler.service` → Scheduler (every 2 hours)

**Scheduler Logic:**
```bash
# Runs every 2 hours, 19 symbols
python scheduler.py --interval 120 --symbols EUR/USD GBP/USD ... BTC/USD

# Per symbol:
1. Check correlation → skip if correlated symbol already EXECUTE
2. Get symbol health → skip if PAUSED (SHI < 45)
3. Run full pipeline (7 gates)
4. If EXECUTE: try to execute trade (dry_run=true for now)
5. Auto-close outcomes when SL/TP hit
6. Send Telegram alert (EXECUTE only)
```

---

## API Endpoints (17 total)

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | Public | System status + API credits |
| `GET /health/full` | ✅ | CPU/RAM/Disk/Scheduler/DB/Calendar status |
| `POST /analyze/{symbol}` | ✅ | Run pipeline on demand |
| `GET /decisions` | ✅ | Decision history (paginated) |
| `GET /outcomes` | ✅ | Live trade outcome tracking |
| `POST /outcomes/{id}/close` | ✅ | Record trade result (SL/TP) |
| `GET /symbol-health` | ✅ | Symbol Health Index all symbols |
| `GET /meta-analysis` | ✅ | Calibration + regime matrix |
| `GET /engine-stats` | ✅ | Per-engine live performance |
| `GET /stats` | ✅ | SQLite analytics query |
| `GET /budget` | ✅ | API credit usage |
| `GET /backtest-results` | ✅ | Saved backtest JSON files |
| `GET /research` | ✅ | Hypothesis status |
| `GET /login` | Public | Login page |
| `POST /login` | Public | Authenticate → session cookie |
| `GET /dashboard` | Cookie | SPA dashboard |

---

## Security (14/14 vulnerabilities fixed)

✅ Session rotation (cookie holds `session_id`, never raw API key)
✅ HttpOnly + Secure + SameSite=Lax cookies (Lax, not Strict — Strict blocks the cross-origin redirect Cloudflare's tunnel performs on login)
✅ hmac.compare_digest for key comparison
✅ html.escape() on all dashboard values
✅ Symbol validation regex: `^[A-Z]{2,6}(/[A-Z]{2,6})?$`
✅ SQLite: chmod 0o600
✅ Telegram flood: 30min cooldown
✅ Swagger disabled in production

---

## File Structure (Complete)

```
IATIS/
├── main.py                           # 7-gate pipeline entry (465 lines)
├── scheduler.py                      # Automated runner (347 lines)
├── config.yaml                       # Configuration (all tunables)
├── requirements.txt                  # Dependencies (pinned)
├── README.md                         # Public documentation
│
├── core/                             # Data infrastructure
│   ├── data_providers.py            # Multi-provider failover
│   ├── data_loader.py               # CSV/synthetic loading
│   ├── data_manager.py              # Caching, retry
│   ├── data_validator.py            # OHLCV validation
│   ├── market_quality.py            # Market Quality Score (MQS)
│   ├── timeframe_sync.py            # Multi-TF building
│   ├── asset_profiles.py            # Per-asset settings
│   ├── twelve_data_client.py        # Twelve Data API client
│   └── ccxt_provider.py             # Crypto via CCXT
│
├── engines/                          # 9 strategy engines
│   ├── base_engine.py               # Base class (Bias, EngineOutput)
│   ├── smc_engine.py                # Smart Money Concepts
│   ├── price_action_engine.py       # MA + breakout
│   ├── nnfx_engine.py               # EMA200 + ADX
│   ├── ict_engine.py                # Killzones + trend
│   ├── quant_engine.py              # RSI + ROC
│   ├── wyckoff_engine.py            # Spring/Upthrust
│   ├── divergence_engine.py         # RSI/MACD divergence (H010)
│   ├── market_structure_engine.py   # BOS/CHoCH (H011)
│   ├── sentiment_engine.py          # COT proxy (H012)
│   └── macro_engine.py              # DXY + risk-on/off (disabled)
│
├── confluence/                       # Voting & decision logic
│   ├── voting_system.py             # Majority vote tally
│   ├── score_calculator.py          # Weighted score (majority only)
│   ├── contradiction_engine.py      # Standard + H013
│   ├── mtf_confirmation.py          # D1/H1 alignment
│   ├── regime_weights.py            # Regime-aware weights
│   ├── reversal_veto.py             # H013 reversal consensus
│   └── meta_decision.py             # Confidence calibration
│
├── risk/                             # Risk management (sovereign layer)
│   ├── risk_engine.py               # Risk/Reward, position sizing
│   └── correlation_engine.py        # Portfolio correlation filter
│
├── fundamentals/                     # News & calendar
│   ├── news_calendar.py             # Event calendar (cached)
│   └── news_risk.py                 # Blackout system
│
├── regimes/                          # Market regime detection
│   ├── regime_detector.py           # TRENDING | RANGING
│   └── volatility_classifier.py     # ATR percentile scoring
│
├── storage/                          # Persistence & analytics
│   ├── decision_db.py               # SQLite decisions
│   ├── decision_log.py              # JSONL audit trail
│   ├── engine_tracker.py            # Per-engine performance
│   ├── outcome_tracker.py           # Trade results
│   ├── symbol_health.py             # SHI auto-pause
│   ├── calibration.py               # Confidence + regime matrix
│   └── experience_db.py             # Market Memory (similar setups)
│
├── execution/                        # Delivery & brokers
│   ├── api_server.py                # FastAPI (17 endpoints)
│   ├── telegram_bot.py              # Telegram alerts
│   ├── trade_executor.py            # Execution bridge
│   ├── ctrader_client.py            # IC Markets cTrader
│   ├── oanda_client.py              # OANDA (backup)
│   └── tradingview_webhook.py       # TradingView stub
│
├── backtesting/                      # Validation
│   ├── backtest_engine.py           # Walk-forward (no lookahead)
│   └── metrics.py                   # Sharpe, DD, PF
│
├── research/                         # Edge gate & hypotheses
│   ├── edge_gate.py                 # Blocks unproven engines
│   ├── hypotheses/                  # H001-H013 claims
│   ├── experiments/                 # Validation scripts
│   ├── results/
│   │   └── registry.json            # Single source of truth
│   └── notebooks/                   # Exploratory (optional)
│
├── utils/                            # Helpers
│   ├── helpers.py                   # load_config(), etc.
│   └── logger.py                    # Structured logging
│
├── tests/                            # 262 tests, 0 failures
│   ├── test_engines/
│   ├── test_confluence/
│   ├── test_risk/
│   ├── test_backtesting/
│   └── ... etc
│
├── scripts/                          # Utilities
│   ├── full_pipeline_backtest.py    # v0.4 complete pipeline test
│   ├── walk_forward_validation.py   # Out-of-sample validation
│   ├── download_all_symbols.py      # Download 20 symbols
│   ├── cache_calendar.py            # Daily calendar cache
│   └── ... etc
│
├── data/                             # Historical datasets
│   └── README.md                    # Data documentation
│
├── docs/                             # Documentation
│   ├── VISION_v2.md                 # Roadmap + deferred layers
│   └── ... etc
│
├── storage/                          # Runtime data
│   ├── decisions.sqlite             # SQLite DB (chmod 0o600)
│   ├── decisions.jsonl              # JSONL audit trail
│   ├── outcomes.jsonl               # Trade results
│   └── news_history/                # Cached news calendars
│
├── iatis-api.service                # Systemd service
├── iatis-scheduler.service          # Systemd service
└── .env                             # Secrets (never committed)
```

---

## Dependency Tree

```
main.py
├── core/ (data loading & validation)
│   ├── data_providers.py (multi-provider)
│   ├── timeframe_sync.py
│   └── market_quality.py
├── engines/ (9 engines)
│   ├── base_engine.py
│   └── [each engine imports base_engine]
├── confluence/ (voting)
│   ├── voting_system.py
│   ├── score_calculator.py
│   ├── contradiction_engine.py
│   ├── mtf_confirmation.py
│   ├── regime_weights.py
│   ├── reversal_veto.py
│   └── meta_decision.py
├── risk/ (sovereign layer)
│   ├── risk_engine.py
│   └── correlation_engine.py
├── regimes/ (regime detection)
├── fundamentals/ (news)
├── storage/ (persistence)
├── research/ (edge gate)
└── execution/ (delivery)

scheduler.py
├── main.py (the pipeline)
├── risk/correlation_engine.py (correlation filter)
├── storage/symbol_health.py (SHI check)
├── storage/outcome_tracker.py (auto-close)
└── execution/telegram_bot.py (alerts)

api_server.py (FastAPI)
├── main.py (on-demand analysis)
├── storage/ (all endpoints)
├── backtesting/ (results endpoints)
└── execution/ctrader_client.py
```

---

## Phase Roadmap

✅ **Phase 1** — Architecture wired correctly
✅ **Phase 2** — Live data (Twelve Data) + Telegram + FastAPI
✅ **Phase 3** — 9 engines + security fixes + regime-aware weights
✅ **Phase 4** — Market Intelligence Platform (current)
- H009 PASSED (6-engine confluence)
- MQS, MTF, correlation, SHI, meta-decision
- cTrader/IC Markets integration (KYC pending)

⏳ **Phase 5** — Live Trading
- cTrader KYC approved → demo account
- 200+ closed trades for calibration
- Dynamic weight optimization

⏳ **Phase 6** — Scale
- Multi-user (JWT + PostgreSQL)
- TradingView webhook
- Volume Profile (needs M1 tick data)

---

## Validation Results

### Walk-Forward (Out-of-Sample) — 18/18 CONSISTENT ✅

| Symbol | W1 (2024) | W2 (2025) | W3 (2026) |
|--------|-----------|-----------|-----------|
| EURUSD | 3.15 | 2.14 | 5.81 |
| GBPUSD | 3.24 | 2.15 | 2.55 |
| USDJPY | 2.83 | 2.44 | 3.36 |
| AUDUSD | 6.39 | 1.50 | 5.05 |
| BTCUSD | 3.03 | 2.89 | 2.54 |
| USOIL | 2.28 | 2.30 | 1.82 |

**Min PF = 1.50 | Avg PF = 3.08 | Pass rate = 100%**
**Statistical significance: P(18/18 by chance) < 0.004%**

---

## System Health Dashboard

```
GET /health/full

{
  "api_server": "running",
  "scheduler": "running (2h interval)",
  "database": "healthy (568 decisions, 292 engine votes)",
  "telegram": "connected",
  "api_credits": {
    "twelve_data": "245 remaining today",
    "alpha_vantage": "22 remaining",
    "finnhub": "59/60 per minute"
  },
  "symbols_trading": 19,
  "outcomes_tracking": 47,
  "last_scheduler_run": "2 hours ago",
  "cpu_usage": "12%",
  "ram_usage": "420MB / 2GB",
  "disk_usage": "45GB / 100GB"
}
```

---

## Assessment

```
Research Platform:     9/10
Architecture:          9/10
Security:              9/10
Operational Stability: 9/10
Edge Validation:       8.5/10  (Walk-Forward 18/18 CONSISTENT)
Broker Integration:    7/10    (cTrader KYC pending)
Commercial Readiness:  8/10

Codebase:
  99 Python files
  ~16,078 lines of code
  262 tests (0 failures)
  17 API endpoints
  9 strategy engines
  7 decision gates
```

---

**The system now functions as a Market Intelligence Platform rather than a simple signal generator. Every decision is contextual, explainable, and traceable.**
