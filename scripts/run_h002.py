#!/usr/bin/env python3
"""
run_h002.py
------------
Runs H002 experiment (qualified liquidity sweep) on real data
fetched from Twelve Data. Run this on the VPS where API keys are set.

Usage (from the repo root):
    python3 scripts/run_h002.py                     # EURUSD default
    python3 scripts/run_h002.py --symbol GBPUSD
    python3 scripts/run_h002.py --symbol XAUUSD --bars 2000

Cost: 2 Twelve Data API credits (M15 + H1).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Moved into scripts/ 2026-07-23 (audit P2-3) — previously relied on being
# run directly from the repo root, which put the repo root on sys.path[0]
# implicitly. Now explicit, so `from core...`/`from research...` below
# resolve regardless of invocation style.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run H002 hypothesis experiment")
    parser.add_argument("--symbol", default="EUR/USD",
                        help="Twelve Data symbol (default: EUR/USD)")
    parser.add_argument("--bars", type=int, default=5000,
                        help="Bars per timeframe (default: 5000, max on Free plan)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force fresh API calls (don't use cache)")
    parser.add_argument("--atr-multiplier", type=float, default=None,
                        help="Override ATR_MULTIPLIER in experiment (e.g. 0.3, 0.5)")
    parser.add_argument("--forward-bars", type=int, default=None,
                        help="Override FORWARD_BARS in experiment (e.g. 10, 20, 40)")
    args = parser.parse_args()

    api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: TWELVE_DATA_API_KEY not set in .env")

    from core.twelve_data_client import TwelveDataClient, RateLimiter
    remaining = RateLimiter().remaining_today()
    print(f"Twelve Data credits remaining: {remaining}")
    if remaining < 3:
        sys.exit("ERROR: Insufficient API credits (need at least 3)")

    print(f"\nFetching {args.symbol} M15 and H1 from Twelve Data...")
    client = TwelveDataClient(api_key=api_key)

    print(f"  Fetching M15 ({args.bars} bars)...")
    df_m15 = client.time_series(
        args.symbol, "M15",
        outputsize=min(args.bars, 5000),
        use_cache=not args.no_cache,
    )
    print(f"  M15: {len(df_m15)} bars | {df_m15.index[0].date()} → {df_m15.index[-1].date()}")

    print(f"  Fetching H1 ({args.bars} bars)...")
    df_h1 = client.time_series(
        args.symbol, "H1",
        outputsize=min(args.bars, 5000),
        use_cache=not args.no_cache,
    )
    print(f"  H1:  {len(df_h1)} bars | {df_h1.index[0].date()} → {df_h1.index[-1].date()}")

    symbol_clean = args.symbol.replace("/", "")
    source = (
        f"real:TwelveData_{symbol_clean}_M15+H1_"
        f"{df_m15.index[0].date()}_{df_m15.index[-1].date()}"
    )

    print(f"\nRunning H002 experiment on {source}...")

    # Apply any overrides before running
    import research.experiments.H002_qualified_sweep as h002_mod
    if args.atr_multiplier is not None:
        h002_mod.ATR_MULTIPLIER = args.atr_multiplier
        print(f"  ATR_MULTIPLIER overridden to {args.atr_multiplier}")
    if args.forward_bars is not None:
        h002_mod.FORWARD_BARS = args.forward_bars
        print(f"  FORWARD_BARS overridden to {args.forward_bars}")

    from research.experiments.H002_qualified_sweep import run_experiment
    result = run_experiment(df_m15, df_h1, source=source)

    # Pretty print
    print("\n" + "=" * 60)
    print(f"H002 RESULT: {result.status}")
    print("=" * 60)
    print(f"Symbol:              {args.symbol}")
    print(f"Data source:         {result.data_source}")
    print(f"Raw sweeps:          {result.sample_size_unfiltered}")
    print(f"Qualified sweeps:    {result.sample_size_qualified}")
    if result.qualified_win_rate is not None:
        print(f"Qualified win rate:  {result.qualified_win_rate:.2%}")
        print(f"H001 baseline:       {result.h001_baseline_win_rate:.2%}")
        print(f"Improvement:         {result.win_rate_improvement:+.2%}")
        print(f"p-value:             {result.p_value:.4f}")
    print(f"Notes:               {result.notes}")
    print()
    print("Filter stats:")
    for k, v in result.filter_stats.items():
        print(f"  {k}: {v}")

    # Update registry if PASSED
    if result.status == "PASSED":
        print("\n✅ H002 PASSED — updating registry.json...")
        _update_registry(result)
        print("Registry updated. Restart scheduler to activate smc_advanced.")
    elif result.status == "FAILED":
        print("\n❌ H002 FAILED — registry unchanged.")
        print("Next step: design H003 with different approach (e.g. session filter).")
    else:
        print(f"\n⚠️  H002 {result.status} — need more data.")

    print(f"\nFull result saved to: research/results/H002_result.json")


def _update_registry(result) -> None:
    import json
    from pathlib import Path
    registry_path = Path("research/results/registry.json")
    with open(registry_path) as f:
        registry = json.load(f)
    registry["hypotheses"]["H002"]["status"] = "PASSED"
    registry["hypotheses"]["H002"]["tested_on"] = result.data_source
    registry["hypotheses"]["H002"]["win_rate"] = result.qualified_win_rate
    registry["hypotheses"]["H002"]["p_value"] = result.p_value
    registry["hypotheses"]["H002"]["last_updated"] = str(__import__("datetime").date.today())
    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2)


if __name__ == "__main__":
    main()
