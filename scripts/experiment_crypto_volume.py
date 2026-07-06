#!/usr/bin/env python3
"""
scripts/experiment_crypto_volume.py
--------------------------------------
Does REAL crypto volume improve the system? (evidence for "add volume")

Measured fact (2026-07-05): every Twelve Data H4 series — including
BTC/ETH — returns volume=0, so Wyckoff's volume analysis silently
degrades to price-only for crypto too. Real exchange volume IS available
via core/ccxt_provider (Binance et al.). This script A/B tests whether
routing crypto through ccxt (real volume) beats the current zero-volume
feed, holding everything else fixed.

Method: for BTCUSD/ETHUSD, fetch H4 both ways, run the frozen production
pipeline over each, compare PF/WR/trades. Wyckoff is the only engine that
consumes volume, so any delta is attributable to its volume signals
activating. Emits an H2 manifest.

MUST run on a host with exchange network access (the VPS) — ccxt is
blocked from the CI/audit sandbox.

    python3 scripts/experiment_crypto_volume.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from utils.helpers import load_config
from backtesting.backtest_engine import BacktestConfig, run_backtest

SYMBOLS = ["BTCUSD", "ETHUSD"]
DATA = Path(__file__).resolve().parent.parent / "data"


def _summ(df: pd.DataFrame, cfg: dict, symbol: str) -> dict:
    r = run_backtest(df, BacktestConfig.from_profile(symbol, step_bars=1), engine_config=cfg)
    closed = [t for t in r.trades if t.exit_bar >= 0]
    return {
        "bars": len(df),
        "volume_nonzero_pct": round(100 * (pd.to_numeric(df["volume"], errors="coerce").fillna(0) > 0).mean(), 1),
        "trades": len(closed),
        "win_rate": round(100 * r.win_rate, 1),
        "profit_factor": round(r.profit_factor, 3),
        "max_dd_pct": round(100 * r.max_drawdown_pct, 1),
    }


def main() -> None:
    cfg = load_config()
    from core.ccxt_provider import fetch_ccxt

    results = {}
    for sym in SYMBOLS:
        # A) current feed (zero-volume): the deep H4 CSV
        csv = DATA / f"{sym}_H4_deep.csv"
        if not csv.exists():
            print(f"{sym}: no {csv.name}; run download_deep_history.py first")
            continue
        zero_df = pd.read_csv(csv, index_col=0, parse_dates=True)
        zero_df.index = pd.to_datetime(zero_df.index, utc=True)

        # B) real volume via ccxt, same tail length as the CSV for a fair compare
        days = int((zero_df.index[-1] - zero_df.index[0]).days) + 5
        print(f"{sym}: fetching {days}d of H4 via ccxt (real volume)…")
        real_df = fetch_ccxt(sym, timeframe="4h", days=min(days, 1500))
        if real_df is None or real_df.empty:
            print(f"{sym}: ccxt returned nothing (network/exchange?) — skipping")
            continue

        a = _summ(zero_df, cfg, sym)
        b = _summ(real_df, cfg, sym)

        # CONTROLLED arm — isolates volume from window/price-source: the
        # SAME ccxt bars, once real and once with volume zeroed. Any delta
        # here is attributable to volume ALONE (only Wyckoff consumes it).
        real_zeroed = real_df.copy()
        real_zeroed["volume"] = 0.0
        c = _summ(real_zeroed, cfg, sym)
        controlled_delta = round(b["profit_factor"] - c["profit_factor"], 3)

        results[sym] = {
            "zero_volume_feed_TD": a,
            "real_volume_ccxt": b,
            "ccxt_bars_volume_zeroed": c,
            "cross_feed_delta_pf": round(b["profit_factor"] - a["profit_factor"], 3),
            "controlled_volume_delta_pf": controlled_delta,
        }
        print(f"  TD zero-vol        : PF={a['profit_factor']} WR={a['win_rate']}% n={a['trades']}")
        print(f"  ccxt real-vol      : PF={b['profit_factor']} WR={b['win_rate']}% n={b['trades']} (vol {b['volume_nonzero_pct']}%)")
        print(f"  ccxt vol-ZEROED    : PF={c['profit_factor']} WR={c['win_rate']}% n={c['trades']}  (same bars, volume removed)")
        print(f"  → CONTROLLED ΔPF (volume alone) = {controlled_delta:+}   [cross-feed ΔPF={results[sym]['cross_feed_delta_pf']:+}, confounded]")

    try:
        from research.manifest import build_manifest, write_manifest
        m = build_manifest(
            kind="crypto_volume_experiment", config=cfg,
            params={"question": "Does real ccxt volume beat the zero-volume TD feed for crypto?",
                    "symbols": SYMBOLS, "decision_timeframe": "H4",
                    "note": "Wyckoff is the only volume consumer; IN-SAMPLE."},
            datasets=[], results=results)
        out = write_manifest(m, f"crypto_volume_{time.strftime('%Y%m%d')}")
        print(f"\nManifest: {out}")
    except Exception as exc:
        print(f"manifest skipped: {exc}")

    print("\nInterpretation: keep the ccxt route for crypto ONLY if ΔPF is a "
          "clear, consistent improvement across both symbols. A wash or "
          "regression means volume adds nothing here — report it honestly.")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
