# IATIS Roadmap — from Decision Engine to Institutional Platform

> **Guiding rule:** every release must add *real production value*, not just
> features. A version ships when it moves IATIS measurably closer to a
> production-grade institutional platform — and every item stays subordinate
> to the project's non-negotiable philosophy (`CLAUDE.md`):
>
> - The final verdict is always produced by the **deterministic core**
>   (confluence + risk). AI explains, summarizes, and assists — it never
>   decides.
> - **No engine is enabled, and no threshold changes, without a pre-registered
>   hypothesis that clears the out-of-sample bar** (`research/edge_gate.py`,
>   `PROMOTION_CRITERIA`). Roadmap items that touch trading behavior are
>   measurement work, not feature toggles.
> - `NO_TRADE` remains a first-class output. Governance and auditability are
>   the product, not an afterthought.

This roadmap describes **planned** direction. Nothing here is implemented
until its release ships. The current, verified state is in
[`README.md`](../README.md).

---

## Engine Maturity Model

Each strategy engine is tracked against an explicit maturity level so
"complete the engines" means *documented and measured*, not merely *present*.
Enabling a new engine (raising it to L2+) requires clearing the OOS bar — per
`CLAUDE.md`, "enabling more engines (any)" is on the measured dead list and may
only be revisited through a fresh pre-registered hypothesis.

| Level | Meaning |
|---|---|
| **L0 – Placeholder** | Stub / not functional |
| **L1 – Implemented** | Code exists, unit-tested, not in the live vote |
| **L2 – Enabled** | Voting live under a `RESEARCH`-status hypothesis (paper only) |
| **L3 – Measured** | Independent OOS performance recorded in the registry |
| **L4 – Proven** | Meets `PROMOTION_CRITERIA` (≥300 OOS trades, OOS PF ≥ 1.2, walk-forward, Monte Carlo) — a *qualifying* `PASSED` |

Current standing (as of v0.5.9):

| Engine | Hypothesis | Level | Note |
|---|---|---|---|
| NNFX | H004 | L2 | Load-bearing (trend baseline) |
| Price Action | EXEMPT | L2 | Load-bearing (structure read) |
| SMC | — | L2 | Mildly negative in-sample; kept by burden-of-proof (H015) |
| Wyckoff | H006 | L2 | Enabled, low weight |
| ICT | H003 | L1 | Disabled; concepts overlap buried ideas |
| Quant | H005 | L1 | Disabled |
| Divergence | H010 | L1 | Disabled (reversal engine) |
| Market Structure | H011 | L1 | Disabled |
| Sentiment | H012 | L1 | Disabled |
| Macro | H007 | L1 | Disabled |

> The frozen `prod4` set (NNFX, Price Action, SMC, Wyckoff) is the only enabled
> configuration. v0.6 raises **documentation and measurement** maturity of all
> engines — it does not promise to enable more of them.

---

## v0.6 — Institutional Foundation

**Goal:** harden the existing platform for production. No new asset-class edge
claims; make what already runs measurable, observable, and operable.

- **Engine maturity documentation** for all ten engines (level, hypothesis,
  last-measured OOS evidence, known failure modes) — a living table backed by
  `research/results/registry.json`.
- **Independent per-engine performance scoring** built on the existing
  `storage/engine_tracker.py` and `storage/calibration.py` — accuracy, vote
  contribution, and calibration per engine, surfaced in Engine Monitor.
- **Evidence-based dynamic engine weighting** — turn `ai/dynamic_weights.py`
  from advisory suggestions into a *measured*, pre-registered adjustment driven
  by realized outcomes, gated behind a hypothesis and shipped dry-run first.
- **Risk hardening** — mature `risk/portfolio_exposure.py`,
  `risk/correlation_engine.py`, and add a **daily-risk** ceiling (per-day loss /
  new-exposure cap) on top of the existing per-trade and drawdown gates.
- **REST API expansion** — round out the ~50 endpoints with consistent
  pagination, filtering, and per-decision drill-downs.
- **Dashboard reporting** — live statistics plus scheduled **daily and weekly**
  reports (decisions, rejections-by-reason, engine attribution, forward-demo
  progress).
- **Ops closure** — execute the service-user migration (off `root`) and make
  off-site backups the default.

**Exit criterion:** the platform runs unattended for a full forward-demo cycle
with daily/weekly reporting and no manual intervention.

---

## v0.7 — Quant Research Platform

**Goal:** make IATIS a first-class quantitative research environment on top of
the existing backtest stack.

- **Walk-Forward Analysis** — productize `backtest/walk_forward.py` into a
  first-class, repeatable workflow with stored artifacts.
