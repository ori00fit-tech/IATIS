# IATIS — Institutional Adaptive Trading Intelligence System

> **Version 0.4 — Production · 19 symbols · 262 tests · Market Intelligence Platform**
> Live: `https://iatis.rahba.site` · Scheduler: every 2 hours · Telegram alerts active

---

## What This Is

IATIS is a **Market Intelligence Platform** — not just a signal generator.

Before any trade executes, the system evaluates:
- **Market Quality** (session, volatility, time of day)
- **Multi-Timeframe alignment** (D1 vs H1)
- **Portfolio correlation** (max 2 signals per correlated group)
- **Reversal engine consensus** (H013 — prevents trend-vs-reversal conflicts)
- **News blackout** (auto-blocks during NFP, FOMC, CPI)
- **Symbol health** (auto-pauses underperforming symbols)

Only after all 7 gates pass does the system generate an EXECUTE signal.

**Core philosophy:**
1. NO_TRADE is a valid (and often correct) output
2. Every rejection has a documented reason
3. Research before production — edge_gate.py enforces this in code
4. No lookahead in backtesting — bar N sees only bars 0..N
5. Asset-aware math — JPY pip ≠ EUR formula
6. Platform > Signal — explain, measure, improve

---

## Live System

| Component | Status |
|---|---|
| API + Dashboard | ✅ `https://iatis.rahba.site` |
| Scheduler | ✅ Every 2 hours, 19 symbols |
| Telegram Intelligence Reports | ✅ Every decision |
| Data Failover | ✅ Twelve Data → Yahoo → Alpha Vantage → Finnhub |
| Cloudflare HTTPS | ✅ tunnel active |
| Broker Integration | ⏳ cTrader/IC Markets (KYC submitted) |
| Outcome Tracking | ✅ Auto-logs every EXECUTE signal |

---

## Architecture

```
LIVE DATA (4-provider failover)
    ↓
MARKET QUALITY SCORE (0-100)
    ↓ POOR (<40) → NO_TRADE immediately
DATA VALIDATION + REGIME DETECTION
    ↓ TRENDING | RANGING | VOLATILE
REGIME-AWARE WEIGHTS
    ↓
9 PARALLEL ENGINES
    ↓
CONFLUENCE (majority vote + weighted score)
    ↓
MTF CONFIRMATION (D1 trend vs H1 signal: ±8/15 pts)
    ↓
CONTRADICTION CHECK (Standard + H013 Group)
    ↓
CORRELATION FILTER (max 2 per group)
    ↓
RISK GATE (sovereign veto)
    ↓
NEWS GATE (blackout 30min before NFP/FOMC/CPI)
    ↓
SYMBOL HEALTH CHECK (auto-pause underperformers)
    ↓
DECISION → Telegram + SQLite + JSONL + Outcome Tracker
```

---

## Engines (9 active)

| Engine | Method | Status | Weight | Notes |
|---|---|---|---|---|
| SMC | Swing structure majority vote | EXEMPT | 0.200 | 65/100 max |
| Price Action | Sigmoid MA + breakout | EXEMPT | 0.185 | 80/100 max |
| NNFX | EMA200 + ADX | RESEARCH | 0.225 | Highest live contribution |
| ICT | Killzones + Premium/Discount | RESEARCH | 0.065 | Trend filter applied |
| Quant | RSI(14) + ROC(10) | RESEARCH | 0.070 | Confirmation role |
| Wyckoff | Spring/Upthrust + VSA | RESEARCH | 0.040 | Reversal detection |
| Divergence | RSI/MACD divergence | RESEARCH | 0.100 | H010 — reversal engine |
| Market Structure | BOS/CHoCH/MSS | RESEARCH | 0.085 | H011 |
| Sentiment | COT + retail proxy | RESEARCH | 0.030 | H012 |
| Macro | DXY + Risk-On/Off | DISABLED | 0.000 | Requires yfinance |

**Weights updated from 292 live votes (data-driven, not assumed).**

