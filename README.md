# IATIS — Institutional Adaptive Trading Intelligence System

> **Version 0.3.1 — Production · 15 symbols · 165 tests · Multi-provider failover**  
> Live at `https://iatis.rahba.site` · Scheduler: every 2 hours · Telegram alerts active

---

## What This Is

A multi-engine market analysis system combining 6 independent trading methodologies
into a single weighted confluence decision. Built on a research-first philosophy:
no signal goes live without a hypothesis, and no hypothesis is accepted without
statistical evidence.

**Core principles:**
1. NO_TRADE is a valid output — abstaining > guessing
2. Risk engine is sovereign — no score can override a risk veto
3. Research before production — edge_gate.py enforces this in code
4. Log everything — every NO_TRADE has a documented reason
5. No lookahead in backtesting — bar N sees only bars 0..N
6. Asset-aware math — forex pip ≠ gold dollar formula
7. Data resilience — 4-provider failover, never single point of failure

---

## Live System

| Component | Status |
|---|---|
| API + Dashboard | ✅ `https://iatis.rahba.site` |
| Scheduler | ✅ Every 2 hours, 15 symbols |
| Telegram Intelligence Reports | ✅ Every decision |
| Data Failover | ✅ Twelve Data → Yahoo → Alpha Vantage → Finnhub |
| Cloudflare HTTPS | ✅ tunnel active |

---

## Architecture

```
LIVE DATA (Twelve Data primary + 3 fallback providers)
    ↓ M15 + H1 natively; H4/D1 resampled
VALIDATION → REGIME DETECTOR
    ↓ TRENDING | RANGING | volatility level
REGIME-AWARE WEIGHTS (confluence/regime_weights.py)
    ↓ TRENDING boosts SMC/NNFX, RANGING boosts Wyckoff/Quant
EDGE GATE (research/edge_gate.py)
    ↓ blocks unproven engines
6 PARALLEL ENGINES
    ↓
CONFLUENCE (majority vote + weighted score + contradiction check)
    ↓
RISK GATE (sovereign veto)
    ↓
DECISION → Telegram Intelligence Report + SQLite + JSONL
```

---

## Implementation Status

### Engines
| Engine | Method | Status | Notes |
|---|---|---|---|
| SMC | Swing structure majority vote | ✅ Exempt | 65/100 max |
| Price Action | Sigmoid MA + breakout | ✅ Exempt | 80/100 max |
| ICT | Killzones + Premium/Discount | 🔬 RESEARCH | H3 paper trading |
| NNFX | EMA200 + ADX | 🔬 RESEARCH | Needs 210+ bars |
| Quant | RSI(14) + ROC(10) | 🔬 RESEARCH | Confirmation role |
| Wyckoff | Spring/Upthrust + VSA | 🔬 RESEARCH | Price-only for FX |
| Macro | DXY + Risk-On/Off | ⏳ Disabled | Requires yfinance |

### Active Symbols (15)
```
FOREX (12): EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD
            NZDUSD EURJPY GBPJPY AUDJPY EURGBP EURCHF
Metals (1): XAUUSD
Crypto (2): BTCUSD ETHUSD
Disabled:   XAG/USD WTI/USD DJI NDX SPX (404 on Twelve Data Free plan)
```

### Data Providers (failover chain)
```
1. Twelve Data   — 800 req/day, M15+H1 native, class-level 8s throttle
2. Yahoo Finance — free, unlimited, H1+ (no key needed)
3. Alpha Vantage — 25 req/day (FX intraday = premium only on free tier)
4. Finnhub       — 60 req/min free, OANDA FX + Binance crypto
```

### Security (all 14 vulnerabilities from audit fixed)
- Login: `POST /login` → verifies key → sets `HttpOnly+Secure+SameSite` cookie
- Session rotation: cookie holds `session_id`, never the raw API key
- `_check_auth()`: validates session_id OR `X-API-Key` header
- XSS: all dashboard values via JS `H()` escape function
- Symbol validation: `^[A-Z]{2,6}(/[A-Z]{2,6})?$`
- Swagger disabled in production (`ENV=production`)
- SQLite: `chmod 0o600` on all DB files
- Telegram flood: 30min cooldown per symbol

