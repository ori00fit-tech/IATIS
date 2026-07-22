# CLAUDE.md — Institutional Memory & Non-Negotiables

Read this before changing ANYTHING. It exists because this project's value
is not its code — it is its measured evidence and the discipline that
produced it. Multiple AI sessions and humans work on this repo in
parallel; this file is how they inherit the rules instead of re-deriving
(or accidentally demolishing) them.

## What this system is — one paragraph

IATIS is a research/paper-trading platform whose *measured* edge is
**disciplined trend-capture on carrier assets (XAUUSD, BTCUSD, ETHUSD) at
H4 with D1 confirmation and hard risk rules (RR ≥ 2, ATR stops, fractional
sizing)**. The FX book is statistically indistinguishable from breakeven
(three independent confirmations). Everything else — 9 engines, confluence
scoring, 7 gates — is packaging whose marginal value was measured at ≈ 0.
Full verdicts: `docs/PHILOSOPHY_AUDIT_2026-07.md` (+ live addendum),
`docs/PRODUCTION_AUDIT_2026-07.md`, `docs/STRATEGY_EVIDENCE_2026-07.md`.

## The evidence rules (non-negotiable)

1. **Pre-register before you build.** A hypothesis entry in
   `research/results/registry.json` with a decision rule written BEFORE
   any result exists. H017 and H015 died honestly because their rules
   pre-existed their data. No exceptions, no EXEMPT labels for new work.
2. **Chronological OOS or it didn't happen.** Train-slice improvements are
   presumed mirages (H008b: 63.6% train → 35.5% test; H015 3-symbol ADOPT
   → died at 15 symbols). The H008c method is the house standard.
3. **The promotion bar is code**, not prose: `research/edge_gate.py`
   `PROMOTION_CRITERIA` (≥300 OOS trades, OOS PF ≥ 1.2, walk-forward,
   Monte Carlo). A PASSED without qualifying evidence gets flagged at
   every boot and must be treated as RESEARCH.
4. **Negative results get committed** with the same care as positive ones
   (registry + `docs/STRATEGY_EVIDENCE_2026-07.md` rejected ledger +
   result JSON + manifest). The manifest must come from a clean tree
   (`scripts/revive_manifests.py` refuses otherwise).
5. **Live decisions follow pre-registered rules**, applied by
   `scripts/forward_review.py`, never invented at read time:
   - **D001**: forward FX PF < 1.0 at n ≥ 40 → cut FX, carriers-only.
   - **D002**: carriers PF ≥ 1.2 at n ≥ 100 → live-capital discussion may open.
6. **Never change entries/exits/thresholds mid-sample.** The forward demo
   counter is the only prospective evidence; altering the system resets it.
   H018 (structural stops) is pre-registered and FROZEN until ~100 closed
   demo trades exist.

## The dead list — measured and buried, do NOT rebuild

| Idea | Killed by | Verdict |
|---|---|---|
| Liquidity sweeps / equal highs-lows as entries | H001/H002/H002b | p=0.63/0.22/0.43 |
| BOS+FVG as entries | H008/H008b/H008c | pooled OOS WR 0.489, p=0.83 |
| SMC full-spec as internal confluence | H017 | TEST ΔPF −0.04, 3/4 symbols worse |
| Enabling more engines (any) | engine_activation + H015 (twice) | every addition dilutes; subset selection is universe-dependent noise |
| Volume/order-flow inputs on current engines | crypto_volume A/B | ΔPF −0.016/0.000 |
| Pairs trading / stat-arb | pairs_trading | 0 of 105 pairs profitable OOS |
| Managed exits (partial TP/BE/trail) | trade-management A/B | +100% was a look-ahead artifact; OOS ≈ 0 |
| Currency-strength indices | measured | PF 0.90/0.49 |
| ICT pattern folklore (Judas, PO3, OTE…) | same concepts as above | untestable variants of buried ideas |
| Complexity/entropy "compression" as a predictability signal | H025 stage 1 | pooled ratio 1.005 (needed ≥1.10), p=0.31; ≈1.0 even inside the mid-ATR tercile |
| Hard regime gate (block RANGING, trade only TRENDING) | H024 | TEST ΔPF −0.024; B>A in 42% of symbols; carriers PF 1.335→1.256 |

