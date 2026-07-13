#!/usr/bin/env python3
"""
scripts/run_h4_yearly_backtest.py
-----------------------------------
Re-run the frozen production system (engines/thresholds untouched — this
is a measurement, never a tuning run) against the deepest H4 history each
symbol's provider actually serves, bucketed by exit year — the same
methodology as the historical h4_yearly_stability manifest
(research/results/h4_yearly_stability_20260705_manifest.json), re-pointed
at scripts/download_deep_history.py's output instead of a smaller window.

D1 confirmation is derived by resampling the SAME H4 series internally
(core.timeframe_sync.build_multi_timeframe_view) — no separate D1 file is
read, exactly matching how backtesting.backtest_engine.run_backtest
already handles multi-timeframe context for every other caller.

IN-SAMPLE relative to system development (the frozen config was chosen
using earlier, shorter backtests) — this is stability evidence across a
longer window, not out-of-sample proof. Forward paper trading remains the
only prospective evidence per CLAUDE.md.

Usage:
    python3 scripts/run_h4_yearly_backtest.py                    # all 20 symbols
    python3 scripts/run_h4_yearly_backtest.py --symbols EURUSD XAUUSD BTCUSD
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ALL_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF",
    "XAUUSD", "XAGUSD", "USOIL", "US30", "NAS100", "SPX500",
    "BTCUSD", "ETHUSD",
]


def _load_h4(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_H4_deep.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run scripts/download_deep_history.py first "
            f"(--symbols {symbol})"
        )
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.sort_index()


def _safe_pf(gross_profit: float, gross_loss: float) -> float | str | None:
    """JSON-safe profit factor — float("inf") is not valid JSON and would
    raise on json.dumps, so an all-wins bucket becomes a sentinel string
    instead (same convention as storage/outcome_tracker.performance_summary)."""
    if gross_loss > 0:
        return round(gross_profit / gross_loss, 2)
    return "inf (no losses)" if gross_profit > 0 else None


def _yearly_breakdown(trades: list) -> dict[str, dict]:
    """Bucket closed trades by exit year — same shape as the historical
    h4_yearly_stability manifest's per-symbol "yearly" block."""
    by_year: dict[int, list] = defaultdict(list)
    for t in trades:
        if t.exit_time is None:
            continue
        year = pd.Timestamp(t.exit_time).year
        by_year[year].append(t)

    out: dict[str, dict] = {}
    for year, yr_trades in sorted(by_year.items()):
        wins = [t for t in yr_trades if t.pnl_usd > 0]
        gross_profit = sum(t.pnl_usd for t in yr_trades if t.pnl_usd > 0)
        gross_loss = abs(sum(t.pnl_usd for t in yr_trades if t.pnl_usd <= 0))
        out[str(year)] = {
            "trades": len(yr_trades),
            "wr": round(len(wins) / len(yr_trades) * 100, 1) if yr_trades else None,
            "pf": _safe_pf(gross_profit, gross_loss),
        }
    return out


def run_one(symbol: str) -> dict | None:
    from backtesting.backtest_engine import run_backtest, BacktestConfig

    t0 = time.time()
    df = _load_h4(symbol)
    config = BacktestConfig.from_profile(symbol)
    result = run_backtest(df, config)
    runtime_s = round(time.time() - t0, 1)

    pf = result.profit_factor
    return {
        "bars": len(df),
        "period": f"{df.index[0].date()} -> {df.index[-1].date()}",
        "trades": result.execute_count,
        "win_rate": round(result.win_rate * 100, 1),
        "profit_factor": round(pf, 3) if pf != float("inf") else "inf (no losses)",
        "max_dd_pct": round(result.max_drawdown_pct * 100, 1),
        "yearly": _yearly_breakdown(result.trades),
        "runtime_s": runtime_s,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=None)
    args = ap.parse_args()

    symbols = args.symbols or ALL_SYMBOLS

    from utils.helpers import load_config
    cfg = load_config()

    per_symbol: dict[str, dict] = {}
    for sym in symbols:
        print(f"{sym}: ", end="", flush=True)
        try:
            per_symbol[sym] = run_one(sym)
            r = per_symbol[sym]
            print(f"{r['trades']} trades, WR={r['win_rate']}%, PF={r['profit_factor']}, "
                  f"span={r['period']}, {r['runtime_s']}s")
        except Exception as exc:
            print(f"FAILED: {exc}")

    if not per_symbol:
        print("\nNo symbols produced a result — nothing to write.")
        return

    try:
        from research.manifest import build_manifest, dataset_fingerprint, write_manifest

        datasets = [
            {"symbol": sym, **dataset_fingerprint(DATA_DIR / f"{sym}_H4_deep.csv")}
            for sym in per_symbol
        ]
        manifest = build_manifest(
            kind="h4_yearly_stability_deep",
            config=cfg,
            params={
                "engine": "backtesting/backtest_engine.run_backtest",
                "method": "One continuous full-history backtest per symbol with "
                          "the frozen production config, using the deepest H4 "
                          "history scripts/download_deep_history.py serves; "
                          "closed trades bucketed by exit year. Frozen config = "
                          "no training = a stability read across regimes, not "
                          "walk-forward optimization.",
                "engines_enabled": cfg.get("engines", {}).get("enabled", {}),
                "decision_timeframe": "H4",
                "note": "IN-SAMPLE relative to system development; forward paper "
                        "trading remains the only prospective evidence "
                        "(CLAUDE.md). Supersedes h4_yearly_stability_20260705 "
                        "with a deeper H4 window per symbol (see per-symbol "
                        "'period').",
            },
            datasets=datasets,
            results={"per_symbol": per_symbol},
        )
        out_path = write_manifest(manifest, f"h4_yearly_stability_deep_{time.strftime('%Y%m%d')}")
        print(f"\nManifest: {out_path}")
    except Exception as exc:
        print(f"\nManifest write failed (results still printed above): {exc}")


if __name__ == "__main__":
    main()
