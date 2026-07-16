# IATIS — Institutional Gap Analysis (July 2026)

**Role of this document:** a CTO-grade review of whether IATIS could realistically
operate inside a professional trading firm, what it is missing relative to how
institutional systems actually work, and a phased plan whose every item is
justified by evidence — from the literature, from regulation, or from this
repository's own measured results.

**Method:** repository inspection at commit `fde51d4` (2026-07-16), cross-checked
against the project's own measured audits (`docs/PRODUCTION_AUDIT_2026-07.md`,
`docs/PHILOSOPHY_AUDIT_2026-07.md` + live addendum, `docs/STRATEGY_EVIDENCE_2026-07.md`)
and the hypothesis registry (`research/results/registry.json`). Where a prior
audit finding has since been fixed, this document says so — several have been.

**Ground rules honored throughout** (from `CLAUDE.md`, non-negotiable):

- Nothing recommended here changes entries, exits, or thresholds mid-sample —
  the forward demo counter is the only prospective evidence and must not be reset.
- Nothing from the measured dead list is recommended for rebuilding.
- Every recommendation is classified **must-have / should-have / nice-to-have**
  and carries a measurable success criterion.

---

## Executive summary (the TLDR for the CTO)

**IATIS's research governance is already at or above institutional standard.
Its execution measurement, operational hardening, and evidence volume are not.**

The surprising finding of this gap analysis is that the usual institutional
checklist — pre-registration, out-of-sample discipline, codified promotion
criteria, negative-result ledgers, counterfactual gate accounting, reproducibility
manifests — is *present and enforced in code* here (`research/edge_gate.py`,
`scripts/forward_review.py`, `storage/shadow_book.py`, `research/manifest.py`),
which is rarer in real firms than practitioners admit. The genuine gaps are on
the other side of the pipeline:

1. **No transaction cost analysis (TCA).** Fill prices are captured from the
   broker (`execution/ctrader_client.py:1382-1385`) but nothing compares them
   to the decision price. The backtest *assumes* 0.5 pips slippage
   (`backtesting/backtest_engine.py:68`); live operation never verifies that
   assumption. This is the single largest institutional gap, it is cheap to
   close, and closing it does **not** touch the strategy (no sample reset).
2. **No decision provenance / replay.** The `decisions` table
   (`storage/decision_db.py:43-57`) stores the verdict and votes but not the
   code version, config hash, or input-data fingerprint that produced them —
   so no past decision can be bit-for-bit replayed or audited against a refactor.
3. **Operational single-points-of-failure.** Services still run as root
   (`iatis-*.service`, migration script exists but unapplied), one VPS, and
   the production audit's credential-rotation order (C1) has no confirmation
   artifact in the repo.
4. **The evidence base is thin where it matters most.** Every institutional
   reviewer will ask one question first: "show me the live track record."
   Today the honest answer is: pre-registered rules D001/D002 are waiting on
   n ≥ 40 / n ≥ 100 closed forward trades. No feature work changes this; only
   time does.

**Overall institutional readiness: 60/100** — held down not by missing
features but by unmeasured execution costs, unproven forward evidence, and
ops hardening. Detailed scoring in Step 9.

---

## Step 1 — How institutional trading systems actually operate

The reference pipeline below is assembled from primary sources: the market
microstructure and optimal-execution literature (Perold 1988; Almgren & Chriss
2000; Kissell 2013), backtest-validity research (Bailey, Borwein, López de
Prado & Zhu 2014, 2017; Harvey, Liu & Zhu 2016; Pardo 2008), regulatory
requirements that codify what firms must build (SEC Rule 15c3-5 "Market Access
Rule" 2010; MiFID II RTS 6, Commission Delegated Regulation (EU) 2017/589;
Federal Reserve SR 11-7 model risk management guidance 2011), and market
structure facts (BIS Triennial Central Bank Survey — FX is decentralized OTC;
there is no consolidated tape or true volume).

