#!/usr/bin/env python3
"""
scripts/backtest_all_symbols.py
---------------------------------
Run IATIS full pipeline backtest on ALL available symbols.

Produces:
  - Console summary table
  - storage/backtest_report_FULL.json   (raw data)
  - storage/backtest_report.html        (visual report, open in browser)

Usage:
    python3 scripts/backtest_all_symbols.py
    python3 scripts/backtest_all_symbols.py --step 8     # faster (every 8 bars)
    python3 scripts/backtest_all_symbols.py --symbols EURUSD GBPUSD
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import asset metadata from download script
from scripts.download_all_symbols import ALL_SYMBOLS, PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT


def run_backtest_for_symbol(symbol: str, csv_path: Path, step: int) -> dict | None:
    """Run backtest for one symbol. Returns result dict or None on failure."""
    from core.data_loader import load_from_csv
    from backtesting.backtest_engine import run_backtest, BacktestConfig

    try:
        df = load_from_csv(str(csv_path))
        if len(df) < 300:
            return {"symbol": symbol, "error": f"Insufficient data: {len(df)} bars"}

        ac = ASSET_CLASS.get(symbol, "forex")
        dpp = DOLLAR_PER_POINT.get(symbol, 1.0)
        pip = PIP_SIZE.get(symbol, 0.0001)

        config = BacktestConfig(
            symbol=symbol,
            initial_balance=10_000.0,
            risk_per_trade=0.01,
            pip_size=pip,
            asset_class=ac,
            dollar_per_point=dpp,
            step_bars=step,
        )

        result = run_backtest(df, config)

        # Save individual JSON
        out_path = Path("storage") / f"backtest_{symbol}_H1.json"
        result.save(out_path)

        return {
            "symbol": symbol,
            "asset_class": ac,
            "period": f"{df.index[0].date()} → {df.index[-1].date()}",
            "total_bars": len(df),
            "total_runs": result.total_runs,
            "execute_count": result.execute_count,
            "execute_rate": round(result.execute_count / max(result.total_runs, 1) * 100, 1),
            "trades": result.execute_count,
            "win_rate": round(result.win_rate * 100, 1),
            "profit_factor": round(result.profit_factor, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct * 100, 1),
            "total_return_pct": round(result.total_return_pct * 100, 1),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
            "error": None,
        }

    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)[:120]}


def classify_result(r: dict) -> str:
    """Classify result quality."""
    if r.get("error"):
        return "ERROR"
    pf = r.get("profit_factor", 0)
    wr = r.get("win_rate", 0)
    dd = r.get("max_drawdown_pct", 100)
    trades = r.get("trades", 0)
    if trades < 10:
        return "INSUFFICIENT_TRADES"
    if pf >= 1.5 and wr >= 50 and dd <= 15:
        return "GOOD"
    if pf >= 1.2 and wr >= 45:
        return "MARGINAL"
    return "POOR"


def generate_html_report(results: list[dict], duration_sec: float) -> str:
    """Generate HTML report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    good = [r for r in results if classify_result(r) == "GOOD"]
    marginal = [r for r in results if classify_result(r) == "MARGINAL"]
    poor = [r for r in results if classify_result(r) == "POOR"]
    errors = [r for r in results if classify_result(r) in ("ERROR", "INSUFFICIENT_TRADES")]

    def row(r: dict) -> str:
        if r.get("error"):
            return (f"<tr class='err'><td>{r['symbol']}</td>"
                    f"<td colspan=9 style='color:#f85149'>{r['error']}</td></tr>")
        grade = classify_result(r)
        color = {"GOOD": "#3fb950", "MARGINAL": "#d29922",
                 "POOR": "#f85149", "INSUFFICIENT_TRADES": "#8b949e"}.get(grade, "#8b949e")
        pf_color = "#3fb950" if r["profit_factor"] >= 1.5 else "#d29922" if r["profit_factor"] >= 1.0 else "#f85149"
        wr_color = "#3fb950" if r["win_rate"] >= 55 else "#d29922" if r["win_rate"] >= 45 else "#f85149"
        return (
            f"<tr>"
            f"<td><b>{r['symbol']}</b></td>"
            f"<td style='color:#8b949e;font-size:0.85em'>{r.get('asset_class','?')}</td>"
            f"<td>{r.get('period','?')}</td>"
            f"<td>{r.get('trades',0)}</td>"
            f"<td style='color:{wr_color}'>{r.get('win_rate',0):.1f}%</td>"
            f"<td style='color:{pf_color}'>{r.get('profit_factor',0):.2f}</td>"
            f"<td>{r.get('total_return_pct',0):.1f}%</td>"
            f"<td style='color:#f85149'>{r.get('max_drawdown_pct',0):.1f}%</td>"
            f"<td>{r.get('sharpe_ratio',0):.2f}</td>"
            f"<td style='color:{color}'><b>{grade}</b></td>"
            f"</tr>"
        )

    rows_html = "\n".join(row(r) for r in sorted(results, key=lambda x: (
        0 if classify_result(x) == "GOOD" else
        1 if classify_result(x) == "MARGINAL" else
        2 if classify_result(x) == "POOR" else 3
    )))

    css = (
        "body{font-family:monospace;background:#0d1117;color:#c9d1d9;margin:24px}"
        "h1{color:#58a6ff}h2{color:#79c0ff;border-bottom:1px solid #30363d;padding-bottom:4px}"
        ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}"
        ".card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 20px;min-width:120px}"
        ".val{font-size:1.8em;font-weight:bold}.lbl{color:#8b949e;font-size:0.8em}"
        "table{width:100%;border-collapse:collapse;margin:8px 0}"
        "th{background:#161b22;color:#8b949e;text-align:left;padding:8px;font-size:0.85em}"
        "td{padding:8px;border-bottom:1px solid #21262d;font-size:0.9em}"
        "tr:hover td{background:#161b22}"
        ".ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}"
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>IATIS Full Backtest Report</title>
<style>{css}</style>
</head>
<body>
<h1>&#x1F4CA; IATIS Full Backtest Report</h1>
<p style="color:#8b949e">Generated: {now} | Duration: {duration_sec:.0f}s | Strategy: 6-Engine Confluence</p>

