# Algorithm & Control Inventory

**Document type:** Algorithmic trading register, prepared in the style of
RTS 6 Article 5 (Commission Delegated Regulation (EU) 2017/589) and the UK
PRA's SS5/18 algorithmic-trading expectations.
**System:** IATIS (research/paper-trading platform)
**Prepared for:** external regulatory / compliance review
**Machine-readable equivalent:** `GET /research/algorithm-inventory`
(this document and that endpoint are generated from the same source files —
see [Keeping this document current](#keeping-this-document-current))

---

## 1. Purpose and scope

This register lists every algorithm in IATIS that participates in a live
trading decision, together with every algorithm that exists in the
codebase but is **not** currently eligible to participate in one. For
each algorithm it states: what it does, on what basis it was approved to
run, whether it is currently active, and whether it is currently
permitted to commit real capital.

The register is deliberately narrower than "every piece of code in the
repository" — it covers the **12 decision-generating algorithms**
("engines") that vote on whether a trade should be opened, plus the rules
that combine their votes into one decision. It does not cover data
ingestion, execution mechanics, or the pre-trade hard-limit control layer;
those are documented separately (see [§7 Related controls](#7-related-controls)).

## 2. How an algorithm is approved to run

No algorithm may be activated by simply editing a configuration file. Every
activation is checked in code, at every system start, by
`research/edge_gate.py::check_edge_gate()`:

1. **A hypothesis must be registered.** Every algorithm key that appears in
   the system's engine configuration is mapped to a specific, named
   hypothesis record (e.g. `H101`, `H004`) held in
   `research/results/registry.json`. An algorithm with no registered
   hypothesis cannot be activated — the system refuses to start
   (`EdgeNotProvenError`) rather than silently running an undocumented
   algorithm.
2. **The hypothesis must carry one of two statuses:**
   - **`RESEARCH`** — approved for paper trading / demo-account data
     collection only. Explicitly **not** eligible for live (non-demo)
     capital.
   - **`PASSED`** — a proven edge on real data, evidenced against a
     codified bar (below). Only a `PASSED` hypothesis with qualifying
     evidence may ever receive live capital.
3. **The `PASSED` promotion bar is code, not opinion.** A hypothesis is
   only trusted as genuinely `PASSED` when its evidence record shows all
   of:

   | Criterion | Threshold |
   |---|---|
   | Out-of-sample trade count | ≥ 300 |
   | Out-of-sample profit factor | ≥ 1.2 |
   | Walk-forward validation | present |
   | Monte Carlo validation | present |

   A hypothesis marked `PASSED` without meeting this bar is treated
   identically to `RESEARCH` by the system — it may run, it may not
   receive live capital.
4. **Live capital is a second, independent gate.** Even a fully `PASSED`
   algorithm cannot commit live (non-demo) capital unless the system's
   `execution.allow_live_trading` configuration flag is explicitly set to
   `true`. **As of this document, that flag is `false`.**

**Bottom line, stated plainly: every algorithm currently enabled in this
system carries `RESEARCH`-status hypotheses, not `PASSED` ones.** No
algorithm in IATIS is currently eligible for live, non-demo capital under
its own governance rules, independent of the `allow_live_trading` flag.
The system's own risk-control layer (a separate, code-enforced pre-trade
validation authority — see §7) sits in front of any order regardless of
this status.

## 3. How votes combine into one decision

An individual algorithm never places a trade on its own. Every enabled
algorithm's output is a **bias** (bullish / bearish / neutral) and a
**score** (0–100, confidence); these are combined by a separate consensus
layer before any order is even considered:

| Rule | Current value | Effect |
|---|---|---|
| Minimum engines agreeing | **2** | At least 2 enabled algorithms must vote the same direction |
| Minimum confluence score | **58** | The combined, weight-adjusted score must clear this floor |
| Minimum informative weight share | **60%** | At least 60% of the enabled panel's total weight must be casting an actual (non-neutral) opinion, so a 2-of-4 quorum can't be satisfied by two engines while the rest of the panel is silently mute |

Each algorithm's vote is weighted (see the register below); an algorithm
that is not enabled contributes nothing and is not counted toward quorum.

## 4. Register summary

12 algorithms exist in the codebase today: **10 base algorithms**, of
which **4 are currently active** (the "prod4" set), and **2 research-only
variants** that cannot reach a live decision under any configuration
without a code change.

| # | Algorithm | Status | Weight | Version | Approval basis |
|---|---|---|---|---|---|
| 1 | Smart Money Concepts (SMC) | 🟢 Active | 0.2020 | 1.0 | `H101` — RESEARCH |
| 2 | Price Action | 🟢 Active | 0.1869 | 1.0 | `H102` — RESEARCH |
| 3 | NNFX (No-Nonsense-Forex) | 🟢 Active | 0.2273 | 1.0 | `H004` — RESEARCH |
| 4 | Wyckoff | 🟢 Active | 0.0707 | 1.0 | `H006` — RESEARCH |
| 5 | ICT (Inner Circle Trader) | ⚪ Registered, disabled | 0.0657 | 1.1 | `H003` — RESEARCH |
| 6 | Market Structure (BOS/CHoCH/MSS) | ⚪ Registered, disabled | 0.0859 | 1.2 | `H011` — RESEARCH |
| 7 | Divergence (RSI/MACD) | ⚪ Registered, disabled | 0.0606 | 2.1 | `H010` — RESEARCH |
| 8 | Quant (statistical/regime) | ⚪ Registered, disabled | 0.0707 | 2.1 | `H005` — RESEARCH |
| 9 | Macro (DXY/risk-on-off) | ⚪ Registered, disabled | 0.0000 | 2.1 | `H007` — RESEARCH |
| 10 | Sentiment (COT/retail) | ⚪ Registered, disabled | 0.0303 | 1.1 | `H012` — RESEARCH |
| 11 | Price Action v2 | 🔵 Research-only variant | shares #2's weight if ever activated | 2.0 | Not registered — see §4a |
| 12 | Wyckoff v2 | 🔵 Research-only variant | shares #4's weight if ever activated | 2.0 | Not registered — see §4a |

🟢 = currently enabled and casting live votes · ⚪ = defined in code, has a
registered hypothesis, but not currently enabled · 🔵 = exists only as an
ad-hoc research tool; structurally cannot reach a live decision

**Prod4** (rows 1–4) is the frozen set of algorithms this system currently
runs. It was arrived at by burden-of-proof: a prior, closed research
program tested enabling additional algorithms twice and found every
addition diluted results and that subset selection was noise driven by the
specific test universe rather than signal (`H015`, closed). Adding any
algorithm back requires a fresh, pre-registered hypothesis and evidence —
not a configuration change alone.

### 4a. Why the two "v2" variants can never reach a live decision

`price_action_v2` and `wyckoff_v2` are real, tested implementations that
exist in the codebase as research tools — they are **not** placeholder or
unfinished code. They are structurally isolated from live trading by two
independent facts, both true simultaneously:

1. They do not appear in the live decision pipeline's own list of
   constructible algorithms (`main.py`'s engine registry) — the running
   system has no code path that can instantiate them during a live
   decision, full stop.
2. They have no entry in the engine-activation configuration file at all,
   so even the algorithm-approval gate described in §2 has nothing to
   check them against — an engineer could not accidentally "enable" them
   the way an existing, registered algorithm might be toggled on.

The only way either variant runs is through a separate research tool
("Mission Center") that evaluates them on historical data, in memory, for
a single research session — and that tool is contractually incapable of
writing its selection back into the live configuration file. Activating
either variant for live trading would require a source-code change (adding
it to the live registry) **and** a new pre-registered hypothesis under §2
— never a configuration-only change.

## 5. Detailed entries

### 5.1 Currently active (prod4)

**Smart Money Concepts (SMC)** — `H101`, RESEARCH
Determines directional bias from swing-point structure: is the market
making a sequence of higher highs/higher lows (bullish structure) or lower
highs/lower lows (bearish structure)? An optional, currently disabled
"full-spec" mode adds order-block, fair-value-gap, and break-of-structure
detection as additional internal inputs; that mode failed its own
validation test (`H017`) and stays off. 15 tunable parameters.

**Price Action** — `H102`, RESEARCH
Reads candlestick patterns, RSI momentum, and Bollinger Band position.
Deliberately uses a different indicator set than NNFX after measurement
showed the two were 97.5% correlated (redundant) under an earlier design.
26 tunable parameters.

**NNFX (No-Nonsense-Forex)** — `H004`, RESEARCH
A layered-confirmation methodology: a 200-period moving average sets
baseline trend direction, the Average Directional Index (ADX) confirms
trend strength, and Average True Range sizes the acceptable stop
distance. Uses only price/volume data available on every symbol this
system trades. 18 tunable parameters.

**Wyckoff** — `H006`, RESEARCH
Reads price/volume relationship for signs of accumulation or
distribution by large participants ("Composite Operator" theory) —
spring/upthrust reversal patterns, position within a trading range, and
stopping-volume signatures. Runs price-only on FX (no reliable volume
data exists for FX), with full volume analysis on metals, indices, and
crypto. 15 tunable parameters.

### 5.2 Registered but disabled

These six algorithms are fully implemented, carry a registered
hypothesis, and pass the approval gate in §2 as `RESEARCH` status — but
are **not enabled** in the live configuration and cast no votes today.

**ICT (Inner Circle Trader) concepts** — `H003`, RESEARCH. Killzone
session-timing bias, premium/discount position within the recent trading
range, and false-breakout ("Judas swing") detection.

**Market Structure (BOS/CHoCH/MSS)** — `H011`, RESEARCH. A more granular
structural-shift detector than SMC: Break of Structure (trend
continuation), Change of Character (first reversal sign), and Market
Structure Shift (confirmed reversal), evaluated across two timeframes.

**Divergence (RSI/MACD)** — `H010`, RESEARCH. Detects when price makes a
new high/low but a momentum indicator (RSI or MACD) does not confirm it —
a classic reversal warning — with triple-confirmation and
multi-timeframe checks.

**Quant (statistical/regime)** — `H005`, RESEARCH. Classifies the current
market regime (trending / mean-reverting / random / unknown) using a vote
across seven independent statistical measures (Hurst exponent, variance
ratio, stationarity test, autocorrelation, efficiency ratio, half-life,
entropy), then selects which signal family to trust based on that
classification.

**Macro (dollar strength / risk sentiment)** — `H007`, RESEARCH. Analyzes
broad market context rather than any one symbol's price: dollar strength,
risk-on/risk-off appetite (equities/VIX/gold/yield-curve/credit-spread/
central-bank balance sheet). Its confluence weight is fixed at **0.0000**
— even if enabled, it currently cannot influence the combined score.

**Sentiment (COT / retail positioning)** — `H012`, RESEARCH. Primary
signal: weekly CFTC Commitments-of-Traders large-speculator positioning
data. Falls back to a retail-positioning proxy derived from price
position within its recent range when COT data is unavailable.

### 5.3 Research-only variants

**Price Action v2** — not registered (see §4a). A from-scratch redesign
using only bar-shape/structure patterns — explicitly excludes the RSI and
Bollinger Band inputs the original Price Action algorithm uses. Reachable
only through the offline research tool described in §4a.

**Wyckoff v2** — not registered (see §4a). Extends the original Wyckoff
algorithm's proven logic (reused, not replaced) with a fuller reconstruction
of the classical Wyckoff schematic: climax, automatic rally/reaction,
secondary test, and sign-of-strength/weakness phases. Reachable only
through the offline research tool described in §4a.

## 6. Live-capital status

As of this document:

| Control | Value | Meaning |
|---|---|---|
| `execution.ctrader_enabled` | `true` | The system connects to a live broker session |
| `execution.dry_run` | `false` | Orders are actually submitted to that session |
| `execution.allow_live_trading` | **`false`** | The broker session is a **demo account only** — this flag is the sole gate for non-demo capital, and it is off |

In plain terms: the system currently places real orders **on a demo
account**. No algorithm in this register — including the four active
ones — is currently eligible for non-demo capital, both because
`allow_live_trading` is `false` and because none carries a `PASSED`
hypothesis under §2's own bar.

## 7. Related controls

This register covers algorithm approval and combination only. Two
adjacent, independently documented controls sit between any algorithm's
vote and a real broker order:

- **Sovereign risk gate & kill switch** (`risk/risk_engine.py`,
  `storage/kill_switch.py`) — evaluated before any order is even
  constructed.
- **Pre-trade hard limits** (`risk/pretrade_limits.py`) — a deterministic,
  fail-closed authority checking 17 independent limits (notional caps,
  position/portfolio exposure, price collar, stop-loss validity, stale-data
  rejection, duplicate-order protection, and more) immediately before
  broker submission, structurally impossible to bypass. See the delivery
  report for that control for its own full documentation and test
  evidence.

## Keeping this document current

This document is a point-in-time snapshot. The authoritative, always-current
version of the same data is served by `GET /research/algorithm-inventory`
(`research/algorithm_inventory.py::build_algorithm_inventory()`), which is
computed fresh from the same source files this document was written from —
`config/engines.yaml`, `config.yaml`'s confluence rules, and
`research/results/registry.json` — every time it is called. If a number in
this document and the live endpoint's response ever disagree, the live
endpoint is correct; this document should be regenerated from it.
