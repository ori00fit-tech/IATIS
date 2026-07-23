# IATIS Production Audit — July 2026

**Scope:** full repository at commit `5484ae0` (main), audited on branch `claude/iatis-production-audit-9xdfnh`.
**Method:** evidence only — repository inspection, test execution, coverage measurement, lint/complexity analysis, hermetic pipeline benchmark, live (read-only) probe of the production D1 Worker. No code was modified; this document is the sole deliverable. Where evidence does not exist, the finding says **NOT ENOUGH EVIDENCE**.

**Repository shape:** 158 Python files, 31,538 lines of Python; 1 Cloudflare Worker (141 lines JS) + D1 schema; React dashboard frontend; 374-test suite.

---

## Overall Verdict

**IATIS is a well-engineered, unusually honest research platform. It is NOT yet production-ready as an institutional system**, for three dominant reasons:

1. **Scientific validation does not support the profitability claims** (Phase 5 — FAIL). The registry's "PASSED, avg PF 3.08" claim is not reproducible from the repository, the walk-forward has a development-lookahead flaw, and the live system has recorded only **5 closed outcomes** in production D1.
2. **All operational credentials must be considered compromised** (Phase 8 — CRITICAL). The full production `.env` (data keys, Telegram token, API server key, cTrader OAuth tokens, Cloudflare API token, D1 proxy token) plus a root SSH identity were pasted into an external chat session on 2026-07-05. Nothing was ever committed to git history (verified with `git log -S` per secret) — the chat is the only exposure — but rotation is mandatory.
3. **No deployment safety net** (Phase 9 — FAIL): no CI/CD, no Docker, no backup/DR procedure, no log rotation, services run as root, D1 write failures crash the pipeline after the decision is made but **before** Telegram delivery.

What is genuinely good: fail-closed API auth with constant-time comparison; parameterized SQL throughout; an edge-gate that blocks unproven engines in code; a network-blocked, credential-isolated test suite (374/374 green); backtests that run the real engine pipeline with commission/slippage/gap modeling; and stub logic that is explicitly labeled instead of faked.

---

## Phase 1 — Repository Audit — **PASS (with debt)**

### Executive summary
Layered, config-driven architecture (data → quality gate → regime → engines → confluence → risk → news → storage/notify). Module boundaries are clean and imports are acyclic in the critical path (`main.py` imports engines/confluence/risk/storage; engines import only `base_engine` + utils). Documentation (README, ARCHITECTURE.md, cloudflare/README.md) matches the code unusually well.

### Evidence
- `main.py:21-55` — the entire pipeline dependency graph in one place; no circular imports observed.
- Dependencies: 10 pinned ranges in `requirements.txt`; `pip-audit`: **No known vulnerabilities found** (run 2026-07-05). One unpinned entry: `ccxt` (no version bound — reproducibility risk).
- All 374 tests pass in a fresh container after `pip install -r requirements.txt` + `pytest` (2m53s) — the project bootstraps cleanly.

### Problems (dead / duplicate / misconfigured)
| Item | Evidence | Classification |
|---|---|---|
| `utils/feature_def.py` (74 lines) | zero importers (grep across repo); 0% coverage | Dead code |
| `execution/tradingview_webhook.py` | self-described "Phase 5 placeholder"; no importers | Dead stub |
| `backtesting/metrics.py` | imported by nothing (grep); 0% coverage | Dead code |
| Two backtest packages: `backtest/` and `backtesting/` | both actively used (`backtest/runner.py` wraps `backtesting/backtest_engine.py`); two separate metrics implementations (`backtest/metrics.py`, `backtesting/metrics.py`) | Duplication / confusion risk |
| Root-level `run_h002.py`, `run_h002b.py`, `run_h008.py`, `run_h008b.py` | research runners living at repo root instead of `research/` | Organization debt |
| `config.yaml:139-143` — `file: storage/system.log` and `level: INFO` are nested under `fundamentals:` | `utils/logger.py` never reads either key; `_configure_root()` is always called with defaults | **Dead config — file logging silently does not exist** |
| `.env.example` documents OANDA keys; `config.yaml` has `oanda_enabled: false` | `execution/oanda_client.py` used only by `trade_executor` + tests | Latent path, acceptable |

