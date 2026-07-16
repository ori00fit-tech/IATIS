# Command Center v0.6 — From Stats Panel to Operations Center

> Specification for the v0.6 dashboard workstream (see `docs/ROADMAP.md` →
> v0.6 Institutional Foundation). Goal: reorganize the 15 existing tabs around
> three operator spines — **System Operation · Data Quality · Decision
> Quality** — topped by an **Executive Overview** that composes existing
> signals into a handful of health scores, so an operator can act in seconds
> instead of reading dozens of tiles.
>
> **Guiding constraint:** most of this data already exists across ~50 API
> endpoints. v0.6 is primarily *synthesis and gap-filling*, not net-new
> measurement. Every metric below is tagged:
> - ♻️ **reuse** — endpoint exists; surface/aggregate only
> - ➕ **extend** — data partially exists; needs a derived metric
> - 🆕 **new** — needs new backend measurement or storage
>
> **Evidence-integrity guardrail (non-negotiable):** below n≈30 closed trades,
> performance figures (PF, expectancy, WR) are noise and MUST be shown with
> their uncertainty (confidence intervals, "data collection" labeling), never
> as headline conclusions. This mirrors `CLAUDE.md` and the dashboard's own
> current framing.

---

## The three spines

The existing tabs map cleanly onto three operator concerns:

| Spine | Question it answers | Existing tabs |
|---|---|---|
| **System Operation** | Is the machine running? | Mission Control, Live Logs, VPS Operations, Alert Center |
| **Data Quality** | Can I trust the inputs? | Data Center, (provider chains, data-confidence) |
| **Decision Quality** | Are the verdicts sound and auditable? | Live Signals, Engine Monitor, System Audit, Forward Demo, Execution Quality, Research & Backtests |

v0.6 does not add a fourth spine — it adds a **roll-up layer** on top.

---

## 1. Executive Overview (top of Mission Control) ➕

Six composite scores (0–100), each a deterministic rollup of signals that
already exist. Not a new opinion — a weighted summary of measured checks.

| Score | Composed from | Source (existing) |
|---|---|---|
| **System Health** | services up, CPU/RAM/disk under thresholds, scheduler freshness, D1 latency | `/health/full` ♻️ |
| **Research Integrity** | leakage findings, manifest reproducibility %, survivorship disclosure % | `/research/integrity` ♻️ |
| **Data Quality** | cache OK %, provider success, cross-provider agreement, starvation flags | `/data-health`, `/data-confidence` ➕ |
| **Decision Quality** | philosophy-audit pass/warn/fail ratio + provenance coverage | `/philosophy-audit` ♻️ |
| **Risk Status** | exposure headroom, drawdown vs limits, open-risk | `/health/full` ♻️ |
| **Production Readiness** | maturity matrix (§8) | new rubric 🆕 |

Each score links straight to the tab that explains it. A red score is never a
dead end — it deep-links to the failing check.

---

## 2. Evidence Progress ➕ / 🆕

Promote the `18 / 100` tile into a full panel. Data exists in `/forward-review`,
`/outcomes`, `/stats`; the statistics need adding.

| Metric | Readiness |
|---|---|
| Evidence progress (closed / target) | ♻️ |
| Win rate **with Wilson confidence interval** | 🆕 (compute CI) |
| Profit Factor, Expectancy, Average R | ➕ (from outcomes) |
| Max Drawdown | ♻️ (`live_portfolio_state`) |
| Confidence level / sample sufficiency band | 🆕 |

**Presentation rule:** while `closed < 30`, render every figure greyed with an
explicit "data collection — not yet significant" banner. The point value is
secondary to the interval.

---

## 3. Data Health (per provider) ♻️ / 🆕

Currently the dashboard shows *which* providers are configured, not *how well*
they perform. Per provider, add:

| Metric | Readiness |
|---|---|
| Last successful sync | ♻️ (from decision `data_providers` provenance) |
| Success rate (fetches OK / attempted) | 🆕 (per-provider counters) |
| Missing bars / gap count | ♻️ (`/data-health`) |
| Average latency | 🆕 (already logged; needs aggregation) |
| Coverage (symbols × timeframes served natively) | ➕ (`/provider-chains`) |

Rationale: prior audits found **data starvation** (NNFX / MTF D1 gate) was the
dominant cause of weak engine behavior — provider quality is a first-order
signal, not a footnote.

---

## 4. Engine Performance ♻️ / 🆕

