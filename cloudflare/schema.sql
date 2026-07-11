-- cloudflare/schema.sql
-- ------------------------
-- Unified D1 schema for IATIS, combining the four local SQLite databases
-- into one D1 database:
--   storage/decisions.db      -> decisions, engine_votes
--   storage/outcomes.db       -> outcomes            (storage/symbol_health.py reads this table too)
--   storage/engine_tracker.db -> engine_performance
--   storage/experience.db     -> experiences
--
-- This file is a one-time convenience for manual setup:
--   wrangler d1 create iatis
--   wrangler d1 execute iatis --remote --file=cloudflare/schema.sql
--
-- It is NOT the only way tables get created: every storage/*.py module
-- still runs its own "CREATE TABLE IF NOT EXISTS" through the D1 proxy
-- on first connect (storage/d1_client.py), identical to how it already
-- self-provisions local SQLite files. Applying this file first just
-- avoids that first-request latency and lets you inspect the schema
-- before any data exists.

-- ─── decisions.db → decisions, engine_votes ────────────────────────────────

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    symbol      TEXT    NOT NULL DEFAULT '',
    verdict     TEXT    NOT NULL,
    regime      TEXT,
    volatility  TEXT,
    trend_str   REAL,
    cf_score    REAL,
    cf_engines  INTEGER,
    risk_passed INTEGER,
    fail_reason TEXT,
    summary     TEXT,
    raw_json    TEXT
);

CREATE TABLE IF NOT EXISTS engine_votes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL REFERENCES decisions(id),
    engine      TEXT    NOT NULL,
    bias        TEXT    NOT NULL,
    score       REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_verdict ON decisions(verdict);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_engine_votes_did ON engine_votes(decision_id);

-- ─── outcomes.db → outcomes ─────────────────────────────────────────────────
-- Also read directly by storage/symbol_health.py (no separate table).

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id   TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    entry_price REAL,
    stop_loss   REAL,
    take_profit REAL,
    entry_time  TEXT NOT NULL,
    exit_time   TEXT,
    exit_price  REAL,
    outcome     TEXT DEFAULT 'open',
    pnl_pips    REAL,
    pnl_usd     REAL,
    cf_score    REAL,
    regime      TEXT,
    news_risk   REAL,
    engines     TEXT,
    notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbol ON outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_outcome ON outcomes(outcome);
CREATE INDEX IF NOT EXISTS idx_regime ON outcomes(regime);

-- ─── engine_tracker.db → engine_performance ────────────────────────────────

CREATE TABLE IF NOT EXISTS engine_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    engine          TEXT NOT NULL,
    bias            TEXT NOT NULL,
    score           REAL NOT NULL,
    final_verdict   TEXT NOT NULL,
    agreed_with_majority INTEGER,
    confluence_score REAL
);

CREATE INDEX IF NOT EXISTS idx_ep_engine ON engine_performance(engine);
CREATE INDEX IF NOT EXISTS idx_ep_symbol ON engine_performance(symbol);
CREATE INDEX IF NOT EXISTS idx_ep_verdict ON engine_performance(final_verdict);

-- ─── experience.db → experiences ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS experiences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id   TEXT    UNIQUE NOT NULL,
    ts              TEXT    NOT NULL,

    symbol          TEXT    NOT NULL,
    session         TEXT,
    day_of_week     INTEGER,
    hour_utc        INTEGER,

    regime          TEXT,
    regime_confidence REAL,
    volatility      TEXT,
    trend_strength  REAL,
    mqs_score       REAL,
    mqs_grade       TEXT,
    atr_percentile  REAL,
    d1_bias         TEXT,
    d1_adx          REAL,

    verdict         TEXT    NOT NULL,
    direction       TEXT,
    confluence_score REAL,
    raw_score       REAL,
    mtf_adjustment  REAL,
    agree_count     INTEGER,
    total_engines   INTEGER,
    bull_conviction REAL,
    bear_conviction REAL,

    confidence      REAL,
    stability       REAL,
    data_quality    REAL,
    position_multiplier REAL,

    contradiction_blocked  INTEGER DEFAULT 0,
    reversal_vetoed        INTEGER DEFAULT 0,
    news_blocked           INTEGER DEFAULT 0,
    news_risk_score        REAL,
    regime_filtered        INTEGER DEFAULT 0,
    meta_blocked           INTEGER DEFAULT 0,

    fail_reason     TEXT,

    entry_price     REAL,
    stop_loss       REAL,
    take_profit     REAL,
    risk_reward     TEXT,

    engines_json    TEXT,

    outcome         TEXT,
    exit_price      REAL,
    pnl_pips        REAL,
    pnl_usd         REAL,
    pnl_r           REAL,
    duration_bars   INTEGER,
    exit_reason     TEXT,
    outcome_ts      TEXT
);

CREATE INDEX IF NOT EXISTS idx_exp_symbol ON experiences(symbol);
CREATE INDEX IF NOT EXISTS idx_exp_regime ON experiences(regime);
CREATE INDEX IF NOT EXISTS idx_exp_session ON experiences(session);
CREATE INDEX IF NOT EXISTS idx_exp_verdict ON experiences(verdict);
CREATE INDEX IF NOT EXISTS idx_exp_ts ON experiences(ts);
CREATE INDEX IF NOT EXISTS idx_exp_outcome ON experiences(outcome);
CREATE INDEX IF NOT EXISTS idx_exp_score ON experiences(confluence_score);

-- ─── shadow_book.py → shadow_signals ───────────────────────────────────────
-- Counterfactual outcomes of gate-rejected signals (philosophy audit's
-- shadow book). Same exit conventions as outcomes.

CREATE TABLE IF NOT EXISTS shadow_signals (
    shadow_id    TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    direction    TEXT NOT NULL,
    entry_price  REAL NOT NULL,
    stop_loss    REAL NOT NULL,
    take_profit  REAL NOT NULL,
    cf_score     REAL,
    primary_gate TEXT,
    fail_reasons TEXT,
    outcome      TEXT DEFAULT 'open',
    exit_time    TEXT,
    exit_price   REAL,
    r_multiple   REAL
);
CREATE INDEX IF NOT EXISTS idx_shadow_outcome ON shadow_signals(outcome);
CREATE INDEX IF NOT EXISTS idx_shadow_gate ON shadow_signals(primary_gate);
