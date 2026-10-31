# PHASE_WIRE_POLICY_ENFORCEMENT.md
## W2 — Observe-only policy resolution on the live path

**Status:** SPEC / PLANNED — no live behavior change. All new flags ship OFF.
**Scope:** W2 only (observe + log). **Not** W3 enforcement. **Not** Authorization wiring.
**Baseline (code audit 2026-10-31):** the policy lifecycle (`backtest/policy_registry.py`,
`storage/policy_registry.py`) and Phase 9 Authorization (`execution/authorization.py`)
exist and are tested, but nothing in `scheduler.py`, `main.py` or `execution/trade_executor.py`
imports them. Live path: engines → confluence → risk → news → kill switch → pretrade → cTrader.

---

## 0. Non-negotiable rules

1. **Every new flag defaults OFF.** With flags off, production decisions are bit-identical
   to today's (W2 only adds fields to the decision record).
2. `policy_gate.enforcement_enabled` must not exist as `true` anywhere in this phase.
3. W2 never calls `place_market_order` or Authorization.
4. No ACTIVE policy is created or forced. The only route is GRANTED (Phase 6 ledger)
   → DRAFT → VALIDATED → ACTIVE via `backtest/policy_registry.py`.
5. Policy states in code: **`DRAFT | VALIDATED | ACTIVE | REVOKED`** only. No LIMITED/SHADOW.
   The shadow roster (`storage/live_roster.py`, `ACTIVE | REMOVED`) is a separate system.
6. Policy-scope regimes: **`TRENDING | RANGING`** (`VALID_REGIME_PROFILES`,
   `backtest/policy_registry.py:88`). The detector also emits ACCUMULATION, DISTRIBUTION,
   MANIPULATION, NEWS_DRIVEN, UNKNOWN (`regimes/regime_detector.py:29`) — all of these resolve
   to NONE / `REGIME_OUT_OF_SCOPE`.
7. Live code must not import `backtest.*`. The read path moves to (or is re-implemented in)
   `storage/` or `execution/`.
8. CLAUDE.md rule 6: W3 enforcement changes entry logic and resets the forward demo counter,
   so it needs its own pre-registered decision. W2 changes no entry decision and does
   **not** reset the counter.

---

## 1. Goals (W2)

### Must
- Once per symbol per tick, after the regime is known, resolve whether an ACTIVE policy
  **would** cover `(symbol, decision_timeframe, regime)`.
- Add to the report (and so to `storage/decisions.jsonl` + `decision_db`):
  - `policy_resolution`: `FOUND | NONE | SKIPPED | ERROR`
  - `policy_id` (FOUND only)
  - `policy_state` (FOUND only)
  - `policy_regime_requested`
  - `policy_resolution_reason` (short machine-readable string)
- Flag off or any resolver error: `final_verdict`, size and broker path unchanged.
- Operator metric: share of `EXECUTE` decisions with `policy_resolution != FOUND`
  (expected ≈100% while the registry is empty).

### Must not
- Approve or reject any trade on policy grounds.
- Create/activate policies.
- Touch pretrade limits / notional math.
- Wire `execution/authorization.py`.

---

## 2. Code map (verified 2026-10-31)

| Item | Location |
|------|----------|
| Resolver (domain logic) | `backtest/policy_registry.py:208` `resolve_eligible_policy(symbol, timeframe, regime)` → `ELIGIBLE_POLICY \| NO_POLICY \| CONFLICT` |
| Storage read | `storage/policy_registry.py:220` `find_active_policy(symbol, timeframe, regime_profile)` — returns ACTIVE rows only, table `research_policies` (Cloudflare D1) |
| Regime known | `main.py:806` `regime_result = detect_regime(...)` inside `run_pipeline` (`main.py:747`) |
| Verdict set | `main.py:514` (stage 6, plus the regime filters at 519–544) |
| Decision persisted | `main.py:879–880` `_safe_store("decision_log", ...)` / `_safe_store("decision_db", ...)` |
| Guard test | `tests/test_policy_registry.py:270` `test_nothing_outside_tests_imports_policy_registry_yet` |
| Authorization (out of scope) | `execution/authorization.py` — header: "deliberately NOT WIRED" |
| Promotion bar | `research/edge_gate.py:67` `PROMOTION_CRITERIA` — H009, H013 are PASSED but meet none of it |

### Implementation shape

1. New live-safe module `storage/policy_resolve.py` (reads via `storage.policy_registry`,
   never imports `backtest.*`):

```text
resolve_policy_for_live(symbol, timeframe, regime) -> PolicyResolution
  status:  FOUND | NONE | ERROR          (SKIPPED is set by the caller when the flag is off)
  policy_id, state, reason
  eligible_for_execution: bool           # True only when exactly one ACTIVE row
```

   Mirror `resolve_eligible_policy`'s semantics exactly: regime outside
   `{TRENDING, RANGING}` → NONE/`REGIME_OUT_OF_SCOPE`; 0 rows → NONE/`NO_ACTIVE_POLICY`;
   >1 rows → ERROR/`CONFLICT`; D1 failure or missing table → ERROR/`<ExcType>`.
   A parity test (see §5) pins the two implementations together.

2. Since `find_active_policy` only returns ACTIVE rows, `policy_state` is always `ACTIVE`
   when FOUND. DRAFT/VALIDATED policies are invisible to this path by design.

---

## 3. Call site

