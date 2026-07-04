# IATIS on Cloudflare D1

Cloudflare D1 is IATIS's only storage backend: the decision, outcome,
engine-performance, and experience data live in one organized,
centrally-managed D1 database instead of local `.db` files on the
VPS's disk — no local SQLite fallback, no disk I/O or file locking on
the VPS at all for storage/*.py.

D1 is only reachable from *inside* a Cloudflare Worker (via a binding),
not directly from an external Python process. So this isn't "point the
VPS at D1" — it's: a small Worker (`worker.js`) exposes D1 over an
authenticated HTTPS API, and the VPS's Python backend talks to that
Worker (`storage/d1_client.py`).

```
Python storage/*.py  --HTTPS-->  Worker (this folder)  --binding-->  D1
```

This setup requires a Cloudflare account and cannot be provisioned from
this repository or by an AI agent without your credentials — the steps
below are for you (or whoever has account access) to run.

## Setup

1. **Install wrangler** (Cloudflare's CLI) and log in:
   ```bash
   cd cloudflare
   npm install
   npx wrangler login
   ```

2. **Create the D1 database:**
   ```bash
   npx wrangler d1 create iatis
   ```
   This prints a `database_id` — paste it into `wrangler.toml`'s
   `[[d1_databases]]` block, replacing `REPLACE_WITH_YOUR_D1_DATABASE_ID`.

3. **Apply the schema** (optional — `storage/*.py` also self-provisions
   its own tables on first connect, same as it does locally, but
   applying this first lets you inspect the schema before any data
   exists):
   ```bash
   npm run db:apply
   ```

4. **Set the shared secret** the Worker will require on every request:
   ```bash
   npx wrangler secret put D1_PROXY_TOKEN
   # paste a long random value, e.g. from: python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

5. **Deploy the Worker:**
   ```bash
   npm run deploy
   ```
   This prints your Worker's URL, e.g. `https://iatis-d1-proxy.<you>.workers.dev`.

6. **On the VPS**, add to `.env` (same value for `D1_PROXY_TOKEN` as step 4):
   ```bash
   D1_WORKER_URL=https://iatis-d1-proxy.<you>.workers.dev
   D1_PROXY_TOKEN=<the same secret you set in step 4>
   ```
   Both are required — `storage/*.py` has no local fallback, so the app
   won't start without them.

That's it — `storage/decision_db.py`, `outcome_tracker.py`,
`engine_tracker.py`, `experience_db.py`, `symbol_health.py`, and
`calibration.py` all go through `storage/d1_client.py`, which talks to
the Worker over HTTPS.

The test suite never touches your real Cloudflare account: `tests/conftest.py`'s
`fake_d1` fixture fakes the Worker with an in-memory SQLite connection
per test (real SQL semantics, fake transport) — see its docstring for
how that works.

## What you get, and what you don't

- **Do get:** one centrally-managed database instead of local files
  that only exist on one VPS's disk; D1's own backups/durability; the
  ability to point a second process (or a Cloudflare Worker/Pages
  dashboard) at the same data without SSHing into the VPS; zero SQLite
  disk I/O or file locking on the VPS.
- **Don't get:** cross-statement transactions for free. Each
  `POST /d1/exec` call is its own atomic D1 statement, but a sequence
  of several `/d1/exec` calls within one Python `with _conn() as con:`
  block is **not** atomic as a group over HTTP (unlike local SQLite's
  commit/rollback). The one call site where that matters most —
  `storage/decision_db.log_decision_db()` writing one `decisions` row
  plus N `engine_votes` rows — uses `POST /d1/batch` for the
  `engine_votes` rows, which *is* atomic as a group
  (`env.DB.batch()` on the Worker side). Everywhere else either writes
  a single row (already atomic) or isn't safety-critical if a very
  rare partial failure occurred mid-write.
- **Known limitation:** `last_insert_rowid()` does **not** reliably
  carry over between statements inside a single `env.DB.batch()` call
  — confirmed live in production as `D1_ERROR: FOREIGN KEY constraint
  failed` when `engine_votes.decision_id` referenced a decision row
  inserted earlier in the same batch. Because of this,
  `log_decision_db()` uses two round-trips instead of one atomic
  batch: it inserts the `decisions` row alone first via a single
  `POST /d1/exec` (whose response reliably reports that statement's
  own `meta.last_row_id`), then batches all `engine_votes` inserts
  together using that concrete `decision_id`. This keeps the N votes
  atomic as a group but means the decision row and its votes are no
  longer atomic with each other — a crash between the two round-trips
  can leave a `decisions` row with no matching `engine_votes` (rare,
  and not safety-critical: analytics queries already tolerate decisions
  with zero votes). Don't reintroduce `last_insert_rowid()` across a
  batch elsewhere in this codebase; use the same two-round-trip pattern
  instead.
- **Don't get:** lower latency than local SQLite — every write/read
  becomes an HTTPS round-trip to the Worker. Fine for this system's
  volume (decisions every few minutes, not per-tick), not something to
  put in a hot loop.