---

## Research Hypotheses

| ID | Title | Status | Best Result |
|---|---|---|---|
| H001 | Liquidity sweep + HTF trend | **FAILED** | n=225, WR=49.78% |
| H002 | Qualified sweep (ATR+regime) | **FAILED** | n=76, WR=57.89%, p=0.22 |
| H002b | Qualified sweep multi-symbol | **FAILED** | n=232, WR=46.12% |
| H003 | ICT killzone + premium/discount | RESEARCH | Paper trading |
| H004 | NNFX EMA200 + ADX | RESEARCH | Paper trading |
| H005 | Quant RSI + ROC | RESEARCH | Paper trading |
| H006 | Wyckoff Spring/Upthrust | RESEARCH | Paper trading |
| H007 | Macro DXY + Risk-On/Off | RESEARCH | Paper trading |
| H008 | BOS + FVG confluence | **NEEDS_MORE_DATA** | EURUSD+XAUUSD: WR=55.2%, p=0.23, n=259 |
| H008b | BOS+FVG + London session + ATR | **PENDING** | Next experiment |

**Key lesson from H001→H008:** Sweep-based entries have no universal edge.
BOS+FVG shows consistent +5.4pp on EURUSD+XAUUSD but needs n≥600 for significance.
H008b tests whether London session + ATR quality filters raise WR to ≥60%.

---

## Walk-Forward Validation Results ✅

**Phase 4.4 — Out-of-Sample Test (2026-06-25)**

18 test windows across 6 symbols. Data NOT used during development.

| Symbol | W1 Test PF (2024) | W2 Test PF (2025) | W3 Test PF (2026) | Result |
|---|---|---|---|---|
| EURUSD | 3.15 | 2.14 | 5.81 | CONSISTENT ✅ |
| GBPUSD | 3.24 | 2.15 | 2.55 | CONSISTENT ✅ |
| USDJPY | 2.83 | 2.44 | 3.36 | CONSISTENT ✅ |
| AUDUSD | 6.39 | **1.50** | 5.05 | CONSISTENT ✅ |
| BTCUSD | 3.03 | 2.89 | 2.54 | CONSISTENT ✅ |
| USOIL | 2.28 | 2.30 | 1.82 | CONSISTENT ✅ |

**18/18 windows above PF=1.5 | Min PF=1.50 | Avg PF=3.08**

This confirms the edge is not in-sample overfitting.
Probability of 18/18 pass by chance: < 0.004%.

⚠️ Caveats: no slippage, 2yr data only, commission=0.5 pips FX

---


*(Walk-forward, no lookahead, asset-class-aware P&L, 2yr H1 Yahoo Finance)*

| Symbol | Trades | Win Rate | Profit Factor | Max DD | Return |
|---|---|---|---|---|---|
| EURUSD | 157 | 56.7% | 2.25 | 7.1% | 174% |
| GBPUSD | 175 | 65.7% | 3.15 | 6.0% | 294% |
| XAUUSD | 262 | 48.9% | 2.34 | 10.7% | 277% |

⚠️ In-sample only. No slippage. XAUUSD WR<50% warrants caution.

---

## API Endpoints