`main.py::run_pipeline`, after `detect_regime` (806) and before persistence (879):

```text
detect_regime → engines/confluence/risk/news (unchanged) → final_verdict (unchanged)
  → [W2, flag on] report.update(resolve_policy_for_live(symbol, decision_tf, regime))
  → log_decision / log_decision_db (879–880)
  → scheduler execution path (unchanged)
```

Requirements on the hook:
- Wrapped like `_safe_store`: any exception → `policy_resolution=ERROR`, logged at WARNING,
  verdict untouched.
- Bounded time: it adds a D1 round-trip to every symbol on the live path; use the D1
  client's timeout and never retry inside the tick.
- Early-exit paths (validation failure 793, MQS gate 800) get `SKIPPED` or no fields — no
  query there.

### Config

```yaml
policy_gate:
  observe_only: false         # W2 — operator flips to true on the VPS after deploy
  enforcement_enabled: false  # W3 — must stay false; code in this phase must ignore it
```

| observe_only | Effect |
|--------------|--------|
| false (default) | no query; `policy_resolution=SKIPPED` (or field absent) |
| true | query + log; verdict unchanged |

---

## 4. Registry entry

Pre-registered decisions live in `research/results/registry.json` → `_decision_rules`
(next to `D001_fx_cut`, `D002_carrier_confirmation`). Add:

```json
"D003_policy_observe_w2": {
  "status": "PLANNED",
  "statement": "W2: observe-only live policy resolution. Logs whether an ACTIVE policy would cover each live decision; never changes final_verdict.",
  "registered": "2026-10-31",
  "flags": {"policy_gate.observe_only": "default false", "policy_gate.enforcement_enabled": false},
  "evidence_impact": {"resets_forward_demo_counter": false, "affects_entry_logic": false},
  "acceptance": [
    "policy_resolution present on every full-pipeline decision while observe_only is true",
    "final_verdict distribution unchanged vs a control window",
    "scheduler.py / main.py / execution/* import no backtest.* module",
    "operator report: pct of EXECUTE with policy_resolution != FOUND"
  ],
  "non_goals": ["NO_POLICY blocking", "policy activation", "Authorization wiring"],
  "rollback": "observe_only: false, or revert the commit; no schema change"
}
```

A future `D004_policy_enforcement_w3` (`resets_forward_demo_counter: true`) is registered
only once ≥1 policy reaches ACTIVE through the full path.

---

## 5. Tests

1. Resolver: one ACTIVE row (mocked `find_active_policy`) → FOUND, eligible true.
2. Resolver: zero rows → NONE/`NO_ACTIVE_POLICY`.
3. Resolver: regime in {UNKNOWN, ACCUMULATION, …} → NONE/`REGIME_OUT_OF_SCOPE`, **no query made**.
4. Resolver: >1 rows → ERROR/`CONFLICT`; D1 exception → ERROR.
5. **Parity:** for the same mocked rows/regimes, `storage.policy_resolve` and
   `backtest.policy_registry.resolve_eligible_policy` agree on every case.
6. Pipeline: with the resolver raising, and with observe_only on vs off, `final_verdict`
   and all execution fields are identical.
7. **Guard test — update deliberately.** The current test only greps for the string
   `"policy_registry"`, so `storage/policy_resolve.py` would pass it without anyone noticing.
   Replace it with:
   - an allowlist: `main.py` may import `storage.policy_resolve` and nothing else policy-related;
   - `scheduler.py`, `main.py`, `execution/*.py` must not import any `backtest.` module;
   - `execution/trade_executor.py` and `execution/ctrader_client.py` must not reference
     `policy_resolve` (W2 never reaches the broker layer).
8. No test needs live D1.

---

## 6. Operator smoke

Registry contents (expected: empty or no table):

```bash
cd /opt/iatis && PYTHONPATH=. python3 -c "
from storage import d1_client as d
with d.d1_connection() as c:
    print(c.execute('SELECT status, COUNT(*) FROM research_policies GROUP BY status').fetchall())
"
```

After deploy with `observe_only: true`:

```bash
tail -n 200 storage/decisions.jsonl | python3 -c "
import sys, json, collections
rows=[json.loads(l) for l in sys.stdin if l.strip()]
print(collections.Counter((r.get('final_verdict'), r.get('policy_resolution')) for r in rows))"
```

Success: field present, EXECUTE count unchanged, NONE share known.

---

## 7. Deferred (W3+)

- `enforcement_enabled: true` → no ACTIVE policy forces `NO_TRADE` / `NO_POLICY`.
  Preconditions: ≥1 ACTIVE policy via the full path, D004 registered, operator accepts the
  demo halt and the forward-counter reset.
- Authorization wiring follows enforcement (Phase 15 per `execution/authorization.py`).
- Note: there is currently **no operational script** that drafts/validates/activates a
  policy — those functions are called only from tests. W3 is blocked on that too.

---

## 8. Implementation order

1. Re-confirm line numbers above (they drift).
2. `storage/policy_resolve.py` + tests 1–5.
3. Hook in `main.py` + config keys (defaults OFF) + test 6.
4. Replace guard test (7).
5. Add `D003` to `_decision_rules`.
6. Stop for review: full pytest, `git diff --stat`, confirm no flag is on in `config.yaml`.

## 9. Rollback

`policy_gate.observe_only: false` or revert. No migration; old decisions without the fields
stay valid.