### Maintainability score
**7/10.** Strong docs and tests; deductions for dead modules, dual backtest packages, one 482-line `main.py` with a CC-71 function (Phase 2), and dead logging config.

---

## Phase 2 — Code Quality — **PASS (with priority fixes)**

### Evidence
- `ruff check .`: 332 findings — 86 F541 (f-string no placeholder), 78 F401 (unused imports), 31 F841 (unused variables), 5 F821 (**undefined name**), 3 bare `except`.
- `radon cc -n D`: worst offenders —
  - `main.py:88 run_pipeline` — **CC 71 (grade F)**, ~390 lines, one function containing the entire pipeline.
  - `backtest/metrics.py:121 calculate_metrics` — CC 65 (F).
  - `backtesting/backtest_engine.py:262 run_backtest` — CC 36 (E); `scheduler.py:94 run_once` — CC 33 (E).

### Confirmed bug
- `scripts/download_smart.py` uses `pd.` at 5 sites (lines 85, 107, 141, 176, 223) but **never imports pandas** — the script crashes on any execution. Non-production path, but it is broken code shipped in the repo.

### Code smells (file-by-file highlights)
- `main.py`: `run_pipeline(config)` mixes data loading, gating, engine execution, scoring, report assembly, persistence, and notification. Any storage exception (see Phase 3) escapes it. Priority: split into composable stages; this also unlocks unit-testing individual gates without a full run.
- `execution/api_server.py` (1,610 lines): endpoints + inline HTML dashboard + login page + session store in one module; 36% coverage. Priority: extract HTML templates and session store.
- `execution/ctrader_client.py` (728 statements, 24% coverage): the largest module in the repo and the one that would place real orders — the least tested. Currently gated off (`ctrader_enabled: false`, `dry_run: true`), which is the correct state. **STALE as of 2026-07-06**: `config.yaml` flipped to `ctrader_enabled: true, dry_run: false` that day — real orders execute on the cTrader *demo* account (`allow_live_trading: false` still correctly gates real money). Coverage was 24% here, 36% by 2026-07-22, and 61% after docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md's P0-2 remediation — this file's claim is left unedited above (audit trail), corrected only here.
- Typing is present on most public functions; storage and confluence modules are well-typed (pydantic v2 used for API models). No mypy config exists — NOT ENOUGH EVIDENCE that annotations are internally consistent.

### Positive patterns worth keeping
- Engines never fake missing analysis: `engines/smc_engine.py:124-137` returns `"NOT_IMPLEMENTED_PHASE_3"` markers instead of fabricated order blocks; `research/edge_gate.py` refuses to boot with an engine enabled whose hypothesis isn't `PASSED`/`RESEARCH`.
- `tests/conftest.py:57-93`: autouse fixtures strip real credentials from the environment and **block real network sockets** in every test.

---

## Phase 3 — Storage Layer — **PASS (with four real defects)**

Architecture: Python (VPS) → HTTPS → Cloudflare Worker (`cloudflare/worker.js`) → D1. `storage/d1_client.py` mimics the sqlite3 API; SQLite fallback was deliberately removed (commit `bd5afab`). Schema (`cloudflare/schema.sql`): 5 tables (`decisions`, `engine_votes`, `outcomes`, `engine_performance`, `experiences`), 16 indexes covering every hot query column observed in `storage/*.py`. Multi-statement atomicity is handled correctly at the one site that needs it via `d1_batch()` (decision + N votes; commit `4827e48` fixed the cross-batch `last_insert_rowid()` bug).

### Live production probe (read-only, 2026-07-05)
- Unauthenticated request → **401** (auth enforced at the edge).
- Authenticated `SELECT 1` ×5 → latency 564–888ms, **median 589ms per query**.
- Row counts: decisions=75, engine_votes=208, outcomes=**5**, experiences=55, engine_performance=220. The store works end-to-end in production; volume confirms the system is days-to-weeks old in live operation.