---

## v0.4 Additions

### Market Quality Score (MQS)
Evaluates market conditions BEFORE running 9 engines. Saves API credits and eliminates weak signals.

```
Score = Session (35pts) + ATR (30pts) + Trend clarity (10pts) + Base (15pts) - Penalties
GOOD (≥60): run analysis
FAIR (40-59): run analysis, caution
POOR (<40): NO_TRADE immediately

Eliminates: Asian dead session, Monday gaps, Friday close, extreme volatility
```

### Multi-TF Confirmation
D1 trend must align with H1 signal direction.
```
D1 confirms H1 → +8 pts bonus
D1 contradicts H1 → -15 pts penalty (counter-trend risk)
ADX < 20 on D1 → no adjustment (weak D1 trend)
```

### Correlation Filter
Prevents over-exposure when multiple correlated symbols signal simultaneously.
```
Groups: USD_MAJORS, JPY_CROSSES, EUR_CROSSES, METALS, RISK_ASSETS
Max 2 signals per group per scheduler run
Example: USDJPY EXECUTE + EURJPY EXECUTE → AUDJPY blocked
```

### Group Contradiction — H013
When 2+ reversal engines (Divergence, Wyckoff, Sentiment) agree on OPPOSITE direction to trend engines → block trade.
```
Evidence (2026-06-26): 4 trend engines BEARISH vs 3 reversal engines BULLISH
→ Market reversed BULLISH, reversal engines were correct
→ H013 would have blocked these 5 losing trades
```

### Symbol Health Index (SHI)
Auto-pauses symbols with persistent poor performance.
```
Score based on last 20 closed trades:
  Win Rate (40pts) + Profit Factor (30pts) + Consecutive losses (15pts) + Recent trend (15pts)
HEALTHY (≥65): trade normally
CAUTION (45-64): 0.5× position size
PAUSED (<45): skip symbol
```

### System Health Dashboard
```
GET /health/full → CPU/RAM/Disk/Scheduler/DB/Calendar/Broker status
GET /symbol-health → SHI for all 19 symbols
GET /outcomes → live trade outcome tracking
```

---

## Validation Results

### Walk-Forward (Out-of-Sample) — 18/18 CONSISTENT ✅
*Data NOT used during development.*

| Symbol | W1 Test PF (2024) | W2 Test PF (2025) | W3 Test PF (2026) |
|---|---|---|---|
| EURUSD | 3.15 | 2.14 | 5.81 |
| GBPUSD | 3.24 | 2.15 | 2.55 |
| USDJPY | 2.83 | 2.44 | 3.36 |
| AUDUSD | 6.39 | 1.50 | 5.05 |
| BTCUSD | 3.03 | 2.89 | 2.54 |
| USOIL | 2.28 | 2.30 | 1.82 |

**Min PF = 1.50 | Avg PF = 3.08 | Pass rate = 100%**

Statistical significance: P(18/18 by chance) < 0.004%

### Backtesting (20 symbols, 2yr H1, no lookahead)

| Grade | Count | Avg WR | Avg PF |
|---|---|---|---|
| GOOD (PF≥1.5, WR≥50%, DD≤15%) | 16 | 60.5% | 2.72 |
| MARGINAL | 4 | 47.8% | 1.89 |
| POOR | 0 | — | — |

---

## Research Hypotheses

| ID | Title | Status |
|---|---|---|
| H001 | Liquidity sweep + HTF trend | FAILED |
| H002 | Qualified sweep (ATR+regime) | FAILED |
| H002b | Qualified sweep multi-symbol | FAILED |
| H003-H007 | Individual engine edges | RESEARCH |
| H008 | BOS + FVG confluence | NEEDS_MORE_DATA |
| H008b | BOS+FVG + London session + ATR | ABANDONED |
| **H009** | **6-engine confluence as primary edge** | **PASSED ✅** |
| H010 | RSI/MACD Divergence engine | RESEARCH |
| H011 | BOS/CHoCH/MSS Market Structure | RESEARCH |
| H012 | COT + Retail Sentiment | RESEARCH |
| H013 | Reversal engine group agreement | RESEARCH |