- **Monte Carlo Simulation** — expose `backtest/monte_carlo.py` robustness runs
  in the research workspace.
- **Parameter Optimization** — bounded, look-ahead-free search with mandatory
  OOS validation (in-sample-only tuning is rejected by design).
- **Strategy Comparison** — side-by-side metrics across engine sets / configs /
  regimes, always chronological-OOS.
- **Feature Engineering** — a versioned indicator/feature registry feeding
  engines and research.
- **Data Quality Scoring** — extend `core/data_confidence.py` /
  `core/data_validator.py` into a per-dataset quality score.
- **Data Catalog** — inventory of available OHLCV/news/macro datasets with
  coverage and provenance.
- **Research Workspace** — full experiment lifecycle (queue, run, archive,
  compare) built out from the existing whitelisted experiment runner.

**Exit criterion:** a new hypothesis can be pre-registered, run OOS, compared,
and archived entirely from the platform, with reproducible manifests.

---

## v0.8 — Institutional Data Platform

**Goal:** treat data as a governed, first-class asset.

- **Unified Data Lake** — one canonical store for OHLCV, news, and economic
  events across asset classes.
- **Market-data management** — native-timeframe-aware ingestion, gap repair,
  and calendar-validated history.
- **Research-result storage** — durable, queryable home for every manifest and
  result JSON.
- **Data-quality monitoring** — continuous scoring and alerting on drift,
  gaps, and cross-provider disagreement.
- **Automatic multi-source sync** — scheduled reconciliation across the
  provider chains.
- **Full decision archival** — every trading decision (executed or rejected)
  retained with provenance, building on the existing D1 archive.

**Exit criterion:** all data and decisions are governed, versioned, and
recoverable from a single catalog.

---

## v0.9 — AI Decision Intelligence

**Goal:** deepen AI as an *assistant*, never a decision-maker. Every capability
here operates strictly downstream of a final, deterministic verdict.

- **Decision explanation** — mature the existing per-decision explain layer.
- **Session summaries** — automated daily/weekly trading-session narratives.
- **Engine-improvement suggestions** — advisory, always dry-run, always
  requiring a pre-registered hypothesis before any behavior change.
- **Loss analysis** — post-mortem clustering of losing outcomes by cause.
- **In-platform developer copilot** — assists analysts and maintainers with
  code, config, and research navigation.

**Guardrail:** nothing in this release may write to `final_verdict`. The
deterministic core remains the sole decision authority.

---

## v1.0 — Institutional Trading Intelligence Platform

**Goal:** the first stable, production-grade release.

- **Integrated Command Center** — the 15-tab console matured to production SLA.
- **Decision Governance** — auditable, pre-registered, tamper-evident.
- **Portfolio management** — multi-position, multi-asset state and reporting.
- **Institutional risk management** — portfolio, correlation, daily, and
  drawdown controls unified.
- **Multi-channel alerting** — extend beyond the current outbound-only Telegram
  to email / webhook / additional channels.
- **Professional reporting** — scheduled, exportable, institution-grade.
- **Stable API** — versioned, documented, backward-compatible contract.
- **Multi-user auth & RBAC** — replace single-operator auth with a real user
  store and role-based access.
- **Backup & restore** — the nightly backup complemented by a rehearsed,
  documented restore path.

**Exit criterion:** a team (not a single operator) can run, audit, and rely on
the platform in production.

---

## Beyond v1.0 — Product Modules

Once v1.0 is stable, the platform can split into independently-releasable
products within one system:

| Module | Purpose |
|---|---|
| **Research Studio** | Build and test strategies |
| **Portfolio Manager** | Manage portfolios and risk |
| **Market Intelligence** | News, economics, sentiment analysis |
| **Execution Hub** | Broker and trading-platform integrations |
| **Data Center** | Manage all data sources |
| **AI Copilot** | Assistant for analysts and researchers |
| **Enterprise Dashboard** | Executive-level control surfaces |
| **Audit & Compliance** | Complete ledger of all decisions and changes |

---

## Vision — ITIP

The end state reframes IATIS from a trading program into:

> **IATIS — Institutional Trading Intelligence Platform (ITIP)**

A single institutional system unifying:

- Data management (Data Platform)
- Quantitative research (Quant Research)
- Multi-engine market analysis
- Trading-decision governance (Decision Governance)
- Risk management
- Live monitoring
- Reporting and analytics
- Broker / trading-platform integration
- An AI assistant that **explains** decisions without replacing the
  deterministic logic

Along this path, every release is a concrete step toward a complete production
platform while preserving the founding philosophy: **the final decision stays
rule-based and auditable; AI is used for explanation, analysis, and workflow —
never to make the decision in place of the deterministic system.**
