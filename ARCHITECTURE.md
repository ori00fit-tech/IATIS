# IATIS Architecture — Complete System Design

## System Overview

IATIS (Institutional Adaptive Trading Intelligence System) is a **Market
Intelligence Platform** that evaluates market conditions across a multi-gate
pipeline before executing trades. The architecture is layered and
deterministic, with each component having clear responsibilities. Two large
subsystems sit deliberately *outside* that deterministic core: an optional
AI explanation layer that narrates a decision after the fact, and a much
larger research/exploration layer (Mission Center, the Provider/Engine
Benchmark labs, ad-hoc engine v2 variants) that generates and stress-tests
*leads* for a human to turn into a real, pre-registered hypothesis. Neither
one can change `final_verdict`, and the second cannot write to any config
file or the hypothesis registry — both properties are enforced with tests,
not just convention.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    IATIS v0.5.9 — Decision Pipeline                  │
└─────────────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────────┐
        │  LIVE DATA (asset-class provider chains, native-  │
        │  timeframe aware, per-symbol failover)             │
        │  crypto: ccxt → alpaca → twelve_data → finnhub     │
        │  fx/metals/indices/energy: cTrader → twelve_data → │
        │    fcs_api → alpha_vantage → finnhub               │
        │  opt-in bridges (never in a default chain): MT5,   │
        │    Dukascopy JForex                                │
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
        │  • Feeds adaptive weights to engines               │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  STRATEGY ENGINES (config-gated, 4 of 10 enabled) │
        │  ✅ SMC | Price Action | NNFX | Wyckoff           │
        │  ⏸ ICT | Quant | Divergence | Market Structure    │
        │     | Sentiment | Macro (all implemented, disabled)│
        │  Ad-hoc-only v2 variants (Price Action, Wyckoff):  │
        │  reachable ONLY via Mission Center's engine_variant │
        │  override — never the live default                │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  CONFLUENCE ENGINE (Gate 2)                       │
        │  • Majority vote + weighted score                 │
        │  • Informative-weight-share gate (quorum ≠ signal) │
        │  • Contradiction check (standard + H013 reversal)  │
        │  • Multi-TF confirmation (D1/H1 alignment)         │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  CORRELATION FILTER (Gate 3)                      │
        │  • Max N EXECUTE per correlation group per run,    │
        │    seeded with THIS run's signals + already-open   │
        │    positions from prior runs                       │
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
        │  NEWS INTELLIGENCE (Gate 5)                       │
        │  • NFP, FOMC, CPI detection                        │
        │  • Blackout window before high-impact events       │
        └──────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────────────┐
        │  SYMBOL HEALTH INDEX (Gate 6)                     │
        │  • Win rate + profit factor over recent trades     │
        │  • Auto-pauses persistently underperforming symbols │
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
        │  • Trade execution (dry_run | cTrader | OANDA |    │
        │    Dukascopy JForex)                                │
        │  • Outcome logged only on a CONFIRMED fill/dry-run  │
        │    simulation — never on a declined attempt         │
        │  • Telegram alerts + Command Center dashboard      │
        │  • Cloudflare D1 + JSONL for audit trail            │
        └──────────────────────────────────────────────────┘
                              ↓
              (on demand, from the dashboard only)
        ┌──────────────────────────────────────────────────┐
        │  AI EXPLANATION LAYER (ai/ai_analyzer.py)         │
        │  • Explains a decision already made above          │
        │  • Never imported by main.py or scheduler.py       │
        │  • Opt-in: config.yaml ai.enabled                   │
        └──────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│         RESEARCH & EXPLORATION LAYER (operator-triggered only,       │
│         never on the scheduler's path, never a decision authority)   │
├─────────────────────────────────────────────────────────────────────┤
│  Mission Center: Optuna-sampled search over engine/timeframe/        │
│  indicator/context-filter/risk-param/engine-variant/confluence-      │
│  quorum combinations → per-trial evidence → SAME_SYMBOL/CROSS_SYMBOL │
│  validation (Monte Carlo, walk-forward, robustness, regime/          │
│  stability/cost-stress diagnostics) → meta-analysis (Bonferroni-     │
│  corrected consensus, feature mining) → AI-grounded draft hypothesis │
│  file (never a registry write)                                       │
│                                                                        │
│  Provider/News/Macro/Analytics Benchmark labs: same-window multi-    │
│  provider data-quality scoring → advisory scorecard/best-provider    │
│  query (never reorders a provider chain itself)                      │
│                                                                        │
│  Engine Benchmark: standalone per-engine ablation backtests           │
│                                                                        │
│  ── every path above is hard-blocked, by source-scan + live test, ── │
│  ── from writing config.yaml / config/*.yaml / registry.json    ──── │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Core Modules

### 1. **core/** — Data Infrastructure

| File | Purpose | Key Classes |
|------|---------|-------------|
| `data_providers.py` | Asset-class provider chains with native-timeframe-aware failover (`DEFAULT_CHAINS`, per-symbol override via `config.yaml`'s `provider_chains`) | `fetch_with_failover()`, `provider_chain_for()` |
| `data_loader.py` | CSV/synthetic data loading, timeframe building | `load_data()`, `load_synthetic()` |
| `data_manager.py` | Caching, retry logic | `DataManager` |
| `data_validator.py` | OHLCV validation (no nulls, monotonic, high/low bounds) + gap detection | `validate_ohlcv()`, `find_gaps()` |
| `data_confidence.py` | Cross-provider close-price divergence check — monitoring only, never a gate | `check_data_confidence()` |
| `market_quality.py` | Market Quality Score (0-100), thresholds from `config.yaml market_quality:` | `assess_market_quality()` |
| `timeframe_sync.py` | Multi-timeframe building and resampling; `SUPPORTED_TIMEFRAMES` is the single source of truth for what can be safely resampled | `build_multi_timeframe_view()` |
| `asset_profiles.py` | Per-asset settings (pip size, session hours, spreads) | `get_profile()` |
| `twelve_data_client.py` | Twelve Data API client with rate limiter + cache | `TwelveDataClient` |
| `ccxt_provider.py` | Crypto data via CCXT | `get_ccxt_data()` |
| `alt_data_loader.py` | Macro snapshot (DXY/VIX/yields/oil/copper/nat-gas/credit-spread/Fed-balance-sheet) via CBOE/FRED, plus Alpha Vantage economic-indicator series (CPI/GDP/unemployment/payrolls) used only by the Macro Benchmark lab | `load_macro_snapshot()`, `load_from_alpha_vantage_economic()` |

Yahoo Finance was removed entirely from every live chain (wrong instruments,
cash-session gaps, a mislabeled "H4"); `load_from_yfinance` survives only for
offline research downloads and failover unit tests. Nothing live calls it.

**Flow:**
```
fetch_with_failover(symbol, timeframe)
  ↓ resolve provider_chain_for(asset_class) — config override or DEFAULT_CHAINS
  ↓ try each provider in order, native-timeframe-aware
  ↓ (empty/error? next provider)
  ↓
validate_ohlcv(df)
build_multi_timeframe_view(df, ["H1","H4","D1"])
```

### 2. **engines/** — 10 Strategy Engines (4 live, 2 with an ad-hoc-only v2 variant)

> `config/engines.yaml`'s `enabled:` block has only `smc`, `price_action`,
> `nnfx`, and `wyckoff` set to `true`. The other six are implemented and
> edge-gated but disabled — enabling one requires its hypothesis in
> `research/results/registry.json` to reach at least `RESEARCH` status
> (see `research/edge_gate.py`).

Every engine is split into a pure `extract_features(...)` step (computes
every raw fact — indicator value, structural event, regime read — with zero
scoring logic) and a pure `decide(features, thresholds)` step (turns those
facts into `bias`/`score`/`reasons`), verified bit-identical to each
engine's pre-split behavior via golden-value regression tests. Every scoring
constant lives in `config/engines.yaml`'s `thresholds.<name>` block, not
hardcoded in the engine file. `EngineOutput` additionally carries
`features` (the raw extraction snapshot, JSON-serializable, feeding
Mission Center's feature-mining tool) and forward-looking, currently-unused-
by-any-live-gate fields (`probability`, `confidence_interval`,
`expected_return`, `evidence_level`) reserved for a future engine variant
whose scoring has actually been measured, not assumed.

| Engine | Weight | Enabled | Method |
|--------|--------|---------|--------|
| NNFX | 0.2273 | ✅ | EMA200 + ADX |
| SMC | 0.202 | ✅ | Swing-point structural bias + optional full-spec component modulation |
| Price Action | 0.1869 | ✅ | RSI/Bollinger + pattern + momentum + breakout scoring |
| Wyckoff | 0.0707 | ✅ | Spring/Upthrust + trading-range position |
| Quant | 0.0707 | ⏸ | Regime classification (Hurst/ADF/variance-ratio/efficiency-ratio vote) → mean-reversion z-score or trend-momentum read |
| Market Structure | 0.0859 | ⏸ | BOS/CHoCH/MSS swing classification |
| ICT | 0.0657 | ⏸ | Killzones + premium/discount zones + Judas swing |
| Divergence | 0.0606 | ⏸ | ZigZag-pivot-based Regular/Hidden/Triple/MTF-confirmed RSI-MACD divergence |
| Sentiment | 0.0303 | ⏸ | COT positioning proxy + retail-sentiment range-position read |
| Macro | 0.0 | ⏸ | DXY trend + risk-on/off vote (yield curve, credit spread, Fed balance sheet, SPY/VIX/Gold) |

`price_action_engine_v2.py` (pure price-action patterns — Inside/Outside
Bar, NR4/NR7, Fakey, Three-Bar-Play, VCP, failed breakout, opening drive,
closing strength; deliberately **no** RSI/Bollinger) and
`wyckoff_engine_v2.py` (v1's proven spring/upthrust logic plus a real Phase
A→E accumulation/distribution state machine and a Composite-Operator-
footprint heuristic) exist as **ad-hoc-only** alternatives, selectable only
through Mission Center's `engine_variants` override
(`backtesting/backtest_engine.py::ENGINE_VARIANT_KEYS`) and read from their
own `thresholds.price_action_v2`/`thresholds.wyckoff_v2` config blocks —
`config/engines.yaml`'s live `enabled:`/`weights:` state is byte-identical
before and after any Mission Center run that uses them, enforced by a
regression test, not just a design intent.

**All engines inherit from `BaseEngine`:**
```python
class BaseEngine:
    name: str
    thresholds: dict          # from config/engines.yaml thresholds.<name>, {} default
    def analyze(mtf_data: dict) -> EngineOutput
    def safe_analyze(mtf_data: dict) -> EngineOutput  # with exception handling
```

### 3. **confluence/** — Voting, Decision Logic, and Research-Only Filters

| File | Purpose |
|------|---------|
| `voting_system.py` | Tally votes: majority wins, breakdown recorded |
| `score_calculator.py` | Weighted average of AGREEING engines only |
| `contradiction_engine.py` | Detect conflicting signals (standard + H013 reversal veto) |
| `mtf_confirmation.py` | D1 trend must align with H1 signal |
| `regime_weights.py` | Adjust engine weights based on market regime |
| `reversal_veto.py` | H013: when 2+ reversal engines unanimously oppose trend |
| `meta_decision.py` | Confidence calibration + engine stability analysis |
| `indicator_filters.py` | Research-only: RSI/MACD/EMA/ADX/ATR as an entry filter, confirmation, or score-weight nudge on top of the engine vote — never an independent signal generator. Only reachable via Mission Center's `indicators` override |
| `context_filters.py` | Research-only: session/day-of-week/volatility-regime/market-regime/direction filters, same three modes as above. Only reachable via Mission Center's `context_filters` override |

**Confluence Flow (live pipeline):**
```
1. Each engine votes: BULLISH | BEARISH | NEUTRAL
2. Find majority bias
3. Calculate weighted score (majority engines only) + informative-weight-share check
4. Check contradictions (can block trade)
5. Check MTF confirmation (D1 aligns with H1?)
6. Check H013 reversal veto (reversal consensus vs trend)
7. Meta Decision: is confidence enough to execute?
```
`indicator_filters.py`/`context_filters.py` never run on this path — they
are read only by `backtesting/backtest_engine.py` when a research run's
`engine_config` override supplies them, and even then only ever *veto or
nudge score*, structurally incapable of setting `direction` (the same
local-veto-ANDed-into-`ok` / additive-score-adjustment pattern the live
`reversal_veto.py`/`mtf_confirmation.py` already use).

### 4. **risk/** — Risk Management (Sovereign Layer)

| File | Purpose |
|------|---------|
| `risk_engine.py` | Hard-gate checks: RR floor, exposure cap, drawdown-stop/-reduce thresholds. Any single failure blocks the trade. |
| `live_portfolio_state.py` | Derives **real** account balance, drawdown-from-peak, open risk, and correlated exposure from the outcomes history — feeds `risk_engine.py`'s `RiskInputs`. Fail-safe: a storage read error returns the configured starting balance with zero derived risk rather than crashing or silently zeroing balance. |
| `correlation_engine.py` | Pre-filter used by `scheduler.py`: max N EXECUTE signals from the same correlation group per run, seeded with this run's new signals *and* already-open positions carried over from prior runs (count-based, cheap early skip). |
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

All modules below are Cloudflare-D1-backed — there is no local SQLite
fallback for any of them.

| File | Purpose |
|------|---------|
| `decision_db.py` | All decisions, queryable for analytics |
| `decision_log.py` | JSONL: append-only decision audit trail (always local — not part of D1) |
| `engine_tracker.py` | Per-engine live vote performance |
| `outcome_tracker.py` | Trade results: entry/exit/SL/TP/P&L; logged only on a confirmed execution, never a declined attempt |
| `symbol_health.py` | Symbol Health Index (SHI) auto-pause logic |
| `calibration.py` | Confidence calibration + regime performance matrix |
| `experience_db.py` | Market Memory: similar historical setups, historical win rate |
| `journal.py` | Trade journal — per-trade R-multiple, engine attribution, notes, tags, formula-injection-safe CSV export |
| `execution_quality.py` | Real-fill slippage/TCA tracking |
| `shadow_book.py` | Counterfactual gate ledger — what a rejected signal would have done, broken down by primary gate, symbol, and regime |
| `audit_log.py` | Config/ops audit trail |
| `market_bars.py` | The data warehouse — pre-flight dataset readiness checks for Mission Center, native-timeframe-aware bar storage |
| `research_missions.py` / `research_mission_validations.py` | Mission Center's 2-table pattern: one run+per-trial table, one run+per-symbol-result table |
| `provider_benchmark.py` / `news_benchmark.py` / `macro_benchmark.py` / `analytics_benchmark.py` / `engine_benchmark.py` | Each benchmark lab's own run+result table pair |
| `migrations.py` | Additive-only schema migrations (14 versions) |
| `d1_client.py` | The only storage backend for every module above — see **Cloudflare D1 Backend** below |

Each module's `_conn()` routes through `d1_client.D1Connection`, whose
`.execute()/.fetchone()/.fetchall()` shape mirrors `sqlite3` closely enough
that none of their SQL query strings need to change if a table were ever
migrated back to local SQLite for testing (which is exactly what
`tests/conftest.py`'s `fake_d1` fixture does).

### 6. **execution/** — Delivery & Broker Integration

| File | Purpose |
|------|---------|
| `api_server.py` | FastAPI app assembly + router registration (~120 endpoints across 24 route modules) |
| `api_core.py` | Shared auth (`_check_auth`), session store, lifespan hook (applies pending D1 migrations at boot) |
| `routes/*.py` | 24 route modules: `health`, `analyze`, `auth`, `ctrader_auth`, `dashboard_legacy`, `experience`, `missions`, `diagnostics`, `provider_benchmark`, `news_benchmark`, `macro_benchmark`, `analytics_benchmark`, `provider_scorecard`, `engine_benchmark`, `research`, `experiments`, `data_providers`, `outcomes`, `journal`, `logs`, `files`, `forward_review`, `alerts`, `ai` |
| `telegram_bot.py` | Telegram alerts (EXECUTE signals only) |
| `trade_executor.py` | Execution bridge across three broker paths (`dry_run` / cTrader / OANDA / Dukascopy JForex) — `dry_run` defaults `true`; only a confirmed execution result is ever logged to the outcome tracker |
| `ctrader_client.py` | IC Markets cTrader integration: OAuth-token-aware connect (pulls a fresh token from `integrations/ctrader/token_manager.py` on every connect attempt), verified app/account auth, live symbol-spec fetch, bounded exponential-backoff auto-reconnect with a deterministic one-shot token-refresh-and-retry on `CH_ACCESS_TOKEN_INVALID`, `ProtoOAReconcileReq` position reconciliation on every (re)connect |
| `oanda_client.py` | OANDA REST API (fallback broker path) |
| `dukascopy_jforex_client.py` | Opt-in, fixed-quantity-only broker path over a local `dukas-api` bridge — no balance/open-position-list endpoint exists on the free bridge, so sizing is a fixed config value, not risk-percent-derived |
| `reconciliation.py` | Broker-vs-internal open-position diff, with an actual repair action (`POST /reconciliation/repair`), self-gated to live-order mode |
| `tradingview_webhook.py` | TradingView webhook stub |

### 7. **integrations/ctrader/** — cTrader OAuth 2.0

`oauth.py` (authorize-URL construction, authorization-code and
refresh-token exchange — plain `requests`, no cTrader SDK dependency),
`token_manager.py` (`get_valid_access_token()` — the one function every
cTrader-touching code path calls; proactively refreshes ahead of expiry,
persists to `.env` and updates `os.environ` in-process so no restart is
needed for an ongoing refresh), `env_store.py` (shared `.env` write
helper), `account.py` (account discovery over a short-lived app-auth TCP
session — built but not called from the OAuth callback path itself, to
avoid a second concurrent cTrader session while the scheduler's own is
live). The web flow (`execution/routes/ctrader_auth.py`) is: dashboard
"Connect cTrader" → `GET /ctrader/authorize` (redirect to cTrader's
consent screen with a CSRF `state`) → cTrader redirects back to
`GET /ctrader/callback` with a ~1-minute authorization code → the API
server exchanges it server-side and writes the resulting access/refresh
token to `.env`. The client secret and every token value never reach the
frontend.

### 8. **backtesting/** + **backtest/** — Simulation, Metrics, Reporting, and Research Orchestration

Two packages, composed rather than duplicated:

| Package | Role |
|---|---|
| `backtesting/backtest_engine.py` | The one simulation engine — the exact 11-stage live decision logic, gap-aware exits, slippage, and `build_engine_config_override()` — the single ephemeral-merge point every research override (timeframes, engines, indicators, context filters, risk params, confluence quorum, engine variants) passes through |
| `backtest/metrics.py` | The one metrics implementation — Sharpe, Sortino, Calmar, SQN, Recovery Factor, Ulcer Index, Kelly, VaR/CVaR, skew/kurtosis, drawdown analysis, per-direction/session/regime/engine breakdowns |
| `backtest/monte_carlo.py` | Monte Carlo robustness analysis (risk of ruin, return distribution) |
| `backtest/report.py` | HTML report + `chart_data.json` generation, KPI serialization |
| `backtest/runner.py` | Entry point composing the two via an explicit adapter — `python -m backtest.runner --symbols EURUSD GBPUSD --data-dir data` |
| `backtest/walk_forward.py` | Out-of-sample walk-forward validation on top of the same engine |
| `backtest/robustness.py` | Parameter-sweep robustness testing (STABLE/SENSITIVE/INSUFFICIENT per point, never an auto-selected winner) |
| `backtest/optimizer.py` | Mission Center's search-space definition (`MissionSearchSpace`) + Optuna sampler wiring (Grid/Random/TPE/NSGA-II) + `resolve_point()`/`evaluate_point()` |
| `backtest/mission_runner.py` | Resumable CLI orchestrator: per-symbol Optuna `Study`, crash-safe trial replay, duplicate-configuration detection, reproducibility fingerprinting |
| `backtest/mission_validator.py` | Multi-stage validation of one trial: SAME_SYMBOL/CROSS_SYMBOL modes, Monte Carlo, walk-forward, robustness-sweep-around-the-candidate, plus ESS significance/regime-robustness/stability-score/cost-stress diagnostics |
| `backtest/meta_analysis.py` | Mission-wide, pooled analysis: engine/timeframe frequency and lift, consensus bands, cross-trial Bonferroni-corrected consensus claims, pooled 3-way breakdown, ranked (never auto-selected) opportunity candidates |
| `backtest/multiple_testing.py` | Bonferroni correction, expected-false-positives, and a binomial sign test — the statistical honesty layer every mission report and consensus claim is built on |
| `backtest/feature_mining.py` | Non-ML, descriptive per-feature win-rate/mean-R association analysis over ~28-34 decision-time features the backtest engine already computes and used to discard |
| `backtest/price_benchmark.py` / `news_benchmark.py` / `macro_benchmark.py` / `analytics_benchmark.py` / `engine_benchmark.py` | The four data-quality benchmark labs plus the standalone-engine ablation benchmark |
| `backtest/provider_scorecard.py` | Pure aggregation over the four benchmark labs' latest results into a combined ranking + `best_provider(symbol/series, domain)` advisory query |

No hardcoded historical PF/WR figures are kept in this document — the
simulation engine has changed since any specific run, so a stale table
would misrepresent current behavior. Run the commands above for current
numbers; results are written under `reports/` alongside the exact engine
config used.

### 9. **research/** — Edge Gate, Hypotheses, Guards, and Diagnostics

| File / dir | Purpose |
|------|---------|
| `edge_gate.py` | Blocks any engine without a registered, at-least-`RESEARCH`-status hypothesis at boot time |
| `hypotheses/` | H001-H0xx: engine claims written before code, plus `drafts/` (AI-Copilot-suggested, unreviewed, never a registration) |
| `experiments/` | Validation scripts for the sweep/BOS-FVG hypotheses |
| `results/registry.json` | Single source of truth for hypothesis status — 35 entries tracked |
| `guards/causal_guard.py` / `static_scan.py` | No-lookahead enforcement, wired into `backtesting/backtest_engine.py`'s hot path |
| `diagnostics/direction_symmetry.py` | AST-based static scanner for one-sided BULLISH/BEARISH branches across `engines/`, `confluence/`, `risk/` — advisory-only, never blocks a build |
| `manifest.py` | Reproducibility manifest (git commit + dirty flag, config SHA256, per-dataset SHA256/bar-count/date-range) — feeds Mission Center's per-trial fingerprinting |

**Key Rule:** No engine enabled in `config/engines.yaml` without at least a
`RESEARCH` (paper-trading-only) entry in `registry.json` — `PASSED` is not
required to enable an engine, only to trust it.

### 10. **ai/** — Optional AI Explanation Layer

Not part of the decision pipeline. Verified: no import of `ai.ai_analyzer`
anywhere in `main.py`, `scheduler.py`, `confluence/`, or `risk/`. It only
runs when a human clicks a button in the Command Center dashboard, after
`final_verdict` is already set.

| File | Purpose |
|------|---------|
| `ai_analyzer.py` | Orchestrator: selects a provider from `config.yaml`'s `ai:` section, applies caching, always returns `status: ok\|disabled\|error` |
| `providers/base.py` | Common `AIProvider` interface + prompt-template loading + JSON extraction |
| `providers/gemini.py` / `openai.py` / `anthropic.py` | Provider implementations |
| `prompts/*.txt` | Externalized templates — explicitly forbid fabricated data and price prediction, enforce JSON-only output, and — for the hypothesis-suggestion prompt — forbid claiming validated status or re-proposing a dead-listed idea without an explicit distinctness argument |
| `cache.py` | TTL cache: news ~20min, macro ~60min; trade explanations keyed by decision id instead |
| `models.py` | Result dataclasses: `TradeExplanation`, `NewsAnalysis`, `MacroAnalysis`, `HypothesisSuggestion` |
| `dynamic_weights.py` | Separate, older feature — engine-weight suggestions (`POST /ai/optimize-weights`), advisory-only and `dry_run` by default |

`_build_copilot_context()` (`execution/routes/ai.py`) grounds
`/ai/suggest-hypothesis` in real data — the hypothesis registry, CLAUDE.md's
dead list, Mission Center's own recent mission findings and feature-mining
leads — all explicitly labeled as leads, never citable as validated
evidence, per the prompt's own rules. `POST /ai/save-hypothesis-draft`
writes only into `research/hypotheses/drafts/`, never `registry.json`.

API keys are read from the environment (`GEMINI_API_KEY` / `OPENAI_API_KEY`
/ `ANTHROPIC_API_KEY`) — never stored in `config.yaml`, matching every other
credential in this codebase.

### 11. **dashboard/frontend/** — Command Center SPA

React 19 + TypeScript + Vite, served at `GET /app` once built
(`npm install && npm run build`), talking to the same FastAPI backend as
everything else. 26 route-level-lazy-loaded modules under
`src/modules/`, organized by `src/lib/tabs.ts` into 7 sidebar sections
(Overview, Live Ops, Research & Backtests, Data & Providers, Engines & AI,
System & Audit, Meta). Below a 1024px viewport, `Sidebar` is replaced by a
fixed bottom-nav + "More" drawer shell; tables collapse into cards below
640px. State/data layer: Zustand (UI layout state only, never server data),
TanStack Query (per-module polling, deduped by query key), TanStack Table
(shared `DataTable.tsx`, sort opt-in per column), Radix UI primitives
(`Tooltip`/`Separator`/`ScrollArea`/`Collapsible`/`Dialog`), `cmdk`
(command palette), Framer Motion (shell/wizard polish), ECharts (lazy-
loaded, heatmap/histogram/line-chart panels only), `lightweight-charts`
(candlestick charts).

| Module (selected) | Shows |
|---|---|
| `mission-control/` | System health, symbol health, API budget, AI Briefing |
| `live-signals/`, `journal/`, `risk-center/`, `execution-quality/`, `reconciliation/` | Live-Ops surfaces over the same decision/outcome data the pipeline writes |
| `research-backtests/`, `backtesting-lab/`, `backtesting-charts/`, `experiment-runner/` | Single-hypothesis backtest/walk-forward/robustness workflow |
| `mission-center/` | Optuna search missions, validation, meta-analysis, feature mining, direction-symmetry scan, AI Copilot draft flow |
| `data-center/`, `provider-eval/`, `ctrader-connect/` | Data cache health, Provider/News/Macro/Analytics Benchmark labs + scorecard, cTrader OAuth connection status |
| `engine-monitor/`, `engine-benchmark/`, `ai-decision-center/`, `ai-settings/` | Per-engine votes/weights, standalone engine ablation, decision anatomy, AI provider settings |
| `system-audit/`, `live-logs/`, `file-explorer/`, `reports/`, `vps-operations/` | Philosophy/integrity audits, log tail, read-only file browser, generated reports, ops controls |

### 12. **cloudflare/** — D1 Storage Backend

D1 databases are only reachable from inside a Cloudflare Worker via a
binding — not directly from this VPS-hosted Python process. This folder
holds the mandatory bridge every storage module requires (`D1_WORKER_URL`/
`D1_PROXY_TOKEN` must be set — there is no local SQLite fallback in
production):

| File | Purpose |
|---|---|
| `worker.js` | Authenticated D1 proxy — `POST /d1/exec` (one parameterized statement) and `POST /d1/batch` (multiple statements, atomic via D1's own `env.DB.batch()`) |
| `schema.sql` | Convenience one-time schema for `wrangler d1 execute` |
| `wrangler.toml` | Worker config + D1 binding declaration |
| `README.md` | Full setup: `wrangler d1 create`, applying the schema, setting the shared-secret, deploying |

```
Python storage/*.py  --HTTPS (Bearer token)-->  cloudflare/worker.js  --D1 binding-->  D1
```

`storage/d1_client.py` is the Python side: `D1Connection`/`D1Cursor`/`D1Row`
mimic `sqlite3`'s connection/cursor/row interface closely enough that every
`storage/*.py` module reads the same SQL it would against local SQLite. The
one place cross-statement atomicity matters — e.g. a decision plus its
engine votes — uses `d1_batch()`; every other call site is a single
statement, already atomic on its own.

Not migrated: `storage/decision_log.py`'s JSONL audit trail stays
local-file-only — it's an append-only log, not a queryable store, and
moving it to D1 would gain nothing.

The test suite never touches a real Cloudflare account: `tests/conftest.py`'s
`fake_d1` autouse fixture fakes the Worker with a private in-memory
`sqlite3` connection per test — real SQL semantics stay under test, only the
HTTPS transport is faked.

---

## Configuration

`config.yaml` is a control plane, not a set of placeholders — every top-level
section maps to a real, already-wired conditional (each documented inline in
the YAML with the file:line it controls):

```yaml
data:
  source: twelve_data   # twelve_data | ctrader | injected (synthetic blocked under system.mode=live)
  symbol: EURUSD
  timeframes: [H1, H4, D1]
  bars_to_load: 500
  provider_chains: {...}   # per-asset-class override of core/data_providers.py's DEFAULT_CHAINS

engines:
  # (config/engines.yaml)
  enabled:
    smc: true
    price_action: true
    nnfx: true
    wyckoff: true
    ict: false          # RESEARCH — not yet enabled
    divergence: false   # rebuilt, still RESEARCH-status
  thresholds:            # every engine's scoring constants, incl. _v2 blocks for ad-hoc variants
    price_action: {rsi_bull: 55, ...}
    price_action_v2: {fakey_score: 30.0, ...}

confluence:
  min_engines_agreeing: 2
  min_score_to_trade: 58
  min_informative_weight_share: 0.6
  weights: {smc: 0.202, nnfx: 0.2273, price_action: 0.1869, "...": "..."}

risk:
  # (config/risk.yaml)
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
  # (config/ai.yaml)
  enabled: false            # opt-in — no external AI call unless turned on
  provider: gemini
  model: gemini-flash-latest

fundamentals:
  news_filter_enabled: true
  blackout_look_ahead_min: 60
```

Every Mission Center / benchmark-lab override is an ephemeral in-memory
merge over exactly this `load_config()` output — no file above is ever
written by a research run.

---

## Key Design Principles

### 1. **NO_TRADE is Valid Output**
The system correctly identifies when **not** to trade. This is modeled as a feature, not a bug.

### 2. **Research Before Production (Edge Gate)**
No engine logic runs in production until:
- A hypothesis is written in `research/hypotheses/`
- An experiment validates it against real, chronologically out-of-sample data
- At least a `RESEARCH` entry exists in `research/results/registry.json` (paper-trading-only; `PASSED` is the higher bar for trusting the result)

### 3. **No Lookahead Bias**
At bar N, the pipeline only sees bars 0..N. Entry is next-bar open, enforced structurally by `research/guards/causal_guard.py` inside the backtest engine's hot path — not just a coding convention.

### 4. **Asset-Aware Math**
JPY pip ≠ EUR pip. Each asset has a profile with pip size, session hours, spread proxy, and min pip move.

### 5. **Sovereign Risk Layer, Fed Real State**
Risk gate is separate from confluence voting, and its hard stops operate on `risk/live_portfolio_state.py`'s real drawdown/open-risk/correlated-exposure derivation — not hardcoded zeros.

### 6. **Multi-Provider, Asset-Class-Aware Failover**
Each asset class has its own provider chain (fx/metals/energy/indices → cTrader-first; crypto → ccxt-first), never one global chain — and never a chain that silently substitutes a wrong-instrument source (Yahoo was removed for exactly this reason).

### 7. **Exploration Is Structurally Incapable of Becoming a Decision**
Mission Center, the benchmark labs, and every ad-hoc engine variant reach the live decision path through zero code paths. Every override channel is an ephemeral, in-memory dict merge; a hard-block test suite (source-scan for any write call + a live before/after byte-identical-file check) pins this for every phase that has ever touched it.

### 8. **Transparent Reasoning**
Every decision includes which engines voted which way, why, which gates passed/failed, a reproducibility fingerprint when generated by the research layer, and — on demand — an AI-generated plain-English explanation that never overrides the decision itself.

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
- `iatis-watchdog.timer` → liveness watchdog
- `iatis-d1-backup.timer` / `iatis-backup.timer` → nightly backup
- `iatis-marketaux-collect.timer`, `iatis-orderflow-collector.service` → opt-in research collectors, installed but not auto-enabled

Units run sandboxed (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`,
`ProtectHome`, `MemoryMax`, `TasksMax`) even though they still run as
`User=root` — a TODO comment in each `.service` file documents the VPS-side
steps needed before switching to a dedicated service account, since flipping
that blind would break the live deploy.

**Scheduler Logic (`scheduler.py`):**
```
Per symbol, per run:
1. Correlation pre-filter → skip if a correlated symbol already EXECUTE this
   run, seeded also with already-open positions from prior runs
2. Symbol health check → skip if PAUSED
3. Run the full decision pipeline
4. If a real EXECUTE was confirmed by the broker (or a dry-run simulation
   ran): log the outcome, attempt trade execution (dry_run gated)
5. Auto-close open outcomes when SL/TP hit; reconcile against the broker
6. Send Telegram alert (EXECUTE only)
```
One symbol's exception doesn't kill the run for the rest — isolated
per-symbol try/except with a 30-minute error-alert cooldown per symbol.
Pending D1 schema migrations are applied at the top of both `--once` mode
and `run_loop()`.

---

## API Endpoints

`execution/api_server.py`, ~120 endpoints across 24 route modules on one
FastAPI app:

| Group | Endpoints | Auth |
|---|---|---|
| Core pipeline | `GET /health`, `GET /health/full`, `POST /analyze/{symbol}`, `GET /candles/{symbol}` | Public / ✅ / ✅ |
| Decisions & outcomes | `GET /decisions`, `GET /outcomes`, `POST /outcomes/{id}/close`, `GET /stats`, `GET /journal`, `POST /journal/{id}/annotate` | ✅ |
| Symbol/engine/data health | `GET /symbol-health`, `GET /engine-stats`, `GET /data-health`, `GET /data-confidence`, `GET /reconciliation`, `POST /reconciliation/repair` | ✅ |
| Research & backtests | `GET /research`, `GET /research/{id}`, `GET /research/compare`, `GET /research/edge-library`, `GET /backtest-results`, `GET /meta-analysis`, `GET /research/diagnostics/direction-symmetry` | ✅ |
| Mission Center | `POST/GET /research/missions`, `GET /research/missions/{id}`, `POST /research/missions/{id}/validate`, `GET .../meta-analysis`, `GET .../feature-mining` | ✅ |
| Provider/Engine benchmarks | `POST/GET /research/{provider,news,macro,analytics,engine}-benchmark`, `GET /research/provider-scorecard`, `GET /research/best-provider` | ✅ |
| Experience DB | `GET /experience/summary`, `GET /experience/query`, `GET /experience/pattern` | ✅ |
| AI explanation layer | `POST /ai/explain-trade`, `GET /ai/explain/{decision_id}`, `GET /ai/news-analysis`, `GET /ai/macro-analysis`, `GET /ai/daily-report`, `POST /ai/research-summary`, `POST /ai/suggest-hypothesis`, `POST /ai/save-hypothesis-draft`, `GET/POST /ai/settings`, `POST /ai/optimize-weights` | ✅ |
| cTrader OAuth | `GET /ctrader/status`, `GET /ctrader/authorize`, `GET /ctrader/callback` | ✅ / ✅ / cookie+state |
| Budget | `GET /budget` | ✅ |
| Auth & dashboard | `GET/POST /login`, `GET /logout`, `GET /dashboard` (legacy SSR), `GET /app` (Command Center SPA) | Public/Cookie |

---

## Security

- Session rotation: cookie holds `session_id`, never the raw API key
- `HttpOnly + Secure + SameSite=Lax` cookies (Lax, not Strict — Strict blocks the cross-origin redirect both Cloudflare's tunnel and cTrader's OAuth callback perform on login/authorize)
- `hmac.compare_digest` for key comparison
- Legacy dashboard session tokens expire via the same TTL-purge precedent as the main session store — no unbounded growth
- Long-running background job history (experiment/mission/benchmark executor) is bounded by a lazy prune-on-access eviction (24h retention, hard ceiling), not left to grow unbounded across server uptime
- Dashboard values escaped consistently client-side (data reaches the page via JSON fetch + DOM injection, not server-side string interpolation)
- Symbol validation regex: `^[A-Z]{2,6}(/[A-Z]{2,6})?$`
- cTrader's OAuth client secret is exchanged server-side only; the short-lived authorization code and every resulting token never reach the frontend
- Session store: `chmod 0o600` (storage is Cloudflare D1, no local DB files to protect)
- Telegram flood protection: 30min cooldown per error key
- Swagger/OpenAPI docs disabled unless `ENV=development`
- Dependency ceiling discipline: `ctrader-open-api` pinned below the next major after a yanked-release incident, `pip-audit` in CI
- systemd sandboxing directives (see Live Deployment above)
- **Known gap:** units still run as `User=root` pending the service-user migration.

---

## File Structure (Complete)

```
IATIS/
├── main.py                           # Decision pipeline entry point
├── scheduler.py                      # Automated multi-symbol runner
├── config.yaml                       # Control plane — see Configuration above
├── requirements.txt                  # Dependencies (pinned)
├── requirements-ctrader.txt          # Optional cTrader SDK deps (live VPS only)
├── README.md                         # Public documentation
│
├── core/                             # Data infrastructure
│   ├── data_providers.py
│   ├── data_loader.py
│   ├── data_manager.py
│   ├── data_validator.py
│   ├── data_confidence.py
│   ├── market_quality.py
│   ├── timeframe_sync.py
│   ├── asset_profiles.py
│   ├── twelve_data_client.py
│   ├── ccxt_provider.py
│   └── alt_data_loader.py
│
├── engines/                          # 10 strategy engines (4 enabled) + 2 v2 variants
│   ├── base_engine.py
│   ├── smc_engine.py
│   ├── price_action_engine.py / price_action_engine_v2.py
│   ├── nnfx_engine.py
│   ├── ict_engine.py
│   ├── quant_engine.py
│   ├── wyckoff_engine.py / wyckoff_engine_v2.py
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
│   ├── meta_decision.py
│   ├── indicator_filters.py          # research-only
│   └── context_filters.py            # research-only
│
├── risk/                             # Risk management (sovereign layer)
│   ├── risk_engine.py
│   ├── live_portfolio_state.py
│   ├── correlation_engine.py
│   └── portfolio_exposure.py
│
├── fundamentals/                     # News & calendar, MarketAux/Finnhub/TAAPI clients
├── regimes/                          # Market regime + session detection
│
├── storage/                          # Persistence & analytics (all D1-backed)
│   ├── decision_db.py / decision_log.py / engine_tracker.py
│   ├── outcome_tracker.py / symbol_health.py / calibration.py / experience_db.py
│   ├── journal.py / execution_quality.py / shadow_book.py / audit_log.py
│   ├── market_bars.py                # data warehouse / readiness checks
│   ├── research_missions.py / research_mission_validations.py
│   ├── provider_benchmark.py / news_benchmark.py / macro_benchmark.py
│   ├── analytics_benchmark.py / engine_benchmark.py
│   ├── migrations.py                 # 14 versions, additive-only
│   └── d1_client.py
│
├── cloudflare/                       # Mandatory D1 storage backend
│   ├── worker.js / schema.sql / wrangler.toml / migrations/ / README.md
│
├── execution/                        # Delivery & brokers
│   ├── api_server.py / api_core.py
│   ├── routes/                       # 24 FastAPI route modules
│   ├── telegram_bot.py / trade_executor.py
│   ├── ctrader_client.py / oanda_client.py / dukascopy_jforex_client.py
│   ├── reconciliation.py / tradingview_webhook.py
│
├── integrations/ctrader/             # cTrader OAuth 2.0
│   ├── oauth.py / token_manager.py / env_store.py / account.py
│
├── ai/                                # Optional AI explanation layer
│   ├── ai_analyzer.py
│   ├── providers/ (base, gemini, openai, anthropic)
│   ├── prompts/*.txt
│   ├── cache.py / models.py / dynamic_weights.py
│
├── backtesting/                      # The one simulation engine
│   └── backtest_engine.py
│
├── backtest/                         # Metrics/reports + Mission Center + benchmark labs
│   ├── metrics.py / monte_carlo.py / report.py / runner.py
│   ├── walk_forward.py / robustness.py
│   ├── optimizer.py / mission_runner.py / mission_validator.py
│   ├── meta_analysis.py / multiple_testing.py / feature_mining.py
│   ├── price_benchmark.py / news_benchmark.py / macro_benchmark.py
│   ├── analytics_benchmark.py / engine_benchmark.py / provider_scorecard.py
│
├── research/                         # Edge gate, hypotheses, guards, diagnostics
│   ├── edge_gate.py
│   ├── hypotheses/ (incl. drafts/)
│   ├── experiments/
│   ├── guards/ (causal_guard.py, static_scan.py)
│   ├── diagnostics/ (direction_symmetry.py)
│   ├── manifest.py
│   └── results/registry.json         # 35 hypotheses tracked
│
├── dashboard/frontend/               # Command Center SPA (React 19 + TS + Vite)
│   └── src/modules/                  # 26 modules, see Dashboard section above
│
├── utils/
│   ├── helpers.py / logger.py / feature_def.py / indicators.py
│
├── tests/                            # 169 files, ~3,088 test functions
│
├── scripts/                          # 74 scripts: data download (Dukascopy/cTrader/
│                                      #   Twelve Data/MT5-bridge), backtests, ops, backup
│
├── data/                             # Historical datasets
├── docs/                             # 13 audit/strategy/roadmap documents
├── reports/forensic/                 # Running forensic bug ledger + audit reports
│
├── iatis-*.service / *.timer         # systemd units (see Live Deployment)
└── .env                              # Secrets (never committed)
```

---

## Dependency Tree

```
main.py
├── core/ (data loading & validation)
├── engines/ (10 engines, only enabled ones instantiated — research/edge_gate.py gates this)
├── confluence/ (voting, scoring, contradiction/MTF/reversal-veto — NOT indicator_filters/context_filters, which are research-only)
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
├── risk/correlation_engine.py (pre-filter, seeded with open positions too)
├── storage/symbol_health.py (SHI check)
├── storage/outcome_tracker.py (auto-close; logged only on confirmed execution)
├── execution/trade_executor.py (dry_run-gated execution across 3 broker paths)
└── execution/telegram_bot.py (alerts)

execution/api_server.py (FastAPI)
├── main.py (on-demand /analyze)
├── storage/ (most endpoints)
├── backtesting/ + backtest/ (research + Mission Center + benchmark-lab endpoints)
├── execution/ctrader_client.py, oanda_client.py, dukascopy_jforex_client.py
├── integrations/ctrader/ (OAuth flow)
├── ai/ai_analyzer.py, ai/dynamic_weights.py (AI endpoints only — not the pipeline above)
└── research/diagnostics/ (direction-symmetry scan endpoint)

dashboard/frontend/ (React SPA)
└── talks to execution/api_server.py exclusively, no direct Python imports
```

Note what's deliberately **not** in `main.py`'s tree: `ai/`, `backtest/`'s
Mission Center and benchmark-lab modules, and `research/diagnostics/` are
all reachable only from `execution/api_server.py`'s own endpoints,
confirmed by grep — there is no path from the live decision pipeline into
any of them.

---

## Phase Roadmap

### ✅ Done
- Core decision pipeline: edge-gated engines, sovereign risk layer, correlation/news/symbol-health gates, real portfolio risk state
- Config control plane fully split (`config/{symbols,engines,risk,ai}.yaml`), every engine's scoring thresholds externalized
- Command Center dashboard: React 19 SPA, 26 tabs, mobile-first shell, route-level code splitting
- AI explanation layer + AI Copilot hypothesis-suggestion → draft-only file flow
- cTrader: OAuth 2.0 web authorization flow, automatic token refresh, deterministic reconnect self-heal, position reconciliation
- Dukascopy: free historical download + opt-in JForex bridge for near-live data and gated order execution
- Mission Center: Optuna-sampled search, multi-stage validation, meta-analysis, feature mining — all hard-blocked from writing config/registry
- Provider/News/Macro/Analytics Benchmark labs + combined scorecard
- Engine Benchmark (standalone ablation) + Trusted Data Center (pre-flight dataset readiness)
- Confluence Engine Overhaul: Feature-Extraction/Decision-Logic split across all engines, unified indicator library, Quant/Divergence/Macro rebuilt on real statistics, ad-hoc-only PriceAction v2/Wyckoff v2 variants
- Broker reconciliation with an actual repair action, not detection-only
- systemd sandboxing

### ⏳ Next
- Migrate `iatis-*.service` off `User=root` to a dedicated service account
- Complete the forward-demo sample (~100 closed cTrader-demo trades) and apply D001/D002
- H018 (structure-based stops) once the sample threshold is reached
- H021 (MarketAux sentiment) once `iatis-marketaux-collect.timer` has accumulated live history
- Off-site backups by default (documented rclone/R2 remote)
- Continue triaging `reports/forensic/`'s remaining findings one confirmed bug at a time
- Multi-user auth if this stops being single-operator

---

## Codebase

```
~110,700 Python lines (excluding dashboard/frontend)
169 test files | ~3,088 test functions
~120 API endpoints across 24 route modules
10 strategy engines (4 enabled) + 2 ad-hoc-only v2 variants | 35 research hypotheses tracked
26 dashboard tabs across 7 sections
14-version D1 schema migration chain
```

This system is a research and paper-trading platform first. Live order
placement exists but defaults to `dry_run: true` everywhere, and
`research/edge_gate.py` keeps unproven engines out of the vote regardless of
what any document claims. Everything in the research/exploration layer —
Mission Center, the benchmark labs, engine v2 variants — exists to generate
better-informed hypotheses faster; none of it is evidence until a human
writes one down and it clears the same OOS bar every prior hypothesis has
had to clear.