| # | Stage | Purpose | Inputs → Outputs | Typical mechanism | Det./Stat. | Why institutions do it |
|---|---|---|---|---|---|---|
| 1 | Market data | One consistent view of price | Vendor feeds → normalized bars/ticks | Primary/secondary feed arbitration, sequence gap detection, exchange timestamps | Deterministic | A signal computed on bad data is a bad signal with high confidence |
| 2 | Data validation | Refuse garbage before it propagates | Raw bars → accepted/quarantined bars + confidence | Staleness, outlier (n-sigma vs realized vol), gap, cross-vendor diff checks | Deterministic thresholds over statistical baselines | MiFID II RTS 6 Art. 5–8 requires tested, monitored inputs |
| 3 | Session / calendar | Know when the market is tradeable | Clock + venue calendar → session state | Static venue calendars + holiday files | Deterministic | Liquidity and spread regime is session-dependent; rollover/settlement windows are toxic |
| 4 | Regime detection | Condition strategy on environment | Returns/vol → regime label | Realized-vol filters, trend filters (Moskowitz, Ooi & Pedersen 2012); statistical variants exist (Hamilton 1989 Markov-switching; Ang & Bekaert 2002) | Both — production systems overwhelmingly use the deterministic kind | Simple, auditable, doesn't overfit; HMMs add parameters that must themselves be validated |
| 5 | Liquidity analysis | Can this size trade without moving price? | Order book / spread history → cost estimate | Depth models where books exist; spread + ADV heuristics where they don't (FX OTC) | Statistical estimate, deterministic limits | Sizing above available liquidity turns paper edge into real loss |
| 6 | Pre-trade risk | Block orders that violate limits *before* they leave | Candidate order + portfolio state → pass/veto | Hard limits: max order size, notional, price collar, duplicate check, credit | **Strictly deterministic** | Legally mandated: SEC 15c3-5 requires automated pre-trade controls; RTS 6 Art. 15 requires a kill switch |
| 7 | Portfolio exposure | The unit of risk is the book, not the trade | Positions + correlations → exposure state | Correlation/factor grouping, net/gross caps, concentration limits | Deterministic caps over statistical estimates | Ten "independent" 1%-risk trades that are 0.9-correlated are one 10% trade |
| 8 | Position sizing | Survive estimation error | Signal + stop distance + equity → size | Fixed-fractional / vol-targeted sizing; full Kelly avoided because edge estimates are noisy | Deterministic formula | Sizing errors compound; fractional sizing bounds ruin probability |
| 9 | Execution | Minimize implementation shortfall | Decision → fills | Order scheduling (Almgren-Chriss), venue selection, limit-vs-market policy | Both | Perold (1988): the gap between paper and real returns *is* execution; it routinely exceeds the alpha itself |
| 10 | Monitoring | Detect failure before the market does | System + positions → alerts | Heartbeats, position reconciliation vs broker, data-feed watchdogs, latency/error budgets | Deterministic | RTS 6 Art. 16 requires real-time monitoring and alerts |
| 11 | Post-trade analytics | Close the measurement loop | Fills + decisions → TCA, attribution, calibration | Implementation-shortfall TCA (Kissell 2013), P&L attribution, hit-rate calibration | Statistical | What is not measured drifts; execution cost drift is invisible without TCA |
| 12 | Continuous research | Keep the edge honest | Hypotheses → validated/rejected changes | Pre-registration, walk-forward (Pardo 2008), deflated Sharpe / PBO (Bailey & López de Prado), multiple-testing haircuts (Harvey, Liu & Zhu 2016), model inventory + independent validation (SR 11-7) | Statistical, deterministically governed | Most published "edges" are selection artifacts; process is the only defense |

Two structural observations that matter for judging IATIS:

- **The regulated stages (2, 6, 10) are deterministic everywhere.** No serious
  firm puts a statistical model in the pre-trade risk path. IATIS's
  "deterministic core, sovereign risk veto" philosophy is not a retail
  simplification — it is exactly the institutional pattern.
- **Stage 9→11 is where institutions spend most engineering effort** and where
  IATIS has spent the least. That asymmetry defines the roadmap below.

---

## Step 2 — Audit of the IATIS repository

### What exists and is sound (verified in code)

| Area | Evidence | Assessment |
|---|---|---|
| Multi-provider data with failover + provenance | `config.yaml data.provider_chains`; per-report `data_providers` (`main.py:570`); asset-class-aware chains, Yahoo demoted last (commit `882c86b`) | Institutional-shaped. Provenance per decision is a genuinely good practice |
| Data validation | `core/data_validator.py`; `scripts/verify_data_integrity.py` (market-hours calendars); `DATA STARVATION` loud-logging in `main.py`; NNFX ≥210-bar and MTF ≥50-D1-bar guards | Good — and the live addendum shows the starvation guard was *earned* (the July finding that live NNFX/MTF were silently dead) |
| Session/market-quality gate | `regimes/session_context.py`, `core/market_quality.py` (session + ATR + trend clarity, config-thresholded) | Adequate deterministic implementation of stage 3 |
| Regime detection | `regimes/regime_detector.py`, `volatility_classifier.py` (ATR+ADX; 97–98% coverage per production audit) | Deterministic, auditable — the institutional default |
| Pre-trade risk (sovereign veto) | `risk/risk_engine.py` + `risk/live_portfolio_state.py`: RR ≥ 2 floor, 1% risk/trade, 5% exposure cap, 15% drawdown halt, real equity-curve state (97–98% coverage) | Maps directly onto SEC 15c3-5-style controls. Drawdown halt = automatic kill switch; `dry_run` + `allow_live_trading` hard-guard = manual one |
| Portfolio exposure | `risk/correlation_engine.py` (static groups, `portfolio.max_per_group`), correlated-exposure in live state | Right shape for 15 symbols at H4. Static groups are a known simplification (see gaps) |
| Position sizing | Fixed-fractional, ATR-distance-derived (risk engine + asset profiles) | The institutional default; nothing to add |
| Execution plumbing | `execution/ctrader_client.py`: live symbol specs, relative SL/TP, bounded-backoff reconnect, `ProtoOAReconcileReq` reconciliation on every (re)connect, real `executionPrice` capture | Plumbing is ahead of its measurement (see gaps) |
| Backtesting | `backtesting/backtest_engine.py`: next-bar entry, gap-aware exits, **measured real IC Markets spreads as commission floor** (`REAL_SPREAD_PIPS`, lines 24–35, from `scripts/measure_ctrader_spread.py`) | Cost realism above retail norm. Walk-forward (`backtest/walk_forward.py`) + Monte Carlo (`backtest/monte_carlo.py`) exist |
| Research governance | `research/edge_gate.py` `PROMOTION_CRITERIA` (≥300 OOS trades, OOS PF ≥ 1.2, WF + MC required) enforced at boot; pre-registration registry with FAILED/ABANDONED entries preserved; `research/guards/causal_guard.py` (look-ahead defense); `research/manifest.py` + `scripts/revive_manifests.py` (clean-tree reproducibility) | **Above** typical institutional practice. This is SR 11-7's model inventory + validation discipline, in executable form |
| Counterfactual gate accounting | `storage/shadow_book.py`: every rejected directional signal is paper-tracked with identical exit mechanics; per-gate ledger of what each gate saves/costs | Rare even in institutions. This is the correct, pre-registered path to threshold recalibration |
| Pre-registered live decisions | `scripts/forward_review.py` applying D001 (FX cut rule) / D002 (live-capital discussion rule) from `registry.json _decision_rules` | Exactly how a firm's investment committee is *supposed* to work |
| Testing & CI | 775 test functions (up from 374 at the July audit); hermetic suite (network-blocked, credential-stripped, in-memory fake D1); `.github/workflows/ci.yml` (ruff E9/F821 + pytest + pip-audit) | The audit's C3 is done. Coverage gaps remain where they were (cTrader client, api_server endpoints) |
| Explainability | Every decision carries engine votes, reasons, gate outcomes; AI layer verified isolated from decision path (no import path from pipeline to `ai/`) | The "explainability first / AI never decides" claims are true in code, not just prose |

