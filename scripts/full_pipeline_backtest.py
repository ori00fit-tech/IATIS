#!/usr/bin/env python3
"""
scripts/full_pipeline_backtest.py
-----------------------------------
IATIS v0.4 Full Pipeline Backtest — uses run_pipeline() directly.

Key design: calls the ACTUAL run_pipeline() function, not a reimplementation.
This guarantees 100% consistency with the live trading system.

Two-pass design:
  Pass 1: run_pipeline() at bar i → if EXECUTE, record signal
  Pass 2: scan forward bars until SL or TP hit (no overlap)

Usage:
    python3 scripts/full_pipeline_backtest.py --all --step 8
    python3 scripts/full_pipeline_backtest.py --symbols EURUSD BTCUSD
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT

ACTIVE_SYMBOLS = [
    "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
    "EURJPY","AUDJPY","EURGBP","EURCHF",
    "XAUUSD","XAGUSD","USOIL",
    "US30","NAS100","SPX500","BTCUSD","ETHUSD",
]


def build_config(symbol: str) -> dict:
    from utils.helpers import load_config
    cfg = load_config()
    cfg["data"]["symbol"] = symbol
    cfg["data"]["twelve_data_symbol"] = symbol
    cfg["fundamentals"] = {"news_filter_enabled": False}
    cfg["telegram"] = {"enabled": False}
    return cfg


def simulate_trade(entry, sl, tp, direction, df, start_idx):
    n = len(df)
    for j in range(start_idx, min(start_idx + 300, n)):
        try:
            bar = df.iloc[j]
            h = float(bar["high"])
            l = float(bar["low"])
        except Exception:
            continue
        if direction == 1:
            if bool(l <= sl): return {"outcome":"loss","exit":sl,"bars":j-start_idx}
            if bool(h >= tp): return {"outcome":"win","exit":tp,"bars":j-start_idx}
        else:
            if bool(h >= sl): return {"outcome":"loss","exit":sl,"bars":j-start_idx}
            if bool(l <= tp): return {"outcome":"win","exit":tp,"bars":j-start_idx}
    last = float(df.iloc[min(start_idx+299, n-1)]["close"])
    diff = (last - entry) * direction
    return {"outcome":"win" if diff > 0 else "loss","exit":last,"bars":300}


def calc_pnl(entry, exit_p, direction, sl_dist, balance, symbol, ac, pip, dpp):
    risk_usd = balance * 0.01
    if ac == "forex":
        if "JPY" in symbol:
            pip_val = (pip / max(entry, 1)) * 100_000
        else:
            pip_val = pip * 100_000
        sl_pips = sl_dist / pip if pip > 0 else 1
        size = max(0.01, min(risk_usd / (sl_pips * pip_val), 10.0)) if sl_pips > 0 else 0.01
        pnl = ((exit_p - entry) * direction) / pip * pip_val * size
    elif ac == "crypto":
        size = max(0.001, min(risk_usd / sl_dist, 1.0)) if sl_dist > 0 else 0.001
        pnl = (exit_p - entry) * direction * size
    else:
        size = max(0.01, min(risk_usd / (sl_dist * dpp), 10.0)) if sl_dist > 0 else 0.01
        pnl = (exit_p - entry) * direction * size * dpp
    return round(pnl, 2)


def backtest_symbol(symbol, df, step=8, warmup=220):
    from main import run_pipeline

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)

    # Build config ONCE — update only _injected_df each iteration
    cfg = build_config(symbol)
    cfg["data"]["source"] = "injected"
    cfg.setdefault("system", {})["backtest_mode"] = True  # offline backtest: skip live persistence (D1)

    balance = 10_000.0
    peak = balance
    max_dd = 0.0
    trades = []
    blocks = {"mqs":0,"news":0,"contradiction":0,"score":0}
    total_runs = 0
    open_until = -1
    n = len(df)

    for i in range(warmup, n - 2, step):
        total_runs += 1
        if i <= open_until:
            continue

        # Use iloc view (no copy) to save memory
        cfg["data"]["_injected_df"] = df.iloc[:i+1]

        try:
            report = run_pipeline(cfg)
        except Exception:
            continue
        finally:
            # Free reference after each call
            cfg["data"]["_injected_df"] = None

        verdict = report.get("final_verdict", "NO_TRADE")

        reason = str(report.get("reason", report.get("summary", "")))
        if "Market Quality" in reason or "MQS" in reason:
            blocks["mqs"] += 1
        elif "news" in reason.lower() or "blackout" in reason.lower():
            blocks["news"] += 1
        elif "contradiction" in reason.lower():
            blocks["contradiction"] += 1
        elif verdict == "NO_TRADE":
            blocks["score"] += 1

        if verdict != "EXECUTE":
            continue

        entry = report.get("entry_price")
        sl = report.get("stop_loss")
        tp = report.get("take_profit")

        if not all([entry, sl, tp]):
            continue

        entry, sl, tp = float(entry), float(sl), float(tp)
        direction = 1 if sl < entry else -1
        sl_dist = abs(entry - sl)

        if sl_dist <= 0 or sl_dist > abs(tp - entry):
            continue

        sim = simulate_trade(entry, sl, tp, direction, df, i+1)
        pnl = calc_pnl(entry, sim["exit"], direction, sl_dist,
                       balance, symbol, ac, pip, dpp)

        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak
        max_dd = max(max_dd, dd)
        open_until = i + sim["bars"]
        trades.append({"outcome": sim["outcome"], "pnl": pnl})

    if not trades:
        return {
            "symbol": symbol, "trades": 0, "error": "No trades",
            **blocks, "total_runs": total_runs,
        }

    wins = sum(1 for t in trades if t["outcome"] == "win")
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.001
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0)) or 0.001

    return {
        "symbol": symbol, "asset_class": ac,
        "trades": len(trades), "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins/len(trades)*100, 1),
        "profit_factor": round(gp/gl, 2),
        "max_drawdown_pct": round(max_dd*100, 1),
        "total_return_pct": round((balance-10_000)/10_000*100, 1),
        "total_runs": total_runs,
        **blocks,
        "error": None,
    }


def grade(r):
    if r.get("error") or r.get("trades",0) < 10: return "SKIP"
    if r.get("profit_factor",0) >= 1.5 and r.get("win_rate",0) >= 44: return "GOOD"
    if r.get("profit_factor",0) >= 1.2 and r.get("win_rate",0) >= 38: return "MARGINAL"
    return "POOR"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=8)
    args = parser.parse_args()

    symbols = ACTIVE_SYMBOLS if (args.all or not args.symbols) else args.symbols

    print(f"\n{'='*70}")
    print(f"IATIS Full Pipeline Backtest v0.5 — uses run_pipeline() directly")
    print(f"{'='*70}")
    print(f"Symbols: {len(symbols)} | Step: {args.step} bars | Two-Pass | No overlap")
    print()

    from core.data_loader import load_from_csv
    data_dir = Path("data")
    results = []
    t0 = time.monotonic()

    for i, sym in enumerate(symbols, 1):
        csv = next((data_dir/f"{sym}_H1_{s}.csv"
                    for s in ["2y","5y"] if (data_dir/f"{sym}_H1_{s}.csv").exists()), None)

        print(f"[{i:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
        if not csv:
            print("❌ no CSV")
            results.append({"symbol":sym,"error":"No CSV","trades":0})
            continue

        try:
            df = load_from_csv(str(csv))
            r = backtest_symbol(sym, df, step=args.step)
            elapsed = time.monotonic() - t0
            g = grade(r)
            icon = {"GOOD":"✅","MARGINAL":"⚠️","POOR":"❌","SKIP":"⏭"}.get(g,"?")
            print(f"{icon} {r.get('trades',0):>3} trades "
                  f"WR={r.get('win_rate',0):.0f}% PF={r.get('profit_factor',0):.2f} "
                  f"DD={r.get('max_drawdown_pct',0):.0f}% ({elapsed:.0f}s total)")
        except Exception as e:
            print(f"❌ {str(e)[:60]}")
            r = {"symbol":sym,"error":str(e)[:100],"trades":0}
        results.append(r)

    duration = time.monotonic() - t0
    valid = [r for r in results if not r.get("error") and r.get("trades",0) >= 10]

    print(f"\n{'='*70}")
    print(f"SUMMARY ({len(valid)}/{len(results)} | {duration/60:.0f} min)")
    print(f"{'='*70}")
    print(f"{'Symbol':<10} {'Class':<7} {'Trades':>6} {'WR%':>6} {'PF':>6} "
          f"{'DD%':>6} {'Return%':>8} {'Grade'}")
    print("-"*60)
    for r in sorted(valid, key=lambda x:(-grade_num(x), -x.get("profit_factor",0))):
        g = grade(r)
        print(f"{r['symbol']:<10} {r.get('asset_class','?'):<7} "
              f"{r.get('trades',0):>6} {r.get('win_rate',0):>6.1f} "
              f"{r.get('profit_factor',0):>6.2f} {r.get('max_drawdown_pct',0):>6.1f} "
              f"{r.get('total_return_pct',0):>8.1f}   {g}")

    if valid:
        avg_wr = sum(r.get("win_rate",0) for r in valid)/len(valid)
        avg_pf = sum(r.get("profit_factor",0) for r in valid)/len(valid)
        goods = sum(1 for r in valid if grade(r)=="GOOD")
        total_contra = sum(r.get("contradiction",0) for r in valid)
        print(f"\nAvg WR: {avg_wr:.1f}% | Avg PF: {avg_pf:.2f} | GOOD: {goods}/{len(valid)}")
        print(f"Contradiction blocked: {total_contra:,}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "v0.5_run_pipeline_direct",
        "step": args.step, "duration_sec": round(duration,1),
        "results": results,
        "summary": {
            "total": len(symbols), "valid": len(valid),
            "good": sum(1 for r in valid if grade(r)=="GOOD"),
            "marginal": sum(1 for r in valid if grade(r)=="MARGINAL"),
            "poor": sum(1 for r in valid if grade(r)=="POOR"),
        }
    }
    date_str = datetime.now().strftime("%Y-%m-%d")
    p = Path("storage")/f"full_pipeline_backtest_{date_str}.json"
    p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {p}")


def grade_num(r):
    g = grade(r)
    return {"GOOD":2,"MARGINAL":1,"POOR":0,"SKIP":-1}.get(g,0)


if __name__ == "__main__":
    main()