If someone (including the operator) asks to rebuild any of these: point at
this table, offer a NEW pre-registered hypothesis if they insist, and let
the OOS split do the refusing.

## Current frozen state

- **Engines enabled**: smc, price_action, nnfx, wyckoff (prod4). Kept by
  burden-of-proof (H015 closed: no robust alternative exists). The only
  stable facts: nnfx + price_action are load-bearing; smc is mildly
  negative in-sample but no alternative survived adoption rules.
- **Thresholds** (min_score 55–60, quorum 2, info-share 0.5, RR/ATR):
  untouched until the shadow book (`GET /shadow-book`,
  `storage/shadow_book.py`) reaches n≈50 per gate — then calibrate from
  its ledger, not from opinion.
- **`engines.smc_full_spec`: false** (H017 FAILED). Detectors stay in the
  codebase for future re-tests only.
- **`features.regime_gate`: false** (H024 NULL, 2026-07-22 — hard gate
  immaterial pooled, strictly worse on carriers; soft regime weighting
  stays).
- **Swap model ships OFF** (`data/swap_rates.json` all zeros). Filling it
  with real cTrader rates and re-running `h4_yearly_stability` is a
  pre-registered check on the FX book.

## Ops runbook (and this session's recurring pitfalls)

- **Deploy** (until non-root migration): `cd /root/IATIS && git pull
  origin main` (after PR merge) or `git pull origin
  <branch> --no-rebase`, then — **as its own line, never glued to a
  comment** (it silently didn't run twice this way):
  `sudo systemctl restart iatis-scheduler iatis-api`
- After `scripts/setup_service_user.sh`: everything moves to
  **/opt/iatis** under user `iatis`; update habits accordingly.
- Frontend changes need `cd dashboard/frontend && npm install && npm run
  build` on the VPS (dist/ is gitignored).
- **Never `rm` tracked files** to fix a pull (it happened; it blocked the
  revival runner). Untracked-file collisions on pull: the repo copy is
  authoritative → `rm` the *untracked* local one only if contents match.
- Timers: `iatis-watchdog.timer` (10 min), `iatis-backup.timer`
  (04:10 UTC). COT cron Saturdays: `scripts/download_cot.py`.
- **Secrets live in `.env` only. Never paste them into a chat, an issue,
  or a commit** — it happened twice and forced rotations. If a secret
  appears anywhere outside `.env`, rotate it the same day.

## The measurement toolbox (use these before writing anything new)

| Question | Tool |
|---|---|
| Is the live system behaving per its philosophy? | `scripts/philosophy_audit.py` (29 checks; also the dashboard's System Audit tab) |
| Are all symbols/engines/data healthy right now? | `scripts/full_system_check.py` |
| Have the pre-registered forward rules triggered? | `scripts/forward_review.py` |
| What are the gates costing? | `GET /shadow-book` (gate ledger) |
| Is an old result manifest stale? | `scripts/revive_manifests.py` |
| Would an SMC/engine change help? | It was measured. See the dead list. |

## Data layer (asset-class chains, native-timeframe aware)

crypto → ccxt/Binance (native H4/D1) · fx/metals/energy/indices → cTrader
broker feed when credentials exist → Twelve Data → FCS API (fx/metals/
indices only) → AV (fx only) → Finnhub. **Yahoo was REMOVED entirely as an
untrusted feed** — from every price chain (2026-07-16: measured wrong
instruments — ^IXIC≠NDX, futures≠spot metals — cash-session gaps, and its
"H4" a 1h resample) and from the macro layer (2026-07-17: replaced by
CBOE/FRED). `load_from_yfinance`/`_fetch_yahoo_finance` survive only for
offline research downloads and the failover unit tests; nothing live calls
them. Macro: CBOE (VIX) / FRED (dollar DTWEXBGS, SPY SP500, GLD LBMA gold,
yields DGS10/DGS2) / CFTC (COT) — official sources only, no Yahoo fallback.
Chains:
`config.yaml data.provider_chains`; per-decision provenance in every
report's `data_providers`. NNFX needs ≥210 decision-TF bars and the MTF
gate ≥50 D1 bars — `main.py` logs `DATA STARVATION` loudly if violated;
never tune weights around a starved engine.
