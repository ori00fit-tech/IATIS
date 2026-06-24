#!/usr/bin/env python3
"""
scripts/run_all_backtests.py
------------------------------
Run backtests on all available historical CSV files in data/.

Usage:
    python3 scripts/run_all_backtests.py
    python3 scripts/run_all_backtests.py --step 8 --years 2
    python3 scripts/run_all_backtests.py --symbols EURUSD XAUUSD
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    from backtesting.backtest_engine import run_backtest, BacktestConfig
    from core.data_loader import load_from_csv

    data_dir = Path("data")
    storage_dir = Path("storage")
    storage_dir.mkdir(exist_ok=True)

    # Find all CSV files
    csv_files = sorted(data_dir.glob("*_H1_*.csv"))
    if args.symbols:
        csv_files = [f for f in csv_files if any(s in f.name for s in args.symbols)]

    if not csv_files:
        print("No CSV files found in data/. Run scripts/download_historical.py first.")
        return

    print(f"\n{'='*55}")
    print(f"IATIS Multi-Symbol Backtest")
    print(f"{'='*55}")
    print(f"Files found: {len(csv_files)}")
    print(f"Step: every {args.step} bars")
    print()

    all_results = []

    for csv_file in csv_files:
        symbol = csv_file.name.split("_")[0]
        pip = 0.01 if any(x in symbol for x in ("JPY", "jpy")) else 0.0001

        print(f"⏳ {symbol} ({csv_file.name})...")
        try:
            df = load_from_csv(str(csv_file))
            print(f"   {len(df)} bars | {df.index[0].date()} → {df.index[-1].date()}")

            config = BacktestConfig(symbol=symbol, pip_size=pip, step_bars=args.step)
            result = run_backtest(df, config)

            out_path = storage_dir / f"backtest_{symbol}_H1_{args.years}y.json"
            result.save(out_path)

            metrics = {
                "symbol": symbol,
                "trades": result.execute_count,
                "win_rate": f"{result.win_rate:.1%}",
                "profit_factor": f"{result.profit_factor:.2f}",
                "max_drawdown": f"{result.max_drawdown_pct:.1%}",
                "total_return": f"{result.total_return_pct:.1%}",
                "sharpe": f"{result.sharpe_ratio:.2f}",
            }
            all_results.append(metrics)

            status = "✅" if result.profit_factor > 1.5 and result.win_rate > 0.5 else "⚠️"
            print(f"   {status} WR={metrics['win_rate']} PF={metrics['profit_factor']} "
                  f"DD={metrics['max_drawdown']} Return={metrics['total_return']}")

        except Exception as exc:
            print(f"   ❌ Failed: {exc}")
        print()

    if all_results:
        print(f"\n{'='*55}")
        print("Summary:")
        print(f"{'Symbol':<10} {'Trades':>6} {'WR':>7} {'PF':>6} {'DD':>7} {'Return':>8} {'Sharpe':>7}")
        print("-" * 55)
        for r in all_results:
            print(f"{r['symbol']:<10} {r['trades']:>6} {r['win_rate']:>7} "
                  f"{r['profit_factor']:>6} {r['max_drawdown']:>7} "
                  f"{r['total_return']:>8} {r['sharpe']:>7}")
        print(f"{'='*55}")

        # Save summary
        summary_path = storage_dir / "backtest_summary.json"
        summary_path.write_text(json.dumps(all_results, indent=2))
        print(f"\nSummary saved: {summary_path}")


if __name__ == "__main__":
    main()
