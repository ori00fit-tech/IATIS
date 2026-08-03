# cTrader Connection Lifecycle — Forensic Re-Audit (2026-08-04)

## Trigger

The operator reported live scheduler logs showing a repeating cycle:

```
TCP_CONNECTED → ERROR → DISCONNECTED → reconnect → TCP_CONNECTED →
ALREADY_LOGGED_IN → app_auth Deferred → ERROR → ...
```

ending in `Giving up after 10 reconnect attempts. Manual intervention
required`, followed by the client reconnecting anyway, and two
`TCP_CONNECTED` lines ~5 seconds apart suggesting overlapping
connections. Requested: a full, non-blind trace of `connect()`,
`authenticate`/`app_auth`, `disconnect()`, reconnect, and bootstrap,
with root cause, exact race sequence, minimal fix, regression tests,
proof of single-connection/single-auth guarantees, terminal-failure
behavior, and logging/metrics recommendations.

## 1. Root cause

**This exact symptom class was already found and fixed in this
codebase, in four dated commits, 2026-07-17 through 2026-07-23 — before
today.** Confirmed via `git log --oneline -- execution/ctrader_client.py`:

| Commit | Date | What it fixed |
|---|---|---|
| `06a77e0` | 2026-07-17 | Stop leaking sockets on reconnect (the ALREADY_LOGGED_IN / EMFILE storm) |
| `7c0400b` | 2026-07-17 | cTrader ALREADY_LOGGED_IN reconnect storm + operator-visible fixes |
| `a62d5ca` | 2026-07-22 | Cross-process session lock for `connect()` (audit P0-3) |
| `9b039ab` | 2026-07-23 | Scope the ALREADY_LOGGED_IN errback swallow to auth contexts (audit P3-4) |

The current source (`execution/ctrader_client.py`, read in full for this
re-audit) already contains, and this pass independently verified line by
line:

- A **superseded-client guard** in both `_on_tcp_connected` (line 420) and
  `_on_disconnect` (line 915): `if client is not None and client is not
  self._client: ... return`. `connect()` nulls `self._client` *before*
  tearing down the old one (`stale, self._client = self._client, None`,
  line 1048), so a stale client's late callback can never clobber the new
  connection's state — this is the direct fix for the exact "doubled TCP
  connected lines and an ALREADY_LOGGED_IN storm" scenario the code's own
  comment (line 419) says was "observed live 2026-07-22."
- **`_stop_client()`** (line 923) tears down the previous Twisted
  `Client`'s service before every `connect()` — the fix for the socket/fd
  leak that the same storm produced.
- **A cross-process `flock` lock** (`_acquire_process_lock`, line 110):
  `connect()` refuses outright (raises `DuplicateSessionError`, caught,
  returns `False`) if another OS process already holds the session lock —
  this is the fix for two *separate processes* (e.g. a manual script run
  alongside the live scheduler) racing for the same cTrader session slot.
- **A single-flight in-process lock** (`core/data_providers.py`'s
  `get_shared_ctrader_client()`, line 912): a `threading.Lock` around the
  lazy singleton's construction — the fix for two *threads in the same
  process* (confirmed, per its own docstring, to be exactly
  `trade_executor.py` opening a second client per EXECUTE-verdict symbol,
  diagnosed 2026-07-14) racing to build/connect a second `CTraderClient`.
- **ALREADY_LOGGED_IN treated as non-fatal in TWO independent places**:
  `_on_error_res` (the `ProtoOAErrorRes` message handler, line 874) and
  `_on_error` (the `send()` Deferred's errback, line 1010, scoped via
  `_ALREADY_LOGGED_IN_BENIGN_CONTEXTS = {"app_auth", "account_auth"}` so
  it can't silently swallow a genuinely unexpected error on, say, a
  reconcile request). Both continue the real bootstrap chain (advance to
  the next state and send the next request) rather than just avoiding
  `ERROR` — this is the fix for the "ERROR → instant retry → fresh
  app-auth → ALREADY_LOGGED_IN again, looping every second" storm
  described in the code's own comment (line 884, "observed 2026-07-22").
- **A single-flight reconnect loop** (`_schedule_reconnect`, line 943):
  `with self._lock: if self._reconnecting: return` — a second disconnect
  event while a reconnect loop is already running is a no-op, not a
  second competing loop.