### Defects
1. **Unwrapped writes crash the pipeline (HIGH).** `main.py:170` (`log_decision_db(mqs_report)`) and `main.py:430-431` (`log_decision_db(report)`, `record_engine_votes(report)`) have no try/except, and they execute **before** `telegram_send(report)` at `main.py:465+`. Reproduced in this audit: with `D1_WORKER_URL` unset, `run_pipeline()` raises `D1Error` and the run dies. Consequence in production: any Cloudflare/network incident silently suppresses EXECUTE signal delivery (the scheduler catches per-symbol at `scheduler.py:182` and moves on). Experience-DB and outcome-tracker writes nearby *are* wrapped — the inconsistency looks unintentional.
2. **No retries anywhere in `d1_client.py`.** One `requests.post`, 15s timeout, fail. Combined with (1), one dropped packet = one lost decision record.
3. **No connection reuse.** `d1_client.py` calls module-level `requests.post` — a new TCP+TLS handshake per query. With ~589ms/query median observed, dashboard endpoints that issue several queries pay multiples of that. A `requests.Session` (kept per-thread) is a one-line-class fix; a retry adapter attaches to the same Session.
4. **No migration mechanism.** Schema changes are "edit schema.sql and re-run wrangler" (`cloudflare/README.md`); tables are also `CREATE TABLE IF NOT EXISTS`-ed from Python at runtime. There is no version table, so schema drift between the Python DDL strings and `schema.sql` would go undetected.

Also noted: `storage/outcome_tracker.py:309 auto_close_outcomes()` resolves TP/SL only against the **latest close at each scheduler tick** (1–2h granularity). Intrabar TP/SL hits between ticks are resolved late and at a stale price, so live outcome labels (the calibration input) are approximate. Acceptable for now; must be documented before anyone reads live win-rates as precise.

---

## Phase 4 — Trading Engine Validation — **PARTIAL FAIL**

FAIL is not for dishonesty — the opposite: the code is explicit about what is unimplemented. It fails the "implementation matches advertised specification" bar.

| Engine | Enabled | Implementation vs. specification | Verdict |
|---|---|---|---|
| **SMC** (weight 0.202) | yes | Only swing-point structural bias (`find_swing_points`, HH/HL vs LH/LL). Order blocks, FVG, BOS/CHOCH, liquidity zones = explicit `NOT_IMPLEMENTED_PHASE_3` stubs (`smc_engine.py:124-137`) | **Name overstates content.** It is a structure-trend engine, ~30% of an SMC spec |
| **NNFX** (0.2273) | yes | EMA-200 baseline + ADX + ATR sizing; volume confirmation dropped (free data has no FX volume, documented) | Simplified but faithful core; ~60% of NNFX spec |
| **Price Action** (0.1869) | yes | Candlestick patterns + RSI + Bollinger + momentum. Rewritten after measured 0.975 correlation with NNFX (docstring) | Real, and the de-correlation rationale is evidence-driven |
| **Wyckoff** (0.0707) | yes | Trading range, spring/upthrust, phases; volume analysis auto-disabled for FX (correct — no real FX volume) | Reasonable price-only Wyckoff subset |
| ICT / Quant / Divergence / MarketStructure / Macro | no | Implemented (~230 lines each), heuristic, 10–18% test coverage | Dormant; RESEARCH status enforced by edge gate |
| **Sentiment** | no | **COT integration not wired** — uses a price-position "retail proxy" as a stand-in (docstring admits it) | Heuristic placeholder; must not be enabled as "sentiment" |
| **Confluence/Meta/Veto** | yes | Weighted vote + contradiction + H013 reversal veto + confidence multiplier — all real code, 90–95% coverage | Sound |
| **Risk engine** | yes | Drawdown-tiered risk %, exposure cap, live portfolio state from D1 (97–98% coverage) | Sound |
| **News filter** | yes | JBlanked + Forex Factory fallback, blackout windows. Note: fetches live even in synthetic runs (observed during benchmark) | Works; add offline mode |
| **Market Quality / Regime** | yes | Session/volatility/day scoring; ATR+ADX regime classification (97–98% coverage) | Sound |

