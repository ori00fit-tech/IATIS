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
    """MUST match the backtest engine's pip convention, or measured
    spreads enter the cost model at the wrong scale. Delegates to the
    shared TCA implementation (storage/execution_quality.pip_size_for),
    which mirrors backtest_engine.config_for_symbol exactly — including
    its unknown-symbol fallback, so sweep pips and engine pips agree for
    every candidate. (The old inline version gave crypto 0.0001 while
    the engine uses 0.01 — a latent 100x cost error for BTC/ETH spreads,
    fixed 2026-07-17.)"""
    from storage.execution_quality import pip_size_for
    return pip_size_for(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", help="Fetch+print bars for ONE symbol and exit (validation)")
    ap.add_argument("--list", action="store_true", help="Print all broker symbols and exit")
    ap.add_argument("--all", action="store_true", help="Include equity CFDs, not just macro")
    ap.add_argument("--limit", type=int, default=0, help="Max symbols to backtest (0=all)")
    ap.add_argument("--count", type=int, default=1200, help="H4 bars to fetch per symbol")
    ap.add_argument("--fresh", action="store_true", help="Ignore any checkpoint and restart the sweep")
    ap.add_argument("--symbols", nargs="+", default=None,
                    help="Re-cost mode: sweep ONLY these broker symbols, re-running "
                         "them even if the checkpoint already has them (use after a "
                         "spread-map/cost fix to re-price prior candidates in minutes)")
    ap.add_argument("--sleep", type=float, default=0.0, help="Seconds to pause between symbols (be nice to the API)")
    args = ap.parse_args()

    from execution.ctrader_client import CTraderClient

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

    if args.symbols:
        wanted = [s.upper() for s in args.symbols]
        by_upper = {s.upper(): s for s in symbols}
        unknown = [s for s in wanted if s not in by_upper]
        if unknown:
            print(f"⚠️ Not on the broker: {unknown} — skipped")
        universe = [by_upper[s] for s in wanted if s in by_upper]
    else:
        universe = symbols if args.all else [s for s in symbols if _looks_macro(s)]
        if args.limit:
            universe = universe[: args.limit]
    print(f"Backtesting {len(universe)} symbols (real broker H4 + real spread)…\n")

    from backtesting.backtest_engine import BacktestConfig, run_backtest
    from utils.helpers import load_config
    cfg = load_config()
    DATA.mkdir(exist_ok=True)

    # Checkpoint: a big sweep (~351 symbols) is long, and a killed process
    # must not lose finished work. We persist after EVERY symbol and, on the
    # next run, skip whatever was already attempted (both scored and skipped),
    # so an interrupted run just resumes. Pass --fresh to start over.
    ckpt = DATA / "ic_symbols_backtest.json"
    rows: list[dict] = []
    attempted: set[str] = set()
    if ckpt.exists() and not args.fresh:
        try:
            saved = json.loads(ckpt.read_text())
            rows = saved.get("rows", saved if isinstance(saved, list) else [])
            attempted = set(saved.get("attempted", [r["symbol"] for r in rows]))
            print(f"↩️  Resuming: {len(attempted)} symbols already attempted "
                  f"({len(rows)} scored). Pass --fresh to restart.\n")
        except Exception as exc:
            print(f"(could not read checkpoint, starting fresh: {exc})")

    def _save() -> None:
        ckpt.write_text(json.dumps(
            {"attempted": sorted(attempted), "rows": rows}, indent=1))

    # Re-cost mode: targeted symbols re-run even if already attempted, and
    # their new row REPLACES the old one (no duplicate symbols in results).
    if args.symbols:
        targeted = {s.upper() for s in args.symbols}
        attempted -= {s for s in attempted if s.upper() in targeted}
        rows[:] = [r for r in rows if r["symbol"].upper() not in targeted]

    print(f"{'symbol':14s}{'bars':>7s}{'spread':>8s}{'trades':>7s}{'WR%':>6s}{'PF':>7s}")
    for i, sym in enumerate(universe, 1):
        if sym in attempted:
            continue
        attempted.add(sym)
        bars = client.get_trendbars(sym, period="H4", count=args.count)
        df = _to_df(bars)
        if df is None or len(df) < MIN_BARS:
            print(f"{sym:14s}  skipped ({0 if df is None else len(df)} bars < {MIN_BARS})")
            _save()
            continue
        # Real spread from a live quote, for EVERY broker symbol
        # (get_spot_by_name, 2026-07-17 — previously only the 20 mapped
        # IATIS symbols paid a real cost). Timestamp recorded because a
        # single snapshot can catch off-hours wide quotes (the XAUUSD
        # 40-pip lesson from the 07-06 sweep) — judge spread WITH its hour.
        q = client.get_spot_by_name(sym)
        spread_pips = round((q[1] - q[0]) / _pip_size(sym), 2) if q else None
        kwargs = {"step_bars": 2}
        if spread_pips is not None:
            kwargs["commission_pips"] = spread_pips
        try:
            r = run_backtest(df, BacktestConfig.from_profile(sym, **kwargs), engine_config=cfg)
        except Exception as exc:
            print(f"{sym:14s}  backtest error: {str(exc)[:40]}")
            _save()
            continue
        rows.append({"symbol": sym, "bars": len(df),
                     "spread_pips": spread_pips,
                     "spread_measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                     if spread_pips is not None else None,
                     "trades": r.execute_count,
                     "win_rate": round(100 * r.win_rate, 1),
                     "profit_factor": round(r.profit_factor, 3),
                     "max_dd_pct": round(100 * r.max_drawdown_pct, 1)})
        print(f"{sym:14s}{len(df):>7}{str(spread_pips):>8}{r.execute_count:>7}"
              f"{100*r.win_rate:>6.1f}{r.profit_factor:>7.2f}  [{i}/{len(universe)}]")
        _save()
        if args.sleep:
            time.sleep(args.sleep)

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

    _save()  # final flush (incremental checkpoint already holds every row)
    print(f"Results: {ckpt}")


if __name__ == "__main__":
    main()
