"""
backtest/report.py
-------------------
Generate professional HTML backtest reports.

Output: reports/SYMBOL_TF_DATE.html
Includes:
  - Equity curve (interactive Plotly)
  - Monthly returns heatmap
  - Drawdown chart
  - Trade list table
  - Engine performance
  - Regime analysis
  - Monte Carlo results
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from backtest.metrics import BacktestMetrics, TradeRecord
from backtest.monte_carlo import MonteCarloResult


REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)


def _color(val: float, good_positive: bool = True) -> str:
    if good_positive:
        return "#00e676" if val > 0 else "#ff5252" if val < 0 else "#ffffff"
    else:
        return "#ff5252" if val > 0 else "#00e676" if val < 0 else "#ffffff"


def generate_html_report(
    metrics: BacktestMetrics,
    trades: list[TradeRecord],
    mc: MonteCarloResult | None = None,
    symbol: str = "ALL",
    timeframe: str = "H1",
    config: dict | None = None,
) -> Path:
    """Generate HTML report and save to reports/ directory."""

    equity_data = []
    bal = 10_000.0
    equity_data.append({"x": "Start", "y": bal})
    for i, t in enumerate(sorted(trades, key=lambda x: x.entry_time)):
        if t.exit_price is not None:
            bal += t.pnl_usd
            equity_data.append({
                "x": str(t.exit_time)[:16] if t.exit_time else str(i),
                "y": round(bal, 2)
            })

    monthly_rows = ""
    for month, pnl in sorted(metrics.monthly_returns.items()):
        color = _color(pnl)
        monthly_rows += f'<tr><td>{month}</td><td style="color:{color}">${pnl:+,.2f}</td></tr>'

    trade_rows = ""
    for t in sorted(trades, key=lambda x: x.entry_time)[:200]:
        if t.exit_price is None:
            continue
        color = "#00e676" if t.is_win else "#ff5252"
        trade_rows += f"""<tr>
            <td>{str(t.entry_time)[:16]}</td>
            <td>{t.symbol}</td>
            <td>{t.direction}</td>
            <td>{t.entry_price:.5f}</td>
            <td>{t.exit_price:.5f}</td>
            <td style="color:{color}">${t.pnl_usd:+.2f}</td>
            <td>{t.rr_actual:.2f}</td>
            <td>{t.regime}</td>
            <td>{'WIN' if t.is_win else 'LOSS'}</td>
        </tr>"""

    regime_rows = ""
    for regime, data in metrics.by_regime.items():
        wr = data.get("win_rate", 0)
        pnl = data.get("pnl", 0)
        color = _color(pnl)
        regime_rows += f"""<tr>
            <td>{regime}</td>
            <td>{data.get('trades',0)}</td>
            <td>{wr:.1f}%</td>
            <td style="color:{color}">${pnl:+,.2f}</td>
        </tr>"""

    mc_section = ""
    if mc:
        mc_section = f"""
        <div class="section">
          <h2>🎲 Monte Carlo ({mc.simulations} simulations)</h2>
          <div class="kpi-grid">
            <div class="kpi"><div class="val" style="color:#00d4ff">{mc.median_return:.1f}%</div><div class="lbl">Median Return</div></div>
            <div class="kpi"><div class="val" style="color:#ff5252">{mc.p5_return:.1f}%</div><div class="lbl">5th Pct Return</div></div>
            <div class="kpi"><div class="val" style="color:#00e676">{mc.p95_return:.1f}%</div><div class="lbl">95th Pct Return</div></div>
            <div class="kpi"><div class="val" style="color:#ff5252">{mc.worst_max_dd:.1f}%</div><div class="lbl">Worst DD</div></div>
            <div class="kpi"><div class="val" style="color:#ffab40">{mc.risk_of_ruin:.1f}%</div><div class="lbl">Risk of Ruin</div></div>
            <div class="kpi"><div class="val" style="color:#00e676">{mc.probability_profit:.1f}%</div><div class="lbl">P(Profit)</div></div>
          </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IATIS Backtest — {symbol} {timeframe}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:#080c14; --surface:#0e1420; --border:#1a2236;
    --accent:#00d4ff; --green:#00e676; --red:#ff5252;
    --amber:#ffab40; --text:#e2e8f0; --muted:#64748b;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'SF Mono',monospace; background:var(--bg); color:var(--text); padding:20px; }}
  h1 {{ color:var(--accent); font-size:1.5em; margin-bottom:4px; }}
  h2 {{ color:var(--accent); font-size:0.9em; text-transform:uppercase; letter-spacing:2px; margin:16px 0 8px; }}
  .subtitle {{ color:var(--muted); font-size:0.85em; margin-bottom:24px; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-bottom:20px; }}
  .kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px; }}
  .kpi .val {{ font-size:1.6em; font-weight:800; }}
  .kpi .lbl {{ font-size:0.7em; color:var(--muted); text-transform:uppercase; margin-top:2px; }}
  .section {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; margin-bottom:16px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.8em; }}
  th {{ background:var(--bg); color:var(--muted); padding:8px; text-align:left; font-size:0.75em; text-transform:uppercase; }}
  td {{ padding:7px 8px; border-bottom:1px solid var(--border); }}
  tr:last-child td {{ border-bottom:none; }}
  .chart-container {{ height:300px; margin-bottom:16px; }}
  .good {{ color:var(--green); }} .bad {{ color:var(--red); }} .warn {{ color:var(--amber); }}
</style>
</head>
<body>
<h1>⚡ IATIS Backtest Report</h1>
<div class="subtitle">{symbol} {timeframe} · Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC · {metrics.total_trades} trades</div>