**Fake logic found: none.** **Placeholder logic found:** SMC Phase-3 features, Sentiment COT proxy, `tradingview_webhook.py` — all explicitly labeled.

**Weights caveat:** the confluence weights (`config.yaml:4-14`, e.g. nnfx 0.2273, smc 0.202) are precise fitted values whose derivation is not in the repository (attribution/ablation scripts exist, their outputs do not). Provenance: NOT ENOUGH EVIDENCE.

---

## Phase 5 — Scientific Validation — **FAIL**

### Claim vs. evidence matrix

| Claim (source) | Evidence in repo | Verdict |
|---|---|---|
| H001/H002/H002b liquidity-sweep hypotheses **FAILED** (registry.json, p=0.63/0.22/0.43) | Registry entries with n, wr, p-values; experiment code present | **Credible** — the platform reports its own negative results, a strong integrity signal |
| H009 "6-engine confluence IS the proven edge — PASSED" (registry.json) | Summary numbers only. `research/results/*_result.json` and `data/*.csv` are **gitignored** — raw results and datasets are not in the repo | **NOT ENOUGH EVIDENCE — not reproducible** |
| Walk-forward "18/18 windows PF≥1.5, avg PF 3.08, min 1.5" (registry `walk_forward_validation`) | `scripts/walk_forward_validation.py` exists and calls the real pipeline via `backtesting/backtest_engine.py` with commission 0.5 pip + slippage 0.5 pip + gap-aware exits + next-bar entries (good mechanics) | **Methodologically unsound as "out-of-sample", see below** |
| "No lookahead in backtesting" (README) | `backtest_engine.py` computes signals from bars 0..N, enters next-bar open; exits model gaps and adverse slippage | **Supported at the bar level** |
| Live profitability | Production D1: **5 closed outcomes** | **NOT ENOUGH EVIDENCE — no live track record exists** |

### Why the walk-forward fails as validation
1. **Nothing is trained.** `run_window()` runs the *same fixed config* on "train" and "test" slices — it is a period-stability check, not walk-forward optimization. The train/test vocabulary overstates it.
2. **Development lookahead.** The test windows are 2024, 2025, and 2026→06-25. The system (weights, `min_score_to_trade: 58`, gates) was developed through June 2026 with access to all of that data. Every "test" window is in-sample relative to the development process. A PF-3.08 system on 0.5-pip-commission H1 bars is an extraordinary claim; genuine OOS can only accrue from **paper trading forward from today**, which the platform is correctly instrumented to do (outcomes table, calibration module) but has only n=5 of.
3. **Not reproducible.** Result JSONs and the 2022–2026 datasets are excluded from git; the registry summary is the only artifact.

**Positive:** `backtest/monte_carlo.py` exists (35% coverage, used by `backtest/runner.py`); the H-registry honestly carries FAILED/ABANDONED/NEEDS_MORE_DATA states; `research/edge_gate.py` turns scientific status into a boot-time invariant.

### Required to flip Phase 5 to PASS
Commit (or archive to D1/R2) raw result manifests + dataset hashes; re-run the walk-forward with config frozen at a tagged commit; accumulate ≥100–200 forward paper-trade outcomes; report PF/WR/expectancy/max-DD with confidence intervals from the Monte Carlo module.

---

## Phase 6 — Testing — **PASS**

