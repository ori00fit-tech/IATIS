# IATIS — Institutional Adaptive Trading Intelligence System

> **Version 0.3.0 — Production deployment on VPS, live data, backtested.**  
> Running on real market data via Twelve Data API, accessible at `https://iatis.rahba.site`

---

## What This Is

A multi-engine market analysis system that combines 6 independent trading methodologies
into a single weighted confluence decision. The core philosophy:

- **NO_TRADE is a valid successful output.** The system abstains more than it trades.
- **Risk engine is sovereign.** No confluence score can override a risk gate veto.
- **No engine may go live without a proven hypothesis.** All research gated by `edge_gate.py`.
- **Every decision is logged** — EXECUTE and NO_TRADE alike — for performance analysis.

---

## Live System Status

| Component | Status | Location |
|---|---|---|
| API Server | ✅ Live | `https://iatis.rahba.site` |
| Scheduler | ✅ Running | VPS — every 60 min |
| Telegram Alerts | ✅ Active | Every decision → your phone |
| Dashboard | ✅ Live | `https://iatis.rahba.site/dashboard` |
| Data Source | ✅ Twelve Data | 800 req/day Free plan |

---

## Architecture

```
LIVE DATA (Twelve Data)
    ↓ M15 + H1 natively; H4/D1 resampled
VALIDATION → REGIME DETECTOR
    ↓ TRENDING | RANGING | ATR volatility
EDGE GATE (research/edge_gate.py)
    ↓ blocks unproven engines
6 PARALLEL ENGINES (independent voters)
    ↓
CONFLUENCE (voting + weighted score + contradiction check)
    ↓
RISK GATE (sovereign — can veto any trade)
    ↓
DECISION → TELEGRAM + SQLite + JSONL
```

---

## Implementation Status

### Data Layer
| Component | Status | Notes |
|---|---|---|
| Synthetic OHLCV generator | ✅ | For testing / development |
| CSV loader | ✅ | Generic + MT4/MT5 + headerless + tab-separated |
| Twelve Data (live) | ✅ | Rate limiting, per-interval cache, multi-symbol |
| Yahoo Finance (historical) | ✅ | Up to 10yr daily data for research/backtesting |
| Alpha Vantage (FX backup) | ✅ | 25 req/day free |
| Multi-timeframe sync | ✅ | Native M15/H1 + resampled H4/D1 |

### Strategy Engines (6 active)
| Engine | Methodology | Status | Max Score | Notes |
|---|---|---|---|---|
| SMC | Smart Money Concepts — swing structure | ✅ Exempt | 65 | Majority vote over 6 swing pairs |
| Price Action | MA trend (sigmoid) + breakout detection | ✅ Exempt | 80 | Sigmoid formula, not linear |
| ICT | Killzones, Premium/Discount, Judas swing | 🔬 RESEARCH | 80 | H1 session + H4 dealing range |
| NNFX | EMA200 baseline + ADX strength filter | 🔬 RESEARCH | 80 | Needs 210+ bars for EMA200 |
| Quant | RSI(14) + ROC(10) momentum | 🔬 RESEARCH | 60 | Confirmation role only |
| Wyckoff | Spring/Upthrust, trading range, VSA* | 🔬 RESEARCH | 75 | *Volume only for metals/indices |
| Macro | DXY trend + Risk-On/Off (Yahoo Finance) | ⏳ Disabled | 70 | Enabled via config when needed |

### Confluence Layer
| Component | Status |
|---|---|
| Voting system (majority bias) | ✅ |
| Score calculator (weighted majority of agreeing engines) | ✅ |
| Contradiction engine (blocks active disagreement ≥40 score) | ✅ |
| Validate config (prevents unreachable thresholds) | ✅ |

### Risk Engine
| Check | Status |
|---|---|
| Minimum R:R (1:3 default) | ✅ |
| Max exposure per symbol | ✅ |
| Drawdown halt (reduce at 10%, stop at 15%) | ✅ |
| Position sizing (risk-based, asset-class aware) | ✅ |

