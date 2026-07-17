#!/usr/bin/env python3
"""
research/experiments/H022_fx_cross_oos.py
------------------------------------------
H022 validation runner — FX-cross universe expansion
(USDCNH, GBPAUD, EURAUD). The DECISION RULE was pre-registered in
research/results/registry.json on 2026-07-17, BEFORE this script ran on
any deep data (CLAUDE.md rule 1). This runner only APPLIES it — nothing
is tuned, nothing in the live config changes.

Method (H008c house standard, per symbol, independently):
  1. Load the deepest available H4 history
     (data/{SYM}_H4_deep.csv — produced by
     scripts/download_deep_history.py --fx-extra USDCNH GBPAUD EURAUD).
  2. Chronological TRAIN(65%)/TEST(35%) split by bar count. Each slice
     runs the FROZEN production strategy through
     backtesting.backtest_engine.run_backtest with the pre-registered
     measured spread as commission. TRAIN is context only; ONLY the
     TEST slice feeds the verdict.
  3. Yearly-stability read on TEST years (h4_yearly_stability shape).

Pre-registered decision rule, applied literally:
  ADOPT-TO-DEMO  iff TEST PF >= 1.2 with n >= 40  AND  no TEST year PF < 0.9
  REJECT         if either condition fails
  INSUFFICIENT_DATA if the TEST slice cannot produce n >= 40

Run on the VPS (passing the names to BOTH flags downloads only these
three: --symbols filters the config universe to nothing, --fx-extra then
appends the candidates):
    venv/bin/python -m scripts.download_deep_history --timeframes 4h \
        --symbols USDCNH GBPAUD EURAUD --fx-extra USDCNH GBPAUD EURAUD
    venv/bin/python -m research.experiments.H022_fx_cross_oos
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

DATA_DIR = PROJECT_ROOT / "data"

# Pre-registered measured broker spreads (pips, engine convention) from
# the 2026-07-17 re-cost sweep — the costs this hypothesis was registered
# against. Do NOT refresh them here mid-validation; a material spread
# change is a reason to re-register, not to silently re-cost.
SPREADS_PIPS: dict[str, float] = {
    "USDCNH": 2.3,
    "GBPAUD": 0.9,
    "EURAUD": 0.7,
}

TRAIN_FRACTION = 0.65
MIN_TEST_TRADES = 40
MIN_TEST_PF = 1.2
MIN_YEAR_PF = 0.9


def _safe_pf(gross_profit: float, gross_loss: float):
    if gross_loss > 0:
        return round(gross_profit / gross_loss, 3)
    return "inf (no losses)" if gross_profit > 0 else None


def _yearly_breakdown(trades: list) -> dict[str, dict]:
    by_year: dict[int, list] = defaultdict(list)
    for t in trades:
        if t.exit_time is None:
            continue
        by_year[pd.Timestamp(t.exit_time).year].append(t)
    out: dict[str, dict] = {}
    for year, yr in sorted(by_year.items()):
        wins = [t for t in yr if t.pnl_usd > 0]
        gp = sum(t.pnl_usd for t in yr if t.pnl_usd > 0)
        gl = abs(sum(t.pnl_usd for t in yr if t.pnl_usd <= 0))
        out[str(year)] = {
            "trades": len(yr),
            "wr": round(len(wins) / len(yr) * 100, 1) if yr else None,
            "pf": _safe_pf(gp, gl),
        }
    return out


def verdict_for(test_pf, test_n: int, yearly: dict[str, dict]) -> tuple[str, list[str]]:
    """Apply the pre-registered H022 rule LITERALLY. Pure function —
    unit-tested so the verdict logic cannot drift from the registry text.

    Returns (verdict, reasons)."""
    reasons: list[str] = []
    if test_n < MIN_TEST_TRADES:
        return "INSUFFICIENT_DATA", [f"TEST n={test_n} < {MIN_TEST_TRADES}"]

    pf_num = test_pf if isinstance(test_pf, (int, float)) else float("inf")
    if pf_num < MIN_TEST_PF:
        reasons.append(f"TEST PF {test_pf} < {MIN_TEST_PF}")

    bad_years = [
        (y, b["pf"]) for y, b in yearly.items()
        if isinstance(b.get("pf"), (int, float)) and b["pf"] < MIN_YEAR_PF
    ]
    for y, pf in bad_years:
        reasons.append(f"TEST year {y} PF {pf} < {MIN_YEAR_PF}")

    return ("REJECT", reasons) if reasons else ("ADOPT_TO_DEMO", [])


def run_symbol(symbol: str) -> dict:
    from backtesting.backtest_engine import BacktestConfig, run_backtest

    path = DATA_DIR / f"{symbol}_H4_deep.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run: python -m scripts.download_deep_history "
            f"--timeframes 4h --fx-extra {symbol}"
        )
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()

    split = int(len(df) * TRAIN_FRACTION)
    train_df, test_df = df.iloc[:split], df.iloc[split:]

    def _run(slice_df: pd.DataFrame) -> dict:
        cfg = BacktestConfig.from_profile(symbol, commission_pips=SPREADS_PIPS[symbol])
        r = run_backtest(slice_df, cfg)
        return {
            "bars": len(slice_df),
            "period": f"{slice_df.index[0].date()} -> {slice_df.index[-1].date()}",
            "trades": r.execute_count,
            "win_rate": round(r.win_rate * 100, 1),
            "profit_factor": (round(r.profit_factor, 3)
                              if r.profit_factor != float("inf") else "inf (no losses)"),
            "max_dd_pct": round(r.max_drawdown_pct * 100, 1),
            "yearly": _yearly_breakdown(r.trades),
        }

    train = _run(train_df)
    test = _run(test_df)
    verdict, reasons = verdict_for(test["profit_factor"], test["trades"], test["yearly"])
    return {
        "symbol": symbol,
        "spread_pips": SPREADS_PIPS[symbol],
        "total_bars": len(df),
        "span_years": round((df.index[-1] - df.index[0]).days / 365.25, 1),
        "train": train,   # context only — never feeds the verdict
        "test": test,
        "verdict": verdict,
        "verdict_reasons": reasons,
    }


def main() -> int:
    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    cfg = load_config()
    results: dict[str, dict] = {}
    datasets = []

    print("H022 — FX-cross OOS validation (pre-registered rule, frozen strategy)\n")
    for symbol in SPREADS_PIPS:
        t0 = time.time()
        try:
            res = run_symbol(symbol)
        except FileNotFoundError as exc:
            print(f"{symbol}: DATA MISSING — {exc}")
            results[symbol] = {"verdict": "DATA_MISSING", "error": str(exc)}
            continue
        results[symbol] = res
        datasets.append(dataset_fingerprint(DATA_DIR / f"{symbol}_H4_deep.csv"))
        t = res["test"]
        print(f"{symbol}: span={res['span_years']}y spread={res['spread_pips']}p")
        print(f"  TRAIN {res['train']['period']}: PF={res['train']['profit_factor']} n={res['train']['trades']}")
        print(f"  TEST  {t['period']}: PF={t['profit_factor']} n={t['trades']} WR={t['win_rate']}% DD={t['max_dd_pct']}%")
        for y, b in t["yearly"].items():
            print(f"    {y}: PF={b['pf']} n={b['trades']}")
        print(f"  VERDICT: {res['verdict']}"
              + (f" — {'; '.join(res['verdict_reasons'])}" if res["verdict_reasons"] else "")
              + f"  ({time.time() - t0:.0f}s)\n")

    manifest = build_manifest(
        kind="h022_fx_cross_oos",
        config=cfg,
        params={
            "hypothesis": "H022",
            "train_fraction": TRAIN_FRACTION,
            "spreads_pips": SPREADS_PIPS,
            "rule": (f"ADOPT-TO-DEMO iff TEST PF >= {MIN_TEST_PF} with n >= "
                     f"{MIN_TEST_TRADES} AND no TEST year PF < {MIN_YEAR_PF} "
                     "(pre-registered 2026-07-17, applied literally)"),
            "engine": "backtesting/backtest_engine.run_backtest (frozen production config)",
        },
        datasets=datasets,
        results=results,
    )
    out = write_manifest(manifest, f"h022_fx_cross_oos_{time.strftime('%Y%m%d')}")
    print(f"Manifest: {out}")
    print("\nNext (HUMAN steps, per the registered rule): update registry.json "
          "H022 status per these verdicts; ADOPT_TO_DEMO symbols may be enabled "
          "in config/symbols.yaml with their own outcome bucket — D001/D002 "
          "symbol sets stay frozen; register D003 BEFORE any adopted outcomes exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