### Fixed since the July production audit (verified this session)

- CI pipeline exists (`.github/workflows/ci.yml`) — audit item C3. ✔
- Dead code removed: `utils/feature_def.py`, `backtesting/metrics.py`,
  `execution/tradingview_webhook.py` all gone — audit item M1 (partial). ✔
- `storage/d1_client.py` now has per-thread `requests.Session` + narrow
  transport-level `Retry` policy — audit items C2/H1. ✔
- Storage writes in `main.py` are try-wrapped (7 guarded sites). ✔
- Backup/watchdog timers exist (`iatis-backup.timer`, `iatis-d1-backup.timer`,
  `iatis-watchdog.timer`) — audit items H5/M7 substantially addressed. ✔

### Weaknesses and debt that remain (verified this session)

| Item | Evidence | Class |
|---|---|---|
| No TCA / slippage measurement | `executionPrice` captured but consumed only for logging/reconciliation; no module computes decision-price → fill-price shortfall; backtest slippage stays an assumption (0.5 pips) | **Missing module (must-have)** |
| No decision provenance | `decisions` schema has no git commit, config hash, or input-bar fingerprint (`storage/decision_db.py:43-57`) | **Missing columns (must-have)** |
| No decision replay | Nothing can re-run a stored decision from its stored inputs; the only "replay" reference in the repo is the philosophy audit script's SQL invariants | Missing module (should-have) |
| Root services | `User=root` in all five `.service` files; `scripts/setup_service_user.sh` exists but is unapplied on the VPS | Ops debt (must-have) |
| Credential rotation unconfirmed | Production audit C1 (full `.env` leaked to an external chat 2026-07-05) has no closure artifact in the repo | Ops debt (must-have to confirm) |
| Raw research artifacts not archived | Result JSONs / datasets gitignored; manifests exist but the audit's Phase-5 "not reproducible" finding stands for H009's PASSED claim (edge_gate now flags it at every boot — correct mitigation) | Evidence debt (should-have) |
| Monolith modules | `main.py` 762 lines (pipeline still one module; audit measured `run_pipeline` at CC 71); `execution/api_server.py` has grown to 3,105 lines | Maintainability debt (should-have) |
| Two backtest packages | `backtest/` + `backtesting/` both remain (duplication halved by deleting `backtesting/metrics.py`, but the split persists) | Debt (nice-to-have) |
| No schema migrations | Runtime `CREATE TABLE IF NOT EXISTS` + hand-run `schema.sql`; no version table | Debt (should-have) |
| Static correlation groups | `risk/correlation_engine.py` hardcodes groups; no measured-correlation refresh | Acceptable at current scale; revisit if universe grows (nice-to-have) |
| Sentiment engine is a proxy | COT wiring incomplete; retail proxy stands in (docstring admits it). Engine disabled + H021 pre-registered | Correctly handled — no action beyond H021's own rule |
| Outcome label precision | SL/TP resolution at scheduler-tick granularity noted in the July audit; shadow book uses intrabar bar-range mechanics — real-outcome labels must match shadow mechanics exactly or gate-ledger comparisons are biased | Measurement risk (should-have to verify) |
| Hidden assumption: single operator | Session auth, one API key, no roles; fine today, incompatible with a second human | Documented, acceptable |

