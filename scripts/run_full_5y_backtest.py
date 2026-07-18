#!/usr/bin/env python3
"""
scripts/run_full_5y_backtest.py
----------------------------------
Comprehensive IATIS Backtest — All Symbols, Maximum History.

Downloads maximum available data and runs the full pipeline backtest
on every active symbol, then generates a consolidated report.

Data sources:
  - H1 (hourly): up to 2 years from Yahoo Finance (yfinance limit)
  - For true 5-year coverage: uses daily data resampled where needed

Usage:
    python3 scripts/run_full_5y_backtest.py              # all symbols, 2y H1
    python3 scripts/run_full_5y_backtest.py --years 5    # 5 years daily
    python3 scripts/run_full_5y_backtest.py --symbols EURUSD XAUUSD
    python3 scripts/run_full_5y_backtest.py --skip-download  # use existing CSVs
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_all_symbols import (
    ALL_SYMBOLS, PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT
)
from scripts.full_pipeline_backtest import (
    ACTIVE_SYMBOLS, build_config, simulate_trade, calc_pnl
)
from utils.logger import get_logger

logger = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def download_all(symbols: dict[str, str], years: int, force: bool = False) -> dict[str, Path]:
    """Download data for all symbols. Returns {symbol: csv_path}."""
    import yfinance as yf
    import pandas as pd

    DATA_DIR.mkdir(exist_ok=True)
    downloaded = {}

    interval = "1h" if years <= 2 else "1d"
    suffix = f"H1_{years}y" if years <= 2 else f"D1_{years}y"

    for sym, yf_ticker in symbols.items():
        filename = f"{sym}_{suffix}.csv"
        filepath = DATA_DIR / filename

        if filepath.exists() and not force:
            try:
                df = pd.read_csv(filepath, index_col=0, parse_dates=True)
                if len(df) >= 100:
                    print(f"  ✓ {sym:10} {len(df):>6} bars (cached)")
                    downloaded[sym] = filepath
                    continue
            except Exception:
                pass

        print(f"  ↓ {sym:10} downloading {years}y {interval}... ", end="", flush=True)
        try:
            ticker = yf.Ticker(yf_ticker)
            df_raw = ticker.history(period=f"{years}y", interval=interval, auto_adjust=True)

            if df_raw.empty:
                print("EMPTY")
                continue

            df = df_raw.rename(columns={
                "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume"
            })[["open", "high", "low", "close", "volume"]].copy()

            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")

            df.index.name = "datetime"
            df.to_csv(filepath)
            print(f"{len(df)} bars ✓")
            downloaded[sym] = filepath
            time.sleep(0.5)  # rate limit

        except Exception as e:
            print(f"ERROR: {str(e)[:60]}")

    return downloaded


def run_backtest_for_symbol(symbol: str, csv_path: Path, step: int = 8,
                            warmup: int = 220) -> dict:
    """Run full pipeline backtest for one symbol."""
    from core.data_loader import load_from_csv
    from main import run_pipeline

    df = load_from_csv(str(csv_path))
    if len(df) < warmup + 50:
        return {"symbol": symbol, "error": f"Insufficient data: {len(df)} bars"}

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)
    cfg = build_config(symbol)

    balance = 10_000.0
    initial_balance = balance
    trades = []
    open_until = -1
    n = len(df)
    equity_curve = [balance]
    peak = balance
    max_dd = 0.0
    engine_votes_log = []

    for i in range(warmup, n - 2, step):
        if i <= open_until:
            continue

        cfg["data"]["source"] = "injected"
        cfg.setdefault("system", {})["backtest_mode"] = True  # offline backtest: skip live persistence (D1)
        cfg["data"]["_injected_df"] = df.iloc[:i+1].copy()
        cfg["data"]["symbol"] = symbol

        try:
            report = run_pipeline(cfg)
        except Exception:
            continue

        if report.get("final_verdict") != "EXECUTE":
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

        sim = simulate_trade(entry, sl, tp, direction, df, i + 1)
        pnl = calc_pnl(entry, sim["exit"], direction, sl_dist,
                       balance, symbol, ac, pip, dpp)
        balance += pnl
        open_until = i + sim["bars"]

        # Track trade
        is_win = sim["outcome"] == "win"
        trades.append({
            "bar": i,
            "date": str(df.index[i])[:10],
            "direction": "BUY" if direction == 1 else "SELL",
            "entry": entry,
            "exit": sim["exit"],
            "pnl": round(pnl, 2),
            "outcome": sim["outcome"],
            "bars_held": sim["bars"],
        })

        # Track engine votes for this trade
        engine_votes = {}
        for eng in report.get("engine_outputs", []):
            name = eng.get("engine", "?")
            engine_votes[name] = {
                "bias": eng.get("bias", "NEUTRAL"),
                "score": eng.get("score", 0),
            }
        engine_votes_log.append({
            "outcome": sim["outcome"],
            "votes": engine_votes,
            "regime": report.get("regime", {}).get("state", "?"),
            "score": report.get("confluence", {}).get("score", 0),
        })

        # Equity tracking
        equity_curve.append(balance)
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Compute results
    total_trades = len(trades)
    if total_trades == 0:
        return {"symbol": symbol, "error": "No trades", "bars": len(df)}

    wins = sum(1 for t in trades if t["outcome"] == "win")
    losses = total_trades - wins
    win_rate = wins / total_trades * 100

    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0
    total_return = (balance - initial_balance) / initial_balance * 100

    avg_win = gross_profit / wins if wins > 0 else 0
    avg_loss = gross_loss / losses if losses > 0 else 0
    expectancy = (win_rate/100 * avg_win) - ((100-win_rate)/100 * avg_loss)

    # Engine attribution
    engine_stats = {}
    for log in engine_votes_log:
        is_win = log["outcome"] == "win"
        for name, vote in log["votes"].items():
            if name not in engine_stats:
                engine_stats[name] = {"wins": 0, "losses": 0, "neutral": 0}
            if vote["bias"] == "NEUTRAL":
                engine_stats[name]["neutral"] += 1
            elif is_win:
                engine_stats[name]["wins"] += 1
            else:
                engine_stats[name]["losses"] += 1

    engine_attribution = {}
    for name, stats in engine_stats.items():
        active = stats["wins"] + stats["losses"]
        if active > 0:
            engine_attribution[name] = {
                "active_trades": active,
                "win_rate": round(stats["wins"] / active * 100, 1),
                "neutral_rate": round(stats["neutral"] / (active + stats["neutral"]) * 100, 1),
            }

    # Regime breakdown
    regime_stats = {}
    for log in engine_votes_log:
        r = log.get("regime", "?")
        if r not in regime_stats:
            regime_stats[r] = {"wins": 0, "losses": 0}
        if log["outcome"] == "win":
            regime_stats[r]["wins"] += 1
        else:
            regime_stats[r]["losses"] += 1

    regime_report = {}
    for r, s in regime_stats.items():
        total = s["wins"] + s["losses"]
        if total > 0:
            regime_report[r] = {
                "trades": total,
                "win_rate": round(s["wins"] / total * 100, 1),
            }

    return {
        "symbol": symbol,
        "bars": len(df),
        "date_range": f"{str(df.index[0])[:10]} → {str(df.index[-1])[:10]}",
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "total_return_pct": round(total_return, 1),
        "max_drawdown_pct": round(max_dd, 1),
        "final_balance": round(balance, 2),
        "expectancy_usd": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "engine_attribution": engine_attribution,
        "regime_breakdown": regime_report,
    }


def main():
    parser = argparse.ArgumentParser(description="IATIS Full System Backtest")
    parser.add_argument("--years", type=int, default=2,
                        help="Years of history (2 for H1, 5 for D1)")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--step", type=int, default=8,
                        help="Bars between checks (default: 8)")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download, use existing CSVs")
    parser.add_argument("--force-download", action="store_true",
                        help="Force re-download even if cached")
    args = parser.parse_args()

    symbols_to_test = args.symbols or list(ALL_SYMBOLS.keys())
    symbols_dict = {s: ALL_SYMBOLS[s] for s in symbols_to_test if s in ALL_SYMBOLS}

    print(f"\n{'='*70}")
    print(f"IATIS COMPREHENSIVE BACKTEST")
    print(f"{'='*70}")
    print(f"Symbols:  {len(symbols_dict)}")
    print(f"History:  {args.years} years")
    print(f"Step:     {args.step} bars")
    print(f"Date:     {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}\n")

    # Phase 1: Download data
    if not args.skip_download:
        print("Phase 1: Downloading data...")
        csv_map = download_all(symbols_dict, args.years, force=args.force_download)
    else:
        csv_map = {}
        suffix = f"H1_{args.years}y" if args.years <= 2 else f"D1_{args.years}y"
        for sym in symbols_dict:
            for pattern in [f"{sym}_{suffix}.csv", f"{sym}_H1_2y.csv",
                           f"{sym}_H1_5y.csv", f"{sym}_D1_5y.csv"]:
                p = DATA_DIR / pattern
                if p.exists():
                    csv_map[sym] = p
                    break

    if not csv_map:
        print("ERROR: No data available. Run without --skip-download first.")
        sys.exit(1)

    print(f"\n{len(csv_map)} symbols with data available.\n")

    # Phase 2: Run backtests
    print("Phase 2: Running backtests...")
    print(f"{'Symbol':<10} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Return%':>9} {'MaxDD%':>7}")
    print("-" * 55)

    results = []
    t0 = time.monotonic()

    for i, (sym, csv_path) in enumerate(sorted(csv_map.items()), 1):
        try:
            r = run_backtest_for_symbol(sym, csv_path, step=args.step)
            results.append(r)

            if "error" in r:
                print(f"{sym:<10} {'—':>7} {'—':>6} {'—':>6} {'—':>9} {'—':>7}  ⚠ {r['error']}")
            else:
                pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] < 100 else "∞"
                print(f"{sym:<10} {r['trades']:>7} {r['win_rate']:>5.1f}% "
                      f"{pf_str:>6} {r['total_return_pct']:>+8.1f}% "
                      f"{r['max_drawdown_pct']:>6.1f}%")
        except Exception as e:
            print(f"{sym:<10} ERROR: {str(e)[:50]}")
            results.append({"symbol": sym, "error": str(e)[:100]})

    duration = time.monotonic() - t0

    # Phase 3: Aggregate results
    valid = [r for r in results if "error" not in r and r.get("trades", 0) > 0]

    if not valid:
        print("\nNo valid backtest results. Check data files.")
        sys.exit(1)

    total_trades = sum(r["trades"] for r in valid)
    total_wins = sum(r["wins"] for r in valid)
    total_pnl = sum(r["final_balance"] - 10000 for r in valid)
    avg_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    avg_pf = sum(r["profit_factor"] for r in valid if r["profit_factor"] < 100) / len(valid) if valid else 0
    avg_dd = sum(r["max_drawdown_pct"] for r in valid) / len(valid) if valid else 0

    # Aggregate engine attribution
    all_engine_stats = {}
    for r in valid:
        for name, stats in r.get("engine_attribution", {}).items():
            if name not in all_engine_stats:
                all_engine_stats[name] = {"total_active": 0, "total_wins": 0}
            all_engine_stats[name]["total_active"] += stats["active_trades"]
            all_engine_stats[name]["total_wins"] += round(
                stats["active_trades"] * stats["win_rate"] / 100
            )

    # Aggregate regime stats
    all_regime_stats = {}
    for r in valid:
        for regime, stats in r.get("regime_breakdown", {}).items():
            if regime not in all_regime_stats:
                all_regime_stats[regime] = {"trades": 0, "wins": 0}
            all_regime_stats[regime]["trades"] += stats["trades"]
            wins_count = round(stats["trades"] * stats["win_rate"] / 100)
            all_regime_stats[regime]["wins"] += wins_count

    print(f"\n{'='*70}")
    print(f"AGGREGATE RESULTS ({len(valid)} symbols, {duration/60:.0f} min)")
    print(f"{'='*70}")
    print(f"Total Trades:    {total_trades}")
    print(f"Win Rate:        {avg_wr:.1f}%")
    print(f"Avg Profit Factor: {avg_pf:.2f}")
    print(f"Avg Max Drawdown:  {avg_dd:.1f}%")
    print(f"Total P&L:       ${total_pnl:+,.0f}")

    # Top 5 / Bottom 5
    ranked = sorted(valid, key=lambda x: x.get("profit_factor", 0), reverse=True)
    print(f"\n{'─'*40}")
    print("TOP 5 SYMBOLS (by Profit Factor):")
    for r in ranked[:5]:
        print(f"  {r['symbol']:<10} PF={r['profit_factor']:.2f}  WR={r['win_rate']:.1f}%  Return={r['total_return_pct']:+.1f}%")

    print("\nBOTTOM 5 SYMBOLS:")
    for r in ranked[-5:]:
        print(f"  {r['symbol']:<10} PF={r['profit_factor']:.2f}  WR={r['win_rate']:.1f}%  Return={r['total_return_pct']:+.1f}%")

    # Engine attribution
    if all_engine_stats:
        print(f"\n{'─'*40}")
        print("ENGINE ATTRIBUTION (across all symbols):")
        print(f"{'Engine':<18} {'Active':>7} {'WR%':>7}")
        print("-" * 35)
        for name, stats in sorted(all_engine_stats.items(),
                                  key=lambda x: x[1]["total_wins"]/max(x[1]["total_active"],1),
                                  reverse=True):
            wr = stats["total_wins"] / stats["total_active"] * 100 if stats["total_active"] > 0 else 0
            print(f"  {name:<16} {stats['total_active']:>7} {wr:>6.1f}%")

    # Regime breakdown
    if all_regime_stats:
        print(f"\n{'─'*40}")
        print("REGIME PERFORMANCE:")
        for regime, stats in sorted(all_regime_stats.items()):
            wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
            print(f"  {regime:<12} {stats['trades']:>5} trades  WR={wr:.1f}%")

    # Save full report
    report = {
        "date": datetime.now().isoformat(),
        "config": {"years": args.years, "step": args.step, "symbols": len(valid)},
        "aggregate": {
            "total_trades": total_trades,
            "win_rate": round(avg_wr, 1),
            "avg_profit_factor": round(avg_pf, 2),
            "avg_max_drawdown": round(avg_dd, 1),
            "total_pnl": round(total_pnl, 2),
        },
        "engine_attribution": {
            name: {
                "active_trades": s["total_active"],
                "win_rate": round(s["total_wins"]/max(s["total_active"],1)*100, 1),
            }
            for name, s in all_engine_stats.items()
        },
        "regime_performance": {
            r: {"trades": s["trades"], "win_rate": round(s["wins"]/max(s["trades"],1)*100, 1)}
            for r, s in all_regime_stats.items()
        },
        "per_symbol": results,
    }

    out_path = Path("storage") / f"backtest_full_{args.years}y_{datetime.now().strftime('%Y%m%d')}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nFull report saved: {out_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