<div class="cards">
  <div class="card"><div class="val">{len(results)}</div><div class="lbl">Total Symbols</div></div>
  <div class="card"><div class="val ok">{len(good)}</div><div class="lbl">GOOD (PF≥1.5)</div></div>
  <div class="card"><div class="val warn">{len(marginal)}</div><div class="lbl">MARGINAL</div></div>
  <div class="card"><div class="val bad">{len(poor)}</div><div class="lbl">POOR</div></div>
  <div class="card"><div class="val" style="color:#8b949e">{len(errors)}</div><div class="lbl">Error/Skip</div></div>
</div>

<h2>Results by Symbol</h2>
<p style="color:#8b949e;font-size:0.85em">
  Criteria: GOOD = PF≥1.5 &amp; WR≥50% &amp; DD≤15% | MARGINAL = PF≥1.2 | POOR = below marginal
</p>
<table>
<tr>
  <th>Symbol</th><th>Class</th><th>Period</th><th>Trades</th>
  <th>Win Rate</th><th>Profit Factor</th><th>Return</th>
  <th>Max DD</th><th>Sharpe</th><th>Grade</th>
</tr>
{rows_html}
</table>

<h2>&#x26A0; Important Caveats</h2>
<ul style="color:#8b949e;line-height:1.8">
  <li>In-sample data only — same period used for development</li>
  <li>No slippage modeled — real slippage reduces performance</li>
  <li>Commission = 0.5 pips for FX, 0 for others</li>
  <li>Position sizing: 1% risk per trade, 1:3 minimum R:R</li>
  <li>Walk-forward simulation (no lookahead bias)</li>
  <li>GOOD grade does NOT guarantee future performance</li>
</ul>

<p style="color:#8b949e;font-size:0.8em;margin-top:32px">
  IATIS v0.3.1 | Full backtest across all asset classes
