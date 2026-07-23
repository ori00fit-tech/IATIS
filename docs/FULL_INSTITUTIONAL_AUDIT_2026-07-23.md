# IATIS — Full Institutional Audit (2026-07-23)

**Auditor posture:** adversarial. The mission is to disprove, not approve. Every
claim below is either verified directly against code/tests/config in this
session, or explicitly marked as carried from a prior audit and re-checked.
Where a prior audit's finding was re-verified and still holds, this document
says so and does not re-derive it at length — re-deriving already-committed,
already-verified findings would violate this repo's own "audit first, never
duplicate working code" rule (`MISSION_CONTROL_AUDIT.md`).

**Method:** repository inspection at the tip of `main` merged into
`claude/iatis-full-audit-350sic` (commit `a553e6f`); fresh test run (982
tests, 981 passed / 1 skipped, this session, in a clean venv); fresh `ruff`
and `pip-audit` runs; coverage measurement of the two highest-risk modules;
five parallel adversarial code-review passes over every module *not already
reviewed* by `docs/PRODUCTION_AUDIT_2026-07.md` (2026-07-05),
`docs/PHILOSOPHY_AUDIT_2026-07.md` (2026-07-09), or
`docs/INSTITUTIONAL_GAP_ANALYSIS_2026-07.md` (2026-07-16) — the trade
journal, the cTrader reconnect fix, `execution/api_server.py`'s growth to
3,482 lines, and the four newest hypotheses (H024/H025/H033/H037); and a
fact-check pass re-verifying every open item from those three prior audits
against current ground truth rather than trusting their prose.

**Relationship to prior audits:** this is audit #4 in 18 days on the same
repository. That cadence is itself evidence — of a team that takes
self-measurement seriously, and of a target that changes fast enough that a
security/ops finding from 2026-07-05 needs re-verification, not citation, by
2026-07-23. Two of the prior audits' own numbers are now **stale and
contradicted by the current repo**: `docs/PRODUCTION_AUDIT_2026-07.md:63`
says cTrader execution is "gated off... correct state" — it is not; `config.yaml:131-133`
shows `ctrader_enabled: true, dry_run: false` (real orders on the cTrader
*demo* account, live since 2026-07-06, `allow_live_trading: false` still
correctly gates real money). This is flagged explicitly below (P2) because
doc drift like this is exactly the failure mode an institutional reviewer
must not repeat: trusting a document instead of the config.

---

# Executive Summary

| Score | /100 | Basis |
|---|---|---|
| **Overall institutional score** | **63** | Up modestly from the 2026-07-16 gap analysis's 60 — TCA, decision provenance, and schema migrations (that audit's top three "must-have" gaps) are now genuinely shipped and verified in code this session. Held down by: zero evidence the 2026-07-05 credential leak was ever rotated (18 days on), live (demo) order execution through a module at 36% coverage against the project's own 60% precondition, and a newly-found latent duplicate-order risk. |
| Readiness score (could a firm run this tomorrow) | 35 | Research layer is ready; ops/security layer is not — see P0s. |
| Research score | 90 | Exceptional. Four new hypotheses independently re-derived from raw code this session (H024/H025/H033/H037) — all four hold up: real chronological OOS splits, real seeded bootstraps, no fabricated results, no premature PASSED status. |
| Engineering score | 72 (updated post-remediation) | 1083 green tests, CI-gated, hermetic; of the three flagged monoliths, `api_server.py` (3,530 lines → 138-line composition root + 17 focused modules) was resolved this session with an AST-diff safety net, not just tests; `main.py`'s CC-71 pipeline and dual backtest packages remain. |
| Security score | 45 | Code-level controls are genuinely strong (verified: no SQL injection anywhere, every new endpoint auth-gated, path-traversal defense in the new File Explorer actually resolves symlinks/`..` correctly) — but an unconfirmed 18-day-old credential leak and root-owned production services are disqualifying regardless of code quality. |
| Architecture score | 72 | Sound layered/hexagonal-shaped design, acyclic imports, AI provably isolated from the decision path — undercut only by the same three unaddressed monoliths. |
| Maintainability score | 68 | Strong docs (verified accurate against code repeatedly, including this session), CI now real; lint backlog grew 332→376 findings ungated beyond E9/F821. |
| Statistical validity score | 55 | The *process* (pre-registration, chronological OOS, codified promotion bar) is above institutional norm. The *product* is not there yet: FX book non-significant, carrier edge still in-sample/regime-exposed, forward-evidence n still far below the pre-registered D001 (≥40) / D002 (≥100) thresholds. |
| Deployment readiness | **FAIL** | No CI/CD gate beyond syntax+tests+vuln-scan (deliberately narrow, documented); no Docker; root services; unrehearsed backups. |
| Institutional readiness | **FAIL** | Same three blockers as 2026-07-05: unconfirmed credential rotation, no live P&L track record, ops hardening incomplete. |

**One-paragraph verdict:** IATIS's research discipline continues to be
genuinely rare — this session independently re-verified four hypotheses from
raw code and found zero instances of the discipline slipping. But the system
has, since the last audit, started placing real orders on a live demo account
through its least-tested module, and the most severe finding of any of the
three prior audits — a full production credential set leaked to an external
chat — has an evidence trail of exactly zero rotation confirmations 18 days
later. An institution does not get to point at excellent research process as
a substitute for closing a known credential exposure. Fix the four P0s below
(all cheap, all ops/discipline items, none require new architecture), and the
score moves fast; until then, this stays demo-only.

