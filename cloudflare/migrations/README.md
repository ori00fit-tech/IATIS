# D1 schema migrations — reference SQL

`ALL_MIGRATIONS.sql` is **generated** from the single source of truth,
`storage/migrations.py` (`python -m storage.migrations --sql`). Do not edit
it by hand — append a new migration to `MIGRATIONS` in the Python module and
regenerate.

Normal application path: the scheduler applies pending migrations at boot
(`apply_migrations_safe()`), and `python -m storage.migrations` applies them
from a deploy step. This file exists only for the manual fallback:

```
wrangler d1 execute <DB_NAME> --file=cloudflare/migrations/ALL_MIGRATIONS.sql
```

Every statement is additive and the runner tolerates
"duplicate column name" / "already exists", so re-running is safe. The
`schema_version` INSERTs will fail on re-run (PRIMARY KEY) — that is the
expected signal that the version was already applied; ignore those errors
when applying manually.