</p>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=4,
                        help="Run pipeline every N bars (default: 4, faster: 8+)")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Specific symbols (default: all available CSVs)")
    args = parser.parse_args()

    data_dir = Path("data")
    storage_dir = Path("storage")
    storage_dir.mkdir(exist_ok=True)

    # Find available CSV files
    if args.symbols:
        csv_files = {s: data_dir / f"{s}_H1_2y.csv" for s in args.symbols}
        csv_files = {s: p for s, p in csv_files.items() if p.exists()}
    else:
        csv_files = {}
        for sym in ALL_SYMBOLS:
            for pattern in [f"{sym}_H1_2y.csv", f"{sym}_H1_5y.csv", f"{sym}_H1_1y.csv"]:
                p = data_dir / pattern
                if p.exists():
                    csv_files[sym] = p
                    break

    if not csv_files:
        print("No CSV files found in data/")
        print("Run first: python3 scripts/download_all_symbols.py")
        sys.exit(1)

    print(f"\n{'='*65}")
    print(f"IATIS Full Multi-Symbol Backtest")
    print(f"{'='*65}")
    print(f"Strategy:  6-Engine Confluence (SMC+PA+ICT+NNFX+Quant+Wyckoff)")
    print(f"Symbols:   {len(csv_files)}")
    print(f"Step:      every {args.step} bars")
    print(f"Risk:      1% per trade, 1:3 minimum R:R")
    print()

    results = []
    t_start = time.monotonic()

    for i, (symbol, csv_path) in enumerate(csv_files.items(), 1):
        ac = ASSET_CLASS.get(symbol, "?")
        print(f"[{i:2}/{len(csv_files)}] {symbol:10} ({ac:6}) ... ", end="", flush=True)
        t0 = time.monotonic()
        result = run_backtest_for_symbol(symbol, csv_path, args.step)
        elapsed = time.monotonic() - t0

        if result is None:
            print(f"❌ skipped")
            continue

        if result.get("error"):
            print(f"❌ {result['error'][:60]}")
        else:
            grade = classify_result(result)
            icon = {"GOOD": "✅", "MARGINAL": "⚠️", "POOR": "❌",
                    "INSUFFICIENT_TRADES": "⏭"}.get(grade, "?")
            print(
                f"{icon} WR={result['win_rate']:.1f}% "
                f"PF={result['profit_factor']:.2f} "
                f"DD={result['max_drawdown_pct']:.1f}% "
                f"Return={result['total_return_pct']:.1f}% "
                f"({elapsed:.0f}s)"
            )
        results.append(result)

    duration = time.monotonic() - t_start

    # Summary table
    valid = [r for r in results if not r.get("error")]
    print(f"\n{'='*65}")
    print(f"SUMMARY ({len(valid)}/{len(results)} symbols completed in {duration:.0f}s)")
    print(f"{'='*65}")
    print(f"{'Symbol':<10} {'Class':<7} {'Trades':>6} {'WR':>7} {'PF':>6} {'DD':>7} {'Return':>8} {'Grade'}")
    print("-" * 65)

    for r in sorted(valid, key=lambda x: classify_result(x)):
        grade = classify_result(r)
        print(
            f"{r['symbol']:<10} {r.get('asset_class','?'):<7} "
            f"{r.get('trades',0):>6} "
            f"{r.get('win_rate',0):>6.1f}% "
            f"{r.get('profit_factor',0):>6.2f} "
            f"{r.get('max_drawdown_pct',0):>6.1f}% "
            f"{r.get('total_return_pct',0):>7.1f}% "
            f"  {grade}"
        )

    # Grade breakdown
    grades = {g: sum(1 for r in valid if classify_result(r) == g)
              for g in ["GOOD", "MARGINAL", "POOR", "INSUFFICIENT_TRADES"]}
    print(f"\nGrades: GOOD={grades['GOOD']} MARGINAL={grades['MARGINAL']} "
          f"POOR={grades['POOR']} SKIP={grades['INSUFFICIENT_TRADES']}")

    # Save JSON
    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": "IATIS 6-Engine Confluence",
        "step_bars": args.step,
        "duration_sec": round(duration, 1),
        "results": results,
        "grade_summary": grades,
    }
    json_path = storage_dir / "backtest_report_FULL.json"
    json_path.write_text(json.dumps(report_data, indent=2, default=str))
    print(f"\nJSON saved: {json_path}")

    # Save HTML
    html_content = generate_html_report(results, duration)
    html_path = storage_dir / "backtest_report.html"
    html_path.write_text(html_content)
    print(f"HTML saved: {html_path}")
    print(f"\nOpen in browser: https://iatis.rahba.site → /backtest-results")
    print(f"Or: cat {html_path} > /tmp/report.html && open /tmp/report.html")


if __name__ == "__main__":
    main()