**Conclusion**: every mechanism the operator's requested audit asked to
verify already exists in the code on this branch and matches, symptom for
symptom, what the operator's live logs show. The overwhelmingly most
likely explanation for seeing this behavior live *today* is that the
deployed VPS process is running code from **before** these four commits
— i.e. a stale deploy, not a new defect in the current source. This
matches `CLAUDE.md`'s own ops runbook warning: `git pull` then `sudo
systemctl restart iatis-scheduler iatis-api` **must run as its own line,
never glued to a comment** — "it silently didn't run twice this way."
This cannot be independently confirmed from this sandboxed session (no
SSH/VPS access here) — it is the operator's own next check.

**No blind patching was applied.** Given the fixes already exist and
match the reported symptoms exactly, applying a *new* patch on top without
first confirming the deploy is current would risk masking the real,
already-solved root cause or introducing a genuinely new bug into an
already-correct area.

## 2. Exact lifecycle / state machine (as currently coded, verified by reading)

```
DISCONNECTED
   │ connect() called
   ├─ _acquire_process_lock()            [cross-process guard, P0-3]
   ├─ _stop_client(old self._client)     [tears down any prior client first]
   ├─ new Client() → self._client
   ├─ register callbacks, client.startService()
   ▼
TCP_CONNECTED  ──(_send_app_auth)──▶  [ProtoOAApplicationAuthReq sent]
   │                                        │
   │                                  success│           ALREADY_LOGGED_IN
   │                                        ▼                    │
   │                                  APP_AUTH_OK  ◀──────────────┘  (_on_error_res,
   │                                        │                         continues bootstrap)
   │                              (_send_account_auth)
   │                                        ▼
   │                          [ProtoOAAccountAuthReq sent]
   │                                        │
   │                                  success│           ALREADY_LOGGED_IN
   │                                        ▼                    │
   │                              ACCOUNT_AUTH_OK  ◀──────────────┘
   │                                        │
   │                    (_send_trader_req + _send_symbols_list_req + _send_reconcile_req, fan-out)
   │                                        ▼
   │                         SYMBOLS_LOADED / (account info in)
   │                                        ▼
   │                         READY (_maybe_ready: BOTH account_info AND symbols loaded)
   ▼
any stage → real (non-ALREADY_LOGGED_IN) error → ERROR
any stage → disconnect (not self-initiated) → DISCONNECTED → _schedule_reconnect()
                                                                    │
                                                    (single-flight: no-op if already running)
                                                                    ▼
                                            backoff loop, up to RECONNECT_MAX_ATTEMPTS (10)
                                                                    │
                                                        success → attempt counter reset, loop exits
                                                                    │
                                                    exhausted → "Giving up..." logged,
                                                    _reconnecting reset to False (NOT wedged —
                                                    see §6), loop exits