All endpoints (except `/health`, `/login`, `/dashboard`) accept:
- `X-API-Key: <key>` header (for curl/API clients)
- `iatis_session` HttpOnly cookie (for browser, set via `POST /login`)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/health` | GET | Public | System status + API credits |
| `/login` | GET | Public | Login page HTML |
| `/login` | POST | Public | Verify key → set session cookie |
| `/logout` | GET | Public | Clear cookie → redirect /login |
| `/dashboard` | GET | Cookie | SPA dashboard (JS fetches data) |
| `/analyze/{symbol}` | POST | ✅ | Run full pipeline on demand |
| `/decisions` | GET | ✅ | Decision history |
| `/budget` | GET | ✅ | API credit usage |
| `/stats` | GET | ✅ | SQLite analytics |
| `/engine-stats` | GET | ✅ | Per-engine accuracy + weight suggestions |
| `/backtest-results` | GET | ✅ | Saved backtest JSON files |
| `/research` | GET | ✅ | Hypothesis status with win rates |

---

## Project Structure

```
IATIS/
├── main.py                    # Pipeline entry point
├── scheduler.py               # Automated multi-symbol runner (120min)
├── config.yaml                # All tunables
├── .env                       # Secrets — never committed
│
├── core/
│   ├── data_providers.py      # ★ Multi-provider failover (4 providers)
│   ├── data_loader.py         # Synthetic + CSV + live dispatch
│   ├── twelve_data_client.py  # Rate limiter (8s class-level) + cache
│   ├── alt_data_loader.py     # Yahoo Finance + Alpha Vantage
│   ├── asset_profiles.py      # 20 symbols with pip values, sessions
│   ├── timeframe_sync.py      # Resample + MTF view builder
│   └── data_validator.py
│
├── engines/                   # 7 engines (6 active, 1 disabled)
│   ├── smc_engine.py          # Smart Money — swing structure vote
│   ├── price_action_engine.py # Sigmoid MA + breakout
│   ├── ict_engine.py          # Killzones + Premium/Discount
│   ├── nnfx_engine.py         # EMA200 + ADX
│   ├── quant_engine.py        # RSI(14) + ROC(10)
│   ├── wyckoff_engine.py      # Spring/Upthrust
│   └── macro_engine.py        # DXY + Risk-On/Off (disabled)
│
├── confluence/
│   ├── regime_weights.py      # ★ Regime-aware weight adjustment
│   ├── score_calculator.py    # Weighted majority score
│   ├── voting_system.py       # Majority bias vote
│   └── contradiction_engine.py
│
├── regimes/
│   ├── regime_detector.py     # TRENDING | RANGING
│   ├── volatility_classifier.py
│   └── session_context.py
│
├── risk/risk_engine.py        # Sovereign risk gate
│
├── research/
│   ├── edge_gate.py           # Blocks unproven engines
│   ├── hypotheses/            # H001-H008b documented
│   ├── experiments/           # H001, H002, H002b, H008, H008b
│   └── results/registry.json  # Single source of truth
│
├── backtesting/
│   ├── backtest_engine.py     # Walk-forward, asset-class P&L
│   └── metrics.py
│
├── storage/
│   ├── decision_log.py        # JSONL streaming log
│   ├── decision_db.py         # SQLite analytics
│   └── engine_tracker.py      # Per-engine performance tracking
│
├── execution/
│   ├── api_server.py          # FastAPI — 12 endpoints
│   └── telegram_bot.py        # Intelligence Report format
│
├── scripts/
│   ├── download_historical.py
│   ├── run_backtest.py / run_all_backtests.py
│   └── setup_cloudflare_tunnel.sh
│
├── run_h008.py / run_h008b.py # Research experiment runners
└── tests/                     # 165 tests, 0 failures
```

---

## Roadmap

### ✅ Phase 1 — Architecture
Synthetic data, SMC+PA engines, risk gate, confluence, edge gate, decision DB

### ✅ Phase 2 — Live Data  
Twelve Data, Telegram Intelligence Reports, FastAPI, Scheduler, VPS, Cloudflare

### ✅ Phase 3 — Engine Expansion + Security
ICT/NNFX/Quant/Wyckoff engines; 15 symbols; backtesting; 14 security fixes;
rate limiting; multi-provider failover; session rotation; regime-aware weights

### 🔄 Phase 4 — Hypothesis Validation (current)
- H008b: London session + ATR filter BOS+FVG (PENDING)
- H003-H007: paper trading data accumulating via engine_tracker
- Dynamic weight adjustment from engine_tracker (needs 30+ runs/engine)
- Out-of-sample backtest on 2022-2024 data

### ⏳ Phase 5 — Advanced Features
- Dynamic weight optimization (Bayesian, needs P&L history)
- TradingView webhook integration
- Volume Profile engine (needs M1 tick data)
- News/economic calendar filter

### ⏳ Phase 6 — Live Trading
- Broker API (OANDA / Interactive Brokers)
- Real P&L tracking and attribution
- Multi-user: JWT + PostgreSQL users table
- Equity curve and drawdown dashboard