### Infrastructure
| Component | Status |
|---|---|
| FastAPI server (8 endpoints) | ✅ |
| HTML Dashboard (auto-refresh 60s) | ✅ |
| Telegram notifications (HTML, flood protection) | ✅ |
| Scheduler (multi-symbol, overlap protection) | ✅ |
| Cloudflare Tunnel (HTTPS) | ✅ |
| SQLite analytics DB | ✅ |
| Engine performance tracker | ✅ |
| systemd services (auto-restart) | ✅ |

### Security (14/14 vulnerabilities fixed)
- Auth: `API_SERVER_KEY` required in production, `hmac.compare_digest`
- XSS: all dashboard values escaped via `html.escape()`
- Input validation: symbol regex `^[A-Z]{2,6}(/[A-Z]{2,6})?$`
- Error messages: generic to client, full detail logged internally
- File permissions: `chmod 0o600` on SQLite DBs
- Telegram: token never logged, 30min flood cooldown per symbol

---

## Research Hypotheses

| ID | Title | Status | Result |
|---|---|---|---|
| H001 | Liquidity sweep + HTF trend | **FAILED** | n=225, WR=49.78%, p=0.625 |
| H002 | Qualified sweep (ATR≥0.5 + TRENDING) | **PENDING** | n=27/76 (inconclusive → 2yr data needed) |
| H003 | ICT killzone + premium/discount | RESEARCH | Paper trading |
| H004 | NNFX EMA200 + ADX | RESEARCH | Paper trading |
| H005 | Quant RSI + ROC | RESEARCH | Paper trading |
| H006 | Wyckoff Spring/Upthrust | RESEARCH | Paper trading |
| H007 | Macro DXY + Risk-On/Off | RESEARCH | Paper trading |

**Rule:** No engine may be enabled (`config.yaml`) unless its hypothesis is `PASSED` or `RESEARCH`.
`RESEARCH` = paper trading approved. `PASSED` = proven edge, safe for live signals.

---

## Backtesting Results (Walk-Forward, No Lookahead)

Period: 2024-07-05 → 2026-06-24 (2 years H1 data via Yahoo Finance)

| Symbol | Trades | Win Rate | Profit Factor | Max DD | Return |
|---|---|---|---|---|---|
| EURUSD | 157 | 56.7% | 2.25 | 7.1% | 174% |
| GBPUSD | 175 | 65.7% | 3.16 | 6.0% | 296% |
| XAUUSD | — | — | — | — | re-running with fixed sizing |

> ⚠️ **Important caveats:** In-sample data only. No slippage. Commission = 0.5 pips.
> These numbers show the system CAN produce directional signal — they do NOT
> predict future performance. Out-of-sample validation is the next step.

---

## API Endpoints

All endpoints except `/health` require `X-API-Key: <API_SERVER_KEY>` header.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | System status + API credits |
| `/analyze/{symbol}` | POST | Run full pipeline on demand |
| `/decisions` | GET | Decision history with filtering |
| `/budget` | GET | Twelve Data daily credit usage |
| `/stats` | GET | SQLite analytics (regime performance) |
| `/engine-stats` | GET | Per-engine accuracy + weight suggestions |
| `/backtest-results` | GET | All saved backtest result files |
| `/dashboard` | GET | HTML dashboard (auto-refresh) |

---

## Project Structure

