"""
execution/routes/dashboard_legacy.py
---------------------------------------
GET /dashboard — the legacy server-rendered HTML dashboard. Runs in
parallel with the React SPA (mounted at /app by execution/api_server.py)
by design (dashboard/README.md) — not duplication to remove.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from execution.api_core import _active_sessions, _save_sessions

router = APIRouter()


@router.get("/dashboard")
async def dashboard(
    request: Request,
    iatis_session: str | None = Cookie(default=None),
):
    """Dashboard — requires valid session, redirects to login if not authenticated."""
    # Server-side session check — no JS required
    if not iatis_session or iatis_session not in _active_sessions:
        return RedirectResponse(url="/login", status_code=302)
    # Refresh session TTL
    _active_sessions[iatis_session] = time.time()
    _save_sessions(_active_sessions)
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IATIS — Market Intelligence</title>
<style>
  :root {
    --bg: #080c14;
    --surface: #0e1420;
    --border: #1a2236;
    --accent: #00d4ff;
    --accent2: #7c5cfc;
    --green: #00e676;
    --red: #ff5252;
    --amber: #ffab40;
    --text: #e2e8f0;
    --muted: #64748b;
    --card-bg: #111827;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'SF Mono', 'Fira Code', monospace; background: var(--bg); color: var(--text); min-height: 100vh; }

  /* Header */
  header { display: flex; align-items: center; justify-content: space-between;
    padding: 16px 24px; border-bottom: 1px solid var(--border);
    background: linear-gradient(90deg, #080c14 0%, #0d1829 100%); }
  .logo { display: flex; align-items: center; gap: 10px; }
  .logo-icon { width: 32px; height: 32px; background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 18px; }
  .logo-text { font-size: 1.1em; font-weight: 700; color: var(--accent); letter-spacing: 2px; }
  .logo-sub { font-size: 0.65em; color: var(--muted); letter-spacing: 1px; }
  #clock { font-size: 0.8em; color: var(--muted); }
  nav a { color: var(--muted); text-decoration: none; font-size: 0.75em; margin-left: 16px; transition: color 0.2s; }
  nav a:hover { color: var(--accent); }

  /* Main */
  main { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }

  /* Status bar */
  #statusbar { display: flex; align-items: center; gap: 8px;
    padding: 8px 14px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; margin-bottom: 20px; font-size: 0.78em; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 6px var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .dot.err { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot.loading { background: var(--amber); box-shadow: 0 0 6px var(--amber); }

  /* KPI cards */
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .kpi { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; position: relative; overflow: hidden; }
  .kpi::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, var(--accent), var(--accent2)); }
  .kpi .val { font-size: 1.8em; font-weight: 800; line-height: 1; margin-bottom: 4px; }
  .kpi .lbl { font-size: 0.7em; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
  .kpi.green .val { color: var(--green); }
  .kpi.red .val { color: var(--red); }
  .kpi.blue .val { color: var(--accent); }
  .kpi.purple .val { color: var(--accent2); }
  .kpi.amber .val { color: var(--amber); }

  /* Grid layout */
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 768px) { .grid2 { grid-template-columns: 1fr; } }

  /* Panels */
  .panel { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .panel-header { display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .panel-title { font-size: 0.8em; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 1.5px; }
  .panel-body { padding: 0; }

  /* Table */
  table { width: 100%; border-collapse: collapse; font-size: 0.82em; }
  th { padding: 8px 12px; color: var(--muted); font-size: 0.75em; text-transform: uppercase;
    letter-spacing: 0.8px; text-align: left; background: var(--surface); font-weight: 600; }
  td { padding: 9px 12px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(0,212,255,0.03); }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 700; }
  .badge.exec { background: rgba(0,230,118,0.15); color: var(--green); }
  .badge.no-trade { background: rgba(255,82,82,0.1); color: var(--red); }
  .badge.good { background: rgba(0,212,255,0.12); color: var(--accent); }
  .badge.marginal { background: rgba(255,171,64,0.12); color: var(--amber); }
  .badge.poor { background: rgba(255,82,82,0.1); color: var(--red); }

  /* Signal list */
  .signal { display: flex; align-items: flex-start; gap: 10px;
    padding: 10px 14px; border-bottom: 1px solid var(--border); }
  .signal:last-child { border-bottom: none; }
  .signal-sym { font-weight: 800; min-width: 70px; color: var(--accent); }
  .signal-info { flex: 1; font-size: 0.82em; color: var(--muted); line-height: 1.5; }
  .signal-score { font-size: 1.1em; font-weight: 700; min-width: 40px; text-align: right; }

  /* Gauge bar */
  .gauge { height: 4px; background: var(--border); border-radius: 2px; margin-top: 4px; }
  .gauge-fill { height: 100%; border-radius: 2px; transition: width 0.8s ease; }

  /* Empty state */
  .empty { padding: 32px; text-align: center; color: var(--muted); font-size: 0.85em; }

  .spin { animation: spin 1s linear infinite; display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-icon">⚡</div>
    <div>
      <div class="logo-text">IATIS</div>
      <div class="logo-sub">MARKET INTELLIGENCE PLATFORM</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span id="clock" style="color:var(--muted);font-size:0.78em"></span>
    <nav>
      <a href="/health">Health</a>
      <a href="/research">Research</a>
      <a href="/outcomes">Outcomes</a>
      <a href="/logout">Logout</a>
    </nav>
  </div>
</header>

<main>
  <div id="statusbar">
    <div class="dot loading" id="dot"></div>
    <span id="statustext">Connecting to IATIS...</span>
  </div>

  <!-- KPIs -->
  <div class="kpi-grid" id="kpis">
    <div class="kpi blue"><div class="val spin">⟳</div><div class="lbl">Total Decisions</div></div>
    <div class="kpi green"><div class="val">—</div><div class="lbl">EXECUTE</div></div>
    <div class="kpi amber"><div class="val">—</div><div class="lbl">API Credits</div></div>
    <div class="kpi purple"><div class="val">—</div><div class="lbl">Execute Rate</div></div>
    <div class="kpi"><div class="val">—</div><div class="lbl">Active Symbols</div></div>
  </div>

  <div class="grid2">
    <!-- Last decisions -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">⟳ Last Decisions</span>
        <span style="font-size:0.7em;color:var(--muted)" id="last-run">—</span>
      </div>
      <div class="panel-body" id="decisions-panel">
        <div class="empty">Loading...</div>
      </div>
    </div>

    <!-- Symbol Health -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">💊 Symbol Health</span>
      </div>
      <div class="panel-body" id="health-panel">
        <div class="empty">Loading...</div>
      </div>
    </div>
  </div>

  <!-- Backtest Results -->
  <div class="panel" style="margin-bottom:16px">
    <div class="panel-header">
      <span class="panel-title">📊 Backtest Results (v0.5)</span>
    </div>
    <div class="panel-body" id="bt-panel">
      <div class="empty">Loading...</div>
    </div>
  </div>

  <!-- Open Outcomes -->
  <div class="panel" style="margin-bottom:16px">
    <div class="panel-header">
      <span class="panel-title">📈 Open Signals</span>
      <span style="font-size:0.7em;color:var(--muted)">Paper trading</span>
    </div>
    <div class="panel-body" id="outcomes-panel">
      <div class="empty">No open signals</div>
    </div>
  </div>
</main>

<script>
const H = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

// Clock
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toUTCString().slice(0,25) + ' UTC';
}, 1000);

async function api(path) {
  const storedKey = sessionStorage.getItem('iatis_key') || localStorage.getItem('iatis_key');
  const headers = storedKey ? {'X-API-Key': storedKey} : {};
  const r = await fetch(path, {credentials:'include', headers});
  if (r.status === 401) { window.location.href='/login'; throw new Error('auth'); }
  if (!r.ok) throw new Error(r.status + ' ' + path);
  return r.json();
}

function scoreColor(s) {
  s = parseFloat(s||0);
  return s >= 65 ? 'var(--green)' : s >= 55 ? 'var(--amber)' : 'var(--red)';
}

function pfBadge(pf) {
  pf = parseFloat(pf||0);
  if (pf >= 1.5) return 'good';
  if (pf >= 1.1) return 'marginal';
  return 'poor';
}

async function load() {
  const dot = document.getElementById('dot');
  const st = document.getElementById('statustext');
  dot.className = 'dot loading';
  st.textContent = 'Refreshing...';

  try {
    // Load critical data first (fast endpoints)
    const [health, stats] = await Promise.all([
      api('/health'), api('/stats')
    ]);

    // KPIs
    const s = stats.summary || {};
    const total = s.total || 0;
    const exec = s.execute || 0;
    const credits = health.twelve_data_credits_remaining ?? '?';
    const execRate = total > 0 ? (exec/total*100).toFixed(1)+'%' : '--';
    const creditClass = credits > 400 ? 'green' : credits > 100 ? 'amber' : 'red';

    document.getElementById('kpis').innerHTML = `
      <div class="kpi blue"><div class="val">${H(total)}</div><div class="lbl">Total Decisions</div></div>
      <div class="kpi green"><div class="val">${H(exec)}</div><div class="lbl">EXECUTE</div></div>
      <div class="kpi ${creditClass}"><div class="val">${H(credits)}</div><div class="lbl">API Credits</div></div>
      <div class="kpi purple"><div class="val">${execRate}</div><div class="lbl">Execute Rate</div></div>
      <div class="kpi"><div class="val">${H(total - exec)}</div><div class="lbl">NO_TRADE</div></div>
    `;

    dot.className = 'dot';
    st.textContent = `Live · v${H(health.version)} · ${new Date().toLocaleTimeString()} UTC`;

    // Load decisions
    try {
      const decisions = await api('/decisions?limit=8');
      const dec = decisions.decisions || [];
      if (dec.length) {
        let html = '';
        for (const d of dec) {
          const isExec = d.verdict === 'EXECUTE';
          const score = parseFloat(d.cf_score||0);
          const reason = (d.fail_reason || d.summary || '').slice(0, 60);
          const ts = (d.ts||'').slice(11,19);
          html += `<div class="signal">
            <div>
              <div class="signal-sym">${H(d.symbol||'?')}</div>
              <div style="font-size:0.7em;color:var(--muted)">${ts}</div>
            </div>
            <div class="signal-info">
              <span class="badge ${isExec ? 'exec' : 'no-trade'}">${H(d.verdict)}</span>
              ${H(d.regime||'')}
              <div style="color:var(--muted);font-size:0.9em;margin-top:2px">${H(reason)}</div>
            </div>
            <div class="signal-score" style="color:${scoreColor(score)}">${score.toFixed(0)}</div>
          </div>`;
        }
        document.getElementById('decisions-panel').innerHTML = html;
        document.getElementById('last-run').textContent = dec[0]?.ts?.slice(0,19) || '—';
      } else {
        document.getElementById('decisions-panel').innerHTML = '<div class="empty">No decisions yet</div>';
      }
    } catch(e) {
      document.getElementById('decisions-panel').innerHTML = '<div class="empty">Could not load decisions</div>';
    }

    // Load symbol health (may be slow)
    try {
      const sh = await api('/symbol-health');
      const syms = sh.symbols || [];
      if (syms.length) {
        let shHtml = '<table><tr><th>Symbol</th><th>SHI</th><th>Status</th><th>WR</th><th>Trades</th></tr>';
        for (const s of syms) {
          const statusColor = s.status === 'HEALTHY' ? 'var(--green)' : s.status === 'CAUTION' ? 'var(--amber)' : 'var(--red)';
          const wr = s.win_rate != null ? s.win_rate.toFixed(1)+'%' : '—';
          shHtml += `<tr>
            <td style="font-weight:700;color:var(--accent)">${H(s.symbol)}</td>
            <td>${H(s.shi_score)}</td>
            <td style="color:${statusColor};font-weight:700">${H(s.status)}</td>
            <td>${wr}</td>
            <td style="color:var(--muted)">${H(s.trades_count)}</td>
          </tr>`;
        }
        shHtml += '</table>';
        document.getElementById('health-panel').innerHTML = shHtml;
      }
    } catch(e) {
      document.getElementById('health-panel').innerHTML = '<div class="empty">No symbol health data yet (need closed trades)</div>';
    }

    // Load backtest
    try {
      const bt = await api('/backtest-results');
      const results = (bt.results || []).filter(r => !r.error && r.trades >= 10)
        .sort((a,b) => (b.profit_factor||0) - (a.profit_factor||0));
      if (results.length) {
        let btHtml = '<table><tr><th>Symbol</th><th>Trades</th><th>WR%</th><th>PF</th><th>DD%</th><th>Return%</th></tr>';
        for (const r of results) {
          const badge = pfBadge(r.profit_factor);
          btHtml += `<tr>
            <td style="font-weight:700;color:var(--accent)">${H(r.symbol)}</td>
            <td>${H(r.trades)}</td>
            <td>${H(r.win_rate)}%</td>
            <td><span class="badge ${badge}">${parseFloat(r.profit_factor||0).toFixed(2)}</span></td>
            <td style="color:var(--red)">${H(r.max_drawdown_pct)}%</td>
            <td style="color:${parseFloat(r.total_return_pct||0)>=0?'var(--green)':'var(--red)'}">${H(r.total_return_pct)}%</td>
          </tr>`;
        }
        btHtml += '</table>';
        document.getElementById('bt-panel').innerHTML = btHtml;
      } else {
        document.getElementById('bt-panel').innerHTML = '<div class="empty">No backtest results yet</div>';
      }
    } catch(e) {
      document.getElementById('bt-panel').innerHTML = '<div class="empty">No backtest data</div>';
    }

    // Load open outcomes
    try {
      const outcomes = await api('/outcomes');
      const open = outcomes.open_signals || [];
      if (open.length) {
        let oHtml = '<table><tr><th>Signal ID</th><th>Symbol</th><th>Direction</th><th>Entry</th><th>Score</th></tr>';
        for (const o of open) {
          const dirColor = (o.direction||'') === 'BULLISH' ? 'var(--green)' : 'var(--red)';
          oHtml += `<tr>
            <td style="font-size:0.75em;color:var(--muted)">${H(o.signal_id)}</td>
            <td style="font-weight:700;color:var(--accent)">${H(o.symbol)}</td>
            <td style="color:${dirColor};font-weight:700">${H(o.direction)}</td>
            <td>${H(o.entry_price)}</td>
            <td style="color:${scoreColor(o.cf_score)}">${H(o.cf_score)}</td>
          </tr>`;
        }
        oHtml += '</table>';
        document.getElementById('outcomes-panel').innerHTML = oHtml;
      }
    } catch(e) { /* silent */ }

    setTimeout(load, 60000);

  } catch(e) {
    dot.className = 'dot err';
    st.textContent = 'Error: ' + e.message + ' — retrying in 15s';
    setTimeout(load, 15000);
  }
}
load();
</script>
</body>
</html>""")