**Hidden assumption worth naming explicitly:** the whole platform assumes
*free-tier data is good enough to measure the edge it trades*. The project's
own evidence (`docs/STRATEGY_EVIDENCE_2026-07.md`, "the binding constraint is
DATA, not code") acknowledges this. Institutions solve stage-1 problems with
money; IATIS solves them with failover chains and integrity checks. That is
the correct poor-man's answer, but it puts a ceiling on achievable evidence
quality (e.g., resampled H4 vs native H4 — already bitten once, live addendum).

---

## Step 3 — Feature comparison matrix

Quality is scored 0–10 against *institutional* implementations, not retail ones.
"Evidence" cites the repo; impact/complexity are Low/Med/High.

| Institutional component | Exists? | Quality | Business impact | Complexity | Priority | Evidence |
|---|---|---|---|---|---|---|
| Market data consolidation + failover | Yes | 7 | High | Med | Keep | `core/data_providers.py`, asset-class chains, provenance per report |
| Data validation at ingestion | Yes | 6 | High | Low | Improve | `core/data_validator.py`, starvation guards; no runtime cross-vendor confidence |
| Cross-vendor price verification | Partial | 4 | Med | Low | Should-have | `scripts/cross_provider_diff.py` exists as offline script only |
| Session / calendar awareness | Yes | 7 | Med | Low | Keep | `regimes/session_context.py`, `core/market_quality.py` |
| Market regime detection | Yes | 7 | Med | Low | Keep (frozen) | `regimes/regime_detector.py`; deterministic ATR+ADX, 98% coverage |
| Liquidity/spread analysis | Partial | 5 | Med | High (data-bound) | Keep as-is | Real spreads measured (`scripts/measure_ctrader_spread.py`) and baked into backtest costs; depth analysis impossible on free FX data — correctly not faked |
| Pre-trade risk controls | Yes | 8 | Critical | — | Keep | `risk/risk_engine.py` + live state; RR floor, exposure caps, drawdown halt |
| Kill switch | Yes | 7 | Critical | — | Keep | Drawdown halt (auto) + `dry_run`/`allow_live_trading` guards (manual) |
| Portfolio exposure / correlation limits | Yes | 6 | High | Low | Keep | `risk/correlation_engine.py` static groups + correlated-exposure state |
| Position sizing | Yes | 8 | Critical | — | Keep | Fixed-fractional 1%, ATR stops — the institutional default |
| Execution integration | Yes | 6 | High | — | Keep | cTrader reconnect + reconciliation; OANDA backup path |
| **Execution quality analytics (TCA)** | **No** | 0 | **High** | **Low** | **Must-have** | Fill price captured (`ctrader_client.py:1382-1385`) but never compared to decision price |
| **Slippage model validated against fills** | **No** | 2 | **High** | **Low** | **Must-have** | Backtest assumes 0.5 pips (`backtest_engine.py:68`); never reconciled live |
| Broker reconciliation (scheduled) | Partial | 5 | High | Low | Must-have | Reconciliation on (re)connect only; no periodic position/P&L diff + alert |
| **Decision provenance (code+config+data fingerprint)** | **No** | 0 | **High** | **Low** | **Must-have** | `decisions` schema lacks all three (`decision_db.py:43-57`) |
| Decision replay | No | 0 | Med | Med | Should-have | No harness; `raw_json` stores outputs, not inputs |
| Post-trade P&L / outcome tracking | Yes | 7 | High | — | Keep | `storage/outcome_tracker.py`, auto-close, R-multiples |
| Counterfactual analytics (shadow book) | Yes | 9 | High | — | Keep | `storage/shadow_book.py` per-gate ledger — exceeds institutional norm |
| Probability calibration | Yes (starved) | 7 design / n-limited | Med | — | Wait for n | `storage/calibration.py` score-bucket → realized WR; needs closed trades, not code |
| Walk-forward validation | Yes | 6 | High | — | Keep + archive | `backtest/walk_forward.py`; July audit's development-lookahead caveat stands — forward demo is the fix, and it's running |
| Monte Carlo robustness | Yes | 6 | Med | — | Keep | `backtest/monte_carlo.py` (risk of ruin, distributions) |
| Research governance / model risk mgmt | Yes | 9 | Critical | — | Keep | Edge gate, registry, pre-registration, causal guard, manifests, forward review |
| Research automation | Yes | 7 | Med | — | Keep | Experiment runners, ablation harness, manifest revival, registry audit at boot |
| Monitoring / alerting | Partial | 5 | High | Low | Improve | Watchdog timer + Telegram; no metrics endpoint, no external uptime check |
| Backup / DR | Yes | 6 | High | — | Keep + rehearse | Backup + D1-backup timers; restore rehearsal not evidenced |
| Dynamic engine weighting | Deliberately absent | — | — | — | **Rejected** | Measured dead: H015 twice, engine-activation study — subset selection is universe-dependent noise |
| Scenario simulation / stress tests | Partial | 3 | Med | Low | Nice-to-have | Gap-aware exits + MC exist; no named scenarios (spread ×5, weekend gap, feed outage) |
| Risk attribution (factor/engine P&L decomposition) | Partial | 4 | Low at this scale | Med | Nice-to-have (defer) | `storage/engine_tracker.py` tracks votes, not attributed P&L; low value at 15 symbols/H4 until n grows |

---

## Step 4 — Missing institutional components (verified against evidence)

Only components that professional systems demonstrably run, that IATIS lacks,
and that survive this project's own evidence discipline:

### Must-have

**M1. Execution Quality Analytics (implementation-shortfall TCA).**
Institutions have measured since Perold (1988) that the paper-vs-real gap is
often the size of the alpha. IATIS's measured carrier edge is PF ≈ 1.2–1.5 —
a *thin* edge in cost terms; an unmeasured 1-pip systematic slippage on XAUUSD
(12-pip spread already consumes much of the margin) could erase the XAUUSD
book silently. Everything needed already exists: decision price (signal bar),
intended entry (next-bar open), and actual `executionPrice` per deal. Missing
is only the join and the ledger.

**M2. Decision provenance stamping.** SR 11-7-style auditability requires
knowing *which model version* made each decision. Add `git_commit`,
`config_hash`, and `bars_fingerprint` (hash of the input OHLCV window per
timeframe) to every decision row. Three columns, computed once per run.
Without them, the forward evidence base — the project's single most valuable
asset — cannot prove it was produced by an unchanged system, which is exactly
what rule 6 ("never change mid-sample") needs to be *verifiable* rather than
promised.

**M3. Scheduled broker reconciliation.** Reconciliation currently happens on
(re)connect only. Institutions reconcile positions and cash daily without
exception (it is the control that catches everything else failing). A
scheduler-tick job diffing broker open positions vs `outcome_tracker` open
outcomes, alerting on any mismatch, closes this.

**M4. Ops hardening completion.** Non-root migration (script already written),
confirmation artifact for the C1 credential rotation, restore rehearsal for the
existing backups. No new code — discipline items with existing tooling.

### Should-have

**S1. Runtime data-confidence layer.** Promote `scripts/cross_provider_diff.py`
logic into a periodic check on live symbols (primary vs one alternate provider,
close-to-close divergence in ATR units), recorded per decision and alerting on
breach. Cheap because the pieces exist; valuable because the project has already
been burned once by silent data degradation (live addendum: resampled-H4
starvation muting NNFX and the D1 gate for 614 decisions).

**S2. Decision replay harness.** Given M2's fingerprints plus a stored bar
window (store the window itself for EXECUTE decisions only — small: ~500 bars ×
3 TFs × ~8 symbols), re-run the pipeline at a chosen commit and diff verdicts.
This is the regression tool that makes the overdue `run_pipeline` refactor
(CC 71) safe, and it is the only way to prove a refactor didn't alter behavior
mid-sample.

**S3. Result artifact archiving.** Push raw result JSONs + dataset SHA-256s to
R2 (or D1) so PASSED claims are reproducible by a third party. Directly
addresses the production audit's Phase-5 FAIL.

**S4. Schema versioning + migration runner.** One `schema_version` table, one
idempotent migration script. Precondition for M2's column additions being safe.

**S5. Metrics/observability endpoint.** A `/metrics` (Prometheus text format)
or equivalent: scheduler heartbeat age, per-provider failure counts, D1 latency,
open-position count. The watchdog timer then has something quantitative to check,
and RTS 6-style real-time monitoring stops being Telegram-only.

### Nice-to-have (defer; do not start without an explicit decision)

- **Named scenario stress tests** (spread ×5, weekend gap through stop, provider
  outage mid-run) as backtest-engine parameterizations — extends existing MC.
- **Measured-correlation refresh** for the static groups (quarterly job, alert
  if a symbol's group membership disagrees with realized correlation).
- **Risk attribution** — revisit when closed-n and symbol count justify it.
- **Statistical regime models (HMM)** — only ever as a pre-registered hypothesis
  with the H008c method; the literature supports regime conditioning, not any
  particular detector, and the current deterministic one is already validated
  as part of the frozen system.

### Explicitly rejected (fashionable but unsupported — or measured dead here)

| Candidate | Why rejected |
|---|---|
| Dynamic engine weighting | Dead list: H015 (twice) + engine-activation study — subset/weight selection was universe-dependent noise. `ai/dynamic_weights.py` correctly stays advisory-only |
| More engines / new entry signals | Dead list: every addition measured dilutive; STRATEGY_EVIDENCE's own conclusion: "more of the same is flailing" |
| ML signal models | No supporting evidence at this data scale; Harvey-Liu-Zhu multiple-testing problem plus ~350 closed trades of labeled data makes any fit an in-sample artifact by construction |
| LLMs anywhere in the decision path | Non-auditable, non-deterministic, violates the architecture's core invariant; current isolation (verified: no import path) is the correct end state, not a phase |
| Order-flow / footprint engine | Not evidence-rejected but data-gated: real FX order flow is paid (CME futures); crypto volume already A/B-measured at ΔPF ≈ 0. A build here is a spend decision, per STRATEGY_EVIDENCE §"forward path" item 4 |
| CQRS / event bus / microservices | No load, no team, no concurrent writers that would justify them (see Step 7) |
| Partial TP / breakeven / trailing exits | Dead list: measured artifact (+100% PF was look-ahead; OOS ≈ 0) |

---

## Step 5 — Scientific validation of each recommendation

| Rec | Problem it solves | Supporting evidence | Expected measurable benefit | Difficulty | Implementation risk | Success metric |
|---|---|---|---|---|---|---|
| M1 TCA | Live edge could be silently smaller than backtested edge | Perold (1988); Kissell (2013) ch. 3–4: shortfall routinely 30–100% of alpha for slow strategies too; project-local: PF margins of 1.2–1.5 are thin | Verified (not assumed) cost model; early warning if realized slippage > 0.5-pip assumption | Low (join two existing data streams) | ~None — read-only over existing data; zero strategy contact, no sample reset | After 50 fills: slippage distribution published with CI; backtest `slippage_pips` either confirmed or corrected *as a pre-registered data update, not a tuning act* |
| M2 Provenance | Forward evidence can't prove the system was unchanged | SR 11-7 (model inventory/versioning); this repo's rule 6 needs verifiability | Every decision attributable to exact code+config+data | Low | Low (additive columns; needs S4 first) | 100% of new decisions carry all three fingerprints; `philosophy_audit.py` gains an invariant: no fingerprint change without a registry entry |
| M3 Reconciliation | Position drift between broker and internal state goes unnoticed | Universal institutional daily-reconciliation control; RTS 6 Art. 16 monitoring | Broker-vs-internal mismatch detected within one scheduler tick instead of at next reconnect | Low | Low (read-only broker call exists: `ProtoOAReconcileReq`) | Zero unexplained mismatches; alert latency ≤ 1 tick when injected in test |
| M4 Ops | Root blast radius; unverified rotation; unrehearsed restore | Production audit Phases 8–9 (measured FAIL) | Removes the two audit FAILs that are pure ops | Low | Medium (service migration touches live deploy — script + documented rollback exists) | Services run as `iatis`; rotation closure note committed; restore rehearsal documented with timestamps |
| S1 Data confidence | Silent data degradation (already happened once) | Live addendum: 614 decisions invalidated by resampled-data starvation; RTS 6 input-monitoring requirement | Divergence alert before, not after, a bad-data decision batch | Low-Med | Low (offline script logic exists; API-budget cost of one extra provider call per symbol per check) | Injected 2×ATR divergence in test triggers alert + decision flag |
| S2 Replay | Refactors can't be proven behavior-neutral | Standard regression practice; enables the audit's H6 (CC-71 split) safely | Refactor safety net; audit answer to "why did decision X happen" | Med | Low (read-only) | Replay of any stored EXECUTE decision reproduces identical verdict + score at the recorded commit |
| S3 Archiving | PASSED claims not third-party reproducible | Production audit Phase 5 FAIL; Bailey et al. (2017) reproducibility standard | Phase-5 finding flips: claims auditable | Low | Low | An outside reviewer can re-derive H-registry numbers from archived artifacts |
| S4 Migrations | Schema drift undetected; M2 unsafe without it | Production audit M6 | Safe additive schema evolution | Low-Med | Med (touches production D1 — backup precondition already satisfied by existing timers) | Version table present; migration applies + rolls back cleanly on the fake-D1 suite and on a staging copy |
| S5 Metrics | Monitoring is Telegram-only, unquantified | RTS 6 Art. 16; production audit "no metrics" gap | Watchdog checks numbers, not vibes; dashboards for free | Low | Low | Scheduler-silence and provider-failure alerts fire in fault-injection tests |

The nice-to-haves deliberately get no table row: none has a measurable benefit
today that survives the "would this change any decision now?" test.

---

## Step 6 — Phased roadmap

**Sequencing constraint honored:** Phases 1–3 contain *zero* strategy contact —
no entry, exit, threshold, weight, or universe change — so the forward demo
sample keeps accumulating unreset through all of them. Phase 4 is gated on
pre-registered rule **D002** (carriers PF ≥ 1.2 at n ≥ 100). Phase 5 is gated
on the shadow book reaching its pre-registered n≈50-per-gate bar.

### Phase 1 — Measurement integrity & ops completion (1–2 weeks of work)

| Item | Files to create | Files to modify | Deps | Tests required | Migration | Rollback |
|---|---|---|---|---|---|---|
| S4 schema versioning (first — M2 depends on it) | `storage/migrations.py`, `cloudflare/migrations/0001_baseline.sql` | `cloudflare/schema.sql` (note pointing at migrations), `storage/d1_client.py` (version check helper) | none | migration apply/reapply idempotence on fake D1; drift detection test | Create `schema_version` table; stamp current schema as v1 | Table is additive; dropping it restores status quo |
| M2 decision provenance | — | `storage/decision_db.py` (3 columns via migration 0002), `main.py` (compute git commit once at boot, config SHA-256 at load, per-run bars fingerprint), `scripts/philosophy_audit.py` (new invariant check) | S4 | fingerprint stability test (same bars → same hash); NULL-tolerance for historical rows; audit-invariant test | Migration 0002, additive columns, old rows NULL | Columns nullable — ignore and (optionally) drop |
| M1 TCA ledger | `storage/execution_quality.py` (fills table + shortfall calc + summary), `GET /execution-quality` endpoint | `execution/trade_executor.py` + `ctrader_client.py` (persist executionPrice + intended price per order), `execution/api_server.py` (endpoint) | S4 (migration 0003) | shortfall math unit tests (long/short, JPY-pip, XAU units); fill-event persistence test through fake broker events | Additive table | Additive — disable write call |
| M3 scheduled reconciliation | `execution/reconciliation.py` | `scheduler.py` (one call per run), `execution/telegram_bot.py` (mismatch alert path) | none | mismatch-injection test (broker says open, tracker says closed, and inverse) | none | Feature-flag `features.reconciliation` |
| M4 ops completion | `docs/OPS_CLOSURE.md` (rotation confirmation + restore rehearsal log) | apply `scripts/setup_service_user.sh` on VPS; flip `User=iatis` in the 5 unit files | none | n/a (runbook items) | Follow script; documented VPS steps | Unit files are in git — revert + restart |

### Phase 2 — Research infrastructure (1–2 weeks, parallelizable with Phase 1 soak)

| Item | Create | Modify | Deps | Tests | Migration | Rollback |
|---|---|---|---|---|---|---|
| S2 replay harness | `research/replay.py` (load stored window → run pipeline at HEAD → diff verdict/score/votes), `storage` of EXECUTE-decision input windows (R2 or local parquet) | `main.py` (persist input window on EXECUTE only) | M2 | golden-decision replay test: recorded fixture decision reproduces exactly; deliberate-config-change test produces a flagged diff | none (new artifact store) | Stop persisting windows; harness is read-only |
| S3 artifact archiving | `scripts/archive_results.py` (result JSONs + dataset SHA-256 → R2), `research/results/ARTIFACTS.md` (index) | `research/manifest.py` (record archive URI) | none | manifest→archive round-trip integrity (hash match) | Backfill existing `research/results/*.json` | Archive is additive storage |
| Refactor `run_pipeline` (audit H6) — *behavior-frozen* | `core/pipeline/` staged modules (data, gates, engines, confluence, risk, persist, notify) | `main.py` becomes composition of stages | **S2 (replay harness is the safety net)** | full replay-equivalence over a corpus of stored decisions (verdict+score+votes identical); existing 775 tests green | none | Git revert; replay corpus proves equivalence both ways |

### Phase 3 — Institutional-grade analytics (opportunistic; mostly waiting on n)

| Item | Create | Modify | Deps | Tests | Migration | Rollback |
|---|---|---|---|---|---|---|
| S1 runtime data confidence | `core/data_confidence.py` | `scheduler.py` (periodic check), decision report field, `main.py` | none | divergence-injection test; API-budget accounting test | Additive report field | Feature flag |
| S5 metrics endpoint | `execution/metrics.py` (`/metrics`) | `api_server.py`, `scripts/watchdog.py` (consume metrics) | none | endpoint contract test; fault-injection alert tests | none | Endpoint removal |
| TCA first report | `docs/TCA_2026-Qx.md` (generated) | — | M1 + ~50 fills | n/a | n/a | n/a |
| Calibration & regime-matrix publication | — | dashboard already wired | n ≥ 100 closed | existing | n/a | n/a |
| Outcome-label parity check | test only: `tests/test_outcome_shadow_parity.py` | fix `outcome_tracker` only if divergence found | none | shadow vs real resolution on identical synthetic bar paths must match | none | n/a |

### Phase 4 — Advanced execution (**gated on D002 verdict: carriers PF ≥ 1.2 at n ≥ 100**)

Only if live capital becomes a real discussion, and each item pre-registered:

- Limit-entry vs market-entry study (H0xx: does resting at signal price for one
  bar improve net-of-cost expectancy? Almgren-Chriss says patience trades
  market impact against opportunity cost — at H4 the opportunity-cost side may
  dominate; that is exactly why it must be measured, not assumed).
- Partial-fill and rejection handling hardening in `ctrader_client.py`
  (coverage to ≥60% first — audit M5 stands).
- Order-rate limiter and per-order notional sanity bound (15c3-5-style
  max-order check — cheap, and appropriate to add *at the moment* live capital
  is enabled, as part of the D002 change itself).

Files: `execution/order_policy.py`, hypothesis H-entry in registry **before**
any A/B code. Rollback: config-gated (`execution.order_policy: market`).

### Phase 5 — Adaptive optimisation (**gated on shadow book n≈50 per gate**)

This phase already has its pre-registered plan in `CLAUDE.md` ("Current frozen
state"): recalibrate `min_score`, quorum, and info-share **from the shadow-book
ledger**, not opinion. The only work here is executing that plan when n arrives:

- `research/experiments/H0xx_gate_calibration.py` reading `gate_ledger()`,
  proposing threshold changes, validated on the chronological-OOS standard.
- Any adopted change restarts the forward counter *by design* — which is why
  this phase is last, and why nothing before it touches thresholds.
- Explicitly out of scope forever, absent new evidence: dynamic engine weights,
  engine additions, exit management (dead list).

---

## Step 7 — Architecture review

| Principle | Verdict | Evidence & justified improvements |
|---|---|---|
| SOLID | Mostly followed; two violations | Engines honor Liskov via `BaseEngine.analyze() → EngineOutput`; storage modules are single-purpose. Violations: `main.py`'s pipeline function (SRP — audit-measured CC 71) and `api_server.py` (3,105 lines: routing + HTML + sessions + endpoints). Both fixes are already roadmapped (Phase 2 refactor; api_server split is worthwhile but lower priority than measurement gaps) |
| Clean/Hexagonal architecture | Substantially present, undeclared | Ports-and-adapters exists de facto: providers (`core/data_providers.py`), brokers (`ctrader_client.py`/`oanda_client.py` behind `trade_executor.py`), storage (`d1_client.py` mimicking sqlite3 — an adapter so faithful the domain SQL never changed). Domain (engines/confluence/risk) does not import adapters directly. No action needed beyond keeping the boundary |
| DDD | Bounded contexts exist without ceremony | `research/`, `risk/`, `confluence/`, `execution/`, `storage/` map cleanly to domains; ubiquitous language is consistent (verdict, gate, engine, hypothesis). Formal DDD machinery would add nothing |
| Repository pattern | Present pragmatically | Each storage module owns its table + queries over the shared connection adapter. Adequate; a generic repository abstraction would be over-engineering |
| Dependency injection | Implicit (module-level), acceptable | Config dict is threaded explicitly; tests substitute the transport via fixtures (`fake_d1`) rather than constructor injection. Works because the process is single-tenant; formal DI container unjustified |
| CQRS | **Not justified** | One writer (scheduler), a handful of readers (dashboard), ~75 decisions in weeks. Splitting read/write models would be pure ceremony |
| Event-driven architecture | **Not justified** | H4 cadence, sequential per-symbol runs, one process. An event bus adds failure modes and removes the current property that the pipeline is a *readable, ordered, auditable* function — which is the product |
| Overall | The architecture matches the institutional pattern for its stage (deterministic pipeline, sovereign risk, isolated advisory AI). The correct architectural work is the two monolith splits, both behavior-frozen and replay-verified — nothing structural | |

---

## Step 8 — Performance review

All figures from the July production audit's hermetic benchmark (the only
measured numbers available), adjusted for verified fixes since:

| Resource | Measured | Assessment |
|---|---|---|
| Pipeline run (500 bars × 3 TFs × 4 engines, all gates) | 12.8–13.1 s | At H4 cadence with ~15 symbols sequential ≈ 3 min/run — **no bottleneck**. Parallelization would add complexity for zero decision-quality gain |
| Peak RSS | 105 MB | Comfortably inside systemd `MemoryMax=1G` |
| D1 round trip | 589 ms median (pre-fix) | Was the dominant cost via per-query TLS; **fixed** — `d1_client.py` now pools per-thread `requests.Session` + retry policy. Remaining: dashboard endpoints still fan out serial queries; batch via existing `/d1/batch` if dashboard latency ever matters (audit L6 — nice-to-have) |
| Test suite | ~3 min (374 tests then; 775 test functions now) | Fine for CI; `-x --lf` loop available locally |
| Backtesting speed | Not the constraint | The constraint on research throughput is data (free-tier limits, history depth), not CPU — per STRATEGY_EVIDENCE. Optimizing the backtester would speed up producing *more in-sample results*, which this project correctly does not want |

**Recommendation: no performance work.** Every optimization candidate fails the
evidence test — nothing currently waits on compute. The one exception already
happened (D1 session pooling). Revisit only if cadence drops below H1 or the
universe grows past ~50 symbols, neither of which is planned.

---

## Step 9 — Final institutional score

Scored against "could a professional firm run this tomorrow," not against effort.

| Dimension | Score /10 | Rationale (one line of evidence each) |
|---|---|---|
| Architecture | 7 | Institutional pattern (deterministic core, sovereign risk, isolated AI); two measured monoliths (CC-71 pipeline, 3.1k-line api_server) |
| Research capability | 9 | Pre-registration + chronological OOS + codified promotion bar + counterfactual shadow book + honest negative-result ledger — above typical institutional practice |
| Risk management | 7 | Real pre-trade controls on real portfolio state (97–98% coverage); static correlation groups and thin live n keep it from 8+ |
| Execution | 4 | Solid plumbing (reconnect, reconciliation-on-connect, real fills) but **zero execution measurement** (no TCA, unvalidated slippage assumption) and 24%-coverage on the live-order module |
| Maintainability | 7 | Accurate docs (verified repeatedly), CI now exists, dead code removed; deductions for monoliths + dual backtest packages |
| Testing | 7 | 775 hermetic tests, network-blocked, fake-D1 with real SQL semantics; gaps exactly where the audit left them (cTrader 24%, api_server endpoints) |
| Scalability | 5 | Single VPS, single process, root services (script ready, unapplied); fine for its scale, nothing institutional about the ops footprint |
| Explainability | 9 | Every decision carries votes+reasons+gates; AI layer provably outside the decision path; "No Trade is valid" implemented, not sloganized |
| Data quality | 5 | Good validation and provenance, but free-tier ceiling is structural, cross-vendor checking is offline-only, and the platform has already shipped one silent data-starvation incident (caught by its own audit — hence 5, not 3) |
| Decision governance | 9 | D001/D002 pre-registered and machine-applied; frozen-state discipline; edge gate flags under-evidenced PASSED at every boot |
| **Overall readiness** | **60/100** | Not the mean (69) — readiness is weakest-link-weighted: unmeasured execution costs, unproven forward evidence (rules waiting on n ≥ 40/100), and unfinished ops hardening gate deployment regardless of how good the research layer is |

### The verdict, stated plainly

IATIS inverts the usual retail failure mode. Retail systems have rich execution
dreams and no research discipline; IATIS has institutional-grade research
discipline and an unmeasured execution layer. A quant firm CTO reviewing this
would say: *the part that is hardest to teach — epistemic discipline — is done;
the parts that remain are the cheap, mechanical ones.* The roadmap is therefore
short and unheroic: measure execution (M1–M3), stamp provenance (M2), finish
ops (M4), archive evidence (S3), and then do the only thing no engineer can
accelerate — let the pre-registered forward sample accumulate to the n that
D001 and D002 demand.

What would move the overall score fastest, in order:
1. **+8–10 points** — forward evidence reaching D002's bar with carriers PF ≥ 1.2
   (nothing to build; already running).
2. **+5 points** — Phase 1 complete (TCA + provenance + reconciliation + non-root).
3. **+3 points** — Phase 2 complete (replay-verified refactor + artifact archive,
   flipping the production audit's Phase-5 reproducibility FAIL).

---

## References

- Perold, A. (1988). "The Implementation Shortfall: Paper Versus Reality."
  *Journal of Portfolio Management*, 14(3).
- Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio
  Transactions." *Journal of Risk*, 3(2).
- Kissell, R. (2013). *The Science of Algorithmic Trading and Portfolio
  Management*. Academic Press.
- Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies*,
  2nd ed. Wiley. (Walk-forward methodology.)
- Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. J. (2014).
  "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest
  Overfitting on Out-of-Sample Performance." *Notices of the AMS*, 61(5).
- Bailey, D. & López de Prado, M. (2014). "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality."
  *Journal of Portfolio Management*, 40(5).
- Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. J. (2017). "The
  Probability of Backtest Overfitting." *Journal of Computational Finance*, 20(4).
- Harvey, C., Liu, Y. & Zhu, H. (2016). "…and the Cross-Section of Expected
  Returns." *Review of Financial Studies*, 29(1). (Multiple-testing haircuts.)
- Hamilton, J. (1989). "A New Approach to the Economic Analysis of
  Nonstationary Time Series and the Business Cycle." *Econometrica*, 57(2).
- Ang, A. & Bekaert, G. (2002). "International Asset Allocation With
  Regime Shifts." *Review of Financial Studies*, 15(4).
- Moskowitz, T., Ooi, Y. H. & Pedersen, L. H. (2012). "Time Series Momentum."
  *Journal of Financial Economics*, 104(2).
- U.S. SEC Rule 15c3-5 (2010). "Risk Management Controls for Brokers or
  Dealers with Market Access." (Mandatory automated pre-trade risk controls.)
- Commission Delegated Regulation (EU) 2017/589 ("MiFID II RTS 6").
  (Organisational requirements for algorithmic trading: testing, kill
  functionality, real-time monitoring, annual self-assessment.)
- Board of Governors of the Federal Reserve, SR 11-7 (2011). "Supervisory
  Guidance on Model Risk Management." (Model inventory, validation,
  governance — the institutional template this repo's registry mirrors.)
- BIS Triennial Central Bank Survey (2022). (FX market structure:
  decentralized OTC, no consolidated volume — the data constraint documented
  in `docs/STRATEGY_EVIDENCE_2026-07.md`.)

**Repository evidence index:** `docs/PRODUCTION_AUDIT_2026-07.md` (10-phase
measured audit), `docs/PHILOSOPHY_AUDIT_2026-07.md` + live addendum (8-axis
live-behavior audit; the data-starvation finding), `docs/STRATEGY_EVIDENCE_2026-07.md`
(the measured edge and the rejected-enhancement ledger),
`research/results/registry.json` (H001–H021 with pre-registered decision rules
D001/D002), `research/edge_gate.py` (codified promotion criteria),
`storage/shadow_book.py` (counterfactual gate ledger).