```
IATIS/
├── main.py                    # Pipeline entry point
├── scheduler.py               # Automated multi-symbol runner
├── config.yaml                # All tunables
├── .env                       # Secrets (not in git)
│
├── core/
│   ├── data_loader.py         # Synthetic + CSV + Twelve Data
│   ├── alt_data_loader.py     # Yahoo Finance + Alpha Vantage
│   ├── asset_profiles.py      # 20 symbols with pip values, session info
│   ├── twelve_data_client.py  # Rate limiter + cache
│   ├── timeframe_sync.py      # Resample + build MTF view
│   └── data_validator.py
│
├── engines/                   # 7 engines (6 active)
│   ├── smc_engine.py          # Swing structure majority vote
│   ├── price_action_engine.py # Sigmoid MA + breakout
│   ├── ict_engine.py          # Killzones, Premium/Discount
│   ├── nnfx_engine.py         # EMA200 + ADX
│   ├── quant_engine.py        # RSI + ROC
│   ├── wyckoff_engine.py      # Spring/Upthrust + VSA
│   └── macro_engine.py        # DXY + Risk-On/Off (disabled)
│
├── confluence/
│   ├── voting_system.py       # Majority bias vote
│   ├── score_calculator.py    # Weighted majority score
│   └── contradiction_engine.py
│
├── regimes/
│   ├── regime_detector.py     # TRENDING | RANGING
│   ├── volatility_classifier.py
│   └── session_context.py     # Asia | London | NY | Overlap
│
├── risk/
│   └── risk_engine.py         # Sovereign risk gate
│
├── research/
│   ├── edge_gate.py           # Blocks unproven engines
│   ├── hypotheses/            # H001-H007 documented
│   ├── experiments/           # H001.py, H002.py
│   └── results/               # registry.json (single source of truth)
│
├── backtesting/
│   ├── backtest_engine.py     # Walk-forward, asset-class-aware P&L
│   └── metrics.py
│
├── storage/
│   ├── decision_log.py        # JSONL append (streaming)
│   ├── decision_db.py         # SQLite analytics
│   └── engine_tracker.py      # Per-engine performance tracking
│
├── execution/
│   ├── api_server.py          # FastAPI — 8 endpoints
│   ├── telegram_bot.py        # HTML notifications
│   └── tradingview_webhook.py # Stub (Phase 4)
│
├── scripts/
│   ├── download_historical.py # Yahoo Finance bulk downloader
│   ├── run_backtest.py        # Single-symbol backtest runner
│   ├── run_all_backtests.py   # Multi-symbol batch backtest
│   └── setup_cloudflare_tunnel.sh
│
└── tests/                     # 156 tests, 0 failures
```

---

## Quick Start (VPS)

```bash
git clone https://github.com/ori00fit-tech/IATIS.git
cd IATIS
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Add your keys

# Test
python3 main.py  # Single run

# Deploy
sudo cp iatis-scheduler.service iatis-api.service /etc/systemd/system/
sudo systemctl enable --now iatis-scheduler iatis-api
```

---

## Quick Start (Development / Mac)

```bash
git clone https://github.com/ori00fit-tech/IATIS.git
cd IATIS && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && cp .env.example .env
# Edit .env with keys, then:
ENV=development uvicorn execution.api_server:app --reload
```

---

## Roadmap

### ✅ Phase 1 — Architecture (done)
Synthetic data, SMC + PA engines, risk gate, confluence, edge gate, No-Trade DB

### ✅ Phase 2 — Live Data (done)
Twelve Data integration, Telegram, FastAPI, Scheduler, VPS deployment, Cloudflare

### ✅ Phase 3 — Engine Expansion (done)
ICT, NNFX, Quant, Wyckoff, Macro engines; Session context; Asset profiles (20 symbols);
Backtesting engine; Yahoo Finance data; Security audit fixes

### 🔄 Phase 4 — Hypothesis Validation (in progress)
- H002: Qualified sweep — needs 2yr M15 data resampled (PENDING)
- H003-H007: Paper trading data collection (RESEARCH)
- Out-of-sample backtest validation on 2022-2024 data
- Dynamic weight adjustment based on engine tracker data

### ⏳ Phase 5 — Advanced Features
- Volume Profile engine (needs M1 tick data)
- VSA full implementation (needs reliable volume — metals/indices)
- TradingView webhook integration
- Bayesian adaptive weight optimization
- Multi-account portfolio correlation engine
- News/economic calendar filter (macro layer)

### ⏳ Phase 6 — Live Trading Integration
- Broker API connection (OANDA / Interactive Brokers)
- Real P&L tracking and attribution
- Automated paper trading with execution simulation
- Performance review dashboard with equity curve

---

## Design Principles

1. **Abstain > Guess.** NEUTRAL is better than a fabricated bias.
2. **Research before trust.** `edge_gate.py` enforces this in code.
3. **Risk is sovereign.** No score, however high, overrides the risk gate.
4. **Log everything.** Every NO_TRADE has a documented reason.
5. **Separate concerns.** Engines vote. Confluence weighs. Risk vetoes.
6. **No lookahead in backtesting.** Bar N only sees bars 0..N.
7. **Asset-aware math.** Forex pip formula ≠ Gold dollar formula.