```

Every transition above was read directly from the current source, not
inferred.

## 3. Minimal safe fix

**None applied this pass.** The mechanisms the audit asked to verify are
already present and correct on this branch (see §1). The one action item
is operational, not code: confirm the VPS is running a commit at or after
`9b039ab` (2026-07-23) and redeploy if not, per the exact two-line
sequence in `CLAUDE.md`'s ops runbook.

## 4. Regression tests (new, closing real coverage gaps found during this re-audit)

Coverage before this pass had real gaps despite the fixes existing:
`_on_error_res`'s ALREADY_LOGGED_IN continuation (both branches) had zero
tests anywhere in the suite; `_schedule_reconnect`'s single-flight
property, its terminal-failure (`_reconnecting` reset) behavior, and its
intentional-disconnect cancellation had zero tests; the in-process
singleton's (`get_shared_ctrader_client`) actual thread-safety had never
been exercised under real concurrent access (only monkeypatched around).
Closed all four:

- `tests/test_ctrader_message_handlers.py` (+4 tests): `_on_error_res`
  continues the bootstrap from `TCP_CONNECTED`→`APP_AUTH_OK` and from
  `APP_AUTH_OK`→`ACCOUNT_AUTH_OK` on ALREADY_LOGGED_IN (asserting the
  *next* request is actually sent, not just that state didn't become
  ERROR); a stray ALREADY_LOGGED_IN at an unhandled state is a no-op, not
  a crash; a real error still sets `ERROR`.
- `tests/test_ctrader_client.py` (+3 tests): two rapid
  `_schedule_reconnect()` calls start exactly one background thread
  (§5); after `RECONNECT_MAX_ATTEMPTS` failed attempts the loop gives up
  AND resets `_reconnecting` to `False`, and a **subsequent**
  `_schedule_reconnect()` call successfully starts a **fresh** attempt
  sequence (§6); a reconnect loop sleeping between attempts is cancelled
  immediately once `_intentional_disconnect` flips (no further `connect()`
  calls after that point).
- `tests/test_provider_chains.py` (+1 test): 8 real Python threads calling
  `get_shared_ctrader_client()` concurrently, with the fake `CTraderClient`
  constructor sleeping 50ms to deliberately widen the race window —
  asserts exactly ONE construction, exactly ONE `connect()` call, and
  every thread receiving the identical shared instance (§5).

All 8 new tests pass; the full affected-file suite
(`test_provider_chains.py` + `test_ctrader_client.py` +
`test_ctrader_message_handlers.py` + `test_ctrader_execution_logic.py`)
passes 141/141 with zero regressions.

## 5. Proof only one connection/authentication attempt can be active

Two independent guarantees, now both test-covered (not just read):

1. **Two threads in one process** (the diagnosed 2026-07-14 root cause of
   `trade_executor.py` opening a second client per symbol):
   `get_shared_ctrader_client()`'s `threading.Lock` around construction —
   proven by `test_get_shared_ctrader_client_constructs_exactly_once_under_concurrency`
   (§4) with a deliberately widened race window.
2. **Two OS processes** (an operator's manual script run alongside the
   live scheduler): `_acquire_process_lock()`'s `flock` — proven by the
   pre-existing `test_acquire_process_lock_rejects_a_second_holder` and
   `test_connect_refuses_when_lock_is_held_by_another_process` (already in
   the suite, re-run and confirmed passing this pass, not newly written).
3. **Reconnect single-flight**: `_schedule_reconnect`'s `_reconnecting`
   flag under `self._lock` — proven by
   `test_schedule_reconnect_is_single_flight` (§4).

No path exists (by direct code read, not assumed) for two live,
authenticated `CTraderClient` sessions to exist simultaneously from this
codebase.

## 6. Behavior after terminal failure ("Giving up after 10 reconnect attempts")

Confirmed by direct code read and now by test
(`test_reconnect_resets_flag_and_stops_after_max_attempts`): the
`_reconnecting` flag is reset to `False` in the loop's `finally` block
**regardless of whether it succeeded or exhausted its attempts**. This
means:

- The client is **not permanently wedged** — `_reconnect_attempt` stays
  at `RECONNECT_MAX_ATTEMPTS` (a real, visible signal that manual
  intervention was needed), but a **future** disconnect event (e.g. the
  broker's own connection eventually recovering and then dropping again,
  or an operator calling `connect()` manually after investigating) is not
  blocked by a stuck flag — `_schedule_reconnect()` can start a genuinely
  new attempt sequence.
- This *is* the operator-observed "gives up, then reconnects anyway"
  behavior — and it is by design, not a bug: the log message says "manual
  intervention required" meaning *this exhausted attempt sequence* needs
  attention, not that the process should never try again. Whether that
  design (self-healing after giving up) or a hard stop (require literal
  manual `connect()`) is the *right* operational choice is a product
  decision, not a correctness bug — flagged here for the operator's
  awareness, not changed unilaterally.

## 7. Logs/metrics recommended for future diagnosis

Not implemented this pass (scope: audit + tests, not new observability
surface — flagged for a future, explicitly-scoped slice):

- A monotonically increasing **connection-attempt counter** (distinct
  from `_reconnect_attempt`, which resets) exposed via `execution/metrics.py`
  (the existing Prometheus surface) — would let an operator see "how many
  times has this process ever reconnected" without grepping logs.
- The **git commit hash** the running process was started from, logged
  once at startup (`git rev-parse HEAD`, best-effort) — would have let
  the operator immediately confirm-or-rule-out the "stale deploy" theory
  from a live log line, rather than needing this forensic pass.
- A **structured (not just log-line) record of ALREADY_LOGGED_IN
  occurrences** — currently only a WARNING-level log line; a counter
  metric would make "is this happening often" visible on a dashboard
  instead of requiring a log search.

## Status

**No code fix applied** — the specific defects the audit was commissioned
to find were already fixed in commits `06a77e0`/`7c0400b`/`a62d5ca`/`9b039ab`
(2026-07-17 through 2026-07-23), verified present and correct in the
current source. **8 new regression tests added** closing real, previously
unverified coverage gaps around these exact mechanisms (single-flight
reconnect, terminal-failure recovery, ALREADY_LOGGED_IN bootstrap
continuation, singleton thread-safety) — all passing, zero regressions in
the 141-test affected-file suite.

**Operator action required**: confirm the live VPS is running a commit at
or after `9b039ab` (2026-07-23); if not, `git pull` + `sudo systemctl
restart iatis-scheduler iatis-api` (as two separate commands, per
`CLAUDE.md`'s own documented pitfall) to deploy the already-fixed code.
This cannot be verified from this sandboxed session (no VPS/SSH access).