- **374/374 tests pass** (fresh container, 2m53s; with coverage 3m01s).
- Hygiene: autouse network blocking, credential isolation, in-memory fake D1 that mirrors the Worker's exact JSON contract (`tests/conftest.py:96-172`).
- **Coverage (production code, excluding tests/scripts/research runners): 61.9% of 7,229 statements.** Critical path is strong: risk_engine 97%, live_portfolio_state 98%, meta_decision 95%, market_quality 97%, regime_detector 98%, enabled engines 78–99%, decision_db 100%, d1_client 91%, main.py 80%, scheduler 81%.
- **Gaps (ranked):** `execution/ctrader_client.py` 24% (728 stmts — the live-order path), `execution/api_server.py` 36% (auth/session logic is tested; the ~20 dashboard data endpoints largely are not), disabled engines 10–18%, `storage/calibration.py` 33%, `core/data_manager.py` 31%, `fundamentals/news_calendar.py` 48%.
- **Missing test classes:** D1 outage injection through `run_pipeline()` (would have caught the Phase 3 crash), api_server endpoint contract tests against fake D1, cTrader protocol tests beyond the current 24%, scheduler overlap/backoff under failure.

---

## Phase 7 — Performance — **PASS (adequate for current cadence)**

Measured in this audit (hermetic, synthetic data, fake in-memory D1, single container — relative numbers, not VPS-calibrated):

| Metric | Measured | Assessment |
|---|---|---|
| Import time (full pipeline graph) | 0.32s | fine |
| One full pipeline run (500 bars × 3 TFs, 4 engines, all gates) | **12.8–13.1s** | fine for hourly/2-hourly cadence; 8 enabled symbols ≈ 1.8min/run sequential |
| Peak RSS | **105MB** | comfortably inside the systemd `MemoryMax=1G` |
| D1 query round-trip (production Worker, median of 5) | **589ms** | the dominant storage cost; multiplied by no-session/no-batch reads (Phase 3) |
| Full test suite | 173s | acceptable; candidate for `-x --lf` in CI PR loop |

**Bottlenecks:** (1) per-query TLS setup in `d1_client` — fix with `requests.Session`; (2) `run_pipeline` recomputes all indicators per run with no incremental caching — irrelevant at hourly cadence, blocking for any M1/M5 ambition; (3) dashboard endpoints fan out serial D1 queries ≈ N×0.6s.

API latency / worker CPU under concurrent load: NOT ENOUGH EVIDENCE (no load test performed; nothing in repo).

---

## Phase 8 — Security — **FAIL (operational), code-level GOOD**

### CRITICAL — credential exposure (act today)
The complete production secret set was pasted into an external AI chat on 2026-07-05, alongside `root@134.209.229.125` + an ed25519 public key. Verified via `git log --all -S<value>` for each secret: **none were ever in git history** — the chat is the only leak, but that is sufficient. **Rotate all of the following, in this order:**
1. `CLOUDFLARE_API_TOKEN` (can reassign DNS/Workers/D1 — highest blast radius)
2. `D1_PROXY_TOKEN` (`wrangler secret put D1_PROXY_TOKEN` + VPS .env) — the Worker executes **arbitrary SQL** for any bearer of this token
3. `API_SERVER_KEY` (full dashboard/API control, including POST /ai/optimize-weights)
4. `TELEGRAM_BOT_TOKEN` (@BotFather /revoke) — an attacker can impersonate signal delivery
5. cTrader `ACCESS/REFRESH_TOKEN` + client secret (demo account today, but OAuth refresh is long-lived)
6. Data/AI keys: Twelve Data, Alpha Vantage, Finnhub, JBlanked, Perplexity
7. Consider the VPS root SSH surface exposed: confirm `PasswordAuthentication no`, review `authorized_keys`, check `last`/auth logs since 2026-07-05.