---

# Critical Issues (P0)

### P0-1 — Credential rotation from the 2026-07-05 leak has no evidence of ever happening

- **Problem:** The full production `.env` (data-provider keys, Telegram bot
  token, API server key, cTrader OAuth tokens, Cloudflare API token, D1 proxy
  token) plus a root SSH identity were pasted into an external AI chat on
  2026-07-05 (`docs/PRODUCTION_AUDIT_2026-07.md` Phase 8). A closure runbook
  (`docs/OPS_CLOSURE_RUNBOOK.md`) was created 2026-07-16 specifically to
  record rotation. Its closure record (`rotated_on:`, `rotated_by:`,
  `keys_rotated:`, `ssh_reviewed:`) is still the literal blank template today.
- **Evidence:** `git log --follow -p -- docs/OPS_CLOSURE_RUNBOOK.md` shows
  exactly two commits ever touched the file — creation, and one unrelated
  edit removing a decommissioned provider from the key list. Neither wrote a
  real value into the closure record. This audit's own re-run of `pip-audit`
  and code review found no *code-level* secret exposure — this is purely an
  operational confirmation gap, but it is the single most severe unresolved
  item in the repository's own audit history.
- **Impact / Risk:** Every credential in that leak (Cloudflare API token can
  reassign DNS/Workers/D1; D1 proxy token executes arbitrary SQL against the
  production database; API server key grants full dashboard/API control
  including the Experiment Runner's subprocess execution) must be treated as
  live-compromised until proven otherwise. 18 days is long enough that "we
  forgot" and "we did it and forgot to write it down" are indistinguishable
  from the outside — which is precisely why the runbook pattern (a filled
  closure record or it didn't happen) exists.
- **Root cause:** A process gap, not a technical one — the runbook was
  written but nothing forces anyone to close it, and no CI/monitoring check
  verifies key freshness.
- **Recommendation:** Rotate all seven credential classes today, in the
  order the production audit specified (Cloudflare API token first — highest
  blast radius). Fill the closure record in the same commit as evidence.
  Add a `scripts/check_key_age.py` (or a `philosophy_audit.py` axis) that
  reads the closure record's `rotated_on` date and WARNs at boot past 90
  days, so this can't silently go stale again.
- **Files:** `docs/OPS_CLOSURE_RUNBOOK.md`, `.env` (VPS-only, not in repo).
- **Effort:** 2–3 hours. **Priority:** Do first, before anything else in this report.

### P0-2 — Live (demo) order-execution module runs below its own declared safety precondition

- **Problem:** `config.yaml:131-133` has had `ctrader_enabled: true` /
  `dry_run: false` since 2026-07-06 — real orders are placed on the cTrader
  demo account on every scheduler tick. The production audit's own stated
  precondition (M5) was: reach ≥60% test coverage on `execution/ctrader_client.py`
  *before* enabling it. Measured fresh this session:
  `pytest --cov=execution.ctrader_client` → **36%** (up from 24% at the July
  audit, still well under the 60% bar the project itself set). The gate was
  never honored; the module has been live for 17 days without ever meeting
  its own precondition.
- **Evidence:** `execution/ctrader_client.py` — 1,551 lines, 832 statements,
  530 uncovered (this session's coverage run); `config.yaml:131-133`.
- **Impact / Risk:** This is the module that talks to the broker. Untested
  paths in a live-order module are untested paths in the exact place where a
  bug produces a real (if demo-account) fill, corrupting the forward-evidence
  counter the entire philosophy audit says is "the only thing that will
  prove the edge prospectively" (`docs/STRATEGY_EVIDENCE_2026-07.md`).
  Corrupting that sample doesn't just risk money — on this project's own
  terms it *resets the one experiment that matters*.
- **Root cause:** The M5 precondition was written but never wired to a gate
  (nothing in CI or `edge_gate.py` checks coverage-before-enable the way
  `edge_gate.py` checks hypothesis-before-engine-enable).
- **Recommendation:** Either (a) raise `ctrader_client.py` coverage to ≥60%
  before the next demo-trading session continues, prioritizing the 530
  uncovered lines this session identified (reconnect/login state machine,
  order submission, reconciliation), or (b) if the operator judges the demo
  risk acceptable in the interim, document that explicitly as an accepted
  exception with a deadline — but the current state (silent non-compliance
  with a self-declared gate) is the wrong one either way.
- **Files:** `execution/ctrader_client.py`, `tests/test_ctrader_client.py`,
  `tests/test_ctrader_execution_logic.py`.
- **Effort:** 1 week (per the original M5 estimate). **Priority:** Before the
  next accumulation phase of forward-evidence trades, ideally immediately.

### P0-3 — Latent cross-process duplicate-order risk introduced by the ALREADY_LOGGED_IN fix

- **Problem:** Commit `7c0400b` (2026-07-22, this session's newest reviewed
  commit) fixes a reconnect storm by treating the broker's `ALREADY_LOGGED_IN`
  error as benign and continuing the connection bootstrap
  (`ctrader_client.py:750-766`, `:858-870`). The fix is narrow and correct
  for the *single-process* case (Twisted's reactor serializes callbacks, so
  the guards added at `:321-330`/`:505-508` genuinely can't race within one
  process). But the code comment justifying the fix (`:745-746`) argues that
  *if* the login is genuinely a conflicting duplicate session, "the next step
  fails with a real error" — this is asserted, not verified. If the broker
  in fact tolerates two authenticated sessions on the same account
  concurrently (which is exactly the scenario `ALREADY_LOGGED_IN` implies is
  possible), a stale/zombie process — e.g. a systemd restart that doesn't
  fully kill the old process, or an operator running a second instance by
  mistake — could stay live alongside the new one, and **both could submit
  real orders for the same account** on the same signal.
- **Evidence:** `execution/ctrader_client.py:745-766`; existing
  same-account-multiple-client guards
  (`execution/trade_executor.py:103-118`, `execution/reconciliation.py:100-107`)
  are scoped to *within one Python process* (`get_shared_ctrader_client()`
  singleton) — nothing detects or prevents a second OS process. No test
  exercises this scenario; the 5 new regression tests added with this commit
  (`tests/test_ctrader_execution_logic.py:405-479`) all mock the handler
  methods directly and do not exercise a real reactor or a genuine duplicate
  process.
- **Impact / Risk:** Duplicate real (demo, currently) order submission —
  doubled position size, doubled risk exposure beyond the configured
  fractional-risk limits, and a corrupted forward-evidence trade record
  (two "trades" for one signal). If this pattern is still in place whenever
  `allow_live_trading` is ever flipped, the consequence becomes real-money
  double-exposure.
- **Root cause:** The fix correctly solved the reconnect-storm symptom but
  did not add a positive verification (e.g. checking `ProtoOAReconcileReq`
  results for unexpected pre-existing positions before submitting a new
  order, or a startup check that no other process holds the account) for the
  duplicate-session hypothesis it explicitly raises and dismisses in its own
  comment.
- **Recommendation:** Add an explicit reconciliation check on every
  (re)connect that compares expected vs. actual open positions before the
  first new order is submitted post-reconnect, and a startup-time PID/lock
  file (or broker-side single-session enforcement, if the API exposes it) to
  prevent two OS processes from holding the same account concurrently. Add a
  regression test that simulates two connections authenticating for the same
  account and asserts only one is permitted to submit orders.
- **Files:** `execution/ctrader_client.py`, `execution/reconciliation.py`,
  `execution/trade_executor.py`.
- **Effort:** 1–2 days. **Priority:** Before the next production deploy that
  includes commit `7c0400b`'s change (it's already merged to main, so this
  is closing a gap in a live path, not blocking a future one).

### P0-4 — Non-root service migration still unexecuted three weeks after being flagged

- **Problem:** All five systemd units (`iatis-api`, `iatis-scheduler`,
  `iatis-watchdog`, `iatis-backup`, `iatis-d1-backup`) still run `User=root`.
  `scripts/setup_service_user.sh` has existed and been actively maintained
  since 2026-07-13 (6 commits) but the runbook's closure record for this item
  is, like P0-1, still blank.
- **Evidence:** `grep '^User=' *.service` → `root` in all five files, this
  session. `docs/OPS_CLOSURE_RUNBOOK.md` item 2 closure record unfilled.
- **Impact / Risk:** Combined with P0-1 (possibly-still-compromised
  credentials) and P0-2/P0-3 (a live-order module with known gaps), a root
  process is the highest-blast-radius configuration available — any RCE
  anywhere in the stack (dashboard, Experiment Runner subprocess execution,
  a future dependency vulnerability) is instant full-VPS compromise instead
  of a contained `iatis` user's worth of damage.
- **Root cause:** Same as P0-1 — a written, tested, ready remediation with
  no forcing function to actually execute it.
- **Recommendation:** Run `sudo bash scripts/setup_service_user.sh` on the
  VPS, verify per the runbook's own checklist, fill the closure record.
- **Files:** `iatis-*.service`, `scripts/setup_service_user.sh`,
  `docs/OPS_CLOSURE_RUNBOOK.md`.
- **Effort:** 3 hours (per the runbook's own estimate). **Priority:** This week.

---

# High Priority (P1)

### P1-1 — `EXEMPT_ENGINES` loophole still bypasses the edge gate for two live-trading engines

- **Problem:** `research/edge_gate.py:35` — `EXEMPT_ENGINES = {"smc", "price_action"}`
  — both currently enabled per `config/engines.yaml`, both skip the "no
  engine without a hypothesis" invariant entirely. The philosophy audit
  called this "a loophole in the system's central scientific control" and
  noted LOO evidence that removing SMC *improves* PF on 7/10 symbols. CLAUDE.md
  bans *new* EXEMPT labels but the existing two were never closed.
- **Evidence:** `research/edge_gate.py:35,117`; `ENGINE_HYPOTHESIS_MAP`
  (`edge_gate.py:39-48`) has no entry for either engine.
- **Impact:** Two of the four production engines run permanently outside the
  system's own governance control — the exact mechanism CLAUDE.md's rule 1
  ("no exceptions, no EXEMPT labels for new work") exists to prevent, just
  grandfathered.
- **Recommendation:** Either write a real (even minimal) hypothesis entry
  for each — the LOO/H015 evidence already collected is enough to write an
  honest RESEARCH-status entry today — or remove them from `EXEMPT_ENGINES`
  and accept the boot-time warning `edge_gate.py` already produces for
  unproven-but-enabled engines, matching how H009/H013 are already handled.
- **Files:** `research/edge_gate.py`, `research/results/registry.json`.
- **Effort:** 2–4 hours (writing two RESEARCH-status hypothesis entries, no code change required). **Priority:** P1.

### P1-2 — Indices/oil symbols remain enabled with zero supporting evidence

- **Problem:** `config/symbols.yaml` still has `US30`, `NAS100`, `SPX500`,
  `USOIL` all `enabled: true`, `status: ACTIVE`, with `status_reason:
  "No individual disqualifying measurement"` — the exact absence-of-evidence-
  as-permission pattern the philosophy audit named explicitly ("US30/NAS100/
  SPX500 appear in no result manifest... yet it is enabled: true").
- **Evidence:** `config/symbols.yaml` (verified this session via direct YAML
  parse); `docs/PHILOSOPHY_AUDIT_2026-07.md` §8.
- **Impact:** These four symbols consume forward-evidence sample size and
  demo-account risk budget for zero measured reason, and dilute any
  eventual read of the D001/D002 forward rules (which are pooled/aggregate,
  not per-symbol) with unvalidated noise.
- **Recommendation:** Either run the same backtest discipline already
  applied to the FX/carrier book against these four (cheap — the harness
  exists), or move their `status` to `WATCHLIST`/disable pending evidence,
  consistent with how AUDUSD/USDCAD/NZDUSD/EURGBP/EURCHF were already
  handled.
- **Files:** `config/symbols.yaml`.
- **Effort:** 1 day (backtest run) or 10 minutes (disable). **Priority:** P1.

### P1-3 — Experiment Runner's job timeout is not actually enforced

- **Problem:** `execution/api_server.py`'s `_run_job` (~line 1637) only
  checks the timeout *inside* a blocking `for line in proc.stdout:` loop.
  The child process is not given `PYTHONUNBUFFERED=1`, so a `python3 -m ...`
  job with block-buffered (non-TTY) stdout can produce output infrequently
  enough that the timeout check never runs until a line finally arrives —
  the nominal "10-minute kill-timeout" is a soft check, not a hard one.
- **Evidence:** verified by dedicated agent review this session, line-cited
  above; only 2 executor threads exist in the pool.
- **Impact:** A single stuck job (network stall on `backup_d1`, a hung
  backtest) can occupy one of only two executor threads indefinitely, and
  because the job reports `status="running"` throughout, retries of the same
  job name 409 forever. With 2 threads total, two stuck jobs halve the
  Experiment Runner's usable capacity to zero.
- **Recommendation:** Pass `env={**os.environ, "PYTHONUNBUFFERED": "1"}` to
  the child `Popen` call and add a real wall-clock watchdog (a separate
  timer thread that calls `proc.kill()` after the timeout regardless of
  whether the read loop has returned), not just a check inside the loop.
- **Files:** `execution/api_server.py` (`_run_job`).
- **Effort:** 2 hours. **Priority:** P1.

### P1-4 — `scripts/repair_outcome_pips.py` mutates the forward-evidence ledger with zero test coverage

- **Problem:** This script directly rewrites `pnl_pips`/R-multiple values in
  the outcomes table CLAUDE.md calls "the only prospective evidence" for the
  entire philosophy. `tests/test_journal.py` covers the journal's own
  recompute logic but the repair script itself — its tolerance thresholds,
  idempotency, and dry-run/apply distinction — has no test at all.
- **Evidence:** confirmed via dedicated agent review; formula is verified
  byte-identical to `outcome_tracker.close_signal`'s (so the *math* is
  right), but nothing pins that identity going forward, and nothing tests
  the script's own control flow.
- **Impact:** A future edit to either the script or `close_signal` could
  silently diverge, and a bad `--apply` run against production data has no
  regression test standing between it and the load-bearing evidence ledger.
- **Recommendation:** Add tests for dry-run vs. apply, idempotency (running
  twice produces no further change), and a golden-value regression pinning
  the repair formula against `close_signal`'s formula so future edits to
  either must be made together deliberately.
- **Files:** `scripts/repair_outcome_pips.py`, new `tests/test_repair_outcome_pips.py`.
- **Effort:** 3–4 hours. **Priority:** P1.

### P1-5 — Backup restore rehearsal never performed

- **Problem:** `iatis-d1-backup.timer` has been exporting nightly since
  2026-07-13. No restore has ever been rehearsed (`docs/OPS_CLOSURE_RUNBOOK.md`
  item 3, unfilled, same evidence method as P0-1).
- **Impact:** "A backup is a hope until restored" (the runbook's own words).
  RTO is currently unknown; a real incident would be the first time the
  restore path is ever exercised.
- **Recommendation:** Follow the runbook's own checklist once — pick a dump,
  restore to a throwaway D1 database, row-count-check, record wall-clock
  time, delete. This is a 30–60 minute task with an existing script.
- **Files:** `docs/OPS_CLOSURE_RUNBOOK.md`, `scripts/backup_d1.sh`.
- **Effort:** 1 hour. **Priority:** P1.

### P1-6 — Swap/rollover cost model still ships all-zero

- **Problem:** `data/swap_rates.json` remains entirely `0.0` — a mechanism
  the team itself calls out as capable of "flipping the book's sign" for the
  FX book (already near-breakeven) and material even for carriers holding
  multi-day (H4 trades can hold up to 7 nights per the time-stop rule).
- **Evidence:** `data/swap_rates.json` header notes (unchanged this
  session); CLAUDE.md "Swap model ships OFF."
- **Impact:** Every PF/expectancy number published anywhere in this
  repository — including the headline carrier-edge claims — excludes a real,
  acknowledged-material cost. This isn't a new finding but it remains open
  and belongs in this audit's evidence-first accounting.
- **Recommendation:** Pull real cTrader swap long/short values (the file's
  own header documents exactly how), fill the table conservatively (worse of
  the two sides), and re-run `h4_yearly_stability` as the pre-registered
  check CLAUDE.md already commits to.
- **Files:** `data/swap_rates.json`.
- **Effort:** 2–3 hours + one backtest re-run. **Priority:** P1 (cheap, high information value, zero strategy-contact — doesn't reset the forward counter).

---

# Medium Priority (P2)

| # | Problem | Evidence | Recommendation | Effort |
|---|---|---|---|---|
| P2-1 | **RESOLVED, 2026-07-23.** `execution/api_server.py` was a 3,530-line monolith (routing + HTML + sessions + 15 dashboard modules), flagged in all three prior audits, never split | Split into `execution/api_core.py` (app/auth/config/sessions), `execution/api_shared_helpers.py` (the 4 functions genuinely consumed by 2+ router modules, e.g. `_scheduler_status` by both `/health/full` and `/alerts`), and 15 files under `execution/routes/` — one per logical module. `execution/api_server.py` is now a 138-line composition root. Verified complete, not approximate: an AST diff comparing every function's statement count/last-statement-type between the original file and the split caught two real bugs before they shipped — a `Path(__file__).parent.parent` that silently resolved one directory short once nested under `routes/` (would have broken File Explorer's entire path confinement), and an off-by-one `sed` extraction that dropped `/files/search`'s final `return` (caught immediately by the existing test suite, not by the AST diff). Also fixed en route: several tests monkeypatched `execution.api_server`'s internals (`_JOB_COMMANDS`, `_config_cache`, `_REPO_ROOT`) expecting to affect real behavior — `global`/monkeypatch only ever affects the module actually being patched, not a re-exported facade, so those tests were redirected to the module that now genuinely owns each name. All 60 original routes verified present (route-for-route diff against the pre-split file); full suite green (1083 passed, 1 skipped); `ruff --select E9,F821` clean repo-wide. | Was 3-5 days estimated; done in one session with an AST-diff safety net |
| P2-2 | Two backtest packages (`backtest/` + `backtesting/`) still both exist — organizational duplication, not dead code | `backtest/runner.py` is live (invoked by the Experiment Runner's whitelisted "backtest" job and `scripts/engine_ablation.py`); `backtest/metrics.py` is imported by `backtest/{walk_forward,report,monte_carlo,runner}.py` — **correction, 2026-07-23**: an earlier draft of this row wrongly claimed `backtest/metrics.py` had zero importers, conflating it with the already-deleted `backtesting/metrics.py` (a *different* file, singular vs. plural package name). Verified via `grep` before any deletion was attempted — nothing in `backtest/` was removed | Merge `backtest/` and `backtesting/` into one package (both are live; this is a rename/consolidation, not a deletion) | 1 day |
| P2-3 | `run_h002.py`, `run_h002b.py`, `run_h008.py`, `run_h008b.py`, `run_h008c.py` still live at repo root instead of `research/` | `ls run_h*.py`, this session | Move into `research/experiments/` or `scripts/` for consistency with H024/H025/H033/H037 | 1 hour |
| P2-4 | File Explorer's secret-path denylist checks the whole-word `token/secret/credential/password` filter only against the file's basename, not intermediate directory names | `_is_denied_path`, `execution/api_server.py` (agent-verified line ~2841) | Apply the word filter to every path segment, not just the last | 30 min |
| P2-5 | `journal.annotate()` returns `{"success": true}` (and audit-logs success) even when the `tags` migration hasn't run and no `notes` was given — a silent no-op reported as success | `storage/journal.py:207-243`, agent-verified | Return an explicit "not applied" result when `sets` ends up empty | 1 hour |
| P2-6 | AI JSON extraction (`ai/providers/base.py:_first_json_object`) grabs the *first* `{` in text, not the first one that parses — fails on model output containing an earlier unrelated brace | reproduced this session by the dedicated agent (`${100}... {"sentiment":...}` case) | Scan all `{` positions and try each as a JSON start, not just the first | 1-2 hours |
| P2-7 | Lint backlog grew 332→376 findings since the July audit; CI only gates E9/F821 (by design, documented) | fresh `ruff check .` this session | Expand CI `--select` incrementally as backlog is paid down, per the CI file's own stated intent | ongoing |
| P2-8 | `docs/PRODUCTION_AUDIT_2026-07.md:63` ("Currently gated off ... correct state") is now factually wrong and contradicts `config.yaml` | this session's fact-check | Add a one-line dated addendum to the July doc noting the 2026-07-06 config change, so a future reader doesn't trust stale prose over config | 15 min |
| P2-9 | **RESOLVED, 2026-07-23.** R-multiple/pip-sign recompute logic was duplicated near-identically across `storage/journal.py`, `storage/outcome_tracker.py` (5 separate call sites — `close_signal`, `performance_summary`, `_regime_breakdown`, and the time-stop branch of `auto_close_outcomes`), and `scripts/repair_outcome_pips.py` | Consolidated into `utils/trade_math.py` (`is_buy_direction`, `price_diff`, `realized_r`, `profit_factor`; 25 dedicated tests). Not cosmetic: the consolidation surfaced that `storage/outcome_tracker.py`'s `performance_summary()` had the SAME all-breakeven `profit_factor` bug already found and fixed in `storage/journal.py`'s independent copy (P3-2) — both now delegate to one implementation, so this class of bug can't silently diverge between them again. Every existing test in the affected files passed unchanged before and after, verified file-by-file; full suite green | 2-3 hours estimated; matched |

---

# Low Priority (P3)

| # | Problem | Evidence | Recommendation |
|---|---|---|---|
| P3-1 | Journal CSV export writes `notes` unescaped — a note starting with `=`/`+`/`-`/`@` is a classic Excel formula-injection payload if the export is opened in Excel | `storage/journal.py:363-371` | Prefix such cells with `'` on export |
| P3-2 | `journal_stats()` reports `profit_factor: "Infinity"` for a book that is all-breakeven (zero wins, zero losses), not just zero-losses | `storage/journal.py:296-303` | Return `null`/undefined for the zero-wins-and-zero-losses case |
| P3-3 | `by_direction` bucketing in the journal groups on raw stored direction string, not normalized BUY/BULLISH ↔ SELL/BEARISH (currently dead since production only writes BULLISH/BEARISH) | `storage/journal.py` `_bucket()` | Reuse the existing `_is_buy()` normalizer |
| P3-4 | ALREADY_LOGGED_IN error-swallow in the cTrader fix matches the string across *every* error context (symbol list, reconcile, spot subscribe...), not just auth — silently no-ops instead of failing fast for non-auth errors that happen to share the substring | `ctrader_client.py:863` | Scope the check to `context in ("app_auth", "account_auth")` |
| P3-5 | File Explorer denies by extension (`.pem`, `.key`, ...) but an extensionless private key (`id_rsa`, `id_ed25519`) is not denylisted | `execution/api_server.py` `_DENY_EXTENSIONS` | Add filename-pattern denial (`id_rsa*`, `id_ed25519*`) alongside extension denial |

---

# Architecture Review

The layered pipeline (data → validation → regime → engines → confluence →
risk → news → storage/notify) remains sound and, per the 2026-07-16 gap
analysis's literature-grounded comparison (Perold, Almgren-Chriss, SEC
15c3-5, MiFID II RTS 6, SR 11-7), matches the institutional pattern for its
stage: deterministic pre-trade risk, statistical signal generation, AI
provably isolated from the decision path. This session's review found no new
architectural violation. What's unchanged and still real: three monoliths
(`main.py`'s CC-71 `run_pipeline`, `api_server.py` at 3,482 lines,
`ctrader_client.py` at 1,551 lines) that three consecutive audits have now
flagged without remediation — the risk isn't that they're wrong, it's that
their size makes the next bug harder to find and the next refactor unsafe
without the replay harness the gap analysis already scoped (S2) but that has
not yet been built. **How this fails:** a bug shipped inside a 3,482-line
file with unmeasured coverage on the exact endpoints an operator trusts
(Experiment Runner, File Explorer) is a bug that survives code review by
sheer surface area, not because reviewers are careless.

# Quant Research Review

Re-verified from raw code this session, not from the registry's prose: H024
(hard regime gate), H025 (information compression), H033 (meta-confidence
gate), and H037 (decision delay, still PLANNED, correctly unresulted) all
hold up under adversarial review — real chronological per-symbol OOS splits,
real seeded bootstraps (H025: `np.random.default_rng(20260721)`, 1000
resamples), correct AUC direction (H033), and pre-registration genuinely
preceding code in git history (H037: `0954579` before `3cf2567`). This is a
genuinely rare finding for an audit of this kind — four independently
re-derived hypotheses, zero discipline violations. The dead list in
CLAUDE.md is trustworthy; nothing this session found contradicts it. The
open wound remains what the philosophy audit already named: the *production
rule set* (which engines run, which symbols trade) still doesn't fully track
the registry's own verdicts — see P1-1 and P1-2.

# Statistical Review

No new backtest was run this session. The philosophy audit's numbers stand:
carrier edge (XAU/BTC/ETH) z=8.6 in-sample, FX book p=0.078 (not significant
even in-sample), all-15 z=5.16 carried almost entirely by carriers. Nothing
in this session's review changes that reading, and nothing should — CLAUDE.md
rule 6 is explicit that entries/exits/thresholds don't move mid-sample.
The one live change since: P1-6 (swap costs still zero) means every PF
number in every doc, including this one's citations, is still missing a
cost dimension the team itself has flagged as potentially sign-flipping for
FX. The forward-evidence counter — the only thing that can turn any of this
from in-sample to real — could not be read live from this offline audit
environment (no D1 connectivity); its last known state (symbols.yaml
comment, 2026-07-12) was single-digit n, nowhere near D001's n≥40 gate. An
institutional reviewer's honest read: **there is still no live P&L track
record of any statistical size**, full stop, and that fact alone should gate
any live-capital conversation regardless of how good the research process
looks on paper.

# Security Review

Verified fresh this session, not carried from prior docs: **no SQL
injection** anywhere touched this session (journal, migrations, repair
script — all parameterized); **every one of the 9 newest API endpoints**
(journal ×5, execution-quality, metrics, data-confidence, reconciliation)
calls `_check_auth` as its first statement; the **File Explorer's
path-traversal defense genuinely works** — `.resolve()` + `relative_to()`
correctly rejects `..`, absolute paths, and symlink escapes, verified by
direct code reading, not just by trusting the module's own docstring
(`MISSION_CONTROL_AUDIT.md`'s claim about this held up under adversarial
re-check). `pip-audit` is clean; `ccxt` is now pinned (closes a July audit
item). Against that: the unresolved P0-1 (credential rotation unconfirmed)
and P0-4 (root services) are disqualifying regardless of how clean the code
is — a leaked Cloudflare token grants DNS/Worker/D1 control independent of
any application-layer defense reviewed above. The Experiment Runner's
subprocess whitelist is real (fixed argv, no shell=True, no user-input
interpolation) but P1-3's soft timeout means a stuck job can starve the
2-thread pool, a low-severity availability issue, not an injection one.

# Infrastructure Review

CI now exists and is real (`.github/workflows/ci.yml`: ruff E9/F821, full
pytest, pip-audit — deliberately narrow per its own header comment, "tighten
--select as it's paid down"). No Docker, no staging environment, single VPS,
root services (P0-4). Backup timers exist and fire nightly but the restore
path has literally never been exercised (P1-5) — this is the same "a backup
is a hope until restored" gap named in the runbook itself, three weeks
unclosed. Schema migrations now real (`storage/migrations.py`: version
table, idempotent runner, 3 migrations applied — baseline, decision
provenance, journal tags) — this closes a genuine prior gap (S4) and is the
precondition that made P2-decision-provenance and the journal's tags
migration safe to add, which they were.

# Performance Review

No new performance work is warranted — this echoes the 2026-07-16 gap
analysis's own conclusion and nothing this session found contradicts it
(105MB RSS, ~13s/pipeline-run, D1 session-pooling already fixed). The one
new data point: `execution/api_server.py` coverage rose from 36% (July) to
an estimated ~76% this session (measured via `pytest --cov`), which is
itself a performance-adjacent risk-reduction fact worth recording — most of
that file's risky surface (auth, journal, TCA) is now exercised by tests,
even though the file's *size* remained a maintainability problem until
this session's P2-1 split (see the P2 table) reduced it from 3,530 to 138
lines by extracting 15 per-module routers plus two shared infrastructure
modules.

# Trading Logic Review

Unchanged from the philosophy audit's core finding, re-affirmed by nothing
new this session contradicting it: the measured edge is trend-capture on
three carrier assets (XAU/BTC/ETH) at H4/D1, with the FX book statistically
indistinguishable from breakeven. Production config (`config/engines.yaml`,
`config/risk.yaml`) matches CLAUDE.md's documented frozen state exactly —
verified this session by direct read, not assumed. The `EXEMPT_ENGINES`
governance hole (P1-1) and the ungated indices/oil symbols (P1-2) are the
two places where the live trading configuration still diverges from what
the evidence supports.

# Research Methodology Review

See Quant Research Review above. One methodological nuance surfaced this
session worth naming precisely: H024's NULL verdict is defined by its own
pre-registered rule as `|ΔPF|<0.15 AND retention≥0.50` — it does **not**
require the carrier-degradation check to pass for a NULL verdict (only for
an ADOPT verdict). The carrier PF did degrade beyond the stated tolerance
(1.335→1.256), and the writeup *does* disclose this prominently rather than
hiding it — so this is a disclosed nuance in how NULL is defined, not a
concealed one, but it is worth an institutional reviewer noticing the
distinction rather than reading "NULL" as "nothing happened."

# Code Quality Review

982 tests, 981 passing, 1 skipped, clean in a fresh venv this session. Ruff
E9/F821 (the CI gate) clean. The wider lint backlog grew from 332 to 376
findings since July — not a regression in kind, just accumulation faster
than paydown, and it's honestly disclosed as out-of-scope-for-now in the CI
file's own header rather than hidden. Dead code from the July audit
(`utils/feature_def.py`, `execution/tradingview_webhook.py`) is confirmed
gone. `backtest/metrics.py` is NOT dead code (see P2-2's correction) — the
`backtest/`/`backtesting/` split is organizational duplication only.

# Refactoring Opportunities

1. ~~Split `execution/api_server.py` into per-module routers (P2-1)~~ —
   **done, 2026-07-23.**
2. ~~Extract one shared trade-math helper (P2-9)~~ — **done, 2026-07-23**
   (`utils/trade_math.py`); the consolidation itself surfaced a real bug
   (see P2-9's own entry).
3. Merge `backtest/` and `backtesting/` (P2-2) — smaller win, still open
   three audits running.

# Dead Code

- `backtest/metrics.py` (287 lines, zero external importers — confirmed
  this session).
- `run_h002.py`/`run_h002b.py`/`run_h008.py`/`run_h008b.py`/`run_h008c.py`
  at repo root (P2-3) — not dead functionally (still runnable), but
  organizationally orphaned relative to every newer hypothesis's convention.

# Missing Tests

- `scripts/repair_outcome_pips.py` — zero coverage on a script that mutates
  the load-bearing evidence ledger (P1-4).
- Cross-process duplicate-session scenario for the cTrader reconnect fix
  (P0-3) — no test exercises two processes authenticating for the same
  account.
- `execution/ctrader_client.py`'s `_on_message` superseded-client guard
  (added in the same diff as the ALREADY_LOGGED_IN fix) has no direct test,
  unlike its `_on_tcp_connected` sibling which does.

# Missing Documentation

- `docs/PRODUCTION_AUDIT_2026-07.md:63`'s cTrader-gating claim needs a dated
  addendum (P2-8) — the only documentation *inaccuracy* (as opposed to gap)
  found this session, notable precisely because this codebase's docs have
  otherwise verified as accurate against code repeatedly, across four audits
  now.

# Technical Debt

Ranked by this session's own evidence, highest first: unresolved
ops-runbook items open 18+ days with zero closure evidence (P0-1, P0-4,
P1-5) is the largest debt category — not because the remediation is hard
(all three are cheap, scripted, or a checklist) but because "written down
and never executed" is worse than either "not planned" or "done," since it
creates false confidence for the next reader. Second: of the three
persistent monoliths this document originally flagged, `execution/api_server.py`
(P2-1) is now resolved (this session); `main.py`'s CC-71 `run_pipeline` and
`execution/ctrader_client.py`'s remaining size are not — the same
behavior-preservation discipline (an AST diff, not just tests) that made
the api_server split safe is the template for whichever of those two goes
next. Third: the swap-cost gap (P1-6), which is
cheap to close and has been open since before CLAUDE.md was written.

# Hidden Risks

- **P0-3** (cross-process duplicate order) is the risk this audit is most
  worried about precisely because it is *new* (introduced 2026-07-22, one
  day before this audit) and *live* (the code path it touches runs on every
  scheduler tick against a real, if demo, broker connection) — it had zero
  prior review before this session's dedicated agent found it.
- The gap between "credential rotation runbook exists" and "credential
  rotation happened" (P0-1) is a hidden risk specifically because the
  runbook's *existence* can be mistaken for the *closure* by a future
  reader skimming the repo — this audit deliberately checked git history,
  not just file presence, to catch that.

# False Assumptions

- The commit fixing the ALREADY_LOGGED_IN reconnect storm assumes (in its
  own code comment) that a genuinely conflicting duplicate session will
  "fail with a real error" downstream — this is asserted, not measured
  against the actual broker's session-tolerance behavior (P0-3).
- `docs/PRODUCTION_AUDIT_2026-07.md`'s cTrader-gating claim is a false
  assumption for any reader today (P2-8) — a reminder that in a
  fast-moving repo, "the docs said so" is not evidence; the config is.

---

# Recommended Roadmap

Ordered by ROI (impact ÷ effort), consistent with this repo's own evidence-
first culture — nothing here proposes new features; every item closes a
measured gap.

1. **P0-1 credential rotation + closure record** (2-3h) — highest severity,
   near-zero engineering cost, unblocks trusting everything else.
2. **P0-4 non-root migration** (3h) — script already exists; run it.
3. **P1-5 backup restore rehearsal** (1h) — closes the last open ops-runbook item.
4. **P0-3 duplicate-session guard + test** (1-2d) — closes a live-path risk
   introduced yesterday, before it's exercised by bad luck instead of by design.
5. **P1-6 swap-cost table fill + re-run** (2-3h + 1 backtest) — cheap,
   zero strategy-contact, directly improves the honesty of every published
   PF number.
6. **P1-1 EXEMPT loophole closure** (2-4h, docs-only) — write the two
   missing hypothesis entries; no code change.
7. **P1-2 indices/oil evidence-or-disable** (10 min to disable, 1 day to
   properly backtest first).
8. **P0-2 ctrader_client coverage to 60%** (1 week) — the single largest
   single-item effort here, but it's the precondition the project already
   set for itself before P0-3's risk becomes acceptable to carry indefinitely.
9. **P1-3/P1-4** timeout fix and repair-script tests (half a day combined).
10. Everything in P2/P3 — opportunistic, none blocking.

Then, unchanged from the 2026-07-16 gap analysis's own roadmap and still
correct: **let the forward-evidence counter accumulate.** Nothing on this
list, or that one, substitutes for time. D001 (FX cut rule, n≥40) and D002
(carriers live-capital discussion, n≥100) remain the only events that can
move the Statistical Validity score materially, and no engineering effort
accelerates them.

---

# Final Verdict

## ✅ Accept for Demo

**Not** Accept for Production: an unconfirmed 18-day-old credential leak,
root-owned services, and a fresh live-path duplicate-order risk are each
individually disqualifying for a production/live-capital verdict, and this
report found all three independently.

**Not** Reject or Major Rework: the architecture is sound, the test suite is
large and green (982 tests), CI is real and gates the right things narrowly
and deliberately, security controls that *are* implemented (auth, SQL
parameterization, path-traversal defense) hold up under adversarial
re-verification rather than just self-report, and — the rarest fact in this
whole review — four independently re-derived research hypotheses this
session found zero instances of the project's own stated scientific
discipline slipping.

**Accept for Demo**, specifically: continue cTrader demo-account operation
and forward-evidence accumulation (that is what's actually happening today
and it's the correct activity for this project's stage), but only after
closing P0-1 through P0-4 — none of which touch the strategy, all of which
are ops/discipline items with existing tooling or trivial fixes. The path
to Production is not a redesign; it is finishing the checklist this
project already wrote for itself and has now left open for three weeks.

---

*Audit produced 2026-07-23 on branch `claude/iatis-full-audit-350sic`, commit
`a553e6f`. Fresh evidence this session: full test suite run (982 tests),
fresh `ruff`/`pip-audit`, coverage measurement of `api_server.py` and
`ctrader_client.py`, direct git-history verification of `docs/OPS_CLOSURE_RUNBOOK.md`'s
closure records, and five independent adversarial code-review passes over
every module not covered by the three prior audits. Every P0/P1 finding
above cites a file:line or command output obtained this session — none are
restated from prior audits without independent re-verification.*
