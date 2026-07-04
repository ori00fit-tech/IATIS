// cloudflare/worker.js
// ------------------------
// D1 query/batch proxy for IATIS.
//
// D1 databases are only reachable from inside a Cloudflare Worker via a
// binding — there is no direct way for the Python backend (running on a
// plain VPS) to talk to D1. This Worker is the bridge: it forwards
// parameterized SQL from the VPS to its own D1 binding (`env.DB`) and
// returns the rows as JSON, so storage/d1_client.py can present an
// interface close enough to sqlite3's that storage/*.py's own SQL
// strings don't need to change.
//
// This is an internal service — not meant for public/browser traffic.
// Every request must carry:
//   Authorization: Bearer <D1_PROXY_TOKEN>
// where D1_PROXY_TOKEN is a Worker secret (`wrangler secret put
// D1_PROXY_TOKEN`) matching the same value in the VPS's .env.
//
// Endpoints:
//   POST /d1/exec   { sql: string, params?: any[] }
//     -> { success: true, results: object[], meta: { last_row_id, changes } }
//   POST /d1/batch  { statements: [{ sql: string, params?: any[] }, ...] }
//     -> executed atomically via D1's batch() API (env.DB.batch), so a
//        multi-insert (e.g. one decision + its N engine votes) either
//        all succeeds or all fails — unlike sequential /d1/exec calls,
//        which are each independently atomic but NOT atomic as a group.
//
// Setup:
//   wrangler d1 create iatis
//   # copy the printed database_id into wrangler.toml
//   wrangler d1 execute iatis --remote --file=cloudflare/schema.sql
//   wrangler secret put D1_PROXY_TOKEN
//   wrangler deploy
//
// TradingView webhook forwarding (the original stub's purpose) is not
// implemented here — execution/tradingview_webhook.py is still an
// unused stub on the Python side too. Add a separate route if that
// becomes needed; don't overload this proxy's auth model for it.

function unauthorized() {
  return new Response(JSON.stringify({ error: "unauthorized" }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}

function checkAuth(request, env) {
  const header = request.headers.get("Authorization") || "";
  const expected = `Bearer ${env.D1_PROXY_TOKEN || ""}`;
  // Constant-time-ish comparison isn't critical here (this isn't a
  // password check against a human-guessable space), but avoid the
  // obvious short-circuit-on-first-mismatch timing leak anyway.
  if (!env.D1_PROXY_TOKEN || header.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < header.length; i++) diff |= header.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

async function handleExec(request, env) {
  const body = await request.json();
  const { sql, params } = body || {};
  if (typeof sql !== "string" || !sql.trim()) {
    return new Response(JSON.stringify({ error: "sql (string) is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  try {
    const stmt = env.DB.prepare(sql).bind(...(params || []));
    const result = await stmt.all();
    return new Response(
      JSON.stringify({
        success: true,
        results: result.results || [],
        meta: {
          last_row_id: result.meta?.last_row_id ?? null,
          changes: result.meta?.changes ?? 0,
          rows_read: result.meta?.rows_read ?? 0,
          rows_written: result.meta?.rows_written ?? 0,
        },
      }),
      { headers: { "Content-Type": "application/json" } }
    );
  } catch (exc) {
    return new Response(JSON.stringify({ success: false, error: String(exc) }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
}

async function handleBatch(request, env) {
  const body = await request.json();
  const statements = body?.statements;
  if (!Array.isArray(statements) || statements.length === 0) {
    return new Response(JSON.stringify({ error: "statements (non-empty array) is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  try {
    const prepared = statements.map((s) => env.DB.prepare(s.sql).bind(...(s.params || [])));
    const results = await env.DB.batch(prepared);
    return new Response(
      JSON.stringify({
        success: true,
        results: results.map((r) => ({
          results: r.results || [],
          meta: {
            last_row_id: r.meta?.last_row_id ?? null,
            changes: r.meta?.changes ?? 0,
          },
        })),
      }),
      { headers: { "Content-Type": "application/json" } }
    );
  } catch (exc) {
    return new Response(JSON.stringify({ success: false, error: String(exc) }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
}

export default {
  async fetch(request, env, ctx) {
    if (!checkAuth(request, env)) return unauthorized();

    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/d1/exec") {
      return handleExec(request, env);
    }
    if (request.method === "POST" && url.pathname === "/d1/batch") {
      return handleBatch(request, env);
    }
    return new Response(JSON.stringify({ error: "not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  },
};
