#!/usr/bin/env python3
"""
scripts/backtest_ic_symbols.py
---------------------------------
Discover which IC Markets (cTrader) symbols the IATIS strategy has an
edge on — using the broker's own historical bars and real spread.

Pipeline:
  1. Connect to cTrader, enumerate every tradeable symbol.
  2. For each (optionally filtered) symbol, pull H4 history via the
     broker's trendbars API, measure the live spread, and run the frozen
     production backtest with that real cost.
  3. Rank by profit factor; write a git-tracked manifest + a CSV.

The IATIS confluence strategy is built for liquid macro instruments
(FX / metals / energy / indices / crypto). IC Markets also lists ~300
single-stock CFDs the strategy is NOT designed for, so the default run
skips obvious equity tickers; pass --all to include everything.

RUN ON THE VPS (cTrader Open API is network-blocked from the sandbox).
Validate first with a single-symbol probe, THEN sweep:

    python3 scripts/backtest_ic_symbols.py --probe EURUSD      # sanity: prints bars
    python3 scripts/backtest_ic_symbols.py --list              # dump all symbol names
    python3 scripts/backtest_ic_symbols.py --limit 40          # backtest first 40 macro
    python3 scripts/backtest_ic_symbols.py --all               # everything (slow)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

DATA = PROJECT_ROOT / "data"
MIN_BARS = 500

# Heuristic: macro instruments the strategy targets look like 6-letter FX,
# metals/energy, or known index/crypto tickers. Everything else (equity
# CFDs like AAPL.US, TSLA.US) is skipped unless --all.
_MACRO_HINTS = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
                "XAU", "XAG", "XPT", "XPD", "OIL", "WTI", "BRENT", "NAS",
                "US30", "US500", "SPX", "GER", "UK100", "BTC", "ETH", "LTC",
                "XRP", "SOL")


def _looks_macro(name: str) -> bool:
    n = name.upper()
    if "." in n:  # equity CFDs are usually TICKER.EXCHANGE
        return False
    return any(h in n for h in _MACRO_HINTS)


def _to_df(bars: list[dict]):
    import pandas as pd
    if not bars:
        return None
    df = pd.DataFrame(bars)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("datetime")[["open", "high", "low", "close", "volume"]].sort_index()


def _pip_size(name: str) -> float:
    n = name.upper()
    if "JPY" in n:
        return 0.01
    if any(x in n for x in ("XAU", "XAG", "OIL", "WTI", "BRENT")):
        return 0.01
    return 0.0001


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", help="Fetch+print bars for ONE symbol and exit (validation)")
    ap.add_argument("--list", action="store_true", help="Print all broker symbols and exit")
    ap.add_argument("--all", action="store_true", help="Include equity CFDs, not just macro")
    ap.add_argument("--limit", type=int, default=0, help="Max symbols to backtest (0=all)")
    ap.add_argument("--count", type=int, default=1200, help="H4 bars to fetch per symbol")
    args = ap.parse_args()

    from execution.ctrader_client import CTraderClient, IATIS_TO_CTRADER

    client = CTraderClient()
    print("Connecting to cTrader…")
    if not client.connect(timeout=30.0):
        sys.exit("❌ Could not reach READY (expired CTRADER_ACCESS_TOKEN?).")
    symbols = client.list_symbols()
    print(f"✅ {len(symbols)} tradeable symbols on the account.\n")

    if args.list:
        for s in symbols:
            print(s)
        client.disconnect()
        return

    if args.probe:
        bars = client.get_trendbars(args.probe, period="H4", count=20)
        print(f"probe {args.probe}: {len(bars)} bars")
        for b in bars[-5:]:
            print("  ", time.strftime("%Y-%m-%d %H:%M", time.gmtime(b["timestamp"])),
                  {k: b[k] for k in ("open", "high", "low", "close", "volume")})
        client.disconnect()
        if not bars:
            print("\n⚠️ No bars — check the trendbars decode/scaling before sweeping.")
        return

    universe = symbols if args.all else [s for s in symbols if _looks_macro(s)]
    if args.limit:
        universe = universe[: args.limit]
    print(f"Backtesting {len(universe)} symbols (real broker H4 + real spread)…\n")

    from backtesting.backtest_engine import BacktestConfig, run_backtest
    from utils.helpers import load_config
    cfg = load_config()
    DATA.mkdir(exist_ok=True)

    rows = []
    print(f"{'symbol':14s}{'bars':>7s}{'spread':>8s}{'trades':>7s}{'WR%':>6s}{'PF':>7s}")
    for sym in universe:
        bars = client.get_trendbars(sym, period="H4", count=args.count)
        df = _to_df(bars)
        if df is None or len(df) < MIN_BARS:
            continue
        # real spread in pips from live quote (only for mapped IATIS symbols;
        # get_spot resolves via IATIS_TO_CTRADER)
        q = client.get_spot(sym) if sym in IATIS_TO_CTRADER else None
        spread_pips = round((q[1] - q[0]) / _pip_size(sym), 2) if q else None
        kwargs = {"step_bars": 2}
        if spread_pips:
            kwargs["commission_pips"] = spread_pips
        try:
            r = run_backtest(df, BacktestConfig.from_profile(sym, **kwargs), engine_config=cfg)
        except Exception as exc:
            print(f"{sym:14s}  backtest error: {str(exc)[:40]}")
            continue
        rows.append({"symbol": sym, "bars": len(df),
                     "spread_pips": spread_pips, "trades": r.execute_count,
                     "win_rate": round(100 * r.win_rate, 1),
                     "profit_factor": round(r.profit_factor, 3),
                     "max_dd_pct": round(100 * r.max_drawdown_pct, 1)})
        print(f"{sym:14s}{len(df):>7}{str(spread_pips):>8}{r.execute_count:>7}"
              f"{100*r.win_rate:>6.1f}{r.profit_factor:>7.2f}")

    client.disconnect()
    rows.sort(key=lambda x: x["profit_factor"], reverse=True)
    winners = [r for r in rows if r["profit_factor"] > 1.1 and r["trades"] >= 20]
    print(f"\n{len(winners)} symbols with PF>1.1 and >=20 trades (of {len(rows)} backtested):")
    for r in winners[:30]:
        print(f"  {r['symbol']:12s} PF={r['profit_factor']} WR={r['win_rate']}% "
              f"n={r['trades']} spread={r['spread_pips']}")

    try:
        from research.manifest import build_manifest, write_manifest
        m = build_manifest(kind="ic_symbols_backtest", config=cfg,
            params={"universe": "all" if args.all else "macro",
                    "count": args.count, "min_bars": MIN_BARS,
                    "source": "IC Markets H4 trendbars + live spread via cTrader",
                    "note": "Discovery scan; IN-SAMPLE. Promote a winner only after "
                            "walk-forward + forward demo evidence."},
            datasets=[], results={"ranked": rows, "winners": winners})
        out = write_manifest(m, f"ic_symbols_backtest_{time.strftime('%Y%m%d')}")
        print(f"\nManifest: {out}")
    except Exception as exc:
        print(f"manifest skipped: {exc}")

    (DATA / "ic_symbols_backtest.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