**Key lesson:** The edge is not in any single pattern (sweep, BOS, FVG) but in the **confluence of 9 independent methodologies**.

---

## Broker Integration

### IC Markets / cTrader (Recommended — Morocco ✅)
```
Status: KYC Submitted (2-3 business days for Active)
Account: Demo 10076823 (cTrader Raw Spread Swap Free)
API: execution/ctrader_client.py

Setup:
  1. Register: https://www.icmarkets.com/
  2. cTrader → Settings → API → Create Application
  3. Add to .env:
       CTRADER_CLIENT_ID=...
       CTRADER_CLIENT_SECRET=...
       CTRADER_ACCOUNT_ID=10076823
       CTRADER_ACCESS_TOKEN=...  (after KYC approval)
       CTRADER_ENVIRONMENT=demo

Recommended leverage:
  FX Majors:   1:30-1:50
  Gold/Silver: 1:20
  Oil/Indices: 1:20
  Crypto:      1:5-1:10
```

### OANDA (Backup)
```
execution/oanda_client.py
Not available in Morocco — kept as fallback
```

---

## API Endpoints (17 total)

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | Public | System status + API credits |
| `GET /health/full` | ✅ | Full system health dashboard |
| `GET /login` | Public | Login page |
| `POST /login` | Public | Authenticate → session cookie |
| `GET /dashboard` | Cookie | SPA dashboard |
| `POST /analyze/{symbol}` | ✅ | Run pipeline on demand |
| `GET /decisions` | ✅ | Decision history |
| `GET /outcomes` | ✅ | Live trade outcome tracking |
| `POST /outcomes/{id}/close` | ✅ | Record trade result |
| `GET /symbol-health` | ✅ | Symbol Health Index all symbols |
| `GET /meta-analysis` | ✅ | Calibration + regime matrix |
| `GET /engine-stats` | ✅ | Per-engine live performance |
| `GET /stats` | ✅ | SQLite analytics |
| `GET /budget` | ✅ | API credit usage |
| `GET /backtest-results` | ✅ | Saved backtest JSON files |
| `GET /research` | ✅ | Hypothesis status |

---

## Project Structure

