// cloudflare/worker.js
// ------------------------
// STUB — Phase 2.
//
// TODO: Webhook gateway that receives TradingView alerts and forwards
// them to the FastAPI backend (execution/api_server.py), acting as a
// lightweight auth/rate-limit layer in front of the origin server.

export default {
  async fetch(request, env, ctx) {
    return new Response(
      "IATIS Cloudflare Worker — not yet implemented (Phase 2)",
      { status: 501 }
    );
  },
};