Move beyond participation to predictive value. Foundation exists in
`/engine-stats`, `storage/engine_tracker.py`, `storage/calibration.py`.

| Metric | Readiness |
|---|---|
| Vote rate, Agreement rate | ♻️ (also in philosophy-audit Axis 2/3) |
| Precision / Recall (votes vs realized outcomes) | 🆕 (link votes → outcomes) |
| Predictive value | 🆕 |
| Last calibration | ♻️ (`calibration.py`) |
| Drift (rolling accuracy delta) | 🆕 |

Ties directly to the **engine maturity model** (`ROADMAP.md`): these are the
metrics that move an engine from L2 (enabled) toward L3 (measured).

---

## 5. Decision Quality ♻️ / ➕

Aggregates over existing decision records:

| Metric | Readiness |
|---|---|
| Quorum quality (informative-weight share distribution) | ♻️ (Axis 8 data) |
| Average confluence score | ➕ |
| Average meta-confidence | ➕ (`meta_decision`) |
| Average risk score | ➕ |
| Explainability coverage (% decisions with an AI explanation) | 🆕 |
| Provenance coverage (% with config_hash) | ♻️ (Axis 9) |

---

## 6. Risk Center ♻️ / 🆕

Expand the single exposure bar into a risk surface:

| Metric | Readiness |
|---|---|
| Exposure vs cap | ♻️ |
| Open risk | ♻️ (`live_portfolio_state`) |
| Portfolio heat | ➕ |
| Correlation heatmap | ➕ (`correlation_engine` data → viz) |
| Daily risk budget | 🆕 (already a v0.6 roadmap item) |
| Weekly risk budget | 🆕 |

The daily/weekly budgets are the same control proposed in `ROADMAP.md` v0.6
risk-hardening — the dashboard is where they surface.

---

## 7. Research Status ♻️

Nearly complete already; needs a dedicated summary card, not new backend.

| Metric | Readiness |
|---|---|
| Last successful experiment | ♻️ (`/research`) |
| Experiment count | ♻️ |
| Reproducibility % | ♻️ (`/research/integrity` — currently 7/16) |
| Manifest health | ♻️ |
| Dataset version | ♻️ (provenance `data_versions`) |

---

## 8. Platform Readiness matrix 🆕

A maturity rubric rolling the above into one glance. Each domain rated
✅ ready / ⚠ forming / ⛔ blocked against explicit, versioned criteria (not vibe):

| Domain | Basis |
|---|---|
| Infrastructure | services, backups, uptime |
| Data | provider success, starvation, coverage |
| Research | reproducibility, integrity checks |
| Execution | reconciliation clean, fills tracked |
| Risk | limits enforced, budgets defined |
| Monitoring | audit/alerts live |
| AI | explanation layer availability |

This is the operator-facing companion to the engine maturity model.

---

## Cross-cutting: diagnostic error taxonomy 🆕

Replace generic panel errors ("Provider error", "News Read: error") with a
typed reason so the operator knows *where* to look:

- `PROVIDER_UNAVAILABLE` — upstream host unreachable / 5xx
- `AUTH_FAILED` — 401/403 (bad or missing key)
- `RATE_LIMITED` — 429 / quota exhausted
- `TIMEOUT` — request exceeded budget
- `BAD_FORMAT` — unexpected/unparseable response
- `AI_PROVIDER_ERROR` — the AI layer failed (distinct from data providers)

Every AI/data panel already returns `status: ok|disabled|error`; extend `error`
with one of these codes. Note: the **economic calendar** panel is already fixed
(keyless Forex Factory, JBlanked removed) — the remaining "News Read" error is
the **AI/news-briefing** layer (`/ai/news-analysis`), a separate surface that
this taxonomy will disambiguate.

---

## Sequencing (cheapest, highest-impact first)

1. **Executive Overview + Research Status** — almost pure synthesis of existing
   endpoints. Highest operator value per unit of work.
2. **Diagnostic error taxonomy** — small, immediately clarifies real failures.
3. **Evidence Progress statistics** (CI, PF, expectancy) with the n<30 guard.
4. **Decision Quality + Risk Center** rollups.
5. **Data Health per-provider** and **Engine precision/recall/drift** — the new
   measurement/storage work; sequence last, behind the data-hardening in v0.6.

No threshold or engine-set change is implied by any of this — the Command
Center observes and organizes; it never alters a verdict.