### Code-level review (mostly solid)
| Area | Evidence | Verdict |
|---|---|---|
| API auth | fail-closed when `API_SERVER_KEY` unset in production (`api_server.py:174-178`); `hmac.compare_digest`; HttpOnly/Secure/SameSite session cookie; raw key never stored in cookie | **Good** |
| Worker auth | bearer check with XOR constant-time compare (`worker.js:47-57`); 401 verified live | **Good**, but single static token + arbitrary-SQL design means token = full DB. No rate limit, no IP allowlist (Cloudflare zone rules could add both) |
| SQL injection | all user-reachable SQL parameterized; the three f-string sites (`engine_tracker.py:138`, `experience_db.py:343,385`) interpolate only fixed fragments/whitelisted keys | **No injection found** |
| XSS/CSRF | dashboard is inline HTML served same-origin; no CORS middleware (same-origin only); state-changing endpoints require header key or SameSite=lax cookie | Low risk; CSRF on cookie-auth POST endpoints is partially mitigated by SameSite=lax — add explicit CSRF token or header-only auth for POSTs |
| RCE surface | no `eval`/`exec`/`pickle.loads` on external input found | Good |
| Dependencies | `pip-audit`: 0 known vulns (2026-07-05); `ccxt` unpinned | Good, pin ccxt |
| Secrets handling | `.env` gitignored; `.env.example` clean; file perms forced 0600 for session store | Good |
| Deployment | services run as `root` (documented TODO in both unit files); API binds `0.0.0.0:8000` | Weak — create `iatis` user; bind 127.0.0.1 behind Cloudflare Tunnel/nginx, or firewall 8000 |

---

## Phase 9 — Production Readiness — **FAIL**

| Capability | State | Evidence |
|---|---|---|
| CI/CD | **Absent** | no `.github/`, no pipeline config anywhere — 374 tests run only when someone remembers |
| Containerization | **Absent** | no Dockerfile/compose; deploy = git pull + venv on VPS |
| Deployment/rollback | Manual; systemd `Restart=always` is the only recovery | unit files present, hardened (NoNewPrivileges, ProtectSystem, MemoryMax) — genuinely good for a VPS setup |
| Health checks | `/health` (public) + `/health/full` (auth, RAM/disk/credit thresholds from config) | Good foundation; nothing *watches* it — no uptime monitor, no alert on scheduler death (Telegram startup message only) |
| Logging | stderr → journald only; config's `file:`/`level:` keys are dead (Phase 1); no rotation concern since journald handles it, but log level is not configurable | Fix config wiring or delete keys |
| Metrics | none (no Prometheus/StatsD); decision history in D1 is the de-facto metric store | Gap |
| Backups / DR | **No procedure.** Relies implicitly on D1 durability (D1 has 30-day point-in-time restore via Cloudflare — not referenced anywhere); `storage/decisions.jsonl` exists only on one VPS disk | Document + schedule `wrangler d1 export` |
| Docs | README/ARCHITECTURE/cloudflare README are accurate and current (verified against code repeatedly during this audit) | Strong |

### Production readiness checklist (what must be true before "institutional")
- [ ] All leaked credentials rotated (Phase 8)
- [ ] CI: pytest + ruff + pip-audit on every PR; branch protection on main
- [ ] D1 writes wrapped + retried; Telegram delivery ordered before/independent of storage
- [ ] Non-root systemd user; API not on public 0.0.0.0
- [ ] Nightly `wrangler d1 export` to R2/off-site + restore rehearsal
- [ ] Uptime/alerting on `/health/full` + scheduler heartbeat
- [ ] Schema version table + migration script
- [ ] Forward paper-trade evidence base (Phase 5) before any live capital

---

## Phase 10 — Roadmap

### CRITICAL (this week)
| # | Task | Difficulty | Time | Risk | Depends on | Benefit |
|---|---|---|---|---|---|---|
| C1 | Rotate all exposed credentials + audit VPS SSH access | Low | 2–3h | None — do first | — | Closes an active compromise window |
| C2 | Wrap `log_decision_db`/`record_engine_votes` in main.py with try/except + retry; ensure Telegram send survives storage outage | Low | 2h | Low | — | Signals survive Cloudflare incidents; no silent loss |
| C3 | Add GitHub Actions: pytest + ruff + pip-audit on PR | Low | 2h | None | — | 374 tests actually guard main |