```
IATIS/
├── main.py                      # 7-gate pipeline entry point
├── scheduler.py                 # Automated multi-symbol runner
├── config.yaml                  # All tunables
├── .env                         # Secrets (never committed)
│
├── core/
│   ├── data_providers.py        # ★ 4-provider failover
│   ├── market_quality.py        # ★ MQS — session/ATR/trend quality
│   ├── twelve_data_client.py    # Rate limiter + cache
│   ├── asset_profiles.py        # 20 symbols with pip values
│   └── timeframe_sync.py        # M15+H1+H4+D1 builder
│
├── engines/ (9 engines)
│   ├── smc_engine.py            # Smart Money Concepts (EXEMPT)
│   ├── price_action_engine.py   # MA + breakout (EXEMPT)
│   ├── ict_engine.py            # Killzones + trend filter
│   ├── nnfx_engine.py           # EMA200 + ADX (highest contribution)
│   ├── quant_engine.py          # RSI + ROC
│   ├── wyckoff_engine.py        # Spring/Upthrust
│   ├── divergence_engine.py     # RSI/MACD divergence (H010)
│   ├── market_structure_engine.py # BOS/CHoCH (H011)
│   └── sentiment_engine.py      # COT proxy (H012)
│
├── confluence/
│   ├── regime_weights.py        # TRENDING/RANGING/VOLATILE weights
│   ├── score_calculator.py      # Weighted majority score
│   ├── contradiction_engine.py  # Standard + H013 Group
│   ├── voting_system.py         # Majority bias vote
│   └── mtf_confirmation.py      # ★ D1/H1 alignment (±8/15 pts)
│
├── risk/
│   ├── risk_engine.py           # Sovereign risk gate
│   └── correlation_engine.py   # ★ Portfolio correlation filter
│
├── fundamentals/
│   ├── news_calendar.py         # JBlanked + FF + local cache
│   └── news_risk.py             # Blackout system
│
├── regimes/
│   ├── regime_detector.py       # TRENDING | RANGING
│   └── volatility_classifier.py
│
├── storage/
│   ├── decision_db.py           # SQLite decisions (568+ records)
│   ├── engine_tracker.py        # Per-engine performance (292 votes)
│   ├── outcome_tracker.py       # ★ Live trade outcomes
│   ├── symbol_health.py         # ★ Symbol Health Index
│   └── calibration.py           # Confidence calibration + regime matrix
│
├── execution/
│   ├── api_server.py            # FastAPI — 17 endpoints
│   ├── telegram_bot.py          # Intelligence Report format
│   ├── ctrader_client.py        # ★ IC Markets broker (primary)
│   ├── oanda_client.py          # OANDA broker (backup)
│   └── trade_executor.py        # ★ Execution bridge (dry_run=True)
│
├── research/
│   ├── edge_gate.py             # Blocks unproven engines
│   ├── hypotheses/              # H001-H013 documented
│   └── results/registry.json   # Single source of truth
│
├── backtesting/
│   ├── backtest_engine.py       # Walk-forward, asset-class P&L
│   └── metrics.py
│
├── scripts/
│   ├── download_all_symbols.py  # 20 symbols from Yahoo Finance
│   ├── full_pipeline_backtest.py # ★ v0.4 complete pipeline test
│   ├── walk_forward_validation.py # Out-of-sample validation
│   ├── backtest_all_symbols.py  # Confluence-only backtest
│   └── cache_calendar.py        # Daily calendar cache
│
└── tests/ (262 tests, 0 failures)
```

---

## Security (14/14 vulnerabilities fixed)

- Session Rotation: cookie holds `session_id`, never raw API key
- `HttpOnly + Secure + SameSite=Strict` cookies
- `hmac.compare_digest` for key comparison
- `html.escape()` on all dashboard values
- Symbol validation regex: `^[A-Z]{2,6}(/[A-Z]{2,6})?$`
- SQLite: `chmod 0o600`
- Telegram flood: 30min cooldown
- Swagger disabled in production

---

## Roadmap

### ✅ Phase 1 — Architecture
Synthetic data, SMC+PA engines, risk gate, confluence, edge gate, decision DB

### ✅ Phase 2 — Live Data
Twelve Data, Telegram, FastAPI, Scheduler, VPS, Cloudflare

### ✅ Phase 3 — Engine Expansion + Security
9 engines, 19 symbols, backtesting, 14 security fixes, news intelligence,
multi-provider failover, session rotation, regime-aware weights

### ✅ Phase 4 — Market Intelligence Platform (current)
- H009 PASSED (walk-forward validated)
- Market Quality Score (MQS)
- Multi-TF Confirmation (D1/H1)
- Correlation Filter
- Group Contradiction H013
- Symbol Health Index
- System Health Dashboard
- Outcome Tracker
- cTrader/IC Markets integration (KYC pending)
- Full Pipeline Backtest v0.4

### ⏳ Phase 5 — Live Trading
- cTrader KYC Active → paper trading on Demo
- Confidence Calibration (needs 200+ closed trades)
- Regime Performance Matrix (needs 200+ closed trades)
- Dynamic Weight optimization (auto-update from engine_tracker)

### ⏳ Phase 6 — Scale
- Live account after 30-day paper trading validation
- Multi-user: JWT + PostgreSQL
- TradingView webhook integration
- Volume Profile (needs M1 tick data)

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

Codebase: 99 Python files | 16,078 lines | 262 tests | 17 API endpoints
```

**The system now functions as a Market Intelligence Platform rather than a simple signal generator. Every decision is contextual, explainable, and traceable.**
