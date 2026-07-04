# IATIS on Cloudflare D1

Optional storage backend: IATIS's decision/outcome/engine-performance/
experience databases, normally local SQLite files, can instead live in
a Cloudflare D1 database — one organized, centrally-managed store
instead of four local `.db` files on the VPS's disk.

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
   IATIS_STORAGE_BACKEND=d1
   D1_WORKER_URL=https://iatis-d1-proxy.<you>.workers.dev
   D1_PROXY_TOKEN=<the same secret you set in step 4>
   ```

That's it — `storage/decision_db.py`, `outcome_tracker.py`,
`engine_tracker.py`, and `experience_db.py` all check
`IATIS_STORAGE_BACKEND` at connection time and switch to D1
automatically. Leaving it unset (or `sqlite`, the default) keeps
everything exactly as it was — local SQLite files, no Cloudflare
account needed. This is also why the test suite is unaffected: tests
never set `IATIS_STORAGE_BACKEND`, so they always exercise the local
SQLite path.

## What you get, and what you don't

- **Do get:** one centrally-managed database instead of four local
  files that only exist on one VPS's disk; D1's own backups/durability;
  the ability to point a second process (or a Cloudflare Worker/Pages
  dashboard) at the same data without SSHing into the VPS.
- **Don't get:** cross-statement transactions for free. Each
  `POST /d1/exec` call is its own atomic D1 statement, but a sequence
  of several `/d1/exec` calls within one Python `with _conn() as con:`
  block is **not** atomic as a group over HTTP (unlike local SQLite's
  commit/rollback). The one call site where that matters most —
  `storage/decision_db.log_decision_db()` writing one `decisions` row
  plus N `engine_votes` rows — uses `POST /d1/batch` instead, which
  *is* atomic (`env.DB.batch()` on the Worker side). Everywhere else
  either writes a single row (already atomic) or isn't safety-critical
  if a very rare partial failure occurred mid-write.
- **Don't get:** lower latency than local SQLite — every write/read
  becomes an HTTPS round-trip to the Worker. Fine for this system's
  volume (decisions every few minutes, not per-tick), not something to
  put in a hot loop.