<!-- KPIs -->
<div class="kpi-grid">
  <div class="kpi"><div class="val {'good' if metrics.net_profit>0 else 'bad'}">${metrics.net_profit:+,.0f}</div><div class="lbl">Net Profit</div></div>
  <div class="kpi"><div class="val {'good' if metrics.win_rate>=50 else 'warn' if metrics.win_rate>=40 else 'bad'}">{metrics.win_rate:.1f}%</div><div class="lbl">Win Rate</div></div>
  <div class="kpi"><div class="val {'good' if metrics.profit_factor>=1.5 else 'warn' if metrics.profit_factor>=1.0 else 'bad'}">{metrics.profit_factor:.2f}</div><div class="lbl">Profit Factor</div></div>
  <div class="kpi"><div class="val {'good' if metrics.sharpe_ratio>=1 else 'warn' if metrics.sharpe_ratio>=0 else 'bad'}">{metrics.sharpe_ratio:.2f}</div><div class="lbl">Sharpe Ratio</div></div>
  <div class="kpi"><div class="val bad">-{metrics.max_drawdown:.1f}%</div><div class="lbl">Max Drawdown</div></div>
  <div class="kpi"><div class="val {'good' if metrics.calmar_ratio>=1 else 'warn' if metrics.calmar_ratio>=0 else 'bad'}">{metrics.calmar_ratio:.2f}</div><div class="lbl">Calmar Ratio</div></div>
  <div class="kpi"><div class="val">{metrics.total_trades}</div><div class="lbl">Total Trades</div></div>
  <div class="kpi"><div class="val {'good' if metrics.total_return_pct>0 else 'bad'}">{metrics.total_return_pct:+.1f}%</div><div class="lbl">Total Return</div></div>
</div>

<!-- Equity Curve -->
<div class="section">
  <h2>📈 Equity Curve</h2>
  <div class="chart-container">
    <canvas id="equityChart"></canvas>
  </div>
</div>

<!-- Detailed Stats -->
<div class="section">
  <h2>📊 Detailed Statistics</h2>
  <table>
    <tr><th>Metric</th><th>Value</th><th>Metric</th><th>Value</th></tr>
    <tr><td>Winning Trades</td><td class="good">{metrics.winning_trades}</td><td>Losing Trades</td><td class="bad">{metrics.losing_trades}</td></tr>
    <tr><td>Avg Win</td><td class="good">${metrics.avg_win:+.2f}</td><td>Avg Loss</td><td class="bad">-${metrics.avg_loss:.2f}</td></tr>
    <tr><td>Largest Win</td><td class="good">${metrics.largest_win:+.2f}</td><td>Largest Loss</td><td class="bad">${metrics.largest_loss:.2f}</td></tr>
    <tr><td>Expectancy ($)</td><td class="{'good' if metrics.expectancy>0 else 'bad'}">${metrics.expectancy:+.2f}</td><td>Expectancy (R)</td><td class="{'good' if metrics.expectancy_r>0 else 'bad'}">{metrics.expectancy_r:+.3f}R</td></tr>
    <tr><td>Avg R:R</td><td>{metrics.avg_rr:.2f}</td><td>Avg Hold (bars)</td><td>{metrics.avg_holding_bars:.0f}</td></tr>
    <tr><td>Max Consec Wins</td><td class="good">{metrics.max_consecutive_wins}</td><td>Max Consec Losses</td><td class="bad">{metrics.max_consecutive_losses}</td></tr>
    <tr><td>Sortino Ratio</td><td>{metrics.sortino_ratio:.2f}</td><td>Annual Return</td><td class="{'good' if metrics.annual_return>0 else 'bad'}">{metrics.annual_return:+.1f}%</td></tr>
    <tr><td>Avg MFE</td><td>{metrics.avg_mfe:.4f}</td><td>Avg MAE</td><td>{metrics.avg_mae:.4f}</td></tr>
  </table>
</div>

<!-- Regime Analysis -->
<div class="section">
  <h2>🌊 Regime Performance</h2>
  <table>
    <tr><th>Regime</th><th>Trades</th><th>Win Rate</th><th>P&L</th></tr>
    {regime_rows}
  </table>
</div>

<!-- Monthly Returns -->
<div class="section">
  <h2>📅 Monthly Returns</h2>
  <table>
    <tr><th>Month</th><th>P&L</th></tr>
    {monthly_rows}
  </table>
</div>

{mc_section}

<!-- Trade List -->
<div class="section">
  <h2>📋 Trade List (first 200)</h2>
  <table>
    <tr><th>Entry</th><th>Symbol</th><th>Dir</th><th>Entry Px</th><th>Exit Px</th><th>P&L</th><th>R:R</th><th>Regime</th><th>Result</th></tr>
    {trade_rows}
  </table>
</div>

<script>
const eqData = {json.dumps(equity_data)};
const ctx = document.getElementById('equityChart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: eqData.map(d => d.x),
    datasets: [{{
      label: 'Equity',
      data: eqData.map(d => d.y),
      borderColor: '#00d4ff',
      backgroundColor: 'rgba(0,212,255,0.05)',
      borderWidth: 2,
      pointRadius: 0,
      fill: true,
      tension: 0.1,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#64748b', maxTicksLimit: 10 }}, grid: {{ color: '#1a2236' }} }},
      y: {{ ticks: {{ color: '#64748b', callback: v => '$'+v.toLocaleString() }}, grid: {{ color: '#1a2236' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    date_str = datetime.now().strftime("%Y%m%d")
    out_path = REPORTS_DIR / f"{symbol}_{timeframe}_{date_str}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
