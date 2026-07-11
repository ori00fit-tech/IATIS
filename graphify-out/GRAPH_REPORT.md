# Graph Report - .  (2026-07-11)

## Corpus Check
- 284 files · ~213,462 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2685 nodes · 5963 edges · 176 communities (160 shown, 16 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 281 edges (avg confidence: 0.62)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Confluence Decision Engine
- Confluence Score Calculator
- Price Action Engine
- Research Edge Gate
- Backtesting Engine
- News & Economic Calendar
- Calendar Build Scripts
- cTrader Broker Client
- Decision Database Storage
- Contradiction Detection Engine
- AI Prompt Templates
- API Server AI Endpoints
- Telegram Notification Bot
- cTrader Client Core
- Frontend React Dashboard
- Task Scheduler
- Multi-Timeframe Confirmation
- Cloudflare D1 Storage
- REST API Server
- Trade Experience Database
- System Entry & Diagnostics
- Dashboard React Components
- Alternative Data Loader
- Market Data Providers
- OANDA Broker Client
- Tests Module
- Tests Module
- Storage Module
- Dashboard Module
- Scripts Module
- Tests Module
- Ai Module
- Tests Module
- Dashboard Module
- Tests Module
- Backtest Module
- Backtest Module
- Tests Module
- Dashboard Module
- Dashboard Module
- Dashboard Module
- Scripts Module
- Tests Module
- Tests Module
- Ai Module
- Regimes Module
- Scripts Module
- Tests Module
- Backtest Module
- Tests Module
- Dashboard Module
- Tests Module
- Tests Module
- Scripts Module
- Ai Module
- Core Module
- Tests Module
- Storage Module
- Research Module
- Tests Module
- Core Module
- Tests Module
- Scripts Module
- Tests Module
- Core Module
- Dashboard Module
- Research Module
- Research Module
- Core Module
- Tests Module
- Core Module
- Research Module
- Scripts Module
- Scripts Module
- Storage Module
- Tests Module
- Research Module
- Storage Module
- Tests Module
- Ai Module
- Core Module
- Regimes Module
- Research Module
- Tests Module
- Storage Module
- Execution Module
- Core Module
- Execution Module
- Research Module
- Cloudflare Module
- Engines Module
- Engines Module
- Execution Module
- Tests Module
- Scripts Module
- Storage Module
- Run Module
- Core Module
- Engines Module
- Scripts Module
- Scripts Module
- Scripts Module
- Tests Module
- Tests Module
- Dashboard Module
- Scripts Module
- Scripts Module
- Scripts Module
- Scripts Module
- Tests Module
- Engines Module
- Scripts Module
- Ai Module
- Tests Module
- Backtesting Module
- Core Module
- Dashboard Module
- Engines Module
- Engines Module
- Engines Module
- Execution Module
- Scripts Module
- Scripts Module
- Scripts Module
- Tests Module
- Utils Module
- Ai Module
- Tests Module
- Cloudflare Module
- Engines Module
- Engines Module
- Risk Module
- Scripts Module
- Scripts Module
- Scripts Module
- Scripts Module
- Storage Module
- Core Module
- Scripts Module
- Scripts Module
- Scripts Module
- Storage Module
- Dashboard Module
- Main Module
-  Module
- Backtest Module
- Dashboard Module
- Execution Module
- Execution Module
- Execution Module
- Execution Module
- Main Module
- Scripts Module
- Scripts Module
- Storage Module
- Dashboard Module
- Dashboard Module

## God Nodes (most connected - your core abstractions)
1. `EngineOutput` - 84 edges
2. `load_config()` - 70 edges
3. `CTraderClient` - 68 edges
4. `Bias` - 64 edges
5. `get_logger()` - 64 edges
6. `run_backtest()` - 54 edges
7. `load_from_csv()` - 46 edges
8. `BacktestConfig` - 42 edges
9. `run_pipeline()` - 39 edges
10. `AIAnalyzer` - 38 edges

## Surprising Connections (you probably didn't know these)
- `Measured Carrier Asset Edge: Trend-capture on XAUUSD/BTCUSD/ETHUSD at H4 with D1 confirmation, RR>=2` --semantically_similar_to--> `Carrier Edge Statistical Finding: z=8.6, p<1e-15, n=912, Expectancy +0.44R/trade (in-sample over 6.4 years)`  [INFERRED] [semantically similar]
  CLAUDE.md → docs/PHILOSOPHY_AUDIT_2026-07.md
- `IATIS-Minimal Architecture: NNFX trend + D1 confirm + ATR/RR risk on XAU/BTC/ETH only (estimated 70-80% complexity reduction at zero measured edge cost)` --semantically_similar_to--> `Measured Carrier Asset Edge: Trend-capture on XAUUSD/BTCUSD/ETHUSD at H4 with D1 confirmation, RR>=2`  [INFERRED] [semantically similar]
  docs/PHILOSOPHY_AUDIT_2026-07.md → CLAUDE.md
- `Dead List: Measured and Buried Ideas (Sweeps H001/H002, BOS+FVG H008, SMC-full H017, more engines H015, pairs trading, managed exits, currency strength, ICT folklore)` --semantically_similar_to--> `Engine Dilution Finding: Baseline-4 PF 1.27 → All-9 PF 1.11; every engine addition lowered PF`  [INFERRED] [semantically similar]
  CLAUDE.md → docs/PHILOSOPHY_AUDIT_2026-07.md
- `AnalyzeRequest` --uses--> `AIAnalyzer`  [INFERRED]
  execution/api_server.py → ai/ai_analyzer.py
- `test_extract_json_raises_on_garbage()` --indirect_call--> `AIProviderError`  [INFERRED]
  tests/test_ai_analyzer.py → ai/providers/base.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Failed SMC/Liquidity Sweep Hypothesis Chain (H001 → H002 → H008, all FAILED)** — research_hypotheses_h001_liquidity_sweep_htf, research_hypotheses_h002_qualified_sweep, research_hypotheses_h008_bos_fvg [EXTRACTED 1.00]
- **July 2026 Comprehensive System Audit (Philosophy + Production + Strategy Evidence)** — docs_philosophy_audit_2026_07, docs_production_audit_2026_07, docs_strategy_evidence_2026_07 [INFERRED 0.95]
- **AI Explanation Layer Prompt Suite (explanation-only, no fabrication, JSON-enforced)** — ai_prompts_explain_trade, ai_prompts_macro_analysis, ai_prompts_news_analysis, ai_prompts_summarize [EXTRACTED 1.00]

## Communities (176 total, 16 thin omitted)

### Community 0 - "Confluence Decision Engine"
Cohesion: 0.08
Nodes (40): MetaDecision, confluence/meta_decision.py ----------------------------- Meta Decision Layer —, Complete meta-analysis of a trading decision., confluence/regime_weights.py ------------------------------ Regime-aware weight, check_reversal_veto(), confluence/reversal_veto.py ------------------------------ H013: Reversal Engine, Result of the reversal veto check., Check if reversal engines unanimously oppose the trade direction.      This impl (+32 more)

### Community 1 - "Confluence Score Calculator"
Cohesion: 0.08
Nodes (53): calculate_score(), _engine_key(), confluence/score_calculator.py ---------------------------------- Weighted confl, Convert engine_name to config weight key.     Handles: direct map, CamelCase→sna, Score = weighted average of the WINNING side's effective votes.      Step 1: the, ScoreResult, effective_bias(), informative_weight_share() (+45 more)

### Community 2 - "Price Action Engine"
Cohesion: 0.07
Nodes (55): _bollinger(), _candle_pattern(), detect_breakout(), DataFrame, Series, Legacy function kept for test compatibility., Detect key candlestick reversal/continuation patterns., _rsi() (+47 more)

### Community 3 - "Research Edge Gate"
Cohesion: 0.05
Nodes (37): audit_passed_hypotheses(), check_edge_gate(), EdgeNotProvenError, _load_registry(), Exception, research/edge_gate.py ------------------------- Enforces the research-layer rule, Raise EdgeNotProvenError if config tries to enable a non-exempt     engine that, One warning per PASSED hypothesis whose `evidence` block fails the     codified (+29 more)

### Community 4 - "Backtesting Engine"
Cohesion: 0.10
Nodes (48): BacktestConfig, BacktestResult, DataFrame, Path, backtesting/backtest_engine.py ---------------------------------- Real walk-forw, Walk-forward backtest on historical OHLCV data — no lookahead., Create config from asset profile automatically.          Commission defaults to, run_backtest() (+40 more)

### Community 5 - "News & Economic Calendar"
Cohesion: 0.07
Nodes (50): _forex_factory_fallback(), _get_api_key(), get_calendar_today(), get_calendar_week(), get_upcoming_events(), _jblanked_request(), _minimal_us_schedule(), _parse_event_time() (+42 more)

### Community 6 - "Calendar Build Scripts"
Cohesion: 0.07
Nodes (49): date, build_and_save(), build_month(), _first_friday(), main(), _nth_weekday(), scripts/build_news_calendar.py -------------------------------- Build a reliable, Get the nth occurrence of a weekday in a month. (+41 more)

### Community 7 - "cTrader Broker Client"
Cohesion: 0.09
Nodes (22): CTraderClient, OpenPosition, Any, Store the latest bid/ask (scaled) for a symbol from a spot stream., Decode ProtoOAGetTrendbarsRes → OHLCV dicts.          cTrader trendbars are delt, Turn a ProtoOAExecutionEvent into a CTraderResult.          Ids and prices live, Cleanly stop the client service (the shared reactor keeps running).          Mar, IC Markets cTrader Open API client.      Thread model: a single Twisted reactor (+14 more)

### Community 8 - "Decision Database Storage"
Cohesion: 0.09
Nodes (33): _conn(), execute_alert_exists_for_bar(), init_db(), log_decision_db(), Any, storage/decision_db.py ----------------------- Cloudflare D1-backed decision sto, Insert one pipeline report into the DB. Never raises — failures are     logged s, Quick aggregate stats from the DB. (+25 more)

### Community 9 - "Contradiction Detection Engine"
Cohesion: 0.07
Nodes (38): check_contradictions(), ContradictionResult, confluence/contradiction_engine.py -------------------------------------- Blocks, Block trade if meaningful engines actively disagree.      Standard threshold = 4, ConfluenceConfigError, Exception, validate_confluence_config(), DataValidationError (+30 more)

### Community 10 - "AI Prompt Templates"
Cohesion: 0.08
Nodes (39): AI Prompt: Explain Trade Decision (JSON-only, no fabrication, no prediction), AI Prompt: Macro Analysis (cross-asset backdrop, dashboard-only), AI Prompt: News Analysis (event impact, does not gate trading), AI Prompt: Summarize Text (2-3 sentences, plain text only), IATIS System Architecture, AI Explanation Layer Structurally Isolated from Decision Pipeline (never imported by main.py/scheduler.py), EngineOutput Data Structure (bias: BULLISH|BEARISH|NEUTRAL, score 0-100, reasons, raw), Multi-Provider Data Failover: Twelve Data → Yahoo Finance → Alpha Vantage → Finnhub (+31 more)

### Community 11 - "API Server AI Endpoints"
Cohesion: 0.09
Nodes (39): ai_explain_trade(), ai_explain_trade_inline(), ai_macro_analysis(), ai_news_analysis(), ai_research_summary(), backtest_results(), budget(), _check_auth() (+31 more)

### Community 12 - "Telegram Notification Bot"
Cohesion: 0.10
Nodes (37): _assess_risk_level(), _bias_icon(), _build_message(), _escape(), _get_credentials(), _get_direction(), _post(), execution/telegram_bot.py ----------------------------- Phase 2: Telegram notifi (+29 more)

### Community 13 - "cTrader Client Core"
Cohesion: 0.08
Nodes (26): ConnectionState, Enum, execution/ctrader_client.py ---------------------------- IC Markets / cTrader Op, Trading spec fetched via ProtoOASymbolByIdRes (all volumes in API units)., # NOTE: ctrader-open-api's Client exposes only these three setters., _SymbolDetails, client(), _details() (+18 more)

### Community 14 - "Frontend React Dashboard"
Cohesion: 0.06
Nodes (35): dependencies, react, react-dom, tailwindcss, @tailwindcss/vite, devDependencies, oxlint, @types/node (+27 more)

### Community 15 - "Task Scheduler"
Cohesion: 0.09
Nodes (34): _credits_warning(), _get_symbols(), _handle_signal(), main(), _now_utc(), scheduler.py -------------- Runs the IATIS pipeline on a schedule without any ex, # NOTE: do not re-import auto_close_outcomes inside this function —, Get enabled symbols from config.yaml's twelve_data_symbols list. (+26 more)

### Community 16 - "Multi-Timeframe Confirmation"
Cohesion: 0.11
Nodes (29): check_mtf_confirmation(), MTFResult, DataFrame, confluence/mtf_confirmation.py -------------------------------- A2: Multi-Timefr, Compare the signal direction against the D1 trend.      Args:         h1_bias: ", check_correlation(), CorrelationCheckResult, portfolio_exposure_summary() (+21 more)

### Community 17 - "Cloudflare D1 Storage"
Cohesion: 0.11
Nodes (31): D1Connection, D1Error, Exception, Drop-in replacement for a sqlite3.Connection in the narrow way     storage/*.py, No-op — see module docstring on cross-statement atomicity., Per-thread persistent Session (requests.Session is not documented     as thread-, Raised for a failed D1 proxy call., _session() (+23 more)

### Community 18 - "REST API Server"
Cohesion: 0.08
Nodes (30): BaseModel, analyze(), AnalyzeRequest, dashboard(), do_login(), experience_pattern_endpoint(), experience_query_endpoint(), experience_summary_endpoint() (+22 more)

### Community 19 - "Trade Experience Database"
Cohesion: 0.14
Nodes (30): _conn(), _detect_session(), experience_summary(), find_similar(), _init_db(), pattern_analysis(), Any, query_experiences() (+22 more)

### Community 20 - "System Entry & Diagnostics"
Cohesion: 0.08
Nodes (22): main(), main(), Minimal backtest without full pipeline — isolates engine config effects., run_simple_backtest(), config(), tests/test_logger_config.py ----------------------------- config.yaml's logging, test_config_yaml_has_logging_block_and_no_dead_keys(), test_read_logging_config_uses_config_yaml() (+14 more)

### Community 21 - "Dashboard React Components"
Cohesion: 0.14
Nodes (25): DotState, stateClass, StatusRow(), apiGet(), AiDailyReport, AiMacroAnalysis, AiNewsAnalysis, Budget (+17 more)

### Community 22 - "Alternative Data Loader"
Cohesion: 0.10
Nodes (22): _close_only_frame(), load_from_alpha_vantage(), load_from_fred(), load_from_yfinance(), load_historical_for_research(), load_macro_snapshot(), load_vix_from_cboe(), DataFrame (+14 more)

### Community 23 - "Market Data Providers"
Cohesion: 0.14
Nodes (28): DataFetchError, _fetch_alpha_vantage(), _fetch_ccxt_provider(), _fetch_ctrader(), _fetch_finnhub(), fetch_multi_timeframe_with_failover(), _fetch_twelve_data(), fetch_with_failover() (+20 more)

### Community 24 - "OANDA Broker Client"
Cohesion: 0.10
Nodes (15): AccountSummary, OandaClient, OpenTrade, execution/oanda_client.py -------------------------- OANDA REST API client for p, OANDA REST API v20 client.      Supports both practice (demo) and live environme, Fetch account balance, NAV, margin info., Get all currently open trades., Check if we already have an open position for this symbol. (+7 more)

### Community 25 - "Tests Module"
Cohesion: 0.17
Nodes (24): _confidence_score(), _data_quality_score(), _engine_contribution(), evaluate_meta_decision(), Any, Measure signal stability: how consistently engines agree.          High stabilit, Estimate data reliability based on available context.          Factors:     - MQ, Composite confidence score combining all signals.          Formula:       30% fr (+16 more)

### Community 26 - "Tests Module"
Cohesion: 0.14
Nodes (19): Executes IATIS signals via broker API (OANDA or cTrader/IC Markets).      Args:, TradeExecutor, _make_report(), tests/test_oanda_execution.py — OANDA client + TradeExecutor tests (no API calls, Crypto should execute in dry_run even though OANDA doesn't support it., A real order must NEVER hit a non-demo cTrader account unless     allow_live_tra, On a demo account, a real order IS placed (this is layer-2 evidence)., With allow_live_trading=True, a live account is permitted (the     explicit real (+11 more)

### Community 27 - "Storage Module"
Cohesion: 0.14
Nodes (22): _conn(), _init_db(), Yields a D1 connection. See storage/d1_client.py., _conn(), get_all_symbol_health(), get_symbol_health(), storage/symbol_health.py -------------------------- Symbol Health Index (SHI): 0, Get health for all symbols, sorted by score. (+14 more)

### Community 28 - "Dashboard Module"
Cohesion: 0.14
Nodes (21): Badge(), BadgeTone, toneClass, AiResearchSummary, BacktestResult, BacktestResultsResponse, EvidenceManifest, getAiResearchSummary() (+13 more)

### Community 29 - "Scripts Module"
Cohesion: 0.19
Nodes (22): run_pipeline(), analyze_attribution(), main(), Analyze engine attribution across all trades., Run backtest with full engine attribution tracking., run_attribution(), _collect_engine_votes(), main() (+14 more)

### Community 30 - "Tests Module"
Cohesion: 0.18
Nodes (21): AIAnalyzer, Map an IATIS decision report onto the explain_trade prompt's         placeholder, Thin orchestrator: config -> provider -> cache -> typed result., PerplexityProvider, _config(), _mock_response(), tests/test_ai_analyzer.py ---------------------------- Tests for ai/ — providers, Explicit regression guard for the design constraint: AIAnalyzer must     not be (+13 more)

### Community 31 - "Ai Module"
Cohesion: 0.13
Nodes (16): AIProvider, extract_json(), load_prompt(), ABC, Any, ai/providers/base.py ---------------------- Common interface every AI provider (, Load ai/prompts/{name}.txt and fill in {placeholders}.      Raises AIProviderErr, Parse a JSON object out of a model response.      Models occasionally wrap JSON (+8 more)

### Community 32 - "Tests Module"
Cohesion: 0.15
Nodes (23): Convert internal symbol format to Twelve Data format.      EURUSD  -> EUR/USD, _to_td_symbol(), _parse_response(), Convert a Twelve Data time_series JSON response to an OHLCV DataFrame.      Twel, API returned an error response., TwelveDataError, _mock_response(), tests/test_twelve_data.py ---------------------------- Tests for core.twelve_dat (+15 more)

### Community 33 - "Dashboard Module"
Cohesion: 0.08
Nodes (23): compilerOptions, allowArbitraryExtensions, allowImportingTsExtensions, erasableSyntaxOnly, jsx, lib, module, moduleDetection (+15 more)

### Community 34 - "Tests Module"
Cohesion: 0.14
Nodes (23): build_active_engines(), decision_timeframe(), The timeframe engine votes are computed on: data.timeframes[0]     (D1 in the D1, _bars(), _execute_report(), tests/test_decision_timeframe.py ----------------------------------- D1-primary, H4-primary (the production setup): engines vote on H4, and the     MTF confirmat, The scheduler's auto-close prices open outcomes from EVERY report's     current_ (+15 more)

### Community 35 - "Backtest Module"
Cohesion: 0.15
Nodes (18): BacktestMetrics, calculate_metrics(), Any, backtest/metrics.py -------------------- Professional trading metrics calculatio, Calculate comprehensive backtest metrics from trade list.      Args:         tra, Single trade record with full context., Complete backtest metrics report., TradeRecord (+10 more)

### Community 36 - "Backtest Module"
Cohesion: 0.14
Nodes (19): main(), ParameterSelector, DataFrame, Enum, Path, str, backtest/walk_forward.py ------------------------ Walk-forward (multi-window out, Run walk-forward validation for one symbol.      Args:         symbol: internal (+11 more)

### Community 37 - "Tests Module"
Cohesion: 0.13
Nodes (22): load_synthetic(), Generate a synthetic but structurally plausible OHLCV series.      Not a price p, _premium_discount_zone(), Position of price within the dealing range.      Returns (zone, equilibrium_pct), synthetic_df(), tests/test_phase3_engines.py ------------------------------- Behavior tests for, Construct data where close ends well above EMA200., Construct strongly rising data to trigger RSI overbought. (+14 more)

### Community 38 - "Dashboard Module"
Cohesion: 0.17
Nodes (18): AiStatusFrame(), Column, DataTable(), Empty(), Panel(), DecisionEntry, DecisionsResponse, explainTrade() (+10 more)

### Community 39 - "Dashboard Module"
Cohesion: 0.13
Nodes (18): colorClass, KpiCard(), KpiColor, CacheStatus, DataHealthResponse, getDataHealth(), SymbolDataHealth, TimeframeStatus (+10 more)

### Community 40 - "Dashboard Module"
Cohesion: 0.15
Nodes (16): ApiError, apiPost(), request(), AuthContext, AuthContextValue, AuthProvider(), AuthStatus, PollingState (+8 more)

### Community 41 - "Scripts Module"
Cohesion: 0.22
Nodes (22): build_base_tf(), build_derived(), build_symbol(), coverage(), fill_gaps_free(), from_binance(), from_stooq(), from_twelvedata_batches() (+14 more)

### Community 42 - "Tests Module"
Cohesion: 0.19
Nodes (21): _active_sessions(), assess_market_quality(), Calculate Market Quality Score for current market conditions.      Args:, Score based on active trading sessions., _session_score(), _make_df(), tests/test_market_quality.py — MQS tests., Synthetic OHLCV DataFrame. (+13 more)

### Community 43 - "Tests Module"
Cohesion: 0.16
Nodes (20): compute_portfolio_state(), _correlated_symbols(), PortfolioState, Path, Snapshot of live portfolio risk state, in the exact units     expected by ``risk, All symbols sharing at least one correlation group with ``symbol``     (excludin, Derive live portfolio state from the outcomes database.      Args:         symbo, _closed() (+12 more)

### Community 44 - "Ai Module"
Cohesion: 0.18
Nodes (16): analyze_and_suggest_weights(), _build_analysis_prompt(), Any, ai/dynamic_weights.py ----------------------- Dynamic Weights AI — uses Claude A, Use Claude API to analyze engine performance and suggest optimized weights., Build prompt for Claude to analyze and suggest weights., AnthropicProvider, ai/providers/anthropic.py ---------------------------- Anthropic Messages API: h (+8 more)

### Community 45 - "Regimes Module"
Cohesion: 0.13
Nodes (19): _dealing_range(), _detect_judas_swing(), DataFrame, Current dealing range: high and low of the last `lookback` bars., Detect a Judas swing: false breakout at session open.      A Judas swing occurs, detect_session(), detect_session_from_df(), _hour_in_session() (+11 more)

### Community 46 - "Scripts Module"
Cohesion: 0.15
Nodes (20): download_symbol_tf(), fetch_av(), fetch_finnhub(), fetch_td(), fetch_yf(), get_existing(), _load_env(), load_progress() (+12 more)

### Community 47 - "Tests Module"
Cohesion: 0.27
Nodes (20): auto_close_outcomes(), get_open_signals(), log_signal(), Get all signals still awaiting outcome., Check open signals against current prices and auto-close if TP/SL hit.      Call, Log an EXECUTE signal for outcome tracking.      Called automatically when IATIS, _age_signal(), tests/test_outcome_hygiene.py ------------------------------ Open-outcome hygien (+12 more)

### Community 48 - "Backtest Module"
Cohesion: 0.18
Nodes (19): find_symbol_csv(), load_symbol_data(), main(), DataFrame, Path, backtest/runner.py ------------------ Orchestrates a full local backtest run: da, Locate the H1 CSV for ``symbol`` under ``data_dir``.      Matches the ``{SYMBOL}, Load and validate one symbol's OHLCV history.      Returns a UTC-indexed, schema (+11 more)

### Community 49 - "Tests Module"
Cohesion: 0.18
Nodes (19): load_from_csv(), Load real historical OHLCV data from a CSV file.      Designed to be tolerant of, Run a battery of structural checks. Raises DataValidationError on failure., validate_ohlcv(), tests/test_csv_loader.py ---------------------------- Tests for core.data_loader, End-to-end sanity check: real CSV data must satisfy the same     OHLCV contract, test_drops_unparseable_rows_with_warning(), test_explicit_column_map_overrides_autodetect() (+11 more)

### Community 50 - "Dashboard Module"
Cohesion: 0.10
Nodes (19): compilerOptions, allowImportingTsExtensions, erasableSyntaxOnly, lib, module, moduleDetection, noEmit, noFallthroughCasesInSwitch (+11 more)

### Community 51 - "Tests Module"
Cohesion: 0.14
Nodes (13): _make_client(), _make_execute_report(), tests/test_ctrader_client.py — cTrader client unit tests (no API connection)., Make client skipping validation (for unit tests)., test_client_raises_without_account(), test_client_raises_without_credentials(), test_executor_ctrader_blocks_news(), test_executor_ctrader_dry_run() (+5 more)

### Community 52 - "Tests Module"
Cohesion: 0.18
Nodes (18): Timestamp, Split ``df`` into N disjoint test windows, each prepended with its     embargo/w, split_windows(), _ohlcv(), DataFrame, Tests for backtest/runner.py and backtest/walk_forward.py.  The methodological c, Full integration: real engine, real metrics, 3 windows., Backtests must cost trades at the measured broker spread by     default, not the (+10 more)

### Community 53 - "Scripts Module"
Cohesion: 0.15
Nodes (8): CTraderDiagnostic, main(), scripts/test_ctrader_messages.py --------------------------------- Diagnostic sc, DIAGNOSTIC MESSAGE HANDLER.         Print ALL message details to understand stru, Connect and run diagnostic., Minimal cTrader client for message inspection., TCP connected - start auth., Send symbols list request.

### Community 54 - "Ai Module"
Cohesion: 0.13
Nodes (9): ai/ai_analyzer.py ------------------ AI Orchestrator — the only file the rest of, Explain an already-decided IATIS report dict (main.py's return         value, or, MacroAnalysis, NewsAnalysis, ai/models.py ------------- Typed result shapes returned by AIAnalyzer, independe, AI read on macro/cross-asset context, for dashboard/report display., Natural-language explanation of an already-decided trade signal., AI read on current economic news, for dashboard/report display only     — the ac (+1 more)

### Community 55 - "Core Module"
Cohesion: 0.19
Nodes (7): DataFrame, Path, Resample OHLCV to higher timeframe., Get OHLCV data for symbol+timeframe.         Uses cache first, then fetches from, Get multi-timeframe data dict.         Automatically resamples from base timefra, Inspect the local cache without fetching — read-only status for         the dash, resample_ohlcv()

### Community 56 - "Tests Module"
Cohesion: 0.24
Nodes (17): detect_fair_value_gaps(), detect_order_blocks(), Most recent UNFILLED fair value gap within `lookback` closed bars.      Bullish, Most recent order block whose zone still holds.      Bullish OB: the last down-c, _df(), _flat(), _mtf(), tests/test_smc_fullspec.py --------------------------- H017 — full-spec SMC comp (+9 more)

### Community 57 - "Storage Module"
Cohesion: 0.18
Nodes (17): engine_stats_endpoint(), meta_analysis(), Phase 4.2: Meta-Analysis Dashboard.      Returns:     - Confidence calibration (, Per-engine performance statistics and suggested weight adjustments., _conn(), engine_stats(), init_tracker(), neutral_rate_by_engine() (+9 more)

### Community 58 - "Research Module"
Cohesion: 0.18
Nodes (17): Enum, str, Regime, _compute_h1_regime_series(), detect_qualified_sweeps(), H002Result, DataFrame, Exception (+9 more)

### Community 59 - "Tests Module"
Cohesion: 0.29
Nodes (16): check_exit(), Determine exit on this bar, modeling gaps and SL slippage.      Pure function (n, _bar(), Series, Tests for backtesting.backtest_engine.check_exit and config alignment.  Proves t, Guards against silent drift between the validated system and the     production, test_backtest_defaults_match_production_config(), test_both_touched_in_one_bar_sl_wins_pessimistic() (+8 more)

### Community 60 - "Core Module"
Cohesion: 0.15
Nodes (16): _auto_detect_columns(), load_data(), load_from_twelve_data(), load_multi_timeframe_from_twelve_data(), load_multi_timeframe_with_failover(), DataFrame, core/data_loader.py -------------------- Phase 1: synthetic OHLCV generator only, Best-effort case-insensitive column name matching for common     OHLCV export fo (+8 more)

### Community 61 - "Tests Module"
Cohesion: 0.16
Nodes (11): DataManager, Unified data access layer with automatic failover and caching.      Usage:, Download all symbol+timeframe combinations., DatetimeIndex, main(), dm(), tests/test_data_manager.py — DataManager.cache_status() (Data Center backend)., test_cache_status_gaps() (+3 more)

### Community 62 - "Scripts Module"
Cohesion: 0.21
Nodes (15): _cot_dir(), main(), parse_cot_text(), Path, scripts/download_cot.py ------------------------ Weekly CFTC Commitments-of-Trad, Merge this week's nets into the per-symbol caches; return symbols written., Parse deafut.txt into {internal_symbol: {name, date, net, oi}}.      Matching: t, _to_int() (+7 more)

### Community 63 - "Tests Module"
Cohesion: 0.12
Nodes (5): tests/test_api_contract.py — contract tests for the dashboard data endpoints (au, One EXECUTE decision + outcome-tracker signal in the fake D1., _seed_execute_signal(), test_health_exposes_decision_timeframe(), test_outcome_lifecycle_via_api()

### Community 64 - "Core Module"
Cohesion: 0.16
Nodes (10): Exception, RateLimiter, RateLimitExceeded, Check limits and increment counter. Returns remaining credits.          Raises R, Request refused because it would exceed the daily or per-minute cap., Thread-safe daily + per-minute request counter persisted to disk so     limits s, test_rate_limiter_blocks_when_daily_limit_reached(), test_rate_limiter_increments_and_returns_remaining() (+2 more)

### Community 65 - "Dashboard Module"
Cohesion: 0.18
Nodes (9): Root(), Shell(), TabId, TABS, RoadmapCard(), useAuth(), PLANNED, RoadmapGrid() (+1 more)

### Community 66 - "Research Module"
Cohesion: 0.26
Nodes (14): causal_bos_fvg_setups(), H008cResult, DataFrame, Exception, research/experiments/H008c_oos.py ----------------------------------- H008c — th, BOS+FVG setups with NO look-ahead.      A swing at position p is only consulted, run_experiment(), SyntheticDataNotAllowedError (+6 more)

### Community 67 - "Research Module"
Cohesion: 0.22
Nodes (14): build_manifest(), dataset_fingerprint(), _git_state(), Any, Path, research/manifest.py ----------------------- Reproducibility manifest for resear, Current commit + dirty flag; never raises (git may be absent)., SHA256 + shape of one input dataset. Pass the loaded DataFrame to     also recor (+6 more)

### Community 68 - "Core Module"
Cohesion: 0.16
Nodes (12): BinanceProvider, core/data_manager.py --------------------- IATIS Data Provider Manager — Institu, Free unlimited crypto history from Binance via ccxt., Yahoo Finance — free, forex/metals/indices, 1h=730d., Stooq — free historical data for forex pairs., Twelve Data — best quality, uses API credits., StooqProvider, TwelveDataProvider (+4 more)

### Community 69 - "Tests Module"
Cohesion: 0.21
Nodes (14): Convert IATIS symbol format to Yahoo Finance ticker.      EUR/USD → EURUSD=X, _to_yfinance_symbol(), _make_df(), tests/test_data_providers.py — Failover provider tests., Ensures the returned DataFrame is the actual data, not a copy., If TWELVE_DATA_API_KEY is missing, falls through to Yahoo., test_failover_direct_yahoo_when_no_twelve_key(), test_failover_returns_df_from_working_provider() (+6 more)

### Community 70 - "Core Module"
Cohesion: 0.17
Nodes (11): Any, Thin wrapper around Twelve Data's REST API with rate limiting     and response c, Quick call to verify the API key is valid and return plan info.         Costs 1, TwelveDataClient, main(), _update_registry(), download_symbol(), main() (+3 more)

### Community 71 - "Research Module"
Cohesion: 0.21
Nodes (13): _compute_atr(), detect_filtered_setups(), H008bResult, DataFrame, Exception, Series, research/experiments/H008b_session_filtered_bos.py -----------------------------, Run H008b experiment with session + ATR filters. (+5 more)

### Community 72 - "Scripts Module"
Cohesion: 0.24
Nodes (14): build_symbol(), fetch_binance_full(), fetch_td_batches(), fetch_yahoo_recent(), main(), merge_dfs(), normalize_df(), DataFrame (+6 more)

### Community 73 - "Scripts Module"
Cohesion: 0.23
Nodes (14): backtest_symbol(), _build_mtf(), _find_csv(), grade(), _load_df(), main(), _pnl(), Path (+6 more)

### Community 74 - "Storage Module"
Cohesion: 0.18
Nodes (7): D1Cursor, D1Row, Any, Mimics sqlite3.Row: supports row["col"] and row[0], and dict(row)., Mimics a sqlite3 cursor after execute() — rows are already fully     fetched (an, test_d1row_converts_to_dict(), test_d1row_supports_string_and_int_access()

### Community 75 - "Tests Module"
Cohesion: 0.27
Nodes (14): close_signal(), Record the outcome of a completed trade.      Args:         signal_id: from log_, _make_report(), tests/test_outcome_tracker.py, test_close_nonexistent_signal(), test_close_signal_loss(), test_close_signal_win(), test_duplicate_signal_id_ignored() (+6 more)

### Community 76 - "Research Module"
Cohesion: 0.22
Nodes (13): detect_liquidity_sweep(), ExperimentResult, DataFrame, Exception, research/experiments/H001_liquidity_sweep_htf.py -------------------------------, Run the H001 experiment. Raises SyntheticDataNotAllowedError unless     `source`, Raised when this experiment is invoked against synthetic data., Flag bars where price wicks beyond a recent swing high/low and     closes back i (+5 more)

### Community 77 - "Storage Module"
Cohesion: 0.21
Nodes (13): calibration_from_backtest(), calibration_from_db(), _conn(), Any, Path, storage/calibration.py ----------------------- Phase 4.1: Confidence Calibration, Compute WR, PF, and expectancy per regime.      This is the Phase 4.3 'most impo, Regime matrix from backtest JSON files (uses per-trade regime if available). (+5 more)

### Community 78 - "Tests Module"
Cohesion: 0.25
Nodes (13): Get most recent signals., recent_signals(), Regression tests for the 2026-07-03 outcome-tracking fixes:  1. ``auto_close_out, The old code recorded price_diff 1:1 in USD for crypto —     a 3 000-point BTC m, Guard against reintroducing the UnboundLocalError: scheduler.py     must not loc, _report(), test_crypto_pnl_is_risk_normalized_not_price_diff(), test_custom_risk_usd_scales_pnl() (+5 more)

### Community 79 - "Ai Module"
Cohesion: 0.21
Nodes (6): Any, ai/cache.py ------------ Small in-memory TTL cache so AIAnalyzer doesn't call a, Thread-safe get-or-compute cache with a fixed TTL per entry., TTLCache, test_ttl_cache_recomputes_after_expiry(), test_ttl_cache_returns_cached_value_within_ttl()

### Community 80 - "Core Module"
Cohesion: 0.18
Nodes (12): all_symbols_by_class(), AssetProfile, get_profile(), get_td_symbol(), core/asset_profiles.py ------------------------ Per-asset configuration: volatil, Return the asset profile for a symbol. Raises KeyError if unknown., Convert internal symbol to Twelve Data format via profile., Return all symbols grouped by asset class. (+4 more)

### Community 81 - "Regimes Module"
Cohesion: 0.22
Nodes (11): DataFrame, regimes/regime_detector.py ----------------------------- Classifies the current, Simple directional strength: normalized linear regression slope     of closing p, RegimeResult, _trend_strength(), atr(), classify_volatility(), DataFrame (+3 more)

### Community 82 - "Research Module"
Cohesion: 0.24
Nodes (12): detect_bos_fvg_setups(), _detect_fvg(), H008Result, DataFrame, Exception, research/experiments/H008_bos_fvg.py --------------------------------------- H00, Find all BOS+FVG setups on the given M15 dataframe.      Returns list of setup d, Run H008 experiment.      Args:         df_m15: M15 OHLCV data         source: d (+4 more)

### Community 83 - "Tests Module"
Cohesion: 0.22
Nodes (11): grade_consistency(), Grade stability across walk-forward windows., Conservative dynamic weight adjustment per Phase 4 recommendations.      Constra, suggested_dynamic_weights(), tests/test_calibration.py — Phase 4 calibration tests., test_grade_consistent(), test_grade_empty(), test_grade_inconsistent() (+3 more)

### Community 84 - "Storage Module"
Cohesion: 0.27
Nodes (9): _auth_headers(), d1_batch(), _parse_worker_response(), _post(), storage/d1_client.py ----------------------- HTTP client for the Cloudflare D1 p, Parse the Worker's JSON body regardless of HTTP status.      worker.js returns a, Execute multiple statements atomically via the Worker's     POST /d1/batch (D1's, Single HTTP seam for this module. Tests monkeypatch THIS function     (`patch("s (+1 more)

### Community 85 - "Execution Module"
Cohesion: 0.17
Nodes (12): apply_weights_to_config(), Apply suggested weights to config.yaml.      Args:         suggested: weight dic, ai_daily_report(), ai_optimize_weights(), Research Center — hypothesis status, engine performance, backtest results., System Health Dashboard — full status of all components., AI Dynamic Weight Optimizer — uses Claude to suggest engine weights.      Analyz, AI-phrased daily summary from already-computed stats — AIAnalyzer     only write (+4 more)

### Community 86 - "Core Module"
Cohesion: 0.18
Nodes (10): _atr_score(), _day_penalty(), MarketQualityResult, DataFrame, datetime, core/market_quality.py ------------------------ Market Quality Score (MQS): 0-10, Score based on ATR percentile (healthy = middle range)., Penalty for low-quality trading times. (+2 more)

### Community 87 - "Execution Module"
Cohesion: 0.17
Nodes (6): CTraderResult, Subscribe briefly and return (bid, ask) scaled ints, then unsubscribe., Return (bid, ask) as real prices for an IATIS symbol, or None., Place a market order with absolute SL/TP prices., Convert centi-lots → broker `volume` unit using live specs.          Returns (vo, Return live specs for a symbol, fetching them on demand if needed.          Boot

### Community 88 - "Research Module"
Cohesion: 0.24
Nodes (11): H002bResult, Any, DataFrame, Exception, research/experiments/H002b_multisymbol_sweep.py --------------------------------, Args:         symbols_data: {symbol: (df_m15, df_h1)}         sources: list of d, Run qualified sweep detection on one symbol. Returns outcomes list., run_experiment() (+3 more)

### Community 89 - "Cloudflare Module"
Cohesion: 0.18
Nodes (10): devDependencies, wrangler, name, private, scripts, db:apply, db:apply:local, deploy (+2 more)

### Community 90 - "Engines Module"
Cohesion: 0.25
Nodes (10): _detect_divergence(), _find_swing_highs(), _find_swing_lows(), _macd(), DataFrame, Series, Return boolean mask of local highs., Return boolean mask of local lows. (+2 more)

### Community 91 - "Engines Module"
Cohesion: 0.29
Nodes (9): _atr(), detect_bos_choch(), find_swing_points(), DataFrame, Break of Structure / Change of Character from CONFIRMED swings.      Latest clos, Pick the highest timeframe that has enough bars for reliable         swing-point, Identify swing highs/lows: a bar whose high/low is the max/min     within +/- `w, Determine directional bias from the sequence of recent swing highs/lows.      Us (+1 more)

### Community 92 - "Execution Module"
Cohesion: 0.25
Nodes (7): CTraderOrder, A market order request.      stop_loss / take_profit are ABSOLUTE prices (the le, TradeOrder, ExecutionResult, execution/trade_executor.py ----------------------------- Bridge between IATIS p, Lazy-load broker client., Execute a trade from an IATIS pipeline report.          Args:             report

### Community 93 - "Tests Module"
Cohesion: 0.22
Nodes (10): MonkeyPatch, _block_real_network(), fake_d1(), _isolate_credentials(), NetworkAccessBlockedError, tests/conftest.py ----------------- Hermetic test isolation for the whole suite., Strip all production credentials from the environment per test.      Guarantees, A test attempted a real outbound network connection. (+2 more)

### Community 94 - "Scripts Module"
Cohesion: 0.33
Nodes (8): _expected_weekend_closed(), FileReport, main(), Path, Timestamp, True if a non-crypto market is expected CLOSED at this UTC time.      Model: clo, _symbol_from_name(), verify_file()

### Community 95 - "Storage Module"
Cohesion: 0.29
Nodes (10): auto_close_shadows(), classify_gate(), _close(), get_open_shadows(), _init_db(), log_shadow_signal(), storage/shadow_book.py ----------------------- The Shadow Book — counterfactual, Record the counterfactual for a rejected directional decision.      Levels repli (+2 more)

### Community 96 - "Run Module"
Cohesion: 0.29
Nodes (8): DataFrame, Resample an OHLCV DataFrame to a higher timeframe., resample(), main(), main(), Run H008 on multiple symbols and test combined hypothesis.          Only include, run_combined(), run_one()

### Community 97 - "Core Module"
Cohesion: 0.31
Nodes (8): _cache_key(), _cache_path(), _load_from_cache(), DataFrame, Path, core/twelve_data_client.py ----------------------------- Twelve Data REST API cl, Fetch OHLCV time series from Twelve Data.          Args:             symbol: e.g, _save_to_cache()

### Community 98 - "Engines Module"
Cohesion: 0.27
Nodes (9): _detect_spring_upthrust(), _effort_vs_result(), _identify_trading_range(), DataFrame, Compare bar spread (effort) to price movement (result).      Wide spread + littl, Volume-based Wyckoff signals (only meaningful for assets with     real volume da, Identify if price is in a trading range (consolidation).      Uses ATR-normalize, Detect Spring (false breakdown below range) or Upthrust (false breakout above). (+1 more)

### Community 99 - "Scripts Module"
Cohesion: 0.40
Nodes (9): RuntimeError, fetch_td_deep(), fetch_yahoo_deep(), _integrity(), main(), DataFrame, Paginate backwards with end_date until the plan's history floor., _td_get() (+1 more)

### Community 100 - "Scripts Module"
Cohesion: 0.29
Nodes (9): _check_tls_stack(), main(), Place ONE tiny market order on the demo account and print the parsed result., Fail fast with a clear message if credentials are missing., Warn if Twisted's TLS verification stack is incomplete (security)., Connect, print account + symbol summary. Returns a READY client or None., _require_env(), run_connection_test() (+1 more)

### Community 101 - "Scripts Module"
Cohesion: 0.36
Nodes (9): greedy_search(), _load(), main(), _pf(), _pooled(), DataFrame, Path, Mean PF (trade-weighted-agnostic simple mean) across symbols on the     given sl (+1 more)

### Community 102 - "Tests Module"
Cohesion: 0.24
Nodes (4): _df(), tests/test_provider_chains.py ------------------------------ Asset-class-aware d, test_ctrader_falls_through_cleanly_without_credentials(), test_h4_starvation_class_fixed_by_native_provider()

### Community 103 - "Tests Module"
Cohesion: 0.33
Nodes (8): tests/test_shadow_book.py -------------------------- Tier-2 measurement layer: t, _report(), test_gate_classification_pipeline_order(), test_neutral_or_executed_decisions_are_not_shadowed(), test_rejected_directional_decision_creates_shadow_with_system_levels(), test_shadow_closes_on_intrabar_tp_and_ledger_attributes_gate(), test_shadow_sl_before_tp_parity_and_saving_losses_verdict(), test_shadow_time_stop()

### Community 104 - "Dashboard Module"
Cohesion: 0.22
Nodes (8): plugins, rules, react/only-export-components, react/rules-of-hooks, $schema, oxc, typescript, warn

### Community 105 - "Scripts Module"
Cohesion: 0.33
Nodes (7): Write to research/results/<name>_manifest.json (tracked by git)., write_manifest(), main(), _pip_size(), Pip size per symbol — mirrors the backtest/ctrader convention., main(), _zscore_backtest()

### Community 106 - "Scripts Module"
Cohesion: 0.36
Nodes (8): classify_result(), generate_html_report(), main(), Path, Run backtest for one symbol. Returns result dict or None on failure., Classify result quality., Generate HTML report., run_backtest_for_symbol()

### Community 107 - "Scripts Module"
Cohesion: 0.36
Nodes (8): detect_symbol(), detect_tf(), main(), print_result(), Path, Extract timeframe from filename like EURUSD_15m_2y.csv, Validate one CSV file. Returns validation result dict., validate_file()

### Community 108 - "Scripts Module"
Cohesion: 0.39
Nodes (8): _check_api(), _check_disk(), _check_scheduler(), _load_state(), main(), _notify(), scripts/watchdog.py -------------------- Independent health watchdog (production, _save_state()

### Community 109 - "Tests Module"
Cohesion: 0.31
Nodes (7): tests/test_research_manifest.py ---------------------------------- research/mani, *_result.json is gitignored; *_manifest.json must NOT be, otherwise     the whol, test_build_manifest_binds_git_config_and_data(), test_dataset_fingerprint_hashes_content(), test_dataset_fingerprint_records_bars_and_range(), test_manifest_filename_is_not_gitignored(), _tiny_csv()

### Community 110 - "Engines Module"
Cohesion: 0.32
Nodes (6): _classify_structure(), DataFrame, The next timeframe coarser than the decision TF, else the         decision frame, Return (highs, lows) as lists of (idx, price) tuples., Classify market structure from recent swings.      Returns dict with:       tren, _swing_points()

### Community 111 - "Scripts Module"
Cohesion: 0.36
Nodes (7): _bucket_stats(), _closed_outcomes(), main(), scripts/forward_review.py -------------------------- Pre-registered forward-evid, gate_ledger(), Any, Per-gate counterfactual ledger — the number the whole module exists     to produ

### Community 112 - "Ai Module"
Cohesion: 0.29
Nodes (4): _build_provider(), Instantiate the configured provider, or None if disabled/misconfigured.      Nev, OpenAIProvider, ai/providers/openai.py ------------------------- OpenAI chat completions API: ht

### Community 113 - "Tests Module"
Cohesion: 0.38
Nodes (7): One test window's outcome, self-describing for the audit trail., WindowResult, A 2-trade window with PF 9.0 is not evidence — the symbol must     NOT be report, _symbol_verdict(), test_insufficient_window_blocks_consistent_verdict(), test_single_failing_window_makes_symbol_inconsistent(), _wr()

### Community 114 - "Backtesting Module"
Cohesion: 0.33
Nodes (5): expectancy(), max_consecutive_losses(), backtesting/metrics.py -------------------------- Standalone metrics calculation, Average USD profit/loss per trade., BacktestResult

### Community 115 - "Core Module"
Cohesion: 0.38
Nodes (6): fetch_ccxt(), fetch_crypto_full_history(), DataFrame, core/ccxt_provider.py ---------------------- CCXT-based data provider — FREE unl, Fetch maximum available history for crypto (since exchange launched).     Binanc, Fetch historical OHLCV using ccxt.      Args:         internal_symbol: e.g. 'BTC

### Community 116 - "Dashboard Module"
Cohesion: 0.29
Nodes (7): Bluesky Icon Symbol, Discord Icon Symbol, Documentation Icon Symbol (purple stroke, code brackets), GitHub Icon Symbol (Octocat), IATIS Icons SVG Sprite, Social/User Profile Icon Symbol (purple stroke, user with star badge), X (Twitter) Icon Symbol

### Community 117 - "Engines Module"
Cohesion: 0.33
Nodes (4): DataFrame, Return (label, df) for the configured decision timeframe,         falling back t, Analyze multi-timeframe OHLCV data and return an opinion.          Args:, Wraps analyze() so an engine crashing never takes down the whole         pipelin

### Community 118 - "Engines Module"
Cohesion: 0.38
Nodes (6): _compute_dxy_bias(), _compute_risk_appetite(), DataFrame, Analyze macro context. Uses Yahoo Finance for DXY/SPY/VIX/GLD.          mtf_data, DXY trend determines broad USD direction.      DXY up = USD strong = bearish for, Determine Risk-On / Risk-Off from SPY trend + VIX level + Gold vs SPY.      Retu

### Community 119 - "Engines Module"
Cohesion: 0.38
Nodes (6): _atr_percentile(), DataFrame, Series, Current ATR as percentile of its own recent history., _roc(), _rsi()

### Community 120 - "Execution Module"
Cohesion: 0.29
Nodes (4): AccountInfo, Verify credentials/connectivity without placing any order., Establish an authenticated, READY connection to the cTrader API., Return the cached account info (populated during connect()).

### Community 121 - "Scripts Module"
Cohesion: 0.48
Nodes (6): dump_tables(), main(), Path, scripts/backup_d1.py --------------------- Nightly backup of the decisions datab, rotate(), verify()

### Community 122 - "Scripts Module"
Cohesion: 0.43
Nodes (6): fix_ohlc_violations(), fix_validator_for_indices(), main(), Path, Fix OHLC violations: high=max(O,H,C), low=min(O,L,C)., Update validate_dataset.py to use correct coverage for indices.     Indices trad

### Community 123 - "Scripts Module"
Cohesion: 0.48
Nodes (6): download_all(), main(), Path, Run full pipeline backtest for one symbol., Download data for all symbols. Returns {symbol: csv_path}., run_backtest_for_symbol()

### Community 125 - "Utils Module"
Cohesion: 0.52
Nodes (5): FeatureDescriptor, FeatureKey, load_feature_registry(), _make_feature_key(), Any

### Community 126 - "Ai Module"
Cohesion: 0.33
Nodes (3): Shared plumbing for every "phrase these already-computed stats         in plain, Plain-text daily summary from already-computed stats (e.g.         storage/decis, Plain-text summary of the research/backtest state (hypothesis         registry +

### Community 127 - "Tests Module"
Cohesion: 0.47
Nodes (6): Convert an engine ``Trade`` into an analytics ``TradeRecord``.      Pure functio, trade_to_record(), test_adapter_derives_rr_from_ground_truth_prices(), test_adapter_gap_exit_worse_than_stop_reflected_in_rr(), test_adapter_sell_loss_yields_negative_rr_actual(), _trade()

### Community 128 - "Cloudflare Module"
Cohesion: 0.60
Nodes (5): checkAuth(), fetch(), handleBatch(), handleExec(), unauthorized()

### Community 129 - "Engines Module"
Cohesion: 0.47
Nodes (5): _adx(), _ema(), DataFrame, Series, Average Directional Index — trend strength (not direction).

### Community 130 - "Engines Module"
Cohesion: 0.40
Nodes (5): _load_cot_data(), DataFrame, Load most recent COT data for symbol from local cache.      Cache location: data, Estimate retail positioning from price position in range.      Logic: Retail tra, _retail_sentiment_proxy()

### Community 132 - "Scripts Module"
Cohesion: 0.70
Nodes (4): _looks_macro(), main(), _pip_size(), _to_df()

### Community 133 - "Scripts Module"
Cohesion: 0.60
Nodes (4): download_symbol(), main(), Path, Download one symbol. Returns (success, path, bars).

### Community 134 - "Scripts Module"
Cohesion: 0.60
Nodes (4): _fetch(), fetch_deep(), main(), Page `end_date` backwards; return bars oldest-first, de-duplicated.

### Community 135 - "Scripts Module"
Cohesion: 0.60
Nodes (4): load_stale(), main(), scripts/revive_manifests.py ---------------------------- Revive NOT-REPRODUCIBLE, _tree_is_clean()

### Community 136 - "Storage Module"
Cohesion: 0.50
Nodes (4): _open_age_hours(), datetime, storage/outcome_tracker.py ---------------------------- Track actual trade outco, Age of an open signal in hours; None when entry_time is unparseable.

### Community 137 - "Core Module"
Cohesion: 0.50
Nodes (4): _fetch_yahoo_finance(), Convert IATIS interval to yfinance (interval, period)., Secondary: Yahoo Finance (free, no rate limit, H1+ only)., _td_interval_to_yf()

### Community 138 - "Scripts Module"
Cohesion: 0.83
Nodes (3): fetch_from_forex_factory(), fetch_from_jblanked(), main()

### Community 139 - "Scripts Module"
Cohesion: 1.00
Nodes (3): say(), security_check(), deploy_vps.sh script

### Community 140 - "Scripts Module"
Cohesion: 0.83
Nodes (3): fail(), say(), setup_service_user.sh script

### Community 141 - "Storage Module"
Cohesion: 0.50
Nodes (3): d1_connection(), No-op — stateless HTTP, nothing to release., Context manager matching the shape of each storage module's own     `_conn()` —

### Community 143 - "Main Module"
Cohesion: 0.67
Nodes (3): _market_quality_gate(), Any, Stage 2: Market Quality Score — gate before running 9 engines.      Returns (mqs

## Knowledge Gaps
- **125 isolated node(s):** `name`, `private`, `deploy`, `dev`, `db:apply` (+120 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_logger()` connect `Confluence Decision Engine` to `Price Action Engine`, `Research Edge Gate`, `Backtesting Engine`, `News & Economic Calendar`, `Decision Database Storage`, `Storage Module`, `Telegram Notification Bot`, `cTrader Client Core`, `Task Scheduler`, `Multi-Timeframe Confirmation`, `REST API Server`, `Trade Experience Database`, `Alternative Data Loader`, `Market Data Providers`, `OANDA Broker Client`, `Storage Module`, `Scripts Module`, `Ai Module`, `Backtest Module`, `Ai Module`, `Backtest Module`, `Scripts Module`, `Ai Module`, `Storage Module`, `Research Module`, `Core Module`, `Scripts Module`, `Core Module`, `Research Module`, `Storage Module`, `Regimes Module`, `Storage Module`, `Core Module`, `Execution Module`, `Storage Module`, `Core Module`, `Ai Module`, `Core Module`, `Scripts Module`?**
  _High betweenness centrality (0.143) - this node is a cross-community bridge._
- **Why does `load_config()` connect `System Entry & Diagnostics` to `Confluence Decision Engine`, `Confluence Score Calculator`, `Price Action Engine`, `Backtesting Engine`, `Scripts Module`, `Decision Database Storage`, `Contradiction Detection Engine`, `API Server AI Endpoints`, `Telegram Notification Bot`, `Task Scheduler`, `REST API Server`, `Alternative Data Loader`, `Scripts Module`, `Tests Module`, `Tests Module`, `Research Module`, `Research Module`, `Tests Module`, `Scripts Module`, `Scripts Module`, `Scripts Module`?**
  _High betweenness centrality (0.098) - this node is a cross-community bridge._
- **Why does `CTraderClient` connect `cTrader Broker Client` to `Scripts Module`, `Scripts Module`, `Scripts Module`, `cTrader Client Core`, `Execution Module`, `Market Data Providers`, `Execution Module`, `Execution Module`, `Execution Module`, `Execution Module`, `Tests Module`, `Tests Module`, `Execution Module`?**
  _High betweenness centrality (0.068) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `EngineOutput` (e.g. with `ContradictionResult` and `MetaDecision`) actually correct?**
  _`EngineOutput` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `CTraderClient` (e.g. with `DataFetchError` and `FetchAttempt`) actually correct?**
  _`CTraderClient` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 26 inferred relationships involving `Bias` (e.g. with `ContradictionResult` and `MetaDecision`) actually correct?**
  _`Bias` has 26 INFERRED edges - model-reasoned connections that need verification._
- **What connects `ai/ai_analyzer.py ------------------ AI Orchestrator — the only file the rest of`, `Instantiate the configured provider, or None if disabled/misconfigured.      Nev`, `Thin orchestrator: config -> provider -> cache -> typed result.` to the rest of the system?**
  _771 weakly-connected nodes found - possible documentation gaps or missing edges._