### HIGH (2–4 weeks)
| # | Task | Difficulty | Time | Risk | Depends on | Benefit |
|---|---|---|---|---|---|---|
| H1 | `requests.Session` + retry adapter in `d1_client.py` | Low | 3h | Low | C2 | ~cuts per-query latency; survives blips |
| H2 | Freeze config at a tag; re-run walk-forward as pure forward test; archive result manifests + dataset hashes in-repo | Medium | 1–2d | None | — | Turns PF claims from NOT ENOUGH EVIDENCE into auditable artifacts |
| H3 | Accumulate ≥100 forward paper outcomes; publish PF/WR/expectancy with Monte Carlo CIs | Low effort, long wall-clock | 4–8 wks elapsed | None | C2 | The only path to a defensible edge claim |
| H4 | Non-root `iatis` user; bind API to 127.0.0.1 behind Cloudflare Tunnel or firewall 8000 | Low | 3h | Low (test restart) | C1 | Removes root blast radius |
| H5 | Nightly `wrangler d1 export` → R2 + documented restore | Low | 4h | None | — | First real backup/DR |
| H6 | Refactor `run_pipeline` (CC 71) into staged functions | Medium | 2–3d | Medium (regression) | C3 (CI first) | Testability, on-call debuggability |
| H7 | Test D1-outage injection through pipeline + api_server endpoint contract tests | Medium | 2d | None | C2 | Locks in the resilience fix |

### MEDIUM (1–2 months)
| # | Task | Difficulty | Time | Risk | Depends on | Benefit |
|---|---|---|---|---|---|---|
| M1 | Delete dead code (`feature_def.py`, `backtesting/metrics.py`, `tradingview_webhook.py`); merge `backtest`/`backtesting` into one package; move `run_h*.py` into `research/` | Low | 1d | Low | C3 | −~500 lines of confusion |
| M2 | Fix logging config wiring (level from config, or delete dead keys); structured JSON logs | Low | 4h | Low | — | Observability baseline |
| M3 | Rename/re-scope SMC engine to `structure` OR implement the Phase-3 SMC features behind edge-gated hypotheses | Medium | 3–5d | Medium | H2 | Name matches capability |
| M4 | Fix `scripts/download_smart.py` import bug; ruff --fix the 190 auto-fixables; add ruff to CI gate | Low | 2h | Low | C3 | Lint debt → zero |
| M5 | Coverage for `ctrader_client.py` to ≥60% before any `ctrader_enabled: true` | High | 1 wk | None | — | Precondition for live execution |
| M6 | Schema version table + migration runner for D1 | Medium | 2d | Medium | H5 (backup first) | Safe schema evolution |
| M7 | Uptime monitoring + Telegram/email alert on scheduler silence >2 intervals | Low | 4h | None | — | You learn about outages before the market does |

### LOW (opportunistic)
| # | Task | Difficulty | Time | Benefit |
|---|---|---|---|---|
| L1 | Dockerfile + compose for reproducible VPS deploys | Medium | 2d | Rollback = re-tag |
| L2 | mypy in CI (gradual, per-package) | Medium | ongoing | Type safety confirmed, not assumed |
| L3 | Intrabar outcome resolution (fetch M1/M5 around open outcomes) | Medium | 3d | Honest live win-rate labels |
| L4 | Wire real COT data into sentiment engine (or keep disabled) | Medium | 3d | Removes last heuristic proxy |
| L5 | Pin `ccxt` version | Trivial | 5min | Reproducible builds |
| L6 | Batch dashboard D1 reads via `/d1/batch` | Low | 1d | Dashboard latency ÷N |

---

## Phase gate summary

| Phase | Verdict |
|---|---|
| 1 Repository | **PASS** (debt noted) |
| 2 Code quality | **PASS** (priority fixes listed) |
| 3 Storage | **PASS** (4 defects, 1 high) |
| 4 Engines | **PARTIAL FAIL** (honest but spec-incomplete; SMC/Sentiment) |
| 5 Scientific validation | **FAIL** (claims not reproducible; n=5 live outcomes) |
| 6 Testing | **PASS** |
| 7 Performance | **PASS** (for current cadence) |
| 8 Security | **FAIL** (credential exposure; code-level good) |
| 9 Production readiness | **FAIL** (no CI/backup/monitoring; root services) |

**Next phase recommendation:** execute C1–C3 immediately (one day of work total), then H1–H5. No code was modified in this audit per the engagement rules — every fix above awaits approval.
