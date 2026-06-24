#!/usr/bin/env python3
"""
scripts/run_backtest.py
------------------------
Run IATIS walk-forward backtest on historical data.

Usage:
    python3 scripts/run_backtest.py --symbol EURUSD --years 2
    python3 scripts/run_backtest.py --symbol XAUUSD --years 3 --interval H1
    python3 scripts/run_backtest.py --file data/EURUSD_H1_2y.csv
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--interval", default="H1")
    parser.add_argument("--file", default=None, help="Use local CSV instead of yfinance")
    parser.add_argument("--balance", type=float, default=10000)
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (0.01 = 1%%)")
    parser.add_argument("--step", type=int, default=4, help="Run pipeline every N bars")
    parser.add_argument("--save", default=None, help="Save result JSON to path")
    args = parser.parse_args()

    from backtesting.backtest_engine import run_backtest, BacktestConfig

    # Load data
    if args.file:
        from core.data_loader import load_from_csv
        print(f"Loading from file: {args.file}")
        df = load_from_csv(args.file)
    else:
        from core.alt_data_loader import load_from_yfinance
        print(f"Downloading {args.symbol} {args.interval} × {args.years}y from Yahoo Finance...")
        df = load_from_yfinance(args.symbol, interval=args.interval, period=f"{args.years}y")

    print(f"Data: {len(df)} bars | {df.index[0].date()} → {df.index[-1].date()}")

    config = BacktestConfig.from_profile(
        args.symbol,
        initial_balance=args.balance,
        risk_per_trade=args.risk,
        step_bars=args.step,
    )

    print(f"\nRunning backtest (step={args.step} bars)...")
    print("This may take a few minutes on 2+ years of H1 data...\n")

    result = run_backtest(df, config)
    print(result.summary())

    if args.save:
        result.save(args.save)
        print(f"\nResult saved: {args.save}")
    else:
        out = f"storage/backtest_{args.symbol}_{args.interval}_{args.years}y.json"
        result.save(out)
        print(f"\nResult saved: {out}")

if __name__ == "__main__":
    main()